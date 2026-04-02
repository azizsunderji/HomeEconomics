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

from store import get_db, bulk_upsert, log_collection_start, log_collection_end, mark_briefing_emailed, get_apify_spend_today
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
    pipeline_errors = []

    # Collect + classify
    logger.info("Phase 1: Collection")
    collection_results = run_collectors(conn, sources=args.sources)
    # Check for sources that returned 0 new items (potential silent failures)
    for source_name, counts in collection_results.items():
        if counts["new"] == 0 and counts["dupe"] == 0:
            pipeline_errors.append(f"{source_name}: 0 items collected (possible auth/budget/API issue)")

    logger.info("Phase 2: Classification")
    try:
        classified = run_classification()
    except Exception as e:
        pipeline_errors.append(f"Classification failed: {e}")
        classified = 0

    logger.info("Phase 3: Arc tracking")
    try:
        update_arcs(conn)
    except Exception as e:
        pipeline_errors.append(f"Arc tracking failed: {e}")

    # Synthesize
    logger.info("Phase 4: Synthesis")
    from analysis.synthesize import generate_daily_briefing
    briefing = generate_daily_briefing(conn)

    if "error" in briefing:
        logger.error(f"Synthesis failed: {briefing['error']}")
        return briefing

    # Inject starred emails
    try:
        from collectors.gmail_starred import get_starred_emails
        briefing["_starred_emails"] = get_starred_emails()
    except Exception as e:
        logger.warning(f"Starred emails failed: {e}")
        briefing["_starred_emails"] = []

    # Inject journal articles (from academic RSS feeds — show all, bypass Sonnet)
    try:
        from datetime import timedelta
        journal_keywords = [
            "Housing Studies", "Journal of Housing Research", "Journal of Real Estate Research",
            "Journal of the American Planning Association", "Real Estate Economics",
            "Cornell Real Estate Review", "NBER New Working Papers", "ScienceDirect",
            "Journal of Urban Economics", "Journal of Housing Economics", "ScienceDirect: Cities",
            "Wiley", "Taylor & Francis",
        ]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        all_rss = conn.execute(
            "SELECT * FROM items WHERE source = 'rss' AND collected_at >= ? ORDER BY collected_at DESC",
            (cutoff,),
        ).fetchall()
        journal_items = []
        seen_journal_titles = set()
        for row in all_rss:
            item = dict(row)
            feed = item.get("feed_name", "")
            if any(k in feed for k in journal_keywords):
                title = item.get("title", "")
                title_key = title[:80].lower().strip()
                if title_key in seen_journal_titles:
                    continue
                seen_journal_titles.add(title_key)
                journal_items.append({
                    "journal": feed.replace("ScienceDirect Publication: ", "").replace("ScienceDirect: ", ""),
                    "title": title,
                    "url": item.get("url", ""),
                })
        # Cap at 30 most recent to keep the section readable
        briefing["_journal_articles"] = journal_items[:30]
        logger.info(f"Journal articles: {len(journal_items)}")
    except Exception as e:
        logger.warning(f"Journal articles failed: {e}")
        briefing["_journal_articles"] = []

    # Inject institutional emails + Gmail newsletters (routed to separate sections)
    try:
        from config import (GMAIL_JUNK_SENDER_PATTERNS, GMAIL_JUNK_TITLE_PATTERNS,
                          INSTITUTIONAL_SENDER_ALLOWLIST, GMAIL_NEWSLETTER_SENDERS)
        import re as _re
        cutoff_36h = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        all_gmail = conn.execute(
            "SELECT * FROM items WHERE source = 'gmail' AND collected_at >= ? ORDER BY collected_at DESC",
            (cutoff_36h,),
        ).fetchall()
        institutional_items = []
        gmail_newsletter_items = []
        for row in all_gmail:
            item = dict(row)
            sender = (item.get("author", "") or "").lower()
            title = (item.get("title", "") or "").lower()
            # Skip junk
            if any(p in sender for p in GMAIL_JUNK_SENDER_PATTERNS):
                continue
            if any(p in title for p in GMAIL_JUNK_TITLE_PATTERNS):
                continue
            # Skip journal alert emails — these are covered by RSS journal feeds
            if any(p in title for p in ["early view alert", "table of contents alert"]):
                continue
            # Clean sender name
            raw_author = item.get("author", "")
            match = _re.match(r'"?([^"<]+)"?\s*<', raw_author)
            display_name = match.group(1).strip() if match else raw_author.split("<")[0].strip() or raw_author
            entry = {
                "source": display_name,
                "author": display_name,
                "headline": item.get("title", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
            }
            # Route: newsletter senders → newsletters section
            if any(p in sender for p in GMAIL_NEWSLETTER_SENDERS):
                gmail_newsletter_items.append(entry)
            # Route: institutional allowlist → institutional signal
            elif any(p in sender or p in display_name.lower() for p in INSTITUTIONAL_SENDER_ALLOWLIST):
                institutional_items.append(entry)
        briefing["_institutional_emails"] = institutional_items
        briefing["_gmail_newsletters"] = gmail_newsletter_items
        logger.info(f"Institutional emails: {len(institutional_items)}, Gmail newsletters: {len(gmail_newsletter_items)}")
    except Exception as e:
        logger.warning(f"Institutional email injection failed: {e}")
        briefing["_institutional_emails"] = []
        briefing["_gmail_newsletters"] = []

    # Inject headlines (RSS-only from allowlisted publication domains)
    try:
        from config import HEADLINE_DOMAIN_ALLOWLIST, JOURNAL_FEED_PATTERNS, HEADLINE_FEED_BLOCKLIST, HEADLINE_CURATED_FEEDS
        cutoff_36h = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        all_rss = conn.execute(
            "SELECT * FROM items WHERE source = 'rss' AND collected_at >= ? ORDER BY collected_at DESC",
            (cutoff_36h,),
        ).fetchall()
        headline_items = []
        seen_titles = set()
        for row in all_rss:
            item = dict(row)
            feed = (item.get("feed_name", "") or "").lower()
            title = item.get("title", "")
            url = item.get("url", "") or ""
            title_key = title[:50].lower()
            if title_key in seen_titles:
                continue
            # Skip junk/boilerplate headlines
            if any(junk in title_key for junk in ["sign up for", "subscribe to", "newsletter"]):
                continue
            if any(p in feed for p in JOURNAL_FEED_PATTERNS):
                continue
            if any(p in feed for p in HEADLINE_FEED_BLOCKLIST):
                continue
            relevance = item.get("relevance_score") or 0
            is_curated = any(p in feed for p in HEADLINE_CURATED_FEEDS)
            if not is_curated and relevance < 30:
                continue
            published = item.get("published_at", "")
            if published and published < cutoff_36h:
                continue
            # Match by URL domain only (RSS items have direct URLs)
            url_lower = url.lower()
            source_label = ""
            for domain, label in HEADLINE_DOMAIN_ALLOWLIST.items():
                if domain in url_lower:
                    source_label = label
                    break
            if not source_label:
                continue
            seen_titles.add(title_key)
            headline_items.append({
                "source": source_label,
                "headline": title,
                "url": url,
                "relevance": relevance,
            })
        briefing["_headlines"] = headline_items
        logger.info(f"Headlines (allowlist only): {len(headline_items)}")
    except Exception as e:
        logger.warning(f"Headlines injection failed: {e}")
        briefing["_headlines"] = []

    # Inject press mentions
    try:
        from collectors.press_mentions import get_press_mentions
        briefing["_press_mentions"] = get_press_mentions()
    except Exception as e:
        logger.warning(f"Press mentions failed: {e}")
        briefing["_press_mentions"] = []

    # Inject Apify spend for the email header
    briefing["_apify_spend_cents"] = get_apify_spend_today(conn)

    # Inject pipeline-level errors into briefing for email rendering
    existing_errors = briefing.get("_collection_errors", [])
    for err_msg in pipeline_errors:
        existing_errors.append({"source": "pipeline", "error": err_msg, "time": ""})
    if existing_errors:
        briefing["_collection_errors"] = existing_errors

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
        pipeline_errors.append(f"Notion push failed: {e}")
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
        pipeline_errors.append(f"Alert check failed: {e}")
        alerts = 0

    elapsed = time.time() - start
    logger.info(
        f"Daily pipeline complete in {elapsed:.0f}s — "
        f"email={'sent' if email_sent else 'FAILED'}, "
        f"{pushed} stories to Notion, {alerts} alerts"
        f"{f', {len(pipeline_errors)} errors' if pipeline_errors else ''}"
    )

    return {
        "briefing": briefing,
        "email_sent": email_sent,
        "notion_pushed": pushed,
        "alerts_sent": alerts,
        "pipeline_errors": pipeline_errors,
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

    # Only run free, no-auth-required sources
    test_sources = ["google_news", "hackernews", "bluesky"]
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
