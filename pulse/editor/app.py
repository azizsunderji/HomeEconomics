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
  GET  /preview/{date}?tier=       the exact HTML that would be sent
  GET  /api/drafts           recent days
  GET  /latest[?k=]        public: latest edition (free; premium with the emailed key)
  GET  /latest.pdf          public: most recent edition as PDF
  GET  /pdf/{date}?tier=    owner: render the draft to PDF now
  GET  /health
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
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
    """Public: the most recent edition PDF (written after each send)."""
    import pdf
    f = pdf.PDF_DIR / "latest.pdf"
    if not f.exists():
        raise HTTPException(status_code=404, detail="no PDF yet")
    return FileResponse(str(f), media_type="application/pdf",
                        headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=\"News at Noon.pdf\""})


@app.get("/pdf/{date}")
def draft_pdf(request: Request, date: str, tier: str = "premium"):
    """Owner: render this draft to PDF now and show it."""
    _require(request)
    import pdf
    row = drafts.get(date)
    if row is None:
        raise HTTPException(status_code=404, detail="no such draft")
    out = pdf.PDF_DIR / "preview" / f"News at Noon {date} {tier}.pdf"
    pdf.make_pdf(row["json"], out, "premium" if tier == "premium" else "free")
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
