"""Twitter/X collector via Apify.

Uses the Apify Twitter Scraper actor to search for tweets.
Conversation-oriented: prioritizes tweets with replies and debate.
Requires APIFY_API_KEY env var.
Budget-tracked: defaults to $1/day max (TWITTER_DAILY_BUDGET_CENTS).
Budget persists in pulse.db (synced via rclone) so it works across
multiple GitHub Actions runs.
"""

from __future__ import annotations

import json
import os
import logging
import sqlite3
import time
from datetime import datetime, timezone, date
from pathlib import Path

import httpx

from collectors import PulseItem
from config import (
    TWITTER_SEARCH_QUERIES, TWITTER_ACCOUNTS, TWITTER_MIN_LIKES,
    TWITTER_MAX_PER_QUERY, TWITTER_DAILY_BUDGET_CENTS,
)

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
# Using xtdata/twitter-x-user-tweets-scraper — $0.0008/tweet (BRONZE tier)
# Takes startUrls (profile URLs), minimum 50 items per run
ACTOR_ID = "xtdata/twitter-x-user-tweets-scraper"

# DB path for budget tracking (same DB as pulse data, synced via rclone)
_DB_PATH = Path(__file__).parent.parent.parent / "data" / "pulse.db"


def _get_db() -> sqlite3.Connection:
    """Get a connection to pulse.db for budget tracking."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apify_budget (
            date TEXT PRIMARY KEY,
            spent_cents INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _check_budget() -> bool:
    """Check if we're within daily Apify budget. Returns True if OK to proceed."""
    today = date.today().isoformat()
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT spent_cents FROM apify_budget WHERE date = ?", (today,)
        ).fetchone()
        conn.close()
        spent = row[0] if row else 0
    except Exception as e:
        logger.warning(f"Budget check failed: {e}")
        return True  # fail open

    if spent >= TWITTER_DAILY_BUDGET_CENTS:
        logger.warning(
            f"Twitter daily budget exhausted: {spent}¢ / {TWITTER_DAILY_BUDGET_CENTS}¢"
        )
        return False

    return True


def _record_spend(cents: int) -> None:
    """Record Apify spend for budget tracking."""
    today = date.today().isoformat()
    try:
        conn = _get_db()
        conn.execute("""
            INSERT INTO apify_budget (date, spent_cents) VALUES (?, ?)
            ON CONFLICT(date) DO UPDATE SET spent_cents = spent_cents + excluded.spent_cents
        """, (today, cents))
        conn.commit()
        row = conn.execute(
            "SELECT spent_cents FROM apify_budget WHERE date = ?", (today,)
        ).fetchone()
        conn.close()
        total = row[0] if row else cents
        logger.info(f"Twitter budget: {total}¢ / {TWITTER_DAILY_BUDGET_CENTS}¢ today")
    except Exception as e:
        logger.warning(f"Budget record failed: {e}")


