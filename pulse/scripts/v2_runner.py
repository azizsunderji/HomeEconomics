"""V2 runner: builds a v2 briefing by replacing v1's roundups with
clustering-generated ones and sends it to a single test recipient for
comparison.

Usage:
    python pulse/scripts/v2_runner.py [--briefing-id 139] [--to me@x.com]

By default it picks the most recent v1 briefing (briefing_type IS NULL
or 'v1') and sends to aziz@home-economics.us.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pulse" / "scripts"))

from analysis.roundup_clustering import cluster_and_write_roundups
from analysis.synthesize import _compute_cited_sources
from delivery.email_briefing import render_briefing_html

import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_DB = os.environ.get(
    "PULSE_DB", "/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"
)
DEFAULT_TO = "aziz@home-economics.us"
EMAIL_FROM = "Pulse V2 <onboarding@resend.dev>"


def load_briefing(conn: sqlite3.Connection,
                  briefing_id: int | None = None) -> tuple[int, dict, str]:
    """Load (id, briefing_json, created_at) for the target v1 briefing."""
    if briefing_id is not None:
        row = conn.execute(
            "SELECT id, content_json, created_at FROM briefings WHERE id = ?",
            (briefing_id,)
        ).fetchone()
        if not row:
            raise SystemExit(f"briefing {briefing_id} not found")
    else:
        row = conn.execute(
            "SELECT id, content_json, created_at FROM briefings "
            "WHERE briefing_type = 'daily' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise SystemExit("no briefings found")
    return row[0], json.loads(row[1]), row[2]


def build_v2_briefing(v1: dict, v2_roundups: list[dict],
                      conn: sqlite3.Connection,
                      v2_stats: dict) -> dict:
    """Construct the v2 briefing dict from v1's themes/etc. and v2's roundups."""
    v2 = json.loads(json.dumps(v1))  # deep copy
    v2["conversation_roundups"] = v2_roundups
    v2["_v2_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": v2_stats,
        "source_v1_briefing_id": v1.get("_briefing_id"),
    }
    # Recompute cited_sources to reflect the new roundup citations.
    if "stats_summary" not in v2:
        v2["stats_summary"] = {}
    v2["stats_summary"]["cited_sources"] = _compute_cited_sources(v2, conn)
    return v2


def send_v2_email(v2_briefing: dict, to: str, source_v1_id: int) -> bool:
    """Render and send the v2 briefing with a distinct subject prefix."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set")
        return False
    html, top_theme, theme_count = render_briefing_html(v2_briefing)
    date = v2_briefing.get("date") or datetime.now(timezone.utc).strftime("%b %d")
    subject = f"[Pulse V2 clusters] {top_theme} | vs #{source_v1_id} | {date}"
    if theme_count <= 1:
        subject = f"[Pulse V2 clusters] {top_theme} | vs #{source_v1_id} | {date}"

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html},
        timeout=30,
    )
    if resp.status_code == 200:
        logger.info(f"v2 email sent: {subject}")
        return True
    logger.error(f"resend {resp.status_code}: {resp.text[:300]}")
    return False


def store_v2_briefing(conn: sqlite3.Connection, v2: dict) -> int:
    """Persist v2 briefing with briefing_type='v2' so it doesn't clash."""
    # Make sure the column exists; if not, just store without it.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(briefings)").fetchall()}
    if "briefing_type" in cols:
        cur = conn.execute(
            "INSERT INTO briefings (briefing_type, content_json, created_at) "
            "VALUES ('daily_v2_clustered', ?, ?)",
            (json.dumps(v2, default=str),
             datetime.now(timezone.utc).isoformat()),
        )
    else:
        cur = conn.execute(
            "INSERT INTO briefings (content_json, created_at) VALUES (?, ?)",
            (json.dumps(v2, default=str),
             datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()
    return cur.lastrowid


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--briefing-id", type=int, default=None,
                   help="v1 briefing to compare against (default: most recent)")
    p.add_argument("--to", default=DEFAULT_TO)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--lookback-hours", type=int, default=24)
    p.add_argument("--max-roundups", type=int, default=5)
    p.add_argument("--debug-dir", default="/tmp/v2_run")
    p.add_argument("--no-send", action="store_true",
                   help="skip email send; just print stats")
    p.add_argument("--no-store", action="store_true",
                   help="skip DB persistence")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    v1_id, v1, v1_created = load_briefing(conn, args.briefing_id)
    v1["_briefing_id"] = v1_id
    print(f"loaded v1 briefing #{v1_id} created at {v1_created}")
    print(f"  v1 roundups: {len(v1.get('conversation_roundups') or [])}")

    # Use the v1 created_at as the END of the corpus window
    end_dt = datetime.fromisoformat(v1_created.replace("Z", "+00:00"))
    print(f"  corpus window: {args.lookback_hours}h ending {end_dt.isoformat()}")

    roundups, stats = cluster_and_write_roundups(
        conn,
        hours_lookback=args.lookback_hours,
        end=end_dt,
        max_roundups=args.max_roundups,
        debug_dir=args.debug_dir,
    )

    print(f"\nv2 pipeline stats: {json.dumps(stats, indent=2)}")
    print(f"\nv2 roundups generated: {len(roundups)}")
    for r in roundups:
        print(f"  - [{r.cluster_size}] {r.topic}")

    v2_roundups_payload = [
        {"topic": r.topic, "summary": r.summary,
         "_cluster_id": r.cluster_id, "_cluster_size": r.cluster_size}
        for r in roundups
    ]
    v2 = build_v2_briefing(v1, v2_roundups_payload, conn, stats)

    # Debug dump
    if args.debug_dir:
        Path(args.debug_dir).mkdir(parents=True, exist_ok=True)
        with open(Path(args.debug_dir) / "v2_briefing.json", "w") as f:
            json.dump(v2, f, indent=2, default=str)

    if not args.no_send:
        ok = send_v2_email(v2, args.to, v1_id)
        if not ok:
            sys.exit(1)
    else:
        print("--no-send set; skipping email")

    if not args.no_store:
        v2_id = store_v2_briefing(conn, v2)
        print(f"stored v2 briefing as id={v2_id}")
    else:
        print("--no-store set; skipping persistence")


if __name__ == "__main__":
    main()
