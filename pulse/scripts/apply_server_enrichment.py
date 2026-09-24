"""Apply the article bodies the noon server's live Chrome fetched to this run's pulse.db.

2026-09-24 (Aziz: "consolidate enrichment on the server Chrome; Browserbase stays as fallback for a
week, then is cancelled if the health report shows no blocks"). Runs in pulse-synth.yml after the
re-collect step and before "Enrich articles via Browserbase". The server (pulse/editor/
enrich_server.py, noon-enrich.timer, Mon-Fri 10:15 UTC) publishes
https://noon.homeeconomics.us/feeds/enriched_bodies.json; the workflow downloads it with curl and
passes the path here.

For each url in the file's items, rows whose body is shorter than the file's body get
body = file body and enrich_mode = 'server_chrome'. Rows with an equal or longer body are left
alone. The Browserbase step then only finds what is still short (rss/hackernews under
MIN_BODY_LEN; gmail and substack rows are selected by that step whatever their length).

Fail-soft: a missing or unreadable file prints a warning and exits 0, so the synthesis goes on
with Browserbase alone.

    python scripts/apply_server_enrichment.py /tmp/enriched_bodies.json [--db pulse/data/pulse.db]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = os.environ.get("PULSE_DB", str(Path(__file__).resolve().parent.parent / "data" / "pulse.db"))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("bodies", help="path of the downloaded enriched_bodies.json")
    p.add_argument("--db", default=DEFAULT_DB)
    args = p.parse_args()

    # The server's Dropbox mirror of pulse.db is read-only; only the GitHub Actions copy is written.
    if "/Dropbox/" in str(Path(args.db).resolve()) or str(Path(args.db).resolve()).startswith("/home/aziz/OVH/"):
        print(f"refusing to write to the Dropbox copy of pulse.db ({args.db})")
        return 2
    try:
        data = json.loads(Path(args.bodies).read_text())
        items = data.get("items") or {}
    except (OSError, ValueError) as e:
        print(f"::warning::server-Chrome bodies not available ({type(e).__name__}); Browserbase enriches alone")
        return 0
    gen = data.get("generated_at", "?")
    try:
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).total_seconds() / 3600
        age = f"{age_h:.1f}h old"
    except (TypeError, ValueError):
        age = "age unknown"
    if not Path(args.db).exists():
        print(f"::warning::pulse.db not found at {args.db}; nothing applied")
        return 0

    conn = sqlite3.connect(args.db, timeout=60)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    if "enrich_mode" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN enrich_mode TEXT")
    updated = urls_hit = 0
    for url, it in items.items():
        body = (it or {}).get("body") or ""
        if not url or not body:
            continue
        cur = conn.execute(
            "UPDATE items SET body = ?, enrich_mode = 'server_chrome' "
            "WHERE url = ? AND LENGTH(COALESCE(body, '')) < ?",
            (body, url, len(body)),
        )
        if cur.rowcount:
            updated += cur.rowcount
            urls_hit += 1
    conn.commit()
    conn.close()
    blocked = ", ".join(sorted((data.get("blocked") or {}).keys())) or "none"
    print(f"server-Chrome enrichment: file generated {gen} ({age}), {len(items)} bodies; "
          f"updated {updated} rows ({urls_hit} urls); hosts blocked on the server run: {blocked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
