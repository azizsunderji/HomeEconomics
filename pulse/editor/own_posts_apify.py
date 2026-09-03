"""The owner's own recent X posts for the "Recent posts" subsection.

X does not let an account be added to a list it owns, so the daily list
scrape never sees @azizsunderji. This pulls the timeline directly with
the apidojo/tweet-scraper actor (pay per result, ~30 items a day) at
draft-build time. Returns the same shape delivery/own_posts.load_own_posts
returns: [{text, url, likes, date}], most liked first.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger("noon.own_posts")

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apidojo/tweet-scraper"
HANDLE = os.environ.get("NOON_OWN_HANDLE", "azizsunderji")
DAYS = 5
LIMIT = 3
MIN_LIKES = 1
MAX_ITEMS = 40


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            d = datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_own_posts(handle: str = HANDLE, days: int = DAYS, limit: int = LIMIT,
                    min_likes: int = MIN_LIKES) -> list[dict]:
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        logger.info("APIFY_API_KEY not set — Recent posts left empty")
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    payload = {
        "twitterHandles": [handle.lstrip("@")],
        "maxItems": MAX_ITEMS,
        "sort": "Latest",
        "start": since.strftime("%Y-%m-%d"),
        "includeSearchTerms": False,
        "onlyImage": False, "onlyQuote": False, "onlyTwitterBlue": False,
        "onlyVerifiedUsers": False, "onlyVideo": False,
    }
    url = f"{APIFY_BASE}/acts/{ACTOR_ID.replace('/', '~')}/run-sync-get-dataset-items"
    try:
        resp = httpx.post(url, params={"timeout": 120}, json=payload,
                          headers={"Authorization": f"Bearer {api_key}"}, timeout=150)
        if resp.status_code not in (200, 201):
            logger.warning(f"Apify {resp.status_code}: {resp.text[:200]}")
            return []
        items = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Apify request failed: {type(e).__name__}: {e}")
        return []
    if not isinstance(items, list):
        return []
    posts: list[dict] = []
    for it in items:
        if not isinstance(it, dict) or it.get("noResults"):
            continue
        author = (it.get("author") or {}).get("userName") or it.get("user", {}).get("screen_name") or ""
        if author and author.lower() != handle.lstrip("@").lower():
            continue  # search results can include replies from others
        if it.get("isRetweet") or it.get("retweeted"):
            continue
        text = (it.get("text") or it.get("full_text") or "").strip()
        # Drop trailing t.co links (media/quote stubs); skip link-only posts.
        text = re.sub(r"\s*https?://t\.co/\S+", "", text).strip()
        if not text:
            continue
        created = _parse_date(it.get("createdAt") or it.get("created_at"))
        if created and created < since:
            continue
        likes = int(it.get("likeCount") or it.get("favorite_count") or 0)
        if likes < min_likes:
            continue
        posts.append({
            "text": text,
            "url": it.get("url") or it.get("twitterUrl") or "",
            "likes": likes,
            "date": created.strftime("%Y-%m-%d") if created else "",
        })
    posts.sort(key=lambda p: -p["likes"])
    logger.info(f"own posts: {len(items)} items from Apify, {len(posts)} kept, top {limit} used")
    return posts[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for p in fetch_own_posts():
        print(p["likes"], p["date"], p["url"], "—", p["text"][:80])
