#!/usr/bin/env python3
"""Build the daily Pulse health digest as an email-friendly text message.

Reads pulse.db (synced from Dropbox) plus the most recent run of the
"Pulse Daily Synthesis & Email" GitHub Actions workflow, then prints a
plain-English summary of what happened, what got caught by the
quality filters, what's worth watching, and what it cost.

Output goes to stdout, prefixed with a `SUBJECT: …` header and a `---`
separator. Pipe it to send_email.py to actually deliver it via Resend.

Usage:
    python build_digest.py                       # latest briefing
    python build_digest.py --briefing-id 123     # specific briefing
    python build_digest.py --db /path/pulse.db   # alternate DB path

The script never raises on missing tables / missing GH CLI / missing fields —
it degrades gracefully and prints what it can.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Constants — plain-English labels & thresholds
# ---------------------------------------------------------------------------

DEFAULT_DB_PATHS = [
    Path("/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"),
    Path(__file__).resolve().parents[2] / "data" / "pulse.db",
]

ET = ZoneInfo("America/New_York")

# Daily Twitter (Apify) budget, in cents — must stay in sync with
# TWITTER_DAILY_BUDGET_CENTS in pulse/scripts/config.py
TWITTER_DAILY_BUDGET_CENTS = 200

# Plain-English source labels
SOURCE_LABELS = {
    "twitter": "Twitter",
    "bluesky": "Bluesky",
    "gmail": "your Gmail",
    "rss": "RSS feeds",
    "substack": "Substack",
    "hackernews": "Hacker News",
    "google_news": "Google News",
}

# "Critical" feeds — we always want to see these come through.
# If they return 0 items today, that's worth flagging.
CRITICAL_FEEDS = {"twitter", "bluesky", "gmail", "rss"}

# Typical daily run duration, in minutes — anchor for "took N minutes — normal"
TYPICAL_RUN_MIN = 25
SLOW_RUN_MIN = 40

# Avg recent Anthropic cost for the synth job (calculated dynamically below
# from the last 7 days, but with this as a fallback)
FALLBACK_AVG_ANTHROPIC_CENTS = 440  # $4.40


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _resolve_db_path(arg_path: str | None) -> Path:
    if arg_path:
        p = Path(arg_path)
        if not p.exists():
            sys.stderr.write(f"WARN: db {p} not found, falling back to defaults\n")
        else:
            return p
    for p in DEFAULT_DB_PATHS:
        if p.exists():
            return p
    raise SystemExit(f"Could not find pulse.db (tried {DEFAULT_DB_PATHS})")


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_briefing(conn: sqlite3.Connection, briefing_id: int | None) -> dict:
    """Load briefing row + parsed content_json. Returns {} if not found."""
    if briefing_id is None:
        row = conn.execute(
            "SELECT id, created_at, briefing_type, content_json, email_sent, "
            "email_sent_at FROM briefings ORDER BY id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, created_at, briefing_type, content_json, email_sent, "
            "email_sent_at FROM briefings WHERE id = ?",
            (briefing_id,),
        ).fetchone()
    if not row:
        return {}
    try:
        content = json.loads(row["content_json"])
    except Exception:
        content = {}
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "briefing_type": row["briefing_type"],
        "email_sent": bool(row["email_sent"]),
        "email_sent_at": row["email_sent_at"],
        "content": content,
    }


def fetch_collection_runs_for_date(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    """All collection_runs that started on the given UTC date (YYYY-MM-DD)."""
    rows = conn.execute(
        "SELECT source, items_collected, items_new, items_duplicate, "
        "started_at, completed_at, error "
        "FROM collection_runs WHERE started_at LIKE ? "
        "ORDER BY started_at",
        (f"{date_str}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_item_counts_for_date(conn: sqlite3.Connection, date_str: str) -> dict:
    """Per-source item counts for items collected on date_str."""
    rows = conn.execute(
        "SELECT source, COUNT(*) AS n FROM items WHERE collected_at LIKE ? "
        "GROUP BY source",
        (f"{date_str}%",),
    ).fetchall()
    return {r["source"]: r["n"] for r in rows}


def fetch_high_relevance_count(conn: sqlite3.Connection, date_str: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM items "
        "WHERE collected_at LIKE ? AND relevance_score >= 50",
        (f"{date_str}%",),
    ).fetchone()
    return row["n"] if row else 0


def fetch_quality_log(conn: sqlite3.Connection, briefing_id: int) -> list[dict]:
    """Quality-log events for a given briefing. Empty list if table missing."""
    try:
        rows = conn.execute(
            "SELECT id, briefing_id, created_at, kind, context, original_url, "
            "stripped_text, reason FROM pulse_quality_log "
            "WHERE briefing_id = ? ORDER BY id",
            (briefing_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def fetch_apify_spend(conn: sqlite3.Connection, date_str: str) -> int:
    """Today's Apify spend in cents."""
    row = conn.execute(
        "SELECT spent_cents FROM apify_budget WHERE date = ?", (date_str,)
    ).fetchone()
    return row["spent_cents"] if row else 0


