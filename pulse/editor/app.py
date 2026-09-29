"""Housing at Noon editor — FastAPI app (127.0.0.1:8240 behind Caddy).

Routes
  GET  /                     editor UI (owner only)
  GET  /login, POST /login   password -> signed cookie
  GET  /magic?d=&k=          one-tap link from the draft-ready email
  GET  /api/draft?d=today    draft row (+ live free count); ingests on demand
  PUT  /api/draft/{date}     {version, json} -> saves a new version (409 if stale)
  POST /api/draft/{date}/status   {status: draft|held}
  POST /api/draft/{date}/send-test {tier}
  POST /api/draft/{date}/send-now  {confirm: true}
  POST /api/draft/{date}/reset     rebuild from the stored brief (discards edits)
  POST /api/upload           multipart PNG/JPEG (<= 8 MB) -> {url, width, height};
                             stored under NOON_IMAGES_DIR/YYYY/MM/<sha1[:12]>.<ext>
  GET  /images/...           public: the uploaded images (StaticFiles)
  GET  /preview/{date}?tier=       the exact HTML that would be sent
  GET  /api/drafts           recent days
  GET  /latest[?k=]        public: latest edition (free; premium with the emailed key)
  GET  /latest.pdf          public: most recent edition as PDF (the social
                            version: free edition with sign-up copy)
  GET  /latest-premium.pdf  owner: most recent premium edition as PDF
  GET  /pdf/{date}?tier=    owner: render the draft to PDF now
                            (tier = premium | free | social)
  GET  /health
  GET  /cards/{date}          owner: render the cards (one per free theme, then a sign-up card) and the carousel
                              PDF, and show them with a text panel per card (static/cards.html, cards.js)
  GET  /cards/{date}/{n}      owner: card n (PNG); /cards/{date}/carousel.pdf the PDF
  GET  /api/cards/{date}      per card: pos, title, body, budget, has_override (+ image URL, title size, fit)
  PUT  /api/cards/{date}/{pos}     {title, body} -> the owner's text for that card (pos 0 = CTA line)
  DELETE /api/cards/{date}/{pos}   back to the generated text
  POST /api/cards/{date}/render    re-render all cards and the PDF (no Claude call for edited cards)
  GET  /cards-font/medium.otf owner: the card title font, so the panel can check the one-line title
  GET  /sources               owner: LinkedIn accounts the collector reads (sources.py)
  GET  /api/sources           accounts with recent activity and include state
  POST /api/sources/include   {key, include} -> rewrites linkedin_targets.json
  POST /api/sources/fetch     {keys} -> recent posts from Apify (<= 25 accounts, cached)
  POST /api/sources/publish   commit and push linkedin_targets.json
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import paths
import auth
import ingest
import render
import sender
import drafts
import sources

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("noon.app")

app = FastAPI(title="Housing at Noon editor", docs_url=None, redoc_url=None)
STATIC = paths.EDITOR_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
# Images the owner drops into a theme from the editor (POST /api/upload). Served by
# this app so the Caddy config need not change; the public URL is
# https://noon.homeeconomics.us/images/YYYY/MM/<id>.png and the email embeds it.
IMAGES_DIR = Path(os.environ.get("NOON_IMAGES_DIR", str(Path.home() / "work" / "noon" / "images")))
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
IMAGE_MAX_BYTES = 8 * 1024 * 1024

SEND_STATE_LABEL = {"draft": "Sends at noon ET", "held": "Held — will not send", "sent": "Sent"}


# ── auth helpers ────────────────────────────────────────────────────────

def _authed(request: Request) -> bool:
    return auth.session_ok(request.cookies.get(auth.COOKIE))


def _require(request: Request) -> None:
    if not _authed(request):
        raise HTTPException(status_code=401, detail="not signed in")


def _set_cookie(resp: Response) -> Response:
    resp.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=paths.BASE_URL.startswith("https"))
    return resp


@app.middleware("http")
async def no_store(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path.startswith("/preview/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ── pages ───────────────────────────────────────────────────────────────

@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    """Same Home Economics mark as the main site, so bookmarks and tabs for the
    editor, the web edition and the PDFs carry the icon."""
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/icon.svg", include_in_schema=False)
def favicon_svg():
    return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, bad: int = 0):
    if _authed(request):
        return RedirectResponse("/", status_code=302)
    html = (STATIC / "login.html").read_text()
    return HTMLResponse(html.replace("{{ERROR}}", "Wrong password." if bad else ""))


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if auth.password_ok(str(form.get("password", ""))):
        return _set_cookie(RedirectResponse("/", status_code=303))
    return RedirectResponse("/login?bad=1", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/magic")
def magic(d: str, k: str):
    if not auth.magic_ok(d, k, drafts.today_et()):
        raise HTTPException(status_code=403, detail="link expired or invalid")
    return _set_cookie(RedirectResponse(f"/?d={d}", status_code=302))


@app.get("/latest", response_class=HTMLResponse)
def latest(k: str | None = None):
    """Public 'Read on the web' page: the latest edition. Free unless the
    premium key from a premium email is present."""
    row = drafts.latest_sent()
    if row is None:
        raise HTTPException(status_code=404, detail="no edition yet")
    tier = "premium" if auth.web_token_ok(k) else "free"
    html = render.preview(row["json"], tier)
    # Web page, not email: every link opens in a new tab so the edition stays put.
    html = html.replace("<head>", '<head>\n<base target="_blank">', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/latest.pdf")
def latest_pdf():
    """Public: the most recent edition PDF (written after each send). This
    is the social version — the free edition with sign-up copy."""
    import pdf
    f = pdf.PDF_DIR / "latest-free.pdf"
    if not f.exists():
        raise HTTPException(status_code=404, detail="no PDF yet")
    return FileResponse(str(f), media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=\"Housing at Noon.pdf\""})


@app.get("/latest-premium.pdf")
def latest_premium_pdf(request: Request):
    """Owner: the most recent premium edition PDF (every theme, live links)."""
    _require(request)
    import pdf
    f = pdf.PDF_DIR / "latest.pdf"
    if not f.exists():
        raise HTTPException(status_code=404, detail="no PDF yet")
    return FileResponse(str(f), media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=\"Housing at Noon premium.pdf\""})


@app.get("/pdf/{date}")
def draft_pdf(request: Request, date: str, tier: str = "premium"):
    """Owner: render this draft to PDF now and show it (tier: premium | free | social)."""
    _require(request)
    import pdf
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    tier = tier if tier in ("premium", "free", "social") else "free"
    out = pdf.PDF_DIR / "preview" / f"Housing at Noon {date} {tier}.pdf"
    pdf.make_pdf(row["json"], out, tier)
    return FileResponse(str(out), media_type="application/pdf",
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": f"inline; filename=\"Housing at Noon {date}.pdf\""})


@app.get("/health")
def health():
    return {"ok": True, "send_mode": paths.SEND_MODE, "today": drafts.today_et()}


# ── API ─────────────────────────────────────────────────────────────────

def _payload(row: dict) -> dict:
    shown, total = render.free_count(row["json"])
    return {
        "date": row["date"], "version": row["version"], "status": row["status"],
        "status_label": SEND_STATE_LABEL.get(row["status"], row["status"]),
        "date_label": render.date_label(row["date"]),
        "sent_at": row.get("sent_at"), "send_log": row.get("send_log"),
        "updated_at": row["updated_at"], "source_id": row.get("source_id"),
        "free_shown": shown, "total": total, "json": row["json"],
        "today": drafts.today_et(), "send_mode": paths.SEND_MODE,
    }


@app.get("/api/draft")
def get_draft(request: Request, d: str = Query("today")):
    _require(request)
    date = drafts.today_et() if d == "today" else d
    row = drafts.get(date)
    if row is None and date == drafts.today_et():
        row = ingest.ingest(date)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No brief for {date} yet.")
    return _payload(row)


@app.put("/api/draft/{date}")
def put_draft(request: Request, date: str, body: dict[str, Any] = Body(...)):
    _require(request)
    version = body.get("version")
    obj = body.get("json")
    if not isinstance(version, int) or not isinstance(obj, dict):
        raise HTTPException(status_code=400, detail="need {version:int, json:object}")
    obj["date"] = date
    try:
        row = drafts.save(date, obj, version)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such draft")
    except drafts.Conflict as c:
        return JSONResponse(status_code=409, content={"detail": "draft changed elsewhere",
                                                     "current": _payload(c.current)})
    return _payload(row)


@app.post("/api/draft/{date}/status")
def set_status(request: Request, date: str, body: dict[str, Any] = Body(...)):
    _require(request)
    status = body.get("status")
    if status not in ("draft", "held"):
        raise HTTPException(status_code=400, detail="status must be draft or held")
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    if row["status"] == "sent":
        raise HTTPException(status_code=400, detail="already sent")
    return _payload(drafts.set_status(date, status))


@app.post("/api/draft/{date}/send-test")
def send_test(request: Request, date: str, body: dict[str, Any] = Body(default={})):
    _require(request)
    tier = body.get("tier", "free")
    if tier not in ("free", "premium"):
        raise HTTPException(status_code=400, detail="tier must be free or premium")
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    ok = sender.send_test(row["json"], tier)
    if not ok:
        raise HTTPException(status_code=502, detail="Resend rejected the test email")
    return {"ok": True, "to": paths.OWNER_EMAIL, "tier": tier}


@app.post("/api/draft/{date}/send-now")
def send_now(request: Request, date: str, body: dict[str, Any] = Body(default={})):
    _require(request)
    if body.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="confirm required")
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    if row["status"] == "sent":
        raise HTTPException(status_code=400, detail="already sent")
    ok, line = sender.send_final(row["json"])
    if not ok:
        raise HTTPException(status_code=502, detail=line)
    payload = _payload(drafts.set_status(date, "sent", send_log=f"manual: {line}"))
    # Same PDF step the timer path runs (cli._pdf_after_send); in a thread so
    # the button returns at once. Failures alert the owner, never the reader.
    threading.Thread(target=_pdf_after_send, args=(row["json"],), daemon=True).start()
    return payload


def _pdf_after_send(draft: dict) -> None:
    try:
        import pdf
        pdf.publish_pdf(draft)
    except Exception as e:  # noqa: BLE001
        logger.error(f"pdf generation failed after manual send: {e}")
        sender.send_alert("PDF generation failed", str(e))


@app.post("/api/draft/{date}/reset")
def reset_draft(request: Request, date: str):
    _require(request)
    row = drafts.get(date)
    if row and row["status"] == "sent":
        raise HTTPException(status_code=400, detail="already sent")
    new = ingest.ingest(date, replace=True)
    if new is None:
        raise HTTPException(status_code=404, detail="no stored brief to rebuild from")
    return _payload(new)


# ── image upload ───────────────────────────────────────────────────────

def _convert_to_png(data: bytes) -> bytes | None:
    """Any image Pillow can decode (HEIC from an iPhone via pillow-heif, WebP, GIF,
    TIFF, BMP) re-encoded as PNG; None when it is not an image. Added 2026-09-08 after
    the owner's first upload from a phone was refused as 'only PNG or JPEG'."""
    try:
        import io
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.info(f"upload is not a decodable image: {e}")
        return None


