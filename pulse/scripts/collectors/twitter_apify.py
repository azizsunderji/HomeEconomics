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
    TWITTER_AUTHOR_BLOCKLIST,
    SUPER_SMART_HANDLES,
)

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apidojo/twitter-list-scraper"
# Twitter List "Pulse" — mirrors @AzizSunderji's follows (~1900 accounts)
PULSE_LIST_ID = "2046263290972582212"
LIST_MAX_ITEMS = 750   # 750 × 4 daily scrapes = 3000 tweets/day total (same as the
                       # old single-scrape budget, ~$1.20/day at $0.0004/tweet).
                       # Multi-scrape distribution lets quiet specialist accounts
                       # surface — under the old single 24h cut, volume tweeters
                       # ate the budget and accounts like Wiebe (1-2 tweets/day)
                       # fell off the back of the chronological window.

# "SuperSmart" curated list — must-have voices. Tweets from this list get
# guaranteed reserved slots in Sonnet synthesis input, regardless of relevance
# score, AND bypass the min_likes filter. List_id can be overridden via env var.
SUPER_SMART_LIST_ID = os.environ.get("SUPER_SMART_LIST_ID", "2053622551553744939")
SUPER_SMART_MAX_ITEMS = 200  # smaller budget — list is curated short

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
        # Poll up to 40 minutes. We've observed the actor take up to ~27 min
        # in the wild for 3000-tweet scrapes — 25 min was too tight and the
        # poll abandoned a still-running actor by 23 seconds on 2026-05-04.
        # Tolerate transient 5xx / non-JSON responses from Apify (e.g. 502 from
        # their CDN) — a single bad poll shouldn't kill the whole run.
        status_resp = None
        last_good = None
        for _ in range(240):  # 240 × 10s = 40 min
            try:
                status_resp = httpx.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=headers, timeout=15,
                )
                if status_resp.status_code >= 500:
                    logger.warning(f"Apify poll got {status_resp.status_code}, retrying")
                    time.sleep(10)
                    continue
                payload = status_resp.json().get("data", {})
                last_good = payload
                status = payload.get("status")
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(f"Apify poll transient error: {e}")
                time.sleep(10)
                continue
            if status == "SUCCEEDED":
                break
            elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                logger.error(f"Apify run {run_id} ended with status: {status}")
                return []
            time.sleep(10)
        else:
            # Poll window expired but actor may finish moments later — give it
            # one final 60-second wait + status check before abandoning.
            logger.warning(f"Apify run {run_id} hit 40-min poll limit; final check")
            time.sleep(60)
            try:
                final = httpx.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=headers, timeout=15,
                ).json().get("data", {})
                if final.get("status") == "SUCCEEDED":
                    logger.info(f"Apify run {run_id} finished after final wait")
                    last_good = final
                else:
                    logger.error(f"Apify run {run_id} did not finish (final status: {final.get('status')})")
                    return []
            except Exception as e:
                logger.error(f"Apify run {run_id} final check failed: {e}")
                return []
        final_run_data = last_good or {}
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


