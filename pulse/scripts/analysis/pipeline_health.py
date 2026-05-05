"""Pipeline health monitor — runs before synthesis and flags breakage.

Checks every stage of the collect→classify→enrich→synthesize pipeline against
expected baselines. Returns a list of FAILURE / WARNING messages. Empty list
means all clear.

Use this to fail loudly when something breaks upstream, rather than shipping
a degraded briefing and relying on the user to notice.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


# Minimum items/authors expected per source in the last 24h — calibrated from
# historical healthy runs (see collection_runs table). Set to the 20th %ile
# of recent healthy days so that a genuine short day doesn't false-alarm,
# but a broken pipeline does.
EXPECTED_MIN = {
    "rss":        {"items": 200, "authors": 30},
    "twitter":    {"items": 50,  "authors": 10},
    "bluesky":    {"items": 30,  "authors": 8},
    "substack":   {"items": 10,  "authors": 5},
    "hackernews": {"items": 5,   "authors": 3},
    "gmail":      {"items": 5,   "authors": 3},
}


def check_health(conn: sqlite3.Connection) -> list[dict]:
    """Return a list of {severity, stage, message} problems.

    severity: "FAILURE" (briefing will be broken), "WARNING" (degraded but usable)
    """
    problems: list[dict] = []
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # ── STAGE 1: Collection volume per source ──────────────────────────
    for source, expected in EXPECTED_MIN.items():
        row = conn.execute(
            """SELECT COUNT(*), COUNT(DISTINCT author)
               FROM items
               WHERE source = ? AND collected_at >= ?""",
            (source, cutoff_24h),
        ).fetchone()
        items, authors = row[0], row[1]

        if items < expected["items"]:
            problems.append({
                "severity": "FAILURE" if items < expected["items"] // 2 else "WARNING",
                "stage": "collection",
                "message": (
                    f"{source}: only {items} items collected in 24h "
                    f"(expected ≥{expected['items']}, {authors} unique authors)"
                ),
            })
        elif authors < expected["authors"]:
            problems.append({
                "severity": "WARNING",
                "stage": "collection",
                "message": (
                    f"{source}: {items} items but only {authors} unique authors "
                    f"(expected ≥{expected['authors']}) — possibly collecting from too few sources"
                ),
            })

    # ── STAGE 1b: Gmail OAuth token health ──────────────────────────────
    # Google expires refresh tokens for Testing-mode External apps with
    # sensitive scopes (gmail.modify) after 7 days. Proactively check:
    #  (a) each token can still refresh right now
    #  (b) the token is approaching its 7-day expiry (warn at 5 days)
    import os, json as _json
    from pathlib import Path as _Path
    from datetime import datetime as _dt, timezone as _tz
    try:
        import httpx
        tokens_raw = os.environ.get("GMAIL_TOKENS", "")
        # Load per-client issue timestamps (recorded by gmail_auth.py)
        stamp_path = _Path(__file__).parent.parent.parent / "data" / "gmail_token_issued.json"
        issued = {}
        if stamp_path.exists():
            try:
                issued = _json.loads(stamp_path.read_text())
            except Exception:
                issued = {}

        if tokens_raw:
            tokens = _json.loads(tokens_raw)
            if not isinstance(tokens, list):
                tokens = [tokens]
            now = _dt.now(_tz.utc)
            for i, t in enumerate(tokens):
                cid = t["client_id"]
                # (a) live refresh test
                try:
                    r = httpx.post(
                        "https://oauth2.googleapis.com/token",
                        data={
                            "client_id": cid,
                            "client_secret": t["client_secret"],
                            "refresh_token": t["refresh_token"],
                            "grant_type": "refresh_token",
                        },
                        timeout=10,
                    )
                    if r.status_code != 200:
                        err = r.json().get("error", "unknown")
                        problems.append({
                            "severity": "FAILURE",
                            "stage": "gmail-auth",
                            "message": (
                                f"Gmail token {i} ({cid[:20]}...) refresh FAILED ({err}). "
                                f"Run: python3 pulse/scripts/gmail_auth.py — then update "
                                f"GMAIL_TOKENS in ~/.zprofile AND in GitHub Secrets."
                            ),
                        })
                        continue
                except Exception as e:
                    problems.append({
                        "severity": "WARNING",
                        "stage": "gmail-auth",
                        "message": f"Gmail token {i} check failed: {e}",
                    })
                    continue

                # (b) proactive expiry warning when within 48 hours
                # of the 7-day testing-mode token expiry
                meta = issued.get(cid, {})
                if meta.get("expires"):
                    try:
                        issue_ts = _dt.fromisoformat(meta["issued_at"])
                        age_days = (now - issue_ts).total_seconds() / 86400
                        lifetime_days = meta.get("expires_after_days", 7)
                        days_left = lifetime_days - age_days
                        if days_left < 2:  # < 48h remaining → warn
                            problems.append({
                                "severity": "FAILURE",
                                "stage": "gmail-auth",
                                "message": (
                                    f"Gmail token {i} ({meta.get('account','?')}) "
                                    f"expires in {days_left:.1f} days. "
                                    f"Re-auth soon: `python3 pulse/scripts/gmail_auth.py`, "
                                    f"then update GMAIL_TOKENS in ~/.zprofile AND GitHub Secrets."
                                ),
                            })
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Gmail token health check skipped: {e}")

    # ── STAGE 2: Collection errors in collection_runs ──────────────────
    err_rows = conn.execute(
        """SELECT source, error, started_at
           FROM collection_runs
           WHERE started_at >= ? AND error != ''
           ORDER BY started_at DESC""",
        (cutoff_24h,),
    ).fetchall()
    for source, err, started in err_rows:
        problems.append({
            "severity": "FAILURE",
            "stage": "collection",
            "message": f"{source} errored at {started[:16]}: {err[:150]}",
        })

    # ── STAGE 3: Classification — are collected items actually classified? ──
    row = conn.execute(
        """SELECT COUNT(*)
           FROM items
           WHERE collected_at >= ?
             AND classified_at IS NULL""",
        (cutoff_24h,),
    ).fetchone()
    unclassified = row[0]
    if unclassified > 50:
        problems.append({
            "severity": "FAILURE",
            "stage": "classify",
            "message": (
                f"{unclassified} items from the last 24h are unclassified — "
                f"classification phase failed or was skipped"
            ),
        })

    # ── STAGE 4: Enrichment — RSS articles with only short teaser bodies ──
    row = conn.execute(
        """SELECT COUNT(*)
           FROM items
           WHERE source = 'rss'
             AND collected_at >= ?
             AND relevance_score >= 60
             AND LENGTH(body) < 500""",
        (cutoff_24h,),
    ).fetchone()
    unenriched_high_relevance = row[0]
    if unenriched_high_relevance > 20:
        problems.append({
            "severity": "WARNING",
            "stage": "enrich",
            "message": (
                f"{unenriched_high_relevance} high-relevance RSS items still "
                f"have teaser-length bodies — enrichment may have skipped them"
            ),
        })

    # ── STAGE 5: Synthesis inputs — will synthesis have anything to work with? ──
    row = conn.execute(
        """SELECT COUNT(*)
           FROM items
           WHERE collected_at >= ?
             AND classified_at IS NOT NULL
             AND relevance_score >= 40""",
        (cutoff_24h,),
    ).fetchone()
    synth_inputs = row[0]
    if synth_inputs < 20:
        problems.append({
            "severity": "FAILURE",
            "stage": "synthesize",
            "message": (
                f"Only {synth_inputs} items meet synthesis threshold (relevance ≥40) "
                f"in last 24h — briefing will be empty or near-empty"
            ),
        })

    return problems


def format_report(problems: list[dict]) -> str:
    """Format health-check problems for logging or alerting."""
    if not problems:
        return "All pipeline checks passed."

    lines = [f"Pipeline health: {len(problems)} issue(s) detected", ""]
    failures = [p for p in problems if p["severity"] == "FAILURE"]
    warnings = [p for p in problems if p["severity"] == "WARNING"]

    if failures:
        lines.append("FAILURES (will break briefing):")
        for p in failures:
            lines.append(f"  [{p['stage']}] {p['message']}")
        lines.append("")
    if warnings:
        lines.append("WARNINGS (degraded but usable):")
        for p in warnings:
            lines.append(f"  [{p['stage']}] {p['message']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from store import get_db

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = get_db()
    problems = check_health(conn)
    print(format_report(problems))
    sys.exit(1 if any(p["severity"] == "FAILURE" for p in problems) else 0)