def _image_kind(data: bytes) -> str | None:
    """'png' or 'jpg' from the file's magic bytes; None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    return None


def _image_size(data: bytes, kind: str) -> tuple[int, int]:
    """(width, height) — Pillow when available, else read from the header."""
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001  (no Pillow, or an odd file)
        pass
    if kind == "png" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if kind == "jpg":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            i += 2 + seg_len
    return 0, 0


@app.post("/api/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Store one PNG or JPEG for use as ![caption](url) in a summary."""
    _require(request)
    data = await file.read(IMAGE_MAX_BYTES + 1)
    if len(data) > IMAGE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="image is over 8 MB")
    kind = _image_kind(data)
    if not kind:
        converted = _convert_to_png(data)
        if converted is None:
            raise HTTPException(status_code=400, detail="that file is not an image this editor can read (PNG, JPEG, HEIC, WebP, GIF, TIFF)")
        if len(converted) > IMAGE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="image is over 8 MB after conversion")
        data, kind = converted, "png"
    width, height = _image_size(data, kind)
    now = datetime.now(timezone.utc)
    rel = f"{now:%Y}/{now:%m}/{hashlib.sha1(data).hexdigest()[:12]}.{kind}"
    dest = IMAGES_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(data)
    logger.info(f"image stored: {rel} ({len(data):,} bytes, {width}x{height})")
    return {"url": f"{paths.BASE_URL}/images/{rel}", "width": width, "height": height, "bytes": len(data)}


