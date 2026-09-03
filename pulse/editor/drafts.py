"""Draft store for the News at Noon editor.

One row per calendar day (US Eastern). The JSON is the v4b briefing dict
with the editor's fields added (`intro`, per-entry `tier`, edited titles
and summaries, `_deleted_entries`). Every save keeps the previous
version in `draft_versions`, and saves are optimistic: a save must name
the version it started from, so two open tabs cannot silently clobber
each other.

Statuses: draft (will send at noon) | held (noon send skipped) |
sent (noon or manual send done).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NOON_DB = Path(os.environ.get("NOON_DB", str(Path.home() / "work" / "noon" / "noon_drafts.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    date        TEXT PRIMARY KEY,
    source_id   INTEGER,
    json        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    notified_at TEXT,
    sent_at     TEXT,
    send_log    TEXT
);
CREATE TABLE IF NOT EXISTS draft_versions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date     TEXT NOT NULL,
    version  INTEGER NOT NULL,
    json     TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_versions_date ON draft_versions(date, version);
"""


class Conflict(Exception):
    """Raised when a save names a version older than the stored one."""

    def __init__(self, current: dict):
        super().__init__("draft changed since it was loaded")
        self.current = current


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def connect() -> sqlite3.Connection:
    NOON_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(NOON_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row_to_dict(r: sqlite3.Row, with_json: bool = True) -> dict:
    d = {k: r[k] for k in r.keys()}
    if with_json:
        d["json"] = json.loads(d["json"])
    else:
        d.pop("json", None)
    return d


def get(date: str) -> dict | None:
    with connect() as conn:
        r = conn.execute("SELECT * FROM drafts WHERE date = ?", (date,)).fetchone()
    return _row_to_dict(r) if r else None


def create(date: str, source_id: int | None, obj: dict, replace: bool = False) -> dict:
    ts = now_iso()
    body = json.dumps(obj, ensure_ascii=False)
    with connect() as conn:
        if replace:
            conn.execute("DELETE FROM drafts WHERE date = ?", (date,))
            conn.execute("DELETE FROM draft_versions WHERE date = ?", (date,))
        conn.execute(
            "INSERT INTO drafts (date, source_id, json, version, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, 'draft', ?, ?)",
            (date, source_id, body, ts, ts),
        )
        conn.execute(
            "INSERT INTO draft_versions (date, version, json, saved_at) VALUES (?, 1, ?, ?)",
            (date, body, ts),
        )
    return get(date)  # type: ignore[return-value]


def save(date: str, obj: dict, expected_version: int) -> dict:
    """Store a new version. Raises Conflict (carrying the current row)
    when expected_version is stale."""
    ts = now_iso()
    body = json.dumps(obj, ensure_ascii=False)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT * FROM drafts WHERE date = ?", (date,)).fetchone()
        if r is None:
            raise KeyError(date)
        if int(r["version"]) != int(expected_version):
            raise Conflict(_row_to_dict(r))
        new_v = int(r["version"]) + 1
        conn.execute(
            "UPDATE drafts SET json = ?, version = ?, updated_at = ? WHERE date = ?",
            (body, new_v, ts, date),
        )
        conn.execute(
            "INSERT INTO draft_versions (date, version, json, saved_at) VALUES (?, ?, ?, ?)",
            (date, new_v, body, ts),
        )
    return get(date)  # type: ignore[return-value]


def set_status(date: str, status: str, send_log: str | None = None) -> dict:
    assert status in ("draft", "held", "sent"), status
    ts = now_iso()
    with connect() as conn:
        if status == "sent":
            conn.execute(
                "UPDATE drafts SET status = 'sent', sent_at = ?, send_log = ?, updated_at = ? "
                "WHERE date = ?",
                (ts, send_log, ts, date),
            )
        else:
            conn.execute(
                "UPDATE drafts SET status = ?, updated_at = ? WHERE date = ?",
                (status, ts, date),
            )
    return get(date)  # type: ignore[return-value]


def mark_notified(date: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE drafts SET notified_at = ? WHERE date = ?", (now_iso(), date))


def list_recent(limit: int = 14) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM drafts ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r, with_json=False) for r in rows]


def versions(date: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, version, saved_at, length(json) AS size FROM draft_versions "
            "WHERE date = ? ORDER BY version", (date,)
        ).fetchall()
    return [dict(r) for r in rows]


def latest_sent() -> dict | None:
    """Most recently sent draft, else the most recent draft of any status."""
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM drafts WHERE status = 'sent' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if r is None:
            r = conn.execute("SELECT * FROM drafts ORDER BY date DESC LIMIT 1").fetchone()
    return _row_to_dict(r) if r else None