def _run_actor(profile_urls: list[str]) -> list[dict]:
    """Run the Apify Twitter scraper actor and wait for results."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        logger.warning("APIFY_API_KEY not set — skipping Twitter collection")
        return []

    if not _check_budget():
        return []

    # Apify API uses ~ separator for actor IDs (not /)
    actor_api_id = ACTOR_ID.replace("/", "~")
    url = f"{APIFY_BASE}/acts/{actor_api_id}/runs"

    payload = {
        "startUrls": profile_urls,
    }

    headers = {"Authorization": f"Bearer {api_key}"}

    # Start the actor run (wait up to 3 minutes for completion)
    resp = httpx.post(
        f"{url}?waitForFinish=180",
        json=payload, headers=headers, timeout=210,
    )
    resp.raise_for_status()
    run_data = resp.json().get("data", {})
    run_id = run_data.get("id")
    status = run_data.get("status")

    if not run_id:
        logger.error("Failed to start Apify actor run")
        return []

    # Check for plan limitation message
    status_message = run_data.get("statusMessage", "")
    if "Free Plan" in status_message or "paid plan" in status_message.lower():
        logger.warning(
            f"Apify Twitter scraping requires a paid plan. "
            f"Message: {status_message[:200]}. "
            f"Upgrade at https://apify.com/pricing or use X API Pay-Per-Use instead."
        )
        return []

    if status not in ("SUCCEEDED",):
        # If waitForFinish didn't complete, poll
        status_resp = None
        for _ in range(30):
            status_resp = httpx.get(
                f"{APIFY_BASE}/actor-runs/{run_id}",
                headers=headers, timeout=15,
            )
            status = status_resp.json().get("data", {}).get("status")
            if status == "SUCCEEDED":
                break
            elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                logger.error(f"Apify run {run_id} ended with status: {status}")
                return []
            time.sleep(5)
        else:
            logger.error(f"Apify run {run_id} timed out")
            return []
        final_run_data = status_resp.json().get("data", {})
    else:
        final_run_data = run_data

    # Record spend — Apify returns cost at top level as "usageTotalUsd"
    cost_usd = final_run_data.get("usageTotalUsd", 0) or 0
    if cost_usd == 0:
        # Fallback: check nested usage dict (older API versions)
        usage = final_run_data.get("usage") or {}
        cost_usd = usage.get("totalCostUsd", 0) or 0
    if cost_usd == 0:
        cost_usd = 0.16  # Conservative fallback based on observed per-run cost
    _record_spend(int(cost_usd * 100))

    # Fetch results
    dataset_id = final_run_data.get("defaultDatasetId")
    if not dataset_id:
        logger.error("No dataset ID in Apify run response")
        return []

    results_resp = httpx.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        headers=headers, timeout=30,
    )
    results_resp.raise_for_status()
    results = results_resp.json()

    return results


def collect(
    queries: list[str] | None = None,
    accounts: list[str] | None = None,
    min_likes: int = TWITTER_MIN_LIKES,
    max_per_query: int = TWITTER_MAX_PER_QUERY,
) -> list[PulseItem]:
    """Collect recent tweets matching housing/economics queries.

    Returns list of PulseItem objects.
    """
    queries = queries or TWITTER_SEARCH_QUERIES
    accounts = accounts or TWITTER_ACCOUNTS

    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        raise RuntimeError("APIFY_API_KEY not set — Twitter collection skipped entirely")

    if not _check_budget():
        raise RuntimeError(
            f"Twitter daily Apify budget exhausted ({TWITTER_DAILY_BUDGET_CENTS}¢ limit). "
            f"Increase TWITTER_DAILY_BUDGET_CENTS in config.py or wait until tomorrow."
        )

    items = []
    seen_ids = set()

    raw_tweets = []

    # Discovery queries (if any — currently empty, account tracking is primary)
    if queries:
        # Discovery queries not supported by the new actor; log and skip
        logger.info(f"Discovery queries ({len(queries)}) skipped — xtdata actor uses profile URLs only")

    # All accounts in a SINGLE run — xtdata actor has minimum 50 items per run,
    # so batching into one run is both cheaper and required
    if accounts:
        profile_urls = [f"https://x.com/{handle}" for handle in accounts]
        batch_results = _run_actor(profile_urls)
        raw_tweets.extend(batch_results)
        logger.info(f"  All accounts ({len(accounts)}): {len(batch_results)} raw tweets")

    for tweet in raw_tweets:
        tweet_id = tweet.get("id", "")
        if not tweet_id or tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)

        likes = tweet.get("favorite_count", 0) or 0
        if likes < min_likes:
            continue

        # Parse date — new format: "Fri Mar 21 14:26:29 +0000 2026"
        published = None
        created_at = tweet.get("created_at", "")
        if created_at:
            try:
                published = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                # Fallback to ISO format parsing
                try:
                    published = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except ValueError:
                    pass

        author = tweet.get("author", {})
        username = author.get("screen_name", "") if isinstance(author, dict) else str(author)

        reply_count = tweet.get("reply_count", 0) or 0

        # Use full_text if available, fall back to text
        tweet_text = tweet.get("full_text") or tweet.get("text") or ""
        # Use url if available, fall back to twitterUrl or construct from username/id
        tweet_url = tweet.get("url") or tweet.get("twitterUrl") or f"https://x.com/{username}/status/{tweet_id}"

        item = PulseItem(
            source="twitter",
            source_id=f"tw_{tweet_id}",
            url=tweet_url,
            title=tweet_text[:200],
            body=tweet_text,
            author=f"@{username}" if username else "",
            published_at=published,
            score=likes + (tweet.get("retweet_count", 0) or 0),
            num_comments=reply_count,
            engagement_raw={
                "likes": likes,
                "retweets": tweet.get("retweet_count", 0) or 0,
                "replies": reply_count,
                "quotes": tweet.get("quote_count", 0) or 0,
                "views": 0,
                "bookmarks": tweet.get("bookmark_count", 0) or 0,
                "is_conversation": reply_count >= 20,
            },
        )
        items.append(item)

    logger.info(f"Twitter total: {len(items)} items (filtered from {len(raw_tweets)} raw tweets)")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:10]:
        conv = "CONV" if item.engagement_raw.get("is_conversation") else "post"
        print(f"[{item.score:>6}] [{conv}] {item.author}: {item.title[:60]}")
    print(f"\nTotal: {len(results)} items")