# One render at a time: the page load, the Re-render button and the noon send's render all
# write the same files.
_CARDS_LOCK = threading.Lock()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _cards_date(date: str) -> dict:
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=404, detail="no such draft")
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    return row


def _render_cards(row: dict) -> None:
    import cards
    with _CARDS_LOCK:
        cards.publish_cards(row["json"])


def _cards_payload(date: str) -> dict:
    """The card panel's data: the last render's manifest plus the stored overrides."""
    import json as _json
    import cards
    mf = cards.manifest_path(date)
    if not mf.exists():
        raise HTTPException(status_code=409, detail="cards not rendered yet")
    m = _json.loads(mf.read_text())
    ovs = drafts.card_overrides(date)

    def img(n: int) -> str:
        f = cards.CARDS_DIR / f"Housing at Noon {date} card{n}.png"
        v = f.stat().st_mtime_ns if f.exists() else 0
        return f"/cards/{date}/{n}?v={v}"

    out = []
    for c in m["cards"]:
        ov = ovs.get(c["pos"])
        out.append({
            "pos": c["pos"], "n": c["n"], "num": c["num"],
            # the panel shows the owner's full text when there is one (the image may have cut it)
            "title": (ov["title"] if ov and ov["title"].strip() else c["title"]),
            "body": (ov["body"] if ov and ov["body"].strip() else c["full_body"]),
            "budget": c["budget"], "has_override": ov is not None,
            "gen_title": c["gen_title"], "title_px": c["title_px"], "title_wrap": c["title_wrap"],
            "shown_chars": c["chars"], "cut": c["cut"], "missing": c["missing"], "how": c["how"],
            "image": img(c["n"]), "updated_at": ov["updated_at"] if ov else None,
        })
    pdf = cards.CARDS_DIR / f"Housing at Noon {date} carousel.pdf"
    cta = m["cta"]
    return {"date": date, "date_label": render.date_label(date), "rendered_at": m["rendered_at"],
            "cards": out,
            "cta": {"pos": 0, "n": cta["n"], "desc": cta["desc"], "default_desc": cta["default_desc"],
                    "has_override": 0 in ovs, "image": img(cta["n"])},
            "pdf": f"/cards/{date}/carousel.pdf?v={pdf.stat().st_mtime_ns if pdf.exists() else 0}",
            "title_limits": {"max_px": 54, "min_px": 40, "width_px": 920, "tracking_em": -0.03}}


