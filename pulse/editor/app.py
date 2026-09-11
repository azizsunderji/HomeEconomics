"""News at Noon editor — FastAPI app (127.0.0.1:8240 behind Caddy).

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
"""
from __future__ import annotations

import hashlib
import logging
import os
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("noon.app")

app = FastAPI(title="News at Noon editor", docs_url=None, redoc_url=None)
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
                        headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=\"News at Noon.pdf\""})


@app.get("/latest-premium.pdf")
def latest_premium_pdf(request: Request):
    """Owner: the most recent premium edition PDF (every theme, live links)."""
    _require(request)
    import pdf
    f = pdf.PDF_DIR / "latest.pdf"
    if not f.exists():
        raise HTTPException(status_code=404, detail="no PDF yet")
    return FileResponse(str(f), media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=\"News at Noon premium.pdf\""})


@app.get("/pdf/{date}")
def draft_pdf(request: Request, date: str, tier: str = "premium"):
    """Owner: render this draft to PDF now and show it (tier: premium | free | social)."""
    _require(request)
    import pdf
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    tier = tier if tier in ("premium", "free", "social") else "free"
    out = pdf.PDF_DIR / "preview" / f"News at Noon {date} {tier}.pdf"
    pdf.make_pdf(row["json"], out, tier)
    return FileResponse(str(out), media_type="application/pdf",
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": f"inline; filename=\"News at Noon {date}.pdf\""})


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


@app.get("/cards/{date}", response_class=HTMLResponse)
def draft_cards(request: Request, date: str):
    """Owner: render this draft's four social cards now and show them for saving.

    Writes to the normal cards folder; the send's own render overwrites them."""
    _require(request)
    import cards
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    out_dir = cards.CARDS_DIR
    paths = cards.render_cards(row["json"], out_dir)
    items = "".join(
        f'<figure><a href="/cards/{date}/{i}" download><img src="/cards/{date}/{i}"></a>'
        f'<figcaption>Card {i} &middot; <a href="/cards/{date}/{i}" download>save</a></figcaption></figure>'
        for i, _p in enumerate(paths, start=1))
    html = (
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Cards &middot; {date}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "background:#F6F7F3;color:#3D3733;margin:0;padding:24px;}"
        "h1{font-size:20px;font-weight:600;margin:0 0 4px 0;}p{color:#777370;font-size:14px;margin:0 0 24px 0;}"
        "figure{margin:0 0 28px 0;}img{width:100%;max-width:540px;height:auto;display:block;}"
        "figcaption{font-size:13px;color:#777370;margin-top:6px;}a{color:#3D3733;}</style>"
        f"<h1>Cards for {date}</h1><p>Rendered from the draft as it stands. "
        "Long-press an image to save it, or use the save link.</p>" + items)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/cards/{date}/{n}")
def draft_card_png(request: Request, date: str, n: int):
    _require(request)
    import cards
    f = cards.CARDS_DIR / f"News at Noon {date} card{int(n)}.png"
    if not f.exists():
        raise HTTPException(status_code=404, detail="card not rendered")
    return FileResponse(str(f), media_type="image/png",
                        headers={"Cache-Control": "no-store",
                                 "Content-Disposition": f'inline; filename="News at Noon {date} card{int(n)}.png"'})


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
