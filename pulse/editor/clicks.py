"""Resend webhook events for Housing at Noon (clicks, opens, deliveries, bounces, complaints), stored in
~/work/noon/clicks.db.

Owner (21 Oct 2026): "take advantage of link click tracking to be smarter about marketing ... push people to the
paid tier"; step one is the plumbing. Resend posts every event to POST /hooks/resend on the editor (app.py), signed
with the Svix scheme (headers svix-id, svix-timestamp, svix-signature; HMAC-SHA256 over "id.timestamp.body" with the
base64 secret after "whsec_"). Each stored row keeps the recipient, the edition and tier (from the tags the sender
attaches), the clicked link, the walled-link id (?l=xxxxxxxx, see variants.wall_link) and whether the click hit the
upgrade wall. The raw event is kept too.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DB = Path(os.environ.get("NOON_CLICKS_DB", str(Path.home() / "work" / "noon" / "clicks.db")))
SECRET = os.environ.get("RESEND_WEBHOOK_SECRET", "")
TOLERANCE_SECONDS = 5 * 60
ET = ZoneInfo("America/New_York")
# A click on any of these is a free reader hitting the upgrade wall (variants.UPGRADE_WALL_URL and the portal's newer path).
WALL_MARKERS = ("homeeconomics.us/pulse/upgrade", "homeeconomics.us/noon/upgrade")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  svix_id     TEXT PRIMARY KEY,
  type        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  received_at TEXT NOT NULL,
  email       TEXT,
  email_id    TEXT,
  subject     TEXT,
  edition     TEXT,
  tier        TEXT,
  link        TEXT,
  link_id     TEXT,
  wall        INTEGER NOT NULL DEFAULT 0,
  user_agent  TEXT,
  ip          TEXT,
  raw         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_edition ON events(edition, type);
CREATE INDEX IF NOT EXISTS ix_events_email ON events(email, type);
"""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def verify(body: bytes, headers) -> bool:
    """Svix signature check. `headers` is any case-insensitive mapping (Starlette's request.headers)."""
    sid = headers.get("svix-id")
    ts = headers.get("svix-timestamp")
    sigs = headers.get("svix-signature")
    if not (SECRET and sid and ts and sigs):
        return False
    try:
        if abs(time.time() - int(ts)) > TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(SECRET.split("_", 1)[1] if SECRET.startswith("whsec_") else SECRET)
    except (ValueError, IndexError):
        return False
    expected = base64.b64encode(hmac.new(key, f"{sid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, s.split(",", 1)[1]) for s in sigs.split() if s.startswith("v1,"))


def _link_id(link: str | None) -> str | None:
    m = re.search(r"[?&]l=([0-9a-f]{8})\b", link or "")
    return m.group(1) if m else None


def _edition_fallback(created_at: str | None) -> str | None:
    """Emails sent before the sender tagged them: the edition is the New York date of the event."""
    try:
        return datetime.fromisoformat((created_at or "").replace("Z", "+00:00")).astimezone(ET).date().isoformat()
    except ValueError:
        return None


INSERT_SQL = "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"


def _row(body: bytes, svix_id: str, received_at: str | None = None) -> tuple:
    event = json.loads(body)
    d = event.get("data") or {}
    tags = d.get("tags") or {}
    if isinstance(tags, list):  # defensive: the send API takes [{name, value}], the webhook doc shows a dict
        tags = {t.get("name"): t.get("value") for t in tags if isinstance(t, dict)}
    click = d.get("click") or {}
    link = click.get("link")
    to = d.get("to") or []
    created = event.get("created_at") or d.get("created_at") or datetime.now(timezone.utc).isoformat()
    return (
        svix_id, event.get("type") or "unknown", created,
        received_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        (to[0] if to else "").lower() or None, d.get("email_id"), d.get("subject"),
        tags.get("edition") or _edition_fallback(created), tags.get("tier"),
        link, _link_id(link), int(bool(link and any(m in link for m in WALL_MARKERS))),
        click.get("userAgent"), click.get("ipAddress"), body.decode("utf-8", "replace"),
    )


def store(body: bytes, svix_id: str) -> bool:
    """Insert one event; returns False when it was a duplicate delivery (same svix-id)."""
    with connect() as conn:
        cur = conn.execute(INSERT_SQL, _row(body, svix_id))
        return cur.rowcount == 1


# ── Background writer (owner, 9 Nov 2026) ─────────────────────────────────────────────────────────────
# Right after a send Resend posts thousands of events within a few minutes. Writing each one to SQLite inside the
# request handler stalled the single-process editor: on 9 Nov about 2,000 reader image loads failed and the edition
# PDF timed out. The webhook now only checks the signature, queues the event and answers; one thread writes the
# queue in batches over a single connection.
import atexit  # noqa: E402
import logging  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402

_log = logging.getLogger("noon.clicks")
_Q: "queue.Queue[tuple[bytes, str, str, int]]" = queue.Queue()
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None
_on_fresh = None
BATCH_MAX = 500
MAX_ATTEMPTS = 5


def enqueue(body: bytes, svix_id: str, on_fresh=None) -> None:
    """Queue one verified event for the background writer. Raises ValueError on bad JSON, as store() did.
    on_fresh(body) is called by the writer for each event that was not a duplicate delivery."""
    json.loads(body)
    global _writer, _on_fresh
    with _writer_lock:
        if on_fresh is not None:
            _on_fresh = on_fresh
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_writer_loop, name="clicks-writer", daemon=True)
            _writer.start()
    _Q.put((body, svix_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), 0))


def _writer_loop() -> None:
    conn = connect()
    while True:
        batch = [_Q.get()]
        while len(batch) < BATCH_MAX:
            try:
                batch.append(_Q.get_nowait())
            except queue.Empty:
                break
        fresh = []
        try:
            with conn:
                for body, sid, received, _ in batch:
                    try:
                        row = _row(body, sid, received)
                    except Exception as e:  # noqa: BLE001  (one bad event never blocks the rest)
                        _log.warning(f"clicks: dropped unreadable event {sid}: {e}")
                        continue
                    if conn.execute(INSERT_SQL, row).rowcount == 1:
                        fresh.append(body)
        except Exception as e:  # noqa: BLE001  (e.g. database locked): put the batch back and try again shortly
            _log.error(f"clicks: batch of {len(batch)} not written ({e}); retrying")
            for body, sid, received, attempts in batch:
                if attempts + 1 < MAX_ATTEMPTS:
                    _Q.put((body, sid, received, attempts + 1))
                else:
                    _log.error(f"clicks: gave up on event {sid} after {MAX_ATTEMPTS} attempts")
            for _ in batch:
                _Q.task_done()
            time.sleep(2)
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = connect()
            continue
        for _ in batch:
            _Q.task_done()
        if _on_fresh is not None:
            for body in fresh:
                try:
                    _on_fresh(body)
                except Exception as e:  # noqa: BLE001
                    _log.warning(f"clicks: on_fresh failed: {e}")


def flush(timeout: float = 15.0) -> bool:
    """Wait (bounded) until the queue is written; called at shutdown so a restart loses nothing queued."""
    end = time.time() + timeout
    while _Q.unfinished_tasks and time.time() < end:
        time.sleep(0.1)
    return not _Q.unfinished_tasks


atexit.register(flush)