@app.get("/cards/{date}", response_class=HTMLResponse)
def draft_cards(request: Request, date: str):
    """Owner: render this draft's social cards and the carousel PDF now, and show them
    for saving, each with a text panel for editing it. Owner's rules (28-29 Sep 2026): no
    posting step, he posts them himself; no intro card; every free theme on ONE card at a
    constant 36 px body, condensed by Claude when it is too long (no continuation cards),
    then a closing sign-up card; for Instagram and X carousels and a LinkedIn document PDF.
    "I want a way to edit the text, some kind of editor, like the main one": the panel's
    text is stored as an override (drafts.card_overrides) and used by every later render.
    The first render of a date calls Claude (a few cents); later renders use
    ~/work/noon/cards_cache.json, and edited cards never call it.

    Uses publish_cards, so they land in the normal cards folder and mirror to Dropbox
    immediately; the send's own render later overwrites both copies (with the overrides)."""
    _require(request)
    row = _cards_date(date)
    _render_cards(row)
    return FileResponse(STATIC / "cards.html", headers={"Cache-Control": "no-store"})


@app.get("/api/cards/{date}")
def api_cards(request: Request, date: str):
    _require(request)
    row = _cards_date(date)
    import cards
    if not cards.manifest_path(date).exists():
        _render_cards(row)
    return _cards_payload(date)