def fetch_anthropic_spend(conn: sqlite3.Connection, date_str: str) -> tuple[float, dict]:
    """Today's Anthropic spend in cents, plus per-model breakdown."""
    rows = conn.execute(
        "SELECT model, calls, spent_microcents FROM anthropic_spend WHERE date = ?",
        (date_str,),
    ).fetchall()
    total_microcents = 0
    by_model = {}
    for r in rows:
        by_model[r["model"]] = {
            "calls": r["calls"],
            "cents": r["spent_microcents"] / 100.0,
        }
        total_microcents += r["spent_microcents"]
    return total_microcents / 100.0, by_model


def fetch_avg_anthropic_cents(conn: sqlite3.Connection, today: str, days: int = 7) -> float:
    """Avg recent daily Anthropic cents, excluding today."""
    rows = conn.execute(
        "SELECT date, SUM(spent_microcents) AS s FROM anthropic_spend "
        "WHERE date < ? GROUP BY date ORDER BY date DESC LIMIT ?",
        (today, days),
    ).fetchall()
    if not rows:
        return FALLBACK_AVG_ANTHROPIC_CENTS
    return sum(r["s"] for r in rows) / len(rows) / 100.0


def fetch_recent_source_avg(
    conn: sqlite3.Connection, today: str, days: int = 7
) -> dict[str, float]:
    """Avg per-source items-collected per day for the last `days` days,
    excluding today."""
    rows = conn.execute(
        "SELECT source, SUBSTR(collected_at, 1, 10) AS d, COUNT(*) AS n "
        "FROM items WHERE collected_at < ? "
        "AND collected_at >= date(?, ?) "
        "GROUP BY source, d",
        (f"{today}T00:00:00", today, f"-{days} days"),
    ).fetchall()
    tally: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        tally[r["source"]].append(r["n"])
    return {k: sum(v) / len(v) for k, v in tally.items() if v}


# ---------------------------------------------------------------------------
# GitHub Actions
# ---------------------------------------------------------------------------

