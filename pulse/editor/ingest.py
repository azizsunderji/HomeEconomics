"""Turn today's stored v4b brief into an editable draft.

Reads the synced production DB read-only, never writes it. Idempotent:
if today's draft exists nothing happens (unless replace=True).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import paths
import drafts
from delivery.email_lunch import FREE_ENTRY_COUNT, _intro_text
from delivery.own_posts import load_own_posts
from own_posts_apify import fetch_own_posts

logger = logging.getLogger("noon.ingest")

# Keys the renderer or the editor use. Everything else in the stored brief
# (391 headlines, starred emails, journal lists…) is left out of the draft.
KEEP_KEYS = {
    "date", "intro", "entries", "paper_of_the_day", "conversation_pulse", "pulse",
    "_press_mentions", "_own_posts", "_briefing_id", "_v4b_meta", "_url_audit",
}


def _et_date(created_at: str) -> str:
    dt = datetime.fromisoformat(created_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(drafts.ET).strftime("%Y-%m-%d")


def latest_brief_for(date: str) -> tuple[int, dict] | None:
    """Newest daily_v4b_attach row created on `date` (US Eastern)."""
    conn = sqlite3.connect(f"file:{paths.PULSE_DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, created_at FROM briefings WHERE briefing_type = 'daily_v4b_attach' "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
        for rid, created in rows:
            if _et_date(created) == date:
                body = conn.execute("SELECT content_json FROM briefings WHERE id = ?", (rid,)).fetchone()[0]
                brief = json.loads(body)
                if "_own_posts" not in brief:
                    try:
                        brief["_own_posts"] = load_own_posts(conn)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"own posts unavailable: {e}")
                        brief["_own_posts"] = []
                return rid, brief
        return None
    finally:
        conn.close()


def build_draft(brief: dict, date: str) -> dict:
    entries = [e for e in (brief.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: (e.get("rank") is None, e.get("rank", 10**6)))
    for i, e in enumerate(entries, start=1):
        e["rank"] = i
        # Explicit tiers from the start; the renderer's fallback (top-N) is
        # reproduced here so an unedited draft renders exactly as before.
        e.setdefault("tier", "free" if i <= FREE_ENTRY_COUNT else "premium")
    draft = {k: v for k, v in brief.items() if k in KEEP_KEYS}
    draft["entries"] = entries
    draft["_deleted_entries"] = []
    # The masthead date is the send date, not the v1 brief's date field
    # (the stored brief carries the previous day's date).
    draft["_brief_date"] = brief.get("date")
    draft["date"] = date
    if not (isinstance(draft.get("intro"), str) and draft["intro"].strip()):
        draft["intro"] = _intro_text(brief, entries)[0]
    return draft


def ingest(date: str | None = None, replace: bool = False) -> dict | None:
    date = date or drafts.today_et()
    existing = drafts.get(date)
    if existing and not replace:
        return existing
    found = latest_brief_for(date)
    if not found:
        logger.info(f"no v4b brief in {paths.PULSE_DB} for {date} yet")
        return None
    rid, brief = found
    draft = build_draft(brief, date)
    if not draft.get("_own_posts"):
        # The list scrape cannot include the owner (X forbids adding yourself
        # to your own list), so pull the timeline directly when a key is set.
        draft["_own_posts"] = fetch_own_posts()
    row = drafts.create(date, rid, draft, replace=replace)
    logger.info(f"draft {date} created from briefing #{rid} ({len(draft['entries'])} entries)")
    return row