@app.put("/api/cards/{date}/{pos}")
def api_card_put(request: Request, date: str, pos: int, body: dict[str, Any] = Body(...)):
    """Store the owner's text for one card. No render and no Claude call here."""
    _require(request)
    _cards_date(date)
    title, text = body.get("title", ""), body.get("body", "")
    if not isinstance(title, str) or not isinstance(text, str):
        raise HTTPException(status_code=400, detail="need {title: string, body: string}")
    if len(title) > 300 or len(text) > 6000:
        raise HTTPException(status_code=400, detail="text too long")
    if pos < 0 or pos > 12:
        raise HTTPException(status_code=404, detail="no such card")
    if pos == 0:
        title = ""
    if not title.strip() and not text.strip():
        drafts.delete_card_override(date, pos)
        return {"ok": True, "has_override": False}
    r = drafts.set_card_override(date, pos, " ".join(title.split()), text.replace("\r", "").strip())
    return {"ok": True, "has_override": True, "updated_at": r["updated_at"]}


@app.delete("/api/cards/{date}/{pos}")
def api_card_delete(request: Request, date: str, pos: int):
    _require(request)
    _cards_date(date)
    return {"ok": True, "removed": drafts.delete_card_override(date, pos)}


@app.post("/api/cards/{date}/render")
def api_cards_render(request: Request, date: str):
    _require(request)
    row = _cards_date(date)
    _render_cards(row)
    return _cards_payload(date)


@app.get("/cards-font/medium.otf")
def cards_font(request: Request):
    """The card title font (ABC Oracle Edu Medium), owner only, so the card panel can
    measure whether a typed title fits on one line."""
    _require(request)
    f = Path(os.environ.get("NOON_CARDS_FONT", str(Path.home() / ".local/share/fonts/ABCOracle-Medium.otf")))
    if not f.exists():
        raise HTTPException(status_code=404, detail="font not found")
    return FileResponse(str(f), media_type="font/otf", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/cards/{date}/carousel.pdf")
def draft_cards_pdf(request: Request, date: str):
    _require(request)
    import cards
    f = cards.CARDS_DIR / f"Housing at Noon {date} carousel.pdf"
    if "/" in date or not f.exists():
        raise HTTPException(status_code=404, detail="carousel not rendered")
    return FileResponse(str(f), media_type="application/pdf",
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": f'attachment; filename="Housing at Noon {date} carousel.pdf"'})


@app.get("/cards/{date}/{n}")
def draft_card_png(request: Request, date: str, n: int):
    _require(request)
    import cards
    f = cards.CARDS_DIR / f"Housing at Noon {date} card{int(n)}.png"
    if not f.exists():
        raise HTTPException(status_code=404, detail="card not rendered")
    return FileResponse(str(f), media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": f'inline; filename="Housing at Noon {date} card{int(n)}.png"'})


@app.get("/api/drafts")
def list_drafts(request: Request):
    _require(request)
    return drafts.list_recent()


@app.get("/preview/{date}", response_class=HTMLResponse)
def preview(request: Request, date: str, tier: str = "free"):
    _require(request)
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    tier = "premium" if tier == "premium" else "free"
    return HTMLResponse(render.preview(row["json"], tier))


# ── LinkedIn sources ────────────────────────────────────────────────────

@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    if not _authed(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC / "sources.html", headers={"Cache-Control": "no-store"})


@app.get("/api/sources")
def sources_list(request: Request):
    _require(request)
    return sources.overview()


@app.post("/api/sources/include")
def sources_include(request: Request, body: dict[str, Any] = Body(...)):
    _require(request)
    try:
        return sources.set_included(str(body.get("key") or ""), bool(body.get("include")))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sources/fetch")
def sources_fetch(request: Request, body: dict[str, Any] = Body(...)):
    _require(request)
    try:
        return sources.fetch_previews([str(k) for k in body.get("keys") or []])
    except Exception as e:  # noqa: BLE001
        logger.exception("sources fetch failed")
        raise HTTPException(status_code=502, detail=f"Apify fetch failed: {e}")


@app.post("/api/sources/publish")
def sources_publish(request: Request):
    _require(request)
    try:
        return sources.publish()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