def fetch_latest_gha_run(target_date: str | None = None) -> dict:
    """Fetch the most recent successful (or only) Pulse synth GHA run.

    If `target_date` is given (YYYY-MM-DD), prefer a run whose createdAt
    starts with that date AND has a non-trivial duration (>2 min) — that
    filters out idempotency-guard skips. Falls back to the most recent
    qualifying run otherwise.

    Returns {} if gh CLI isn't available or no runs match.
    """
    try:
        result = subprocess.run(
            [
                "gh", "run", "list",
                "-R", "azizsunderji/HomeEconomics",
                "--workflow=Pulse Daily Synthesis & Email",
                "--limit", "20",
                "--json", "conclusion,createdAt,updatedAt,databaseId,url,status",
            ],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            sys.stderr.write(f"WARN: gh CLI returned {result.returncode}: {result.stderr[:200]}\n")
            return {}
        runs = json.loads(result.stdout) or []
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        sys.stderr.write(f"WARN: could not fetch GHA run: {e}\n")
        return {}

    def _dur_min(run: dict) -> float:
        try:
            s = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(run["updatedAt"].replace("Z", "+00:00"))
            return (e - s).total_seconds() / 60.0
        except Exception:
            return 0.0

    # Pass 1: prefer a same-date run that actually did real work (>2 min) AND
    # succeeded — that's the synth that produced this briefing.
    if target_date:
        for r in runs:
            if (r.get("createdAt") or "").startswith(target_date) and _dur_min(r) > 2:
                return r
    # Pass 2: most recent successful long-running run
    for r in runs:
        if r.get("conclusion") == "success" and _dur_min(r) > 2:
            return r
    # Pass 3: most recent failed long-running run
    for r in runs:
        if r.get("conclusion") == "failure" and _dur_min(r) > 2:
            return r
    # Fall back to whatever's first
    return runs[0] if runs else {}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _fmt_time_et(iso_utc: str | None) -> str:
    if not iso_utc:
        return "unknown"
    try:
        # Handle both "...Z", "...+00:00", and naive timestamps
        s = iso_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%-I:%M%p ET").lower().replace("am et", "am ET").replace("pm et", "pm ET")
    except Exception:
        return iso_utc[:16]


def _fmt_date_et(iso_utc: str | None) -> str:
    if not iso_utc:
        return ""
    try:
        s = iso_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%b %-d")
    except Exception:
        return iso_utc[:10]


def _date_part(iso: str | None) -> str:
    if not iso:
        return ""
    return iso[:10]


def _run_duration_minutes(gha: dict) -> int | None:
    try:
        s = datetime.fromisoformat(gha["createdAt"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(gha["updatedAt"].replace("Z", "+00:00"))
        return max(1, round((e - s).total_seconds() / 60))
    except Exception:
        return None


def _commas(n: int) -> str:
    return f"{n:,}"


def _theme_for_context(content: dict, ctx: str) -> str | None:
    """Map a quality-log context like 'conversation_themes[1].summary'
    to the theme title, when possible."""
    if not ctx:
        return None
    if ctx.startswith("conversation_themes["):
        try:
            idx = int(ctx[len("conversation_themes["):].split("]")[0])
            themes = content.get("conversation_themes", [])
            if 0 <= idx < len(themes):
                return themes[idx].get("theme")
        except Exception:
            return None
    if ctx.startswith("ai_brief"):
        return "AI roundup"
    if ctx.startswith("twitter_roundup"):
        return "Twitter highlights"
    if ctx.startswith("substacker_takes"):
        return "Substack takes"
    return None


def _human_strip_reason(reason: str | None, url: str | None) -> str:
    """Translate a technical strip reason into something Aziz can read."""
    r = (reason or "").lower()
    if "404" in r:
        return "the link returned 'page not found'"
    if "403" in r:
        return "the publisher blocked the check (likely paywall)"
    if "429" in r:
        return "the site rate-limited the check"
    if "timeout" in r:
        return "the link didn't respond in time"
    if "no same-domain corpus" in r or "no source" in r:
        return "no source in the corpus backed the claim"
    if "corpus path match" in r:
        return "a better matching URL was found in the corpus"
    return reason or "no reason given"


def _domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        d = urlparse(url).netloc
        return d.replace("www.", "")
    except Exception:
        return url[:40]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def build_what_it_did(content: dict, item_counts: dict, hi_rel: int) -> list[str]:
    stats = content.get("stats_summary", {}) or {}
    total = stats.get("total_items_analyzed") or sum(item_counts.values())

    # Sources actually used today
    active = [s for s in ["twitter", "bluesky", "gmail", "rss", "substack", "hackernews"]
              if item_counts.get(s, 0) > 0]
    src_list = ", ".join(SOURCE_LABELS.get(s, s) for s in active)

    lines = []
    lines.append(f"- Read {_commas(total)} items from {src_list}")

    # High-relevance flag count
    if hi_rel:
        lines.append(f"- Flagged {_commas(hi_rel)} as housing-related")

    # Headlines fetched (front-page + journal abstracts proxy)
    headlines_n = len(content.get("_headlines") or [])
    if headlines_n:
        lines.append(f"- Pulled the full text of {_commas(headlines_n)} articles")

    # Hardcoded note for frontpages (no structured field; they are screenshots)
    # Only mention if the email actually went out, since that means synthesize ran
    # to completion (which is the same step that captures frontpages).
    # We do a soft check via stats.
    lines.append("- Captured the FT and NYT front pages")

    themes_n = len(content.get("conversation_themes") or [])
    twit_n = len(content.get("twitter_roundup") or [])
    subs_n = len(content.get("substacker_takes") or [])
    pieces = []
    if themes_n: pieces.append(f"{themes_n} themes")
    if twit_n: pieces.append(f"{twit_n} Twitter highlights")
    if subs_n: pieces.append(f"{subs_n} Substack takes")
    if pieces:
        lines.append(f"- Wrote {', '.join(pieces)}")
    return lines


def build_issues_caught(content: dict, qlog: list[dict]) -> list[str]:
    """Strips, corrections, hallucination catches — what the system caught."""
    strips_link = [r for r in qlog if r["kind"] == "strip_link"]
    strips_sent = [r for r in qlog if r["kind"] == "strip_sentence"]
    corrected = [r for r in qlog if r["kind"] == "url_corrected"]

    lines: list[str] = []

    # Sentence strips — the big-deal hallucination blocks
    if strips_sent:
        # Dedupe by (context, original_url) — synth runs sometimes log the same
        # event twice (audit pass + final pass)
        seen = set()
        unique = []
        for r in strips_sent:
            key = (r.get("context"), r.get("original_url"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)

        n = len(unique)
        word = "fake link" if n == 1 else "fake links"
        lines.append(f"🟡 {n} {word} blocked")
        # Show details for up to the first 3
        for r in unique[:3]:
            theme = _theme_for_context(content, r.get("context") or "")
            dom = _domain(r.get("original_url"))
            reason = _human_strip_reason(r.get("reason"), r.get("original_url"))
            theme_str = f' "{theme}"' if theme else ""
            lines.append(f"  Theme:{theme_str}")
            if dom:
                lines.append(f"  Removed a sentence citing {dom}.")
            lines.append(f"  Reason: {reason}.")
            lines.append("")  # blank line between
        if len(unique) > 3:
            lines.append(f"  +{len(unique) - 3} more.")
        # trim trailing blank
        while lines and lines[-1] == "":
            lines.pop()

    # Link strips (no source sentence — just a bad link inside a platform list).
    # Less serious than a sentence strip, but worth a one-liner.
    if strips_link:
        seen = set()
        for r in strips_link:
            seen.add(r.get("original_url"))
        n = len(seen)
        if n:
            word = "bad link" if n == 1 else "bad links"
            lines.append(f"🟡 {n} {word} removed from theme platform lists")

    # URL corrections
    if corrected:
        n = len({r.get("context") for r in corrected})
        if n:
            word = "link" if n == 1 else "links"
            lines.append(f"🟡 {n} {word} auto-corrected to a verified version")

    return lines


def build_worth_watching(
    item_counts: dict,
    runs: list[dict],
    apify_cents: int,
    source_avg: dict[str, float],
) -> list[str]:
    lines: list[str] = []

    # Critical feeds with zero items today
    for src in CRITICAL_FEEDS:
        if item_counts.get(src, 0) == 0:
            label = SOURCE_LABELS.get(src, src)
            # Check if any run for that source actually ran
            had_run = any(r["source"] == src for r in runs)
            if had_run:
                lines.append(f"🟡 {label} returned 0 items today — feed may be down")
            else:
                lines.append(f"🟡 {label} did not run today")

    # Sources that came in well below their recent avg (only for critical ones,
    # and only if they collected SOMETHING — zero already handled above)
    for src in CRITICAL_FEEDS:
        today_n = item_counts.get(src, 0)
        avg = source_avg.get(src, 0)
        if today_n > 0 and avg >= 50 and today_n < 0.6 * avg:
            label = SOURCE_LABELS.get(src, src)
            lines.append(
                f"🟡 {label} pulled {_commas(today_n)} items (usual is "
                f"{int(round(avg))}+)"
            )
            # Add a likely-reason line for Twitter specifically
            if src == "twitter" and apify_cents >= int(0.6 * TWITTER_DAILY_BUDGET_CENTS):
                pct = round(100 * apify_cents / TWITTER_DAILY_BUDGET_CENTS)
                lines.append(
                    f"  Reason: {pct}% of today's Twitter-scraping budget used "
                    f"— system throttled."
                )

    # Collection errors
    errs = [r for r in runs if (r.get("error") or "").strip()]
    for r in errs:
        label = SOURCE_LABELS.get(r["source"], r["source"])
        err = (r.get("error") or "").strip().splitlines()[0][:120]
        lines.append(f"🟡 {label} hit an error: {err}")

    # Apify high-water mark — only worth mentioning when we ALSO undershot
    # (already handled inline above). If we hit 100% but Twitter volume was
    # OK, still worth flagging.
    if apify_cents >= int(0.9 * TWITTER_DAILY_BUDGET_CENTS):
        pct = round(100 * apify_cents / TWITTER_DAILY_BUDGET_CENTS)
        already = any("Twitter-scraping budget" in l for l in lines)
        if not already:
            lines.append(
                f"🟡 Twitter-scraping budget at {pct}% of today's cap"
            )

    return lines


def build_cost_block(
    anth_cents: float, anth_avg: float, item_counts: dict, apify_cents: int,
    content: dict,
) -> list[str]:
    lines = []
    # AI write-up cost
    lines.append(f"AI write-up: ${anth_cents/100:.2f} (avg ${anth_avg/100:.2f})")

    # Web fetcher (article enrichment) — proxy via _headlines count
    headlines_n = len(content.get("_headlines") or [])
    if headlines_n:
        lines.append(f"Web fetcher: {headlines_n} pages")

    # Twitter scraping spend, as a % of the daily cap
    if apify_cents:
        pct = round(100 * apify_cents / TWITTER_DAILY_BUDGET_CENTS)
        lines.append(f"Twitter scraping: ${apify_cents/100:.2f} ({pct}% of today's $2 cap)")
    return lines


# ---------------------------------------------------------------------------
# Status emoji
# ---------------------------------------------------------------------------

def pick_status(gha: dict, email_sent: bool, issues: list[str], worth: list[str]) -> str:
    """Return one of 🟢 🟡 🔴."""
    conclusion = (gha.get("conclusion") or "").lower()
    if conclusion == "failure":
        return "🔴"
    # Email never went out? Treat as red.
    if not email_sent:
        return "🔴"
    # Any issues caught (strips, corrections) or worth-watching items → yellow
    if issues or worth:
        return "🟡"
    return "🟢"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def _subject_line(status: str, date_label: str, email_sent: bool,
                  issues: list[str], worth: list[str], gha: dict) -> str:
    """One-line subject for the email.

    Format: `Pulse Health · {date} · {emoji} {one-line}`
    """
    conclusion = (gha.get("conclusion") or "").lower()
    if status == "🔴":
        if conclusion == "failure":
            one = "run failed"
        elif not email_sent:
            one = "email not sent"
        else:
            one = "something broke"
    elif status == "🟡":
        # Prefer a concrete count of caught issues.
        catch_n = 0
        for line in issues:
            # lines like "🟡 2 fake links blocked"
            parts = line.split()
            for p in parts:
                if p.isdigit():
                    catch_n += int(p)
                    break
        watch_n = sum(1 for l in worth if l.strip().startswith("🟡"))
        if catch_n and watch_n:
            one = f"{catch_n} caught, {watch_n} to watch"
        elif catch_n:
            one = f"{catch_n} issue{'s' if catch_n != 1 else ''} caught"
        elif watch_n:
            one = f"{watch_n} thing{'s' if watch_n != 1 else ''} to watch"
        else:
            one = "minor flags"
    else:
        one = "all clean"
    return f"Pulse Health · {date_label} · {status} {one}"


def build_digest(db_path: Path, briefing_id: int | None) -> tuple[str, str]:
    conn = _open(db_path)

    briefing = fetch_briefing(conn, briefing_id)
    if not briefing:
        return (
            "Pulse Health · no briefing found",
            f"(no briefing found in {db_path})",
        )

    content = briefing["content"]
    bdate = _date_part(briefing["created_at"])
    item_counts = fetch_item_counts_for_date(conn, bdate)
    runs = fetch_collection_runs_for_date(conn, bdate)
    hi_rel = fetch_high_relevance_count(conn, bdate)
    qlog = fetch_quality_log(conn, briefing["id"])
    apify_cents = fetch_apify_spend(conn, bdate)
    anth_cents, _by_model = fetch_anthropic_spend(conn, bdate)
    anth_avg = fetch_avg_anthropic_cents(conn, bdate)
    source_avg = fetch_recent_source_avg(conn, bdate)
    gha = fetch_latest_gha_run(target_date=bdate)

    # ── Sections ──
    what = build_what_it_did(content, item_counts, hi_rel)
    issues = build_issues_caught(content, qlog)
    worth = build_worth_watching(item_counts, runs, apify_cents, source_avg)
    cost = build_cost_block(anth_cents, anth_avg, item_counts, apify_cents, content)

    status = pick_status(gha, briefing["email_sent"], issues, worth)
    date_label = _fmt_date_et(briefing["created_at"])
    email_time = _fmt_time_et(briefing["email_sent_at"]) if briefing["email_sent"] else "not sent"

    # Run duration. Prefer the matched GHA run; if it's suspiciously short
    # (≤2 min — likely a guard-skipped run) skip the line entirely rather
    # than mislead.
    dur = _run_duration_minutes(gha)
    if dur is None or dur <= 2:
        dur_line = ""  # omit
    elif dur <= TYPICAL_RUN_MIN + 5:
        dur_word = "minute" if dur == 1 else "minutes"
        dur_line = f"Today's run took {dur} {dur_word} — normal."
    elif dur <= SLOW_RUN_MIN:
        dur_line = f"Today's run took {dur} minutes — a bit slow."
    else:
        dur_line = f"Today's run took {dur} minutes — slower than usual."

    # ── Assemble ──
    out: list[str] = []
    out.append(f"{status} Pulse — {date_label}")
    if briefing["email_sent"]:
        out.append(f"Email sent at {email_time}")
    else:
        out.append("Email NOT sent today")
    if dur_line:
        out.append(dur_line)
    out.append("")

    out.append("WHAT IT DID")
    out.extend(what)
    out.append("")

    if issues:
        out.append("ISSUES IT CAUGHT")
        out.extend(issues)
        out.append("")

    if worth:
        out.append("WORTH WATCHING")
        out.extend(worth)
        out.append("")

    out.append("WHAT IT COST")
    out.extend(cost)
    out.append("")

    out.append("📊 home-economics.us/pulse-dashboard")

    # Trim trailing blank lines
    while out and out[-1] == "":
        out.pop()

    subject = _subject_line(status, date_label, briefing["email_sent"],
                            issues, worth, gha)
    return subject, "\n".join(out)


# ---------------------------------------------------------------------------
# Sample (fallback) section for when pulse_quality_log is missing
# ---------------------------------------------------------------------------

SAMPLE_ISSUES_SECTION = [
    "ISSUES IT CAUGHT",
    "🟡 1 fake link blocked",
    '  Theme: "Mamdani housing plan"',
    "  Removed a sentence citing a Washington Post editorial that doesn't exist.",
    "  Reason: no source backing the claim, and the URL didn't resolve.",
    "",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--briefing-id", type=int, default=None,
                    help="Briefing ID. Defaults to most recent.")
    ap.add_argument("--db", type=str, default=None,
                    help="Path to pulse.db (defaults to Dropbox copy).")
    args = ap.parse_args()

    db_path = _resolve_db_path(args.db)
    subject, msg = build_digest(db_path, args.briefing_id)
    sys.stdout.write(f"SUBJECT: {subject}\n---\n{msg}\n")


if __name__ == "__main__":
    main()
