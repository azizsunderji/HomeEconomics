"""Twitter/X collector via Apify.

Scrapes the Pulse Twitter List timeline (all accounts @AzizSunderji follows).
Uses apidojo/twitter-list-scraper for organic feed coverage including retweets
and cross-account conversations — much better than per-account batching.
Requires APIFY_API_KEY env var.
Budget-tracked: defaults to $2/day max (TWITTER_DAILY_BUDGET_CENTS).
Budget persists in pulse.db (synced via rclone) so it works across
multiple GitHub Actions runs.
"""

from __future__ import annotations

import os
import logging
import sqlite3
import time
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

import httpx

from collectors import PulseItem
from config import (
    TWITTER_MIN_LIKES,
    TWITTER_DAILY_BUDGET_CENTS,
)

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apidojo/twitter-list-scraper"
# Twitter List "Pulse" — mirrors @AzizSunderji's follows (~1900 accounts)
PULSE_LIST_ID = "2046263290972582212"
LIST_MAX_ITEMS = 3000  # ~$1.20/day at $0.0004/tweet — covers a full 24h of ~970 accounts

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


def _run_actor(list_id: str = PULSE_LIST_ID, max_items: int = LIST_MAX_ITEMS) -> list[dict]:
    """Run the Apify twitter-list-scraper actor and wait for results."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        logger.warning("APIFY_API_KEY not set — skipping Twitter collection")
        return []

    if not _check_budget():
        return []

    actor_api_id = ACTOR_ID.replace("/", "~")
    url = f"{APIFY_BASE}/acts/{actor_api_id}/runs"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "listIds": [list_id],
        "maxItems": max_items,
    }

    # Wait up to 5 minutes via the API's blocking endpoint, then fall back to polling
    resp = httpx.post(
        f"{url}?waitForFinish=300",
        json=payload, headers=headers, timeout=330,
    )
    resp.raise_for_status()
    run_data = resp.json().get("data", {})
    run_id = run_data.get("id")
    status = run_data.get("status")

    if not run_id:
        logger.error("Failed to start Apify actor run")
        return []

    if status not in ("SUCCEEDED",):
        # Poll up to 25 minutes — a full 3000-tweet scrape can take 15+ minutes
        # and we'd rather wait than abandon a still-running scrape and re-spend
        # the budget tomorrow.
        status_resp = None
        for _ in range(150):  # 150 × 10s = 25 min
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
            time.sleep(10)
        else:
            logger.error(f"Apify run {run_id} did not finish within 25-min poll window")
            return []
        final_run_data = status_resp.json().get("data", {})
    else:
        final_run_data = run_data

    cost_usd = final_run_data.get("usageTotalUsd", 0) or 0
    if cost_usd == 0:
        usage = final_run_data.get("usage") or {}
        cost_usd = usage.get("totalCostUsd", 0) or 0
    if cost_usd == 0:
        cost_usd = 0.32  # ~$0.0004 × 800 tweets
    _record_spend(int(cost_usd * 100))

    dataset_id = final_run_data.get("defaultDatasetId")
    if not dataset_id:
        logger.error("No dataset ID in Apify run response")
        return []

    results_resp = httpx.get(
        f"{APIFY_BASE}/datasets/{dataset_id}/items",
        headers=headers, timeout=60,
    )
    results_resp.raise_for_status()
    results = results_resp.json()
    results = [r for r in results if not r.get("noResults")]
    logger.info(f"Apify list scraper returned {len(results)} raw tweets from list {list_id}")
    return results


def collect(
    list_id: str = PULSE_LIST_ID,
    max_items: int = LIST_MAX_ITEMS,
    min_likes: int = TWITTER_MIN_LIKES,
) -> list[PulseItem]:
    """Collect recent tweets from the Pulse Twitter List timeline.

    Scrapes the organic list feed — includes retweets and cross-account
    conversations, giving much better coverage than per-account batching.
    Returns list of PulseItem objects.
    """
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        raise RuntimeError("APIFY_API_KEY not set — Twitter collection skipped entirely")

    if not _check_budget():
        raise RuntimeError(
            f"Twitter daily Apify budget exhausted ({TWITTER_DAILY_BUDGET_CENTS}¢ limit). "
            f"Increase TWITTER_DAILY_BUDGET_CENTS in config.py or wait until tomorrow."
        )

    raw_tweets = _run_actor(list_id=list_id, max_items=max_items)

    items = []
    seen_ids = set()

    for tweet in raw_tweets:
        tweet_id = tweet.get("id", "")
        if not tweet_id or tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)

        likes = tweet.get("likeCount", 0) or 0
        if likes < min_likes:
            continue

        # Parse date — Apify actors use different field names over time
        published = None
        for field in ("createdAt", "created_at", "timeParsed", "timestamp", "date", "tweetedAt"):
            created_at = tweet.get(field, "")
            if not created_at:
                continue
            try:
                if isinstance(created_at, (int, float)):
                    # Unix timestamp (seconds or ms)
                    ts = created_at / 1000 if created_at > 1e12 else created_at
                    published = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    # ISO string or Twitter's "Tue Apr 11 14:30:00 +0000 2026" format
                    s = str(created_at).replace("Z", "+00:00")
                    try:
                        published = datetime.fromisoformat(s)
                    except ValueError:
                        # Try Twitter's legacy format
                        published = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
                break
            except (ValueError, TypeError):
                continue

        # Skip tweets older than 48 hours — prevents old tweets from polluting
        # today's briefing when the Apify scraper pulls an account's history.
        if published is not None:
            age = datetime.now(timezone.utc) - published
            if age > timedelta(hours=48):
                continue

        author = tweet.get("author", {})
        username = author.get("userName", "") if isinstance(author, dict) else str(author)

        reply_count = tweet.get("replyCount", 0) or 0

        # Use fullText if available, fall back to text
        tweet_text = tweet.get("fullText") or tweet.get("text") or ""
        # Use twitterUrl if available, fall back to url or construct from username/id
        tweet_url = tweet.get("twitterUrl") or tweet.get("url") or f"https://x.com/{username}/status/{tweet_id}"

        item = PulseItem(
            source="twitter",
            source_id=f"tw_{tweet_id}",
            url=tweet_url,
            title=tweet_text[:200],
            body=tweet_text,
            author=f"@{username}" if username else "",
            published_at=published,
            score=likes + (tweet.get("retweetCount", 0) or 0),
            num_comments=reply_count,
            engagement_raw={
                "likes": likes,
                "retweets": tweet.get("retweetCount", 0) or 0,
                "replies": reply_count,
                "quotes": tweet.get("quoteCount", 0) or 0,
                "views": tweet.get("viewCount", 0) or 0,
                "bookmarks": tweet.get("bookmarkCount", 0) or 0,
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
