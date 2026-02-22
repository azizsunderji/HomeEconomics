#!/usr/bin/env python3
"""Pulse pipeline orchestrator.

Usage:
    python run_pipeline.py collect        # Run all collectors + classify
    python run_pipeline.py daily          # Collect + classify + synthesize + email
    python run_pipeline.py weekly         # Weekly contrarian analysis
    python run_pipeline.py collect-only   # Just collect, no classification
    python run_pipeline.py classify-only  # Just classify unclassified items
    python run_pipeline.py test           # Test run: collect from 1-2 sources, no email
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# Add scripts/ to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from store import get_db, bulk_upsert, log_collection_start, log_collection_end, mark_briefing_emailed
from analysis.classify import run_classification
from analysis.arc_tracker import update_arcs
from analysis.crosswalk import build_index

logger = logging.getLogger("pulse")


def _collect_source(conn, source_name: str, collect_fn, **kwargs) -> tuple[int, int]:
    """Run a single collector with logging."""
    run_id = log_collection_start(conn, source_name)
    try:
        items = collect_fn(**kwargs)
        new_count, dupe_count = bulk_upsert(conn, items)
        log_collection_end(conn, run_id, len(items), new_count, dupe_count)
        logger.info(f"  {source_name}: {new_count} new, {dupe_count} dupes (of {len(items)} collected)")
        return new_count, dupe_count
    except Exception as e:
        log_collection_end(conn, run_id, 0, 0, 0, error=str(e))
        logger.error(f"  {source_name}: FAILED — {e}")
        return 0, 0


def run_collectors(conn, sources: list[str] | None = None) -> dict:
    """Run all (or selected) collectors.

    Returns dict of source -> {new, dupe} counts.
    """
    results = {}

    # Define all collectors
    all_collectors = {
        "reddit": lambda: __import__("collectors.reddit", fromlist=["collect"]).collect(),
        "google_news": lambda: __import__("collectors.rss_news", fromlist=["collect"]).collect(),
        "bluesky": lambda: __import__("collectors.bluesky", fromlist=["collect"]).collect(),
        "hackernews": lambda: __import__("collectors.hackernews", fromlist=["collect"]).collect(),
        "rss": lambda: __import__("collectors.rss_feeds", fromlist=["collect"]).collect(),
        "substack": lambda: __import__("collectors.rss_substacks", fromlist=["collect"]).collect(),
        "twitter": lambda: __import__("collectors.twitter_apify", fromlist=["collect"]).collect(),
        "gmail": lambda: __import__("collectors.gmail", fromlist=["collect"]).collect(),
    }

    # Filter to requested sources
    if sources:
        collectors = {k: v for k, v in all_collectors.items() if k in sources}
    else:
        collectors = all_collectors

    total_new = 0
    total_dupe = 0

    for name, fn in collectors.items():
        new, dupe = _collect_source(conn, name, fn)
        results[name] = {"new": new, "dupe": dupe}
        total_new += new
        total_dupe += dupe

    logger.info(f"Collection complete: {total_new} new items, {total_dupe} dupes across {len(collectors)} sources")
    return results


def cmd_collect(args):
    """Collect + classify pipeline (runs 4x daily)."""
    conn = get_db()
    logger.info("=== PULSE COLLECT + CLASSIFY ===")
    start = time.time()

    # Collect
    logger.info("Phase 1: Collection")
    collection_results = run_collectors(conn, sources=args.sources)

    # Classify
    logger.info("Phase 2: Classification")
    classified = run_classification()

    # Update arcs
    logger.info("Phase 3: Arc tracking")
    arc_summary = update_arcs(conn)

    elapsed = time.time() - start
    logger.info(f"Pipeline complete in {elapsed:.0f}s — {classified} items classified, {len(arc_summary)} topics tracked")

    return {
        "collection": collection_results,
        "classified": classified,
        "arcs": len(arc_summary),
        "elapsed_seconds": round(elapsed),
    }


def cmd_daily(args):
    """Full daily pipeline: collect + classify + synthesize + email."""
    conn = get_db()
    logger.info("=== PULSE DAILY BRIEFING ===")
    start = time.time()

    # Collect + classify
    logger.info("Phase 1: Collection")
    run_collectors(conn, sources=args.sources)

    logger.info("Phase 2: Classification")
    run_classification()

    logger.info("Phase 3: Arc tracking")
    update_arcs(conn)

    # Synthesize
    logger.info("Phase 4: Synthesis")
    from analysis.synthesize import generate_daily_briefing
    briefing = generate_daily_briefing(conn)

    if "error" in briefing:
        logger.error(f"Synthesis failed: {briefing['error']}")
        return briefing

    # Email
    logger.info("Phase 5: Email delivery")
    from delivery.email_briefing import send_email
    email_sent = send_email(briefing)

    if email_sent and "_briefing_id" in briefing:
        mark_briefing_emailed(conn, briefing["_briefing_id"])

    # Notion push
    logger.info("Phase 6: Notion story queue")
    try:
        from delivery.notion_queue import push_all_unpushed
        pushed = push_all_unpushed()
    except Exception as e:
        logger.warning(f"Notion push skipped: {e}")
        pushed = 0

    # Convergence alerts
    logger.info("Phase 7: Alert check")
    try:
        from analysis.convergence import compute_convergence
        from delivery.pushover_alert import check_and_alert
        convergence = compute_convergence(conn, hours=6)  # Only very recent for alerts
        alerts = check_and_alert(convergence)
    except Exception as e:
        logger.warning(f"Alert check skipped: {e}")
        alerts = 0

    elapsed = time.time() - start
    logger.info(
        f"Daily pipeline complete in {elapsed:.0f}s — "
        f"email={'sent' if email_sent else 'FAILED'}, "
        f"{pushed} stories to Notion, {alerts} alerts"
    )

    return {
        "briefing": briefing,
        "email_sent": email_sent,
        "notion_pushed": pushed,
        "alerts_sent": alerts,
        "elapsed_seconds": round(elapsed),
    }


def cmd_weekly(args):
    """Weekly contrarian analysis (Sunday mornings)."""
    conn = get_db()
    logger.info("=== PULSE WEEKLY CONTRARIAN ===")
    start = time.time()

    # Refresh data lake index
    logger.info("Phase 1: Refreshing data lake index")
    build_index()

    # Run contrarian analysis
    logger.info("Phase 2: Contrarian analysis")
    from analysis.contrarian import run_weekly_contrarian
    analysis = run_weekly_contrarian(conn)

    # Push new story opportunities to Notion
    logger.info("Phase 3: Notion push")
    try:
        from delivery.notion_queue import push_all_unpushed
        pushed = push_all_unpushed()
    except Exception as e:
        logger.warning(f"Notion push skipped: {e}")
        pushed = 0

    elapsed = time.time() - start
    logger.info(f"Weekly analysis complete in {elapsed:.0f}s — {pushed} stories to Notion")

    return {
        "analysis": analysis,
        "notion_pushed": pushed,
        "elapsed_seconds": round(elapsed),
    }


def cmd_collect_only(args):
    """Just run collectors, no classification."""
    conn = get_db()
    logger.info("=== PULSE COLLECT ONLY ===")
    results = run_collectors(conn, sources=args.sources)
    return results


def cmd_classify_only(args):
    """Just classify unclassified items."""
    logger.info("=== PULSE CLASSIFY ONLY ===")
    classified = run_classification()
    return {"classified": classified}


def cmd_test(args):
    """Test run with minimal sources."""
    conn = get_db()
    logger.info("=== PULSE TEST RUN ===")

    # Only run free, no-auth-required sources (reddit now auth-free with .json endpoints)
    test_sources = ["reddit", "google_news", "hackernews", "bluesky"]
    results = run_collectors(conn, sources=test_sources)

    logger.info(f"Test collection: {json.dumps(results, indent=2)}")

    # Classify if we got items
    classified = run_classification()
    logger.info(f"Classified {classified} items")

    return {
        "collection": results,
        "classified": classified,
    }


def main():
    parser = argparse.ArgumentParser(description="Pulse pipeline orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Pipeline command")

    # Shared arguments
    for name, func in [
        ("collect", cmd_collect),
        ("daily", cmd_daily),
        ("weekly", cmd_weekly),
        ("collect-only", cmd_collect_only),
        ("classify-only", cmd_classify_only),
        ("test", cmd_test),
    ]:
        sub = subparsers.add_parser(name)
        sub.set_defaults(func=func)
        if name not in ("classify-only", "weekly"):
            sub.add_argument(
                "--sources", nargs="*",
                help="Specific sources to collect from (default: all)"
            )

    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = args.func(args)

    # Print summary
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
