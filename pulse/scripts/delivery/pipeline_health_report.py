"""Pulse pipeline health diagnostic email.

Runs after the synthesis step and produces a stage-by-stage report on the
previous 24 hours of the Pulse briefing pipeline. The goal is operational:
every morning the user gets a single, scannable read on whether each stage
ran correctly, so a silent failure in any one collector/enrichment/synth
step never hides for more than a day.

Usage:
    python pulse/scripts/delivery/pipeline_health_report.py [--to <email>]
                                                            [--dry-run]
                                                            [--db <path>]

`--dry-run` writes the HTML to /tmp/health_report.html and skips Resend.

Every stage probe is wrapped in try/except: one broken probe never breaks
the whole report. Every HTTP probe has an explicit timeout so the script
never stalls.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx

# Make pulse/scripts importable so we can share helpers + config.
_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Lazy / safe config import — never let a missing constant break the run
try:
    import config as _cfg
except Exception:
    _cfg = None  # type: ignore

# Re-use email_briefing helpers for HTML safety / spacing
try:
    from delivery.email_briefing import _esc, _spacer
except Exception:  # fallback if path issues
    def _esc(text: str) -> str:
        import html as _html
        return _html.escape(str(text), quote=True)

    def _spacer(height: int = 24) -> str:
        return (
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td height="{height}" style="line-height:{height}px; font-size: 1px;">&nbsp;</td>'
            f'</tr></table>\n'
        )

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_DB = os.environ.get(
    "PULSE_DB",
    "/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db",
)
DEFAULT_TO = "aziz@home-economics.us"
EMAIL_FROM = "Pulse Health <onboarding@resend.dev>"
HTTP_TIMEOUT = 10  # seconds — every probe MUST set a timeout

# Brand palette
COLOR_BG = "#F6F7F3"
COLOR_OK = "#0BB4FF"
COLOR_WARN = "#FEC439"
COLOR_BROKEN = "#F4743B"
COLOR_INK = "#3D3733"
COLOR_MUTED = "#888"
FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
)

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_BROKEN = "broken"

STATUS_GLYPH = {
    STATUS_OK: "[OK]",
    STATUS_WARN: "[DEGRADED]",
    STATUS_BROKEN: "[BROKEN]",
}

STATUS_LABEL = {
    STATUS_OK: "working",
    STATUS_WARN: "degraded",
    STATUS_BROKEN: "broken",
}

STATUS_COLOR = {
    STATUS_OK: COLOR_OK,
    STATUS_WARN: COLOR_WARN,
    STATUS_BROKEN: COLOR_BROKEN,
}


# ── Stage container ────────────────────────────────────────────────────

class Stage:
    """Holds the result of probing one pipeline stage.

    `data_rows` is a list of (label, value) pairs rendered as a compact
    two-column table. `notes` is a list of free-form sentences (errors,
    methodology callouts) rendered below the table.
    """

    def __init__(self, key: str, title: str, prose: str) -> None:
        self.key = key
        self.title = title
        self.prose = prose
        self.status: str = STATUS_OK
        self.headline: str = ""
        self.data_rows: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def set(self, status: str, headline: str = "") -> None:
        # Don't downgrade once broken/warn has been set
        order = {STATUS_OK: 0, STATUS_WARN: 1, STATUS_BROKEN: 2}
        if order[status] > order[self.status]:
            self.status = status
        if headline:
            self.headline = headline

    def row(self, label: str, value: Any) -> None:
        self.data_rows.append((label, str(value)))

    def note(self, text: str) -> None:
        if text:
            self.notes.append(text)


# ── Generic helpers ────────────────────────────────────────────────────

def _safe(stage: Stage, fn) -> None:
    """Run probe `fn(stage)` and capture any exception as a broken note."""
    try:
        fn(stage)
    except Exception as e:
        stage.set(STATUS_BROKEN, "probe failed")
        stage.note(f"Probe raised: {type(e).__name__}: {e}")
        logger.exception("Probe %s failed", stage.key)


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def _last_24h_iso(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _parse_opml(path: str) -> list[dict]:
    """Minimal OPML parser — returns [{title, folder, url}, ...]."""
    feeds: list[dict] = []
    try:
        tree = ET.parse(path)
    except Exception:
        return feeds
    root = tree.getroot()

    def _walk(node, folder: str = "") -> None:
        for child in node:
            xml_url = child.get("xmlUrl", "").strip()
            title = (child.get("title") or child.get("text") or "").strip()
            if xml_url:
                feeds.append({"title": title, "folder": folder, "url": xml_url})
            sub_folder = title if not xml_url else folder
            _walk(child, sub_folder)

    body = root.find(".//body")
    if body is not None:
        _walk(body)
    return feeds


# ── Stage probes ───────────────────────────────────────────────────────

def probe_twitter(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    row = conn.execute(
        "SELECT COUNT(*) AS c, COUNT(DISTINCT author) AS authors "
        "FROM items WHERE source='twitter' AND collected_at >= ?",
        (cutoff,),
    ).fetchone()
    items = row["c"]
    authors = row["authors"]
    expected_handles = set()
    if _cfg is not None:
        for name in ("TWITTER_ACCOUNTS", "AI_ROUNDUP_ACCOUNTS", "SUPER_SMART_HANDLES"):
            try:
                v = getattr(_cfg, name)
                expected_handles.update(h.lower() for h in (v or []))
            except Exception:
                pass

    seen_handles: set[str] = set()
    for r in conn.execute(
        "SELECT DISTINCT LOWER(REPLACE(author,'@','')) AS h "
        "FROM items WHERE source='twitter' AND collected_at >= ?",
        (cutoff,),
    ).fetchall():
        if r["h"]:
            seen_handles.add(r["h"])
    expected_covered = (
        len(expected_handles & seen_handles) if expected_handles else 0
    )
    missing_expected = (
        sorted(expected_handles - seen_handles) if expected_handles else []
    )

    # Apify spend / errors from collection_runs
    err_rows = conn.execute(
        "SELECT started_at, error FROM collection_runs "
        "WHERE source='twitter' AND started_at >= ? AND error != '' "
        "ORDER BY started_at DESC LIMIT 5",
        (cutoff,),
    ).fetchall()

    stage.row("Items collected (24h)", _fmt_int(items))
    stage.row("Distinct authors (24h)", _fmt_int(authors))
    stage.row(
        "Configured handles covered",
        f"{expected_covered} / {len(expected_handles)}" if expected_handles
        else "config not readable",
    )
    if err_rows:
        stage.row("Recent errors", len(err_rows))
        for r in err_rows[:3]:
            stage.note(f"{r['started_at'][:16]}: {(r['error'] or '')[:160]}")

    if items == 0:
        stage.set(STATUS_BROKEN, "0 tweets collected in 24h")
    elif items < 200 or (expected_handles and expected_covered < len(expected_handles) * 0.5):
        stage.set(STATUS_WARN, f"only {_fmt_int(items)} items / {expected_covered} of {len(expected_handles)} handles")
    else:
        stage.headline = f"{_fmt_int(items)} items from {authors} authors"

    if missing_expected and stage.status != STATUS_BROKEN:
        sample = ", ".join(missing_expected[:8])
        if len(missing_expected) > 8:
            sample += f", +{len(missing_expected) - 8} more"
        stage.note(f"{len(missing_expected)} configured handle(s) returned 0 items in 24h: {sample}")


def probe_bluesky(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    row = conn.execute(
        "SELECT COUNT(*) AS c, COUNT(DISTINCT author) AS authors "
        "FROM items WHERE source='bluesky' AND collected_at >= ?",
        (cutoff,),
    ).fetchone()
    items, authors = row["c"], row["authors"]
    expected = []
    if _cfg is not None:
        try:
            expected = list(getattr(_cfg, "BLUESKY_ACCOUNTS", []))
        except Exception:
            expected = []
    stage.row("Items collected (24h)", _fmt_int(items))
    stage.row("Distinct authors (24h)", _fmt_int(authors))
    stage.row("Configured accounts", _fmt_int(len(expected)))
    if items == 0:
        stage.set(STATUS_BROKEN, "0 Bluesky posts collected in 24h")
    elif items < 50 or (expected and authors < max(5, len(expected) * 0.4)):
        stage.set(STATUS_WARN, f"low volume: {items} items / {authors} authors")
    else:
        stage.headline = f"{_fmt_int(items)} items from {authors} authors"


def probe_hackernews(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE source='hackernews' AND collected_at >= ?",
        (cutoff,),
    ).fetchone()
    items = row["c"]
    stage.row("Items collected (24h)", _fmt_int(items))
    if items == 0:
        stage.set(STATUS_WARN, "0 HN stories collected")
    else:
        stage.headline = f"{_fmt_int(items)} stories"


def _probe_rss_subset(
    stage: Stage,
    conn: sqlite3.Connection,
    feeds: list[dict],
    source_filter: str = "rss",
    extra_where: str = "",
    top_n: int = 25,
) -> None:
    cutoff = _last_24h_iso(24)
    where = f"source = '{source_filter}' AND collected_at >= ?"
    if extra_where:
        where += " AND " + extra_where
    row = conn.execute(
        f"SELECT COUNT(*) c FROM items WHERE {where}", (cutoff,),
    ).fetchone()
    total_items = row["c"]

    feed_counts = {}
    for r in conn.execute(
        f"SELECT COALESCE(feed_name,'') fn, COUNT(*) c FROM items WHERE {where} GROUP BY feed_name",
        (cutoff,),
    ).fetchall():
        feed_counts[r["fn"]] = r["c"]

    # Feed-name normalization: the OPML title isn't always identical to the
    # feed_name stored on items (e.g. OPML lists "Asia" inside an Economist
    # folder but rss_feeds stores "Economist: Asia"). Match by case-insensitive
    # substring so the missing-feed count isn't dominated by cosmetic
    # differences.
    expected_titles = {f["title"] for f in feeds if f.get("title")}
    expected_lower = {t.lower(): t for t in expected_titles}
    seen_titles = {fn for fn in feed_counts if fn}
    seen_lower = {fn.lower() for fn in seen_titles}

    def _expected_seen(title_lower: str) -> bool:
        # Title or any seen feed name contains the other
        if title_lower in seen_lower:
            return True
        for s in seen_lower:
            if title_lower in s or s in title_lower:
                return True
        return False

    missing = [
        orig for low, orig in expected_lower.items()
        if not _expected_seen(low)
    ]

    # Silent 14d — same substring rule but against a 14d window
    cutoff_14d = _last_24h_iso(24 * 14)
    seen_14d = set()
    for r in conn.execute(
        f"SELECT DISTINCT LOWER(COALESCE(feed_name,'')) fn FROM items "
        f"WHERE source='{source_filter}' AND collected_at >= ?",
        (cutoff_14d,),
    ).fetchall():
        if r["fn"]:
            seen_14d.add(r["fn"])
    silent_14d = []
    for low, orig in expected_lower.items():
        hit = low in seen_14d or any(low in s or s in low for s in seen_14d)
        if not hit:
            silent_14d.append(orig)

    stage.row("Items collected (24h)", _fmt_int(total_items))
    stage.row("Feeds with items (24h)", _fmt_int(len(seen_titles)))
    if expected_titles:
        stage.row("Expected feeds in OPML/config", _fmt_int(len(expected_titles)))
        stage.row("Expected feeds absent 24h", _fmt_int(len(missing)))
        stage.row("Expected feeds silent 14d", _fmt_int(len(silent_14d)))

    # Top N
    top_lines = sorted(feed_counts.items(), key=lambda x: -x[1])[:top_n]
    if top_lines:
        top_blob = " · ".join(f"{n} {name or '(no name)'}" for name, n in top_lines)
        stage.note(f"Top feeds (24h): {top_blob}")

    if silent_14d:
        sample = ", ".join(silent_14d[:8])
        if len(silent_14d) > 8:
            sample += f", +{len(silent_14d) - 8} more"
        stage.note(f"Expected feeds silent ≥14d (likely broken/renamed): {sample}")

    if total_items == 0:
        stage.set(STATUS_BROKEN, f"0 {source_filter} items in 24h")
    elif expected_titles and len(silent_14d) > max(10, len(expected_titles) * 0.25):
        stage.set(
            STATUS_WARN,
            f"{len(silent_14d)} of {len(expected_titles)} expected feeds silent 14d+",
        )
    else:
        stage.headline = f"{_fmt_int(total_items)} items, {len(seen_titles)} feeds active"


def probe_rss_news(stage: Stage, conn: sqlite3.Connection) -> None:
    feeds: list[dict] = []
    opml_paths = [
        _SCRIPTS_DIR.parent / "HomeEconomicsRSS.opml",
        _SCRIPTS_DIR.parent / "FeedsApr20.opml",
    ]
    for p in opml_paths:
        if p.exists():
            feeds.extend(_parse_opml(str(p)))
    # News feeds = anything NOT in Journals / Twitter / Substack folders
    news_feeds = [
        f for f in feeds
        if f["folder"] not in ("Journals", "Twitter", "Substack", "Substacks", "Substack feeds")
    ]
    _probe_rss_subset(stage, conn, news_feeds, source_filter="rss")


def probe_rss_substack(stage: Stage, conn: sqlite3.Connection) -> None:
    # Substack feeds come either via OPML (folder=Substack) or COMPETITOR_SUBSTACKS
    feeds = []
    if _cfg is not None:
        try:
            for name, url in getattr(_cfg, "COMPETITOR_SUBSTACKS", []) or []:
                feeds.append({"title": name, "folder": "Substack", "url": url})
        except Exception:
            pass
    _probe_rss_subset(stage, conn, feeds, source_filter="substack")


def probe_rss_journals(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    cutoff_7d = _last_24h_iso(24 * 7)
    row_24 = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE feed_priority='journal' AND collected_at >= ?",
        (cutoff,),
    ).fetchone()
    row_7d = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE feed_priority='journal' AND collected_at >= ?",
        (cutoff_7d,),
    ).fetchone()
    feeds_seen = conn.execute(
        "SELECT COALESCE(feed_name,'') fn, COUNT(*) c FROM items "
        "WHERE feed_priority='journal' AND collected_at >= ? "
        "GROUP BY feed_name ORDER BY c DESC",
        (cutoff_7d,),
    ).fetchall()
    stage.row("Journal items (24h)", _fmt_int(row_24["c"]))
    stage.row("Journal items (7d)", _fmt_int(row_7d["c"]))
    stage.row("Journals with items (7d)", _fmt_int(len(feeds_seen)))
    if feeds_seen:
        names = ", ".join(f"{r['fn']} ({r['c']})" for r in feeds_seen[:10])
        stage.note(f"Top journal feeds (7d): {names}")
    if row_7d["c"] == 0:
        stage.set(STATUS_BROKEN, "0 journal items in 7 days")
    elif row_24["c"] == 0:
        # Journals naturally trickle — only WARN if 7d is also weak.
        stage.headline = "no new in 24h (normal pace)"
    else:
        stage.headline = f"{_fmt_int(row_24['c'])} new in 24h"


def probe_gmail_workspace(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    # Workspace items come through gmail collector — source='gmail' or 'substack'.
    # We can't perfectly distinguish workspace vs IMAP via the items table.
    # Use a heuristic: GMAIL_TOKEN test = workspace, GMAIL_IMAP* = personal.
    token_blob = os.environ.get("GMAIL_TOKEN") or os.environ.get("GMAIL_TOKENS") or ""
    token_status = "not set"
    refresh_ok = False
    if token_blob:
        try:
            try:
                data = json.loads(token_blob)
            except json.JSONDecodeError:
                import base64
                data = json.loads(base64.b64decode(token_blob))
            tokens = data if isinstance(data, list) else [data]
            for t in tokens:
                try:
                    resp = httpx.post(
                        "https://oauth2.googleapis.com/token",
                        data={
                            "client_id": t.get("client_id", ""),
                            "client_secret": t.get("client_secret", ""),
                            "refresh_token": t.get("refresh_token", ""),
                            "grant_type": "refresh_token",
                        },
                        timeout=HTTP_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        refresh_ok = True
                        token_status = "refresh OK"
                    else:
                        token_status = f"refresh HTTP {resp.status_code}: {resp.text[:120]}"
                        break
                except Exception as e:
                    token_status = f"refresh failed: {type(e).__name__}: {e}"
                    break
        except Exception as e:
            token_status = f"GMAIL_TOKEN parse failed: {e}"

    # Items: gmail + substack from IMAP accounts excluded would be ideal; fallback
    items_count = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source IN ('gmail','substack') "
        "AND collected_at >= ? AND source_id NOT LIKE 'imap_%'",
        (cutoff,),
    ).fetchone()["c"]

    top_senders = conn.execute(
        "SELECT author, COUNT(*) c FROM items WHERE source IN ('gmail','substack') "
        "AND collected_at >= ? AND source_id NOT LIKE 'imap_%' "
        "GROUP BY author ORDER BY c DESC LIMIT 10",
        (cutoff,),
    ).fetchall()

    stage.row("OAuth status", token_status)
    stage.row("Items (24h, workspace)", _fmt_int(items_count))
    if top_senders:
        senders = ", ".join(f"{(r['author'] or '?')[:40]} ({r['c']})" for r in top_senders[:6])
        stage.note(f"Top senders (24h): {senders}")

    if not token_blob:
        stage.set(STATUS_BROKEN, "GMAIL_TOKEN / GMAIL_TOKENS not set")
    elif not refresh_ok:
        stage.set(STATUS_BROKEN, "OAuth refresh failed")
    elif items_count == 0:
        stage.set(STATUS_WARN, "OAuth OK but 0 items in 24h")
    else:
        stage.headline = f"{_fmt_int(items_count)} items, refresh OK"


def probe_gmail_imap(stage: Stage, conn: sqlite3.Connection) -> None:
    import imaplib

    user = os.environ.get("GMAIL_IMAP_USER", "")
    pw = os.environ.get("GMAIL_IMAP_PASSWORD", "")
    login_ok = False
    login_msg = "creds not set"
    if user and pw:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=HTTP_TIMEOUT)
            try:
                mail.login(user, pw)
                login_ok = True
                login_msg = "login OK"
                try:
                    mail.logout()
                except Exception:
                    pass
            except imaplib.IMAP4.error as e:
                login_msg = f"login failed: {e}"
        except Exception as e:
            login_msg = f"connect failed: {type(e).__name__}: {e}"

    cutoff = _last_24h_iso(24)
    items_count = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source IN ('gmail','substack') "
        "AND collected_at >= ? AND source_id LIKE 'imap_%'",
        (cutoff,),
    ).fetchone()["c"]
    top_senders = conn.execute(
        "SELECT author, COUNT(*) c FROM items WHERE source IN ('gmail','substack') "
        "AND collected_at >= ? AND source_id LIKE 'imap_%' "
        "GROUP BY author ORDER BY c DESC LIMIT 10",
        (cutoff,),
    ).fetchall()

    stage.row("IMAP user", user or "not set")
    stage.row("IMAP login", login_msg)
    stage.row("Items (24h, IMAP)", _fmt_int(items_count))
    if top_senders:
        senders = ", ".join(f"{(r['author'] or '?')[:40]} ({r['c']})" for r in top_senders[:6])
        stage.note(f"Top senders (24h): {senders}")

    if not user or not pw:
        stage.set(STATUS_WARN, "GMAIL_IMAP_USER / GMAIL_IMAP_PASSWORD not set")
    elif not login_ok:
        stage.set(STATUS_BROKEN, "IMAP login failed")
    elif items_count == 0:
        stage.set(STATUS_WARN, "IMAP login OK but 0 items in 24h")
    else:
        stage.headline = f"{_fmt_int(items_count)} items, IMAP login OK"


def probe_press_mentions(stage: Stage, conn: sqlite3.Connection) -> None:
    # press_mentions doesn't necessarily insert into items; it's fetched at synth
    # time. Check the last briefing's _press_mentions list.
    row = conn.execute(
        "SELECT content_json FROM briefings WHERE briefing_type='daily' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    pm_count = 0
    if row:
        try:
            cj = json.loads(row["content_json"])
            pm_count = len(cj.get("_press_mentions") or [])
        except Exception:
            pass
    # gmail_starred — check items source=='gmail' with engagement_raw.starred?
    starred_count = 0
    if row:
        try:
            cj = json.loads(row["content_json"])
            starred_count = len(cj.get("_starred_emails") or [])
        except Exception:
            pass
    stage.row("Press mentions in last briefing", _fmt_int(pm_count))
    stage.row("Starred emails in last briefing", _fmt_int(starred_count))
    if pm_count == 0 and starred_count == 0:
        stage.set(STATUS_WARN, "no press mentions or starred emails in last briefing")
    else:
        stage.headline = f"{pm_count} press · {starred_count} starred"


# ── Enrichment ─────────────────────────────────────────────────────────

def probe_article_enrichment(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    enriched = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source='rss' "
        "AND collected_at >= ? AND LENGTH(body) >= 500",
        (cutoff,),
    ).fetchone()["c"]
    teaser = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source='rss' "
        "AND collected_at >= ? AND LENGTH(body) < 500",
        (cutoff,),
    ).fetchone()["c"]
    high_rel_no_body = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source='rss' "
        "AND collected_at >= ? AND relevance_score >= 60 AND LENGTH(body) < 500",
        (cutoff,),
    ).fetchone()["c"]
    stage.row("RSS items enriched (body ≥500 chars)", _fmt_int(enriched))
    stage.row("RSS items still teaser-length", _fmt_int(teaser))
    stage.row("High-relevance & still teaser", _fmt_int(high_rel_no_body))
    total = enriched + teaser
    pct = (enriched / total * 100) if total else 0
    stage.row("Enrichment rate", f"{pct:.0f}%")
    if total == 0:
        stage.set(STATUS_WARN, "no RSS items to enrich")
    elif high_rel_no_body > 20:
        stage.set(STATUS_WARN, f"{high_rel_no_body} high-relevance items missing body")
    else:
        stage.headline = f"{_fmt_int(enriched)} enriched, {pct:.0f}%"


def probe_tweet_link_enrichment(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    total = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source='twitter' AND collected_at >= ?",
        (cutoff,),
    ).fetchone()["c"]
    enriched = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE source='twitter' "
        "AND collected_at >= ? AND LENGTH(body) >= 500",
        (cutoff,),
    ).fetchone()["c"]
    stage.row("Tweets in 24h", _fmt_int(total))
    stage.row("Tweets with enriched body (≥500 chars)", _fmt_int(enriched))
    if total:
        stage.row("Enrichment rate", f"{(enriched / total * 100):.0f}%")
    if total == 0:
        stage.set(STATUS_WARN, "no tweets to enrich")
    else:
        stage.headline = f"{_fmt_int(enriched)} of {_fmt_int(total)} enriched"


def probe_journal_abstracts(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24 * 7)
    with_abs = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE feed_priority='journal' "
        "AND collected_at >= ? AND LENGTH(body) >= 400",
        (cutoff,),
    ).fetchone()["c"]
    without_abs = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE feed_priority='journal' "
        "AND collected_at >= ? AND LENGTH(body) < 400",
        (cutoff,),
    ).fetchone()["c"]
    stage.row("Journal items with abstract (7d)", _fmt_int(with_abs))
    stage.row("Journal items without abstract (7d)", _fmt_int(without_abs))
    total = with_abs + without_abs
    if total == 0:
        stage.set(STATUS_WARN, "no journal items to enrich")
    elif without_abs > with_abs:
        stage.set(STATUS_WARN, "majority of journal items missing abstracts")
    else:
        stage.headline = f"{_fmt_int(with_abs)} with abstract"


def probe_dedup(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    total_runs = conn.execute(
        "SELECT SUM(items_collected) c, SUM(items_new) n, SUM(items_duplicate) d "
        "FROM collection_runs WHERE started_at >= ?",
        (cutoff,),
    ).fetchone()
    coll = total_runs["c"] or 0
    new = total_runs["n"] or 0
    dup = total_runs["d"] or 0

    # Content-hash duplicates currently in DB (sample)
    hash_dupes = conn.execute(
        "SELECT COUNT(*) c FROM ("
        "  SELECT content_hash FROM items WHERE collected_at >= ? "
        "  GROUP BY content_hash HAVING COUNT(*) > 1"
        ")", (cutoff,),
    ).fetchone()["c"]

    stage.row("Collected (sum of runs, 24h)", _fmt_int(coll))
    stage.row("New (sum of runs, 24h)", _fmt_int(new))
    stage.row("Duplicate (sum of runs, 24h)", _fmt_int(dup))
    if coll:
        stage.row("Duplicate rate", f"{(dup / coll * 100):.0f}%")
    stage.row("Cross-source content-hash collisions (24h)", _fmt_int(hash_dupes))
    stage.headline = f"{_fmt_int(new)} new / {_fmt_int(dup)} dup"


# ── Classification ─────────────────────────────────────────────────────

def probe_trigger_classifier(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    classified = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE classified_at >= ?",
        (cutoff,),
    ).fetchone()["c"]
    unclassified = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE collected_at >= ? AND classified_at IS NULL",
        (cutoff,),
    ).fetchone()["c"]
    cache_size = 0
    try:
        cache_size = conn.execute(
            "SELECT COUNT(*) FROM trigger_classifier_cache"
        ).fetchone()[0]
    except Exception:
        pass
    trig_dist = []
    try:
        trig_dist = conn.execute(
            "SELECT trigger_type, COUNT(*) c FROM trigger_classifier_cache "
            "WHERE classified_at >= ? GROUP BY trigger_type ORDER BY c DESC",
            (cutoff,),
        ).fetchall()
    except Exception:
        pass

    stage.row("Items classified (24h)", _fmt_int(classified))
    stage.row("Items still unclassified (24h)", _fmt_int(unclassified))
    stage.row("Trigger-classifier cache size", _fmt_int(cache_size))
    if trig_dist:
        dist_blob = " · ".join(f"{r['trigger_type']}: {r['c']}" for r in trig_dist[:8])
        stage.note(f"Trigger distribution (24h): {dist_blob}")
    if unclassified > 50:
        stage.set(STATUS_BROKEN, f"{unclassified} items unclassified — classifier likely skipped")
    elif classified == 0:
        stage.set(STATUS_WARN, "0 items classified in 24h")
    else:
        stage.headline = f"{_fmt_int(classified)} classified"


def probe_relevance_scoring(stage: Stage, conn: sqlite3.Connection) -> None:
    cutoff = _last_24h_iso(24)
    rows = conn.execute(
        "SELECT relevance_score, COUNT(*) c FROM items "
        "WHERE classified_at >= ? GROUP BY relevance_score / 10 * 10 ORDER BY relevance_score",
        (cutoff,),
    ).fetchall()
    total = sum(r["c"] for r in rows)
    high = sum(r["c"] for r in rows if (r["relevance_score"] or 0) >= 70)
    medium = sum(r["c"] for r in rows if 40 <= (r["relevance_score"] or 0) < 70)
    low = sum(r["c"] for r in rows if (r["relevance_score"] or 0) < 40)
    stage.row("Total scored (24h)", _fmt_int(total))
    stage.row("High relevance (≥70)", _fmt_int(high))
    stage.row("Medium (40-69)", _fmt_int(medium))
    stage.row("Low (<40)", _fmt_int(low))
    if total == 0:
        stage.set(STATUS_BROKEN, "no items scored")
    elif high < 10:
        stage.set(STATUS_WARN, f"only {high} high-relevance items — synthesis may be thin")
    else:
        stage.headline = f"{_fmt_int(high)} high / {_fmt_int(medium)} medium"


def probe_blocklist(stage: Stage, conn: sqlite3.Connection) -> None:
    # The collector drops blocklisted authors before insert, so we count the
    # blocklist size and verify none slipped through.
    blocklist = set()
    if _cfg is not None:
        try:
            blocklist = {h.lower() for h in getattr(_cfg, "TWITTER_AUTHOR_BLOCKLIST", set())}
        except Exception:
            pass
    stage.row("Blocklisted handles (configured)", _fmt_int(len(blocklist)))
    leaks = 0
    cutoff = _last_24h_iso(24)
    if blocklist:
        for handle in blocklist:
            row = conn.execute(
                "SELECT COUNT(*) c FROM items WHERE source='twitter' "
                "AND LOWER(author) IN (?, ?) AND collected_at >= ?",
                (f"@{handle}", handle, cutoff),
            ).fetchone()
            leaks += row["c"]
    stage.row("Blocklisted-author items in last 24h", _fmt_int(leaks))
    if leaks > 0:
        stage.set(STATUS_WARN, f"{leaks} blocklisted-author items leaked through")
    else:
        stage.headline = f"{_fmt_int(len(blocklist))} configured, 0 leaks"


# ── Synthesis V1 ───────────────────────────────────────────────────────

def _latest_v1(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, created_at, content_json, email_sent, email_sent_at "
        "FROM briefings WHERE briefing_type='daily' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _latest_v2(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, created_at, content_json, email_sent, email_sent_at "
        "FROM briefings WHERE briefing_type='daily_v2_clustered' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()


def probe_v1_pool(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing exists")
        return
    cj = json.loads(row["content_json"])
    stats = cj.get("stats_summary", {})
    stage.row("Latest briefing id", row["id"])
    stage.row("Created at", row["created_at"])
    stage.row("Total items analyzed", _fmt_int(stats.get("total_items_analyzed", 0)))
    stage.row("Conversation items", _fmt_int(stats.get("conversation_items", 0)))
    stage.row("Platforms active", _fmt_int(stats.get("platforms_active", 0)))
    breakdown = stats.get("source_breakdown") or {}
    if breakdown:
        top = sorted(breakdown.items(), key=lambda x: -x[1])[:10]
        blob = " · ".join(f"{n} {name}" for name, n in top)
        stage.note(f"Top item-pool sources: {blob}")
    if stats.get("total_items_analyzed", 0) < 500:
        stage.set(STATUS_WARN, "item pool below 500 — possibly thin")
    else:
        stage.headline = f"{_fmt_int(stats.get('total_items_analyzed', 0))} items"


def probe_v1_anthropic(stage: Stage, conn: sqlite3.Connection) -> None:
    # Query the canonical DB directly — anthropic_spend.py hardcodes the
    # legacy pulse/data/pulse.db path which differs from the canonical
    # /.../Data/Pulse/pulse.db that GHA writes to.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_microcents = 0
    by_model: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT model, input_tokens, output_tokens, calls, spent_microcents "
            "FROM anthropic_spend WHERE date = ?", (today,)
        ).fetchall()
        for r in rows:
            mc = r["spent_microcents"] or 0
            total_microcents += mc
            by_model[r["model"]] = {
                "cents": mc / 100,
                "calls": r["calls"] or 0,
                "input": r["input_tokens"] or 0,
                "output": r["output_tokens"] or 0,
            }
    except Exception as e:
        stage.note(f"anthropic_spend query failed: {e}")

    total_dollars = total_microcents / 10_000  # microcents → dollars
    stage.row("Total Anthropic spend (today UTC)", f"${total_dollars:.2f}")
    for model, m in by_model.items():
        stage.note(
            f"{model}: {m['calls']} calls · in {_fmt_int(m['input'])} · "
            f"out {_fmt_int(m['output'])} · ${m['cents']/100:.2f}"
        )
    # Apify spend from last briefing
    row = _latest_v1(conn)
    if row:
        try:
            cj = json.loads(row["content_json"])
            stage.row("Apify spend in last briefing", f"${cj.get('_apify_spend_cents', 0)/100:.2f}")
        except Exception:
            pass
    stage.headline = f"${total_dollars:.2f} Anthropic today"


def probe_v1_themes(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing exists")
        return
    cj = json.loads(row["content_json"])
    themes = cj.get("conversation_themes") or []
    stage.row("Themes generated", _fmt_int(len(themes)))
    for i, t in enumerate(themes[:8]):
        stage.note(f"{i+1}. [{t.get('heat_level','?')}] {t.get('theme','')[:140]}")
    if not themes:
        stage.set(STATUS_BROKEN, "0 themes generated")
    elif len(themes) < 3:
        stage.set(STATUS_WARN, f"only {len(themes)} themes")
    else:
        stage.headline = f"{len(themes)} themes"


def probe_v1_roundups(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing exists")
        return
    cj = json.loads(row["content_json"])
    roundups = cj.get("conversation_roundups") or []
    stage.row("Roundups generated", _fmt_int(len(roundups)))
    for i, r in enumerate(roundups[:10]):
        stage.note(f"{i+1}. {r.get('topic','')[:160]}")
    if not roundups:
        stage.set(STATUS_WARN, "0 roundups (rare but possible)")
    else:
        stage.headline = f"{len(roundups)} roundups"


def probe_v1_paper(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing exists")
        return
    cj = json.loads(row["content_json"])
    paper = cj.get("paper_of_the_day") or None
    # Build the list of previously-picked paper titles from last 14d.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    prev_titles: list[str] = []
    for r in conn.execute(
        "SELECT content_json FROM briefings WHERE briefing_type='daily' "
        "AND created_at >= ? AND id != ? ORDER BY id DESC",
        (cutoff, row["id"]),
    ).fetchall():
        try:
            cj_prev = json.loads(r["content_json"])
            p = cj_prev.get("paper_of_the_day") or None
            if p and isinstance(p, dict):
                t = (p.get("title") or "").strip()
                if t:
                    prev_titles.append(t)
        except Exception:
            continue
    if paper and isinstance(paper, dict):
        stage.row("Today's paper", (paper.get("title") or "")[:150])
        stage.row("Publication", paper.get("publication", ""))
        stage.row("URL", (paper.get("url") or "")[:120])
        stage.headline = f"\"{(paper.get('title') or '')[:60]}\""
    else:
        stage.row("Today's paper", "(none picked)")
        stage.set(STATUS_WARN, "no paper-of-the-day picked")
    stage.row("Recently picked papers (14d)", _fmt_int(len(prev_titles)))
    if prev_titles:
        sample = "; ".join(t[:90] for t in prev_titles[:6])
        if len(prev_titles) > 6:
            sample += f" … +{len(prev_titles) - 6} more"
        stage.note(f"Recent picks (14d): {sample}")


def probe_v1_pulse(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing")
        return
    cj = json.loads(row["content_json"])
    pulse = cj.get("conversation_pulse") or ""
    chars = len(pulse)
    stage.row("Pulse summary chars", _fmt_int(chars))
    if chars == 0:
        stage.set(STATUS_BROKEN, "empty pulse summary")
    elif chars < 300:
        stage.set(STATUS_WARN, "pulse summary very short")
    else:
        stage.headline = f"{_fmt_int(chars)} chars"
    if chars:
        stage.note(f"Preview: {pulse[:240]}…" if chars > 240 else f"Preview: {pulse}")


def probe_v1_postprocessors(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing")
        return
    cj = json.loads(row["content_json"])
    # All scalar _* counters
    counters = {
        k: v for k, v in cj.items()
        if k.startswith("_") and isinstance(v, (int, float)) and k != "_briefing_id"
    }
    # URL audit
    url_audit = cj.get("_url_audit") or {}

    for k, v in sorted(counters.items()):
        stage.row(k.lstrip("_"), _fmt_int(v))
        if v > 0:
            stage.note(f"{k}: caught {_fmt_int(v)} thing(s) the model missed")
    if url_audit:
        for k, v in url_audit.items():
            stage.row(f"url_audit.{k}", _fmt_int(v))
    # Flag a degraded status if URL stripping was non-trivial (model hallucinating URLs)
    if url_audit.get("stripped", 0) > 3:
        stage.set(STATUS_WARN, f"URL validator stripped {url_audit['stripped']} URLs")
    else:
        stage.headline = "no anomalies above threshold"


def probe_v1_cited_sources(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing")
        return
    cj = json.loads(row["content_json"])
    cited = (cj.get("stats_summary") or {}).get("cited_sources") or {}
    total_unique = 0
    for typ, m in cited.items():
        stage.row(f"{typ} unique cited", _fmt_int(len(m)))
        total_unique += len(m)
        if m:
            names = ", ".join(sorted(m.keys()))[:240]
            stage.note(f"{typ}: {names}")
    if total_unique == 0:
        stage.set(STATUS_WARN, "no sources cited — briefing may have no inline links")
    else:
        stage.headline = f"{total_unique} unique sources cited"


# ── V2 (clustering) ────────────────────────────────────────────────────

def probe_v2(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v2(conn)
    if not row:
        stage.set(STATUS_WARN, "no v2 briefing yet today")
        return
    cj = json.loads(row["content_json"])
    meta = cj.get("_v2_meta") or {}
    s = meta.get("stats") or {}
    stage.row("Latest v2 briefing id", row["id"])
    stage.row("Created at", row["created_at"])
    for k in (
        "lookback_hours", "items_after_filter", "embed_seconds",
        "clusters_total", "largest_cluster_size",
        "housing_relevant_clusters", "roundups_written", "pipeline_seconds",
    ):
        if k in s:
            stage.row(k, _fmt_int(s[k]) if isinstance(s[k], (int, float)) and abs(s[k]) >= 1 else s[k])
    roundups = cj.get("conversation_roundups") or []
    for r in roundups[:10]:
        cs = r.get("_cluster_size") or "?"
        stage.note(f"[{cs}] {r.get('topic','')[:160]}")
    if s.get("roundups_written", 0) == 0:
        stage.set(STATUS_WARN, "v2 produced 0 roundups")
    elif s.get("items_after_filter", 0) < 50:
        stage.set(STATUS_WARN, f"only {s.get('items_after_filter')} items after filter")
    else:
        stage.headline = f"{s.get('roundups_written', 0)} roundups from {s.get('clusters_total', 0)} clusters"


# ── Front pages ────────────────────────────────────────────────────────

def probe_frontpages_local(stage: Stage, conn: sqlite3.Connection) -> None:
    candidates = [
        Path("/tmp/front_pages"),
        _SCRIPTS_DIR.parent / "data" / "screenshots",
    ]
    headlines_path = None
    counts_by_paper = {}
    pdfs_found = 0
    for d in candidates:
        if not d.exists():
            continue
        for suffix in ("*.pdf", "*.png", "*.jpg"):
            for p in d.glob(suffix):
                pdfs_found += 1
        jp = d / "headlines.json"
        if jp.exists():
            headlines_path = jp
    stage.row("Local artifact dirs checked", _fmt_int(len(candidates)))
    stage.row("PDF/PNG/JPG artifacts found", _fmt_int(pdfs_found))
    if headlines_path:
        try:
            data = json.loads(headlines_path.read_text())
            for slug, paper in data.items():
                counts_by_paper[slug] = len(paper.get("headlines") or [])
            stage.row("headlines.json path", str(headlines_path))
            stage.row("Papers with headlines", _fmt_int(len(counts_by_paper)))
            for slug, n in counts_by_paper.items():
                stage.note(f"{slug}: {n} headlines")
        except Exception as e:
            stage.note(f"headlines.json parse failed: {e}")
    if pdfs_found == 0 and not headlines_path:
        stage.set(STATUS_WARN, "no front-pages artifacts on local disk (may run only in CI)")
    else:
        stage.headline = f"{pdfs_found} artifacts, {len(counts_by_paper)} papers"


def probe_frontpages_remote(stage: Stage, conn: sqlite3.Connection) -> None:
    url = "https://home-economics.us/pulse-screenshots/headlines.json"
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT)
    except Exception as e:
        stage.set(STATUS_BROKEN, f"HTTP fetch failed: {e}")
        stage.row("URL", url)
        stage.row("Error", f"{type(e).__name__}: {e}")
        return
    stage.row("URL", url)
    stage.row("HTTP status", resp.status_code)
    stage.row("Content length", _fmt_int(len(resp.content)))
    if resp.status_code != 200:
        stage.set(STATUS_BROKEN, f"HTTP {resp.status_code}")
        return
    if len(resp.content) < 50:
        stage.set(STATUS_BROKEN, "Response empty")
        return
    try:
        data = resp.json()
        per_paper = {slug: len(p.get("headlines") or []) for slug, p in data.items()}
        for slug, n in per_paper.items():
            stage.note(f"{slug}: {n} headlines")
        stage.headline = f"{len(per_paper)} papers uploaded"
    except Exception as e:
        stage.set(STATUS_WARN, f"JSON parse failed: {e}")


# ── Delivery / infrastructure ──────────────────────────────────────────

def probe_v1_email(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v1(conn)
    if not row:
        stage.set(STATUS_BROKEN, "no v1 briefing exists")
        return
    sent = bool(row["email_sent"])
    sent_at = row["email_sent_at"] or ""
    stage.row("Briefing id", row["id"])
    stage.row("Created at", row["created_at"])
    stage.row("email_sent", "yes" if sent else "no")
    stage.row("email_sent_at", sent_at or "(never)")
    if not sent:
        stage.set(STATUS_BROKEN, "v1 email never marked sent")
    else:
        stage.headline = f"sent at {sent_at[:16]}"


def probe_v2_email(stage: Stage, conn: sqlite3.Connection) -> None:
    row = _latest_v2(conn)
    if not row:
        stage.set(STATUS_WARN, "no v2 briefing yet")
        return
    sent = bool(row["email_sent"])
    sent_at = row["email_sent_at"] or ""
    stage.row("Briefing id", row["id"])
    stage.row("Created at", row["created_at"])
    stage.row("email_sent", "yes" if sent else "no")
    stage.row("email_sent_at", sent_at or "(never)")
    if not sent:
        # v2 send is via v2_runner which doesn't call mark_briefing_emailed by default
        # (it sends via Resend but doesn't update the DB flag). So absence isn't FAIL.
        stage.set(STATUS_WARN, "v2 briefing exists but email_sent flag not set")
    else:
        stage.headline = f"sent at {sent_at[:16]}"


def probe_db(stage: Stage, db_path: str, conn: sqlite3.Connection) -> None:
    try:
        size = os.path.getsize(db_path)
    except Exception:
        size = 0
    stage.row("DB path", db_path)
    stage.row("DB size", _fmt_bytes(size))
    tables = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    for t in tables:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            stage.row(f"{t} rows", _fmt_int(n))
        except Exception:
            stage.row(f"{t} rows", "(unable to count)")
    stage.headline = f"{_fmt_bytes(size)} · {len(tables)} tables"


# ── Build the report ───────────────────────────────────────────────────

STAGE_DEFINITIONS = [
    # (key, title, prose, probe_fn)
    (
        "1.1", "Twitter via Apify",
        "Pulls tweets from a curated list of housing/economics handles via the "
        "Apify Twitter scraper. The collector enforces a daily Apify budget, "
        "applies a like-count floor, and bypasses that floor for SuperSmart "
        "handles.",
        probe_twitter,
    ),
    (
        "1.2", "Bluesky",
        "Fetches recent posts from a curated list of Bluesky accounts (housing "
        "economists, journalists, urbanists) using the public AT-proto APIs.",
        probe_bluesky,
    ),
    (
        "1.3", "Hacker News",
        "Pulls high-scoring HN stories matching housing/economy keywords.",
        probe_hackernews,
    ),
    (
        "1.4", "RSS News feeds",
        "Parses every OPML-listed news/Google-Alerts feed (Bloomberg, FT, WSJ, "
        "regional papers, reporter alerts). Surfaces any feed that's silent for "
        "14d+ as likely broken.",
        probe_rss_news,
    ),
    (
        "1.5", "RSS Substack feeds",
        "Pulls competitor and adjacent Substack newsletters (Calculated Risk, "
        "ResiClub, Apricitas, Stratechery, etc.) via their RSS feeds.",
        probe_rss_substack,
    ),
    (
        "1.6", "RSS Journal feeds",
        "Academic journal RSS — NBER, Journal of Housing Economics, Urban "
        "Studies, Cities, etc. Journals trickle slowly; 7d is the right "
        "window for health.",
        probe_rss_journals,
    ),
    (
        "1.7", "Gmail Workspace (aziz@home-economics.us)",
        "OAuth Gmail collector for the work inbox. Pulls inbox mail, filters "
        "junk patterns, and routes institutional newsletters into the synthesis "
        "pool. Refresh-token health is checked live.",
        probe_gmail_workspace,
    ),
    (
        "1.8", "Gmail personal (aziz.sunderji@gmail.com) via IMAP",
        "IMAP collector for the personal Gmail account (OAuth doesn't work for "
        "personal accounts under the current Workspace app config). Uses an "
        "app-specific password and pulls inbox mail through the same junk/"
        "allowlist filters.",
        probe_gmail_imap,
    ),
    (
        "1.9", "Press mentions / Starred emails",
        "Press mentions for Home Economics (RSS-ish), plus emails starred in "
        "any inbox flow as high-signal items into the synthesis pool.",
        probe_press_mentions,
    ),
    (
        "2.1", "Article body enrichment (Browserbase)",
        "RSS articles arrive with a 200-char teaser; Browserbase fetches the "
        "full body so the LLM has substantive text. High-relevance items "
        "still in teaser-state indicate enrichment skipped them.",
        probe_article_enrichment,
    ),
    (
        "2.2", "Tweet link enrichment",
        "Resolves t.co and quoted-tweet links so the synthesizer can read what "
        "tweets are actually referencing.",
        probe_tweet_link_enrichment,
    ),
    (
        "2.3", "Journal abstract fetching",
        "Pulls abstracts for journal items whose RSS only carries a title — "
        "needed for Paper of the Day picks to have substance.",
        probe_journal_abstracts,
    ),
    (
        "2.5", "Content dedup",
        "Cross-source dedup via content_hash and source_id uniqueness. "
        "A high dup rate is normal (Twitter resurfaces); high cross-source "
        "hash collisions indicate the same article appearing across feeds.",
        probe_dedup,
    ),
    (
        "3.1", "Trigger classifier",
        "Opus-driven classifier assigning a trigger_type to every item "
        "(action_event, opinion, commentary, etc.). Cache hits avoid "
        "re-classifying items still in the 24h window across multiple runs.",
        probe_trigger_classifier,
    ),
    (
        "3.2", "Relevance scoring",
        "Haiku assigns a 0-100 relevance score per item. The synthesis pool "
        "uses score ≥40; the highlight pool uses ≥70.",
        probe_relevance_scoring,
    ),
    (
        "3.3", "Twitter author blocklist",
        "Tweet collector drops any author in TWITTER_AUTHOR_BLOCKLIST before "
        "insertion. We verify the blocklist is honored.",
        probe_blocklist,
    ),
    (
        "4.1", "V1 synthesis — item pool",
        "Inputs to the v1 Sonnet synth call. Source breakdown, total items, "
        "and conversation items measure pipeline width into the LLM.",
        probe_v1_pool,
    ),
    (
        "4.2", "V1 synthesis — Anthropic spend",
        "Today's Anthropic API spend, broken down by model (Sonnet/Haiku/Opus). "
        "Surfaces cost drift and model-mix changes.",
        probe_v1_anthropic,
    ),
    (
        "4.3", "V1 synthesis — Themes",
        "News-anchored themes generated by Sonnet (each carries heat_level and "
        "platform badges). Empty list is a synthesis failure.",
        probe_v1_themes,
    ),
    (
        "4.4", "V1 synthesis — Roundups",
        "Topical conversation roundups without a hard news trigger. Typically "
        "3-5 entries; 0 is rare.",
        probe_v1_roundups,
    ),
    (
        "4.5", "V1 synthesis — Paper of the day",
        "One curated academic paper from the journal feed pool. We also list "
        "papers picked in the last 14 days so the LLM can avoid repeating.",
        probe_v1_paper,
    ),
    (
        "4.6", "V1 synthesis — Conversation pulse",
        "Top-of-briefing prose summary of the day. Empty/short summary is a "
        "synthesis-output failure.",
        probe_v1_pulse,
    ),
    (
        "4.7", "V1 synthesis — Post-processors",
        "Counters from the synth post-processing stack. Each non-zero counter "
        "is something the model produced badly that we cleaned up — track "
        "these to see whether the prompt should be tightened.",
        probe_v1_postprocessors,
    ),
    (
        "4.8", "V1 synthesis — Cited sources",
        "Unique sources cited inline in the email, grouped by type. The "
        "renderer reads this directly from stats_summary.cited_sources.",
        probe_v1_cited_sources,
    ),
    (
        "5.1", "V2 synthesis (clustering)",
        "Embedding-based clustering pipeline that produces v2 roundups. "
        "Stats come straight from _v2_meta.stats. v2 ships alongside v1 for "
        "comparison.",
        probe_v2,
    ),
    (
        "6.1", "Front pages — local artifacts",
        "Freedom Forum PDFs are downloaded, headlines extracted, page snapshots "
        "rendered, and a headlines.json sidecar written. Local artifact "
        "presence indicates the capture step ran.",
        probe_frontpages_local,
    ),
    (
        "6.3", "Front pages — Bluehost SFTP upload",
        "The page snapshots + headlines.json are uploaded to Bluehost so the "
        "email can reference them by URL. A live HTTP probe checks the "
        "uploaded headlines.json is fresh and parseable.",
        probe_frontpages_remote,
    ),
    (
        "7.1", "V1 email send",
        "Reads the latest daily briefing's email_sent + email_sent_at to "
        "verify Resend delivery + DB acknowledgement.",
        probe_v1_email,
    ),
    (
        "7.2", "V2 email send",
        "Reads the latest daily_v2_clustered briefing. v2_runner doesn't "
        "currently mark the briefing as emailed in the DB, so a WARN here is "
        "informational, not a true failure.",
        probe_v2_email,
    ),
]


def build_stages(conn: sqlite3.Connection, db_path: str) -> list[Stage]:
    stages: list[Stage] = []
    for key, title, prose, probe in STAGE_DEFINITIONS:
        st = Stage(key, f"{key} — {title}", prose)
        _safe(st, lambda s, fn=probe: fn(s, conn))
        stages.append(st)
    # Infra always last
    infra = Stage(
        "8.1", "8.1 — Database",
        "Pulse SQLite database size and per-table row counts. Catches "
        "runaway tables and verifies the DB exists / is readable.",
    )
    _safe(infra, lambda s: probe_db(s, db_path, conn))
    stages.append(infra)
    return stages


# ── HTML render ────────────────────────────────────────────────────────

def _render_status_pill(status: str) -> str:
    label = STATUS_LABEL.get(status, status)
    glyph = STATUS_GLYPH.get(status, "")
    color = STATUS_COLOR.get(status, COLOR_INK)
    return (
        f'<span style="display: inline-block; background: {color}; color: #fff; '
        f'padding: 3px 10px; border-radius: 12px; font-size: 12px; '
        f'font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">'
        f'{glyph} {label}</span>'
    )


def _render_data_table(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    cells = []
    for label, value in rows:
        cells.append(
            f'<tr>'
            f'<td style="padding: 3px 14px 3px 0; color: {COLOR_MUTED}; '
            f'font-size: 14px; white-space: nowrap; vertical-align: top;">{_esc(label)}</td>'
            f'<td style="padding: 3px 0; color: {COLOR_INK}; font-size: 14px; '
            f'font-family: ui-monospace, SFMono-Regular, Menlo, monospace; '
            f'word-break: break-word; vertical-align: top;">{_esc(value)}</td>'
            f'</tr>'
        )
    return (
        f'<table cellpadding="0" cellspacing="0" style="margin: 8px 0 0 0; '
        f'width: 100%; border-collapse: collapse;">'
        + "".join(cells) +
        '</table>'
    )


def _render_notes(notes: list[str]) -> str:
    if not notes:
        return ""
    lis = "".join(f"<li style=\"margin-bottom: 4px;\">{_esc(n)}</li>" for n in notes)
    return (
        f'<ul style="margin: 8px 0 0 0; padding-left: 18px; color: {COLOR_INK}; '
        f'font-size: 14px; line-height: 1.5;">{lis}</ul>'
    )


def _render_stage(stage: Stage) -> str:
    color = STATUS_COLOR[stage.status]
    headline_html = (
        f'<div style="font-size: 14px; color: {COLOR_MUTED}; margin-top: 4px;">'
        f'{_esc(stage.headline)}</div>'
        if stage.headline else ""
    )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin: 0 0 18px 0; border-left: 4px solid {color}; '
        f'background: #fff;"><tr><td style="padding: 14px 16px;">'
        f'<div style="display: flex; align-items: center; gap: 12px; '
        f'margin-bottom: 6px;">'
        f'<span style="font-size: 18px; font-weight: 700; color: {COLOR_INK}; '
        f'flex: 1;">{_esc(stage.title)}</span>'
        f'{_render_status_pill(stage.status)}'
        f'</div>'
        f'{headline_html}'
        f'<div style="font-size: 14px; color: {COLOR_INK}; margin-top: 8px; '
        f'line-height: 1.55;">{_esc(stage.prose)}</div>'
        f'{_render_data_table(stage.data_rows)}'
        f'{_render_notes(stage.notes)}'
        f'</td></tr></table>'
    )


def render_html(stages: list[Stage], date_str: str) -> tuple[str, int, int]:
    broken = [s for s in stages if s.status == STATUS_BROKEN]
    degraded = [s for s in stages if s.status == STATUS_WARN]

    parts: list[str] = []
    parts.append(
        f'<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f'</head><body style="margin: 0; padding: 0; background-color: {COLOR_BG};">'
        f'<center><table width="100%" cellpadding="0" cellspacing="0" '
        f'bgcolor="{COLOR_BG}" style="background-color: {COLOR_BG};">'
        f'<tr><td align="center">'
        f'<table cellpadding="0" cellspacing="0" style="max-width: 760px; '
        f'width: 100%; font-family: {FONT_STACK}; color: {COLOR_INK};">'
        f'<tr><td style="padding: 20px 18px;">'
    )

    # Header
    parts.append(
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="border-bottom: 3px solid {COLOR_INK}; padding-bottom: 12px;">'
        f'<h1 style="font-size: 22px; margin: 0 0 4px 0; letter-spacing: -0.5px;">'
        f'Pulse Pipeline Health</h1>'
        f'<p style="color: {COLOR_MUTED}; font-size: 14px; margin: 0;">'
        f'{_esc(date_str)} · {len(stages)} stages · {len(broken)} broken · '
        f'{len(degraded)} degraded</p>'
        f'</td></tr></table>'
    )
    parts.append(_spacer(20))

    # Top breakage summary
    if broken or degraded:
        items_html = ""
        for s in broken + degraded:
            items_html += (
                f'<li style="margin-bottom: 4px;"><strong>{_esc(s.title)}</strong>'
                f' &mdash; {_render_status_pill(s.status)}'
                f'{(" " + _esc(s.headline)) if s.headline else ""}'
                f'</li>'
            )
        bg = "#FBCAB5" if broken else "#FEC439"
        text_color = COLOR_BROKEN if broken else COLOR_INK
        parts.append(
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td bgcolor="{bg}" style="background-color: {bg}; padding: 14px 16px; '
            f'border-radius: 6px;">'
            f'<div style="font-size: 12px; text-transform: uppercase; '
            f'letter-spacing: 1px; color: {text_color}; font-weight: 700; '
            f'margin-bottom: 8px;">Things broken right now</div>'
            f'<ul style="margin: 0; padding-left: 18px; font-size: 14px; '
            f'line-height: 1.6;">{items_html}</ul>'
            f'</td></tr></table>'
        )
        parts.append(_spacer(20))
    else:
        parts.append(
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td bgcolor="#C6DCCB" style="background-color: #C6DCCB; '
            f'padding: 14px 16px; border-radius: 6px;">'
            f'<div style="font-size: 14px; color: #3D3733; font-weight: 600;">'
            f'All {len(stages)} stages reported clean.</div>'
            f'</td></tr></table>'
        )
        parts.append(_spacer(20))

    # Stages
    for s in stages:
        parts.append(_render_stage(s))

    # Footer
    parts.append(
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="border-top: 2px solid {COLOR_INK}; padding-top: 12px; '
        f'font-size: 13px; color: {COLOR_MUTED}; text-align: center;">'
        f'Pulse Pipeline Health · generated {_esc(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))}'
        f'</td></tr></table>'
    )

    parts.append(
        '</td></tr></table></td></tr></table></center></body></html>'
    )

    return "".join(parts), len(broken), len(degraded)


# ── Send ───────────────────────────────────────────────────────────────

def send_report_email(html: str, subject: str, to: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set — cannot send")
        return False
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
            },
            timeout=30,
        )
    except Exception as e:
        logger.error(f"Resend request failed: {e}")
        return False
    if resp.status_code != 200:
        logger.error(f"Resend HTTP {resp.status_code}: {resp.text[:300]}")
        return False
    return True


# ── Entrypoint ─────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser()
    p.add_argument("--to", default=DEFAULT_TO)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--dry-run", action="store_true",
                   help="Write HTML to /tmp/health_report.html and skip Resend.")
    p.add_argument("--out", default="/tmp/health_report.html")
    args = p.parse_args()

    if not os.path.exists(args.db):
        logger.error(f"DB not found: {args.db}")
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    stages = build_stages(conn, args.db)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    html, n_broken, n_degraded = render_html(stages, date_str)
    subject = f"[Pulse Pipeline Health] {date_str} — {n_broken} broken, {n_degraded} degraded"

    Path(args.out).write_text(html)
    logger.info(f"Wrote report to {args.out}")
    logger.info(f"Subject: {subject}")
    logger.info(
        f"Status summary: "
        f"{sum(1 for s in stages if s.status == STATUS_OK)} ok, "
        f"{n_degraded} degraded, {n_broken} broken."
    )

    if args.dry_run:
        return 0

    ok = send_report_email(html, subject, args.to)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