def _convert_raw_tweets(
    raw_tweets: list[dict],
    min_likes: int,
    super_smart: bool = False,
    seen_ids: set | None = None,
) -> list[PulseItem]:
    """Shared tweet-to-PulseItem conversion. Used by both Pulse and SuperSmart
    scrapes. If super_smart=True, items get platform_tags=["super_smart"] so
    synthesis can reserve guaranteed slots for them."""
    if seen_ids is None:
        seen_ids = set()
    items: list[PulseItem] = []
    for tweet in raw_tweets:
        tweet_id = tweet.get("id", "")
        if not tweet_id or tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)

        likes = tweet.get("likeCount", 0) or 0
        # Author-level SuperSmart check: any author on the SUPER_SMART_HANDLES
        # set bypasses min_likes regardless of which scrape captured them.
        # This means @ezraklein tweets pulled by the broad Pulse scrape ALSO
        # get the super_smart tag, not just tweets from the dedicated
        # SuperSmart-list scrape (which only captures accounts on that list).
        author_obj = tweet.get("author", {})
        author_handle = (author_obj.get("userName", "") if isinstance(author_obj, dict) else str(author_obj)).lower()
        is_super_smart_author = author_handle in SUPER_SMART_HANDLES
        # super_smart param: from the dedicated SuperSmart-list scrape
        # OR the author is on the handle set (regardless of source)
        is_super_smart = super_smart or is_super_smart_author
        if not is_super_smart and likes < min_likes:
            continue

        published = None
        for field in ("createdAt", "created_at", "timeParsed", "timestamp", "date", "tweetedAt"):
            created_at = tweet.get(field, "")
            if not created_at:
                continue
            try:
                if isinstance(created_at, (int, float)):
                    ts = created_at / 1000 if created_at > 1e12 else created_at
                    published = datetime.fromtimestamp(ts, tz=timezone.utc)
                else:
                    s = str(created_at).replace("Z", "+00:00")
                    try:
                        published = datetime.fromisoformat(s)
                    except ValueError:
                        published = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
                break
            except (ValueError, TypeError):
                continue

        if published is not None:
            age = datetime.now(timezone.utc) - published
            if age > timedelta(hours=48):
                continue

        author = tweet.get("author", {})
        username = author.get("userName", "") if isinstance(author, dict) else str(author)

        if username and username.lower() in {h.lower() for h in TWITTER_AUTHOR_BLOCKLIST}:
            continue

        reply_count = tweet.get("replyCount", 0) or 0
        tweet_text = tweet.get("fullText") or tweet.get("text") or ""
        tweet_url = tweet.get("twitterUrl") or tweet.get("url") or f"https://x.com/{username}/status/{tweet_id}"

        kwargs = dict(
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
        if is_super_smart:
            kwargs["platform_tags"] = ["super_smart"]
        items.append(PulseItem(**kwargs))
    return items


def collect(
    list_id: str = PULSE_LIST_ID,
    max_items: int = LIST_MAX_ITEMS,
    min_likes: int = TWITTER_MIN_LIKES,
) -> list[PulseItem]:
    """Collect recent tweets from the Pulse Twitter List timeline AND, if
    configured, the SuperSmart curated list.

    Returns combined list. SuperSmart items are tagged with platform_tags
    ["super_smart"] so downstream synthesis can reserve guaranteed slots.
    """
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        raise RuntimeError("APIFY_API_KEY not set — Twitter collection skipped entirely")

    if not _check_budget():
        raise RuntimeError(
            f"Twitter daily Apify budget exhausted ({TWITTER_DAILY_BUDGET_CENTS}¢ limit). "
            f"Increase TWITTER_DAILY_BUDGET_CENTS in config.py or wait until tomorrow."
        )

    # Phase 1: SuperSmart list scrape FIRST so its tweets get the super_smart
    # tag — if a tweet appears on both lists, the SuperSmart version wins
    # (Pulse's pass below skips already-seen IDs).
    seen_ids: set[str] = set()
    items: list[PulseItem] = []
    if SUPER_SMART_LIST_ID:
        try:
            ss_raw = _run_actor(list_id=SUPER_SMART_LIST_ID, max_items=SUPER_SMART_MAX_ITEMS)
            ss_items = _convert_raw_tweets(ss_raw, min_likes=0, super_smart=True, seen_ids=seen_ids)
            logger.info(f"SuperSmart list: {len(ss_items)} items (from {len(ss_raw)} raw)")
            items.extend(ss_items)
        except Exception as e:
            logger.warning(f"SuperSmart scrape failed: {e}")
    else:
        logger.info("SuperSmart list not configured (SUPER_SMART_LIST_ID env var unset)")

    # Phase 2: Pulse list scrape (broad)
    raw_tweets = _run_actor(list_id=list_id, max_items=max_items)
    pulse_items = _convert_raw_tweets(raw_tweets, min_likes=min_likes, super_smart=False, seen_ids=seen_ids)
    logger.info(f"Pulse list: {len(pulse_items)} items (filtered from {len(raw_tweets)} raw tweets)")
    items.extend(pulse_items)

    logger.info(f"Twitter total: {len(items)} items combined")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:10]:
        conv = "CONV" if item.engagement_raw.get("is_conversation") else "post"
        print(f"[{item.score:>6}] [{conv}] {item.author}: {item.title[:60]}")
    print(f"\nTotal: {len(results)} items")
