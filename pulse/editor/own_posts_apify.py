"""The owner's own recent X and LinkedIn posts for the "Recent posts" subsection.

X does not let an account be added to a list it owns, so the daily list
scrape never sees @azizsunderji. This pulls the timeline directly with
the apidojo/tweet-scraper actor (pay per result, ~30 items a day) at
draft-build time. Returns the same shape delivery/own_posts.load_own_posts
returns: [{text, url, likes, date}], newest first (since 23 Oct 2026; was most liked). LinkedIn posts come
from fetch_own_linkedin_posts (harvestapi/linkedin-profile-posts, the actor the
LinkedIn collector uses) and carry "platform": "linkedin"; X posts have no
platform key, which the renderer reads as X.
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
MAX_ITEMS = 100               # 23 Oct 2026: 40 with sort=Latest returned a different partial set each run
LINKEDIN_URL = os.environ.get("NOON_OWN_LINKEDIN", "https://www.linkedin.com/in/azizsunderji/")
LINKEDIN_LIMIT = 2


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
    base = {"maxItems": MAX_ITEMS, "sort": "Latest", "includeSearchTerms": False,
            "onlyImage": False, "onlyQuote": False, "onlyTwitterBlue": False,
            "onlyVerifiedUsers": False, "onlyVideo": False}
    # Two queries, merged by id: the handle timeline and a dated search. Either alone misses posts (23 Oct 2026:
    # the timeline query returned no 22 Oct post at all on one run and all of them on the next).
    payloads = [dict(base, twitterHandles=[handle.lstrip("@")], start=since.strftime("%Y-%m-%d")),
                dict(base, searchTerms=[f"from:{handle.lstrip('@')} since:{since.strftime('%Y-%m-%d')}"])]
    url = f"{APIFY_BASE}/acts/{ACTOR_ID.replace('/', '~')}/run-sync-get-dataset-items"
    items_by_id: dict = {}
    for payload in payloads:
        try:
            resp = httpx.post(url, params={"timeout": 120}, json=payload,
                              headers={"Authorization": f"Bearer {api_key}"}, timeout=150)
            if resp.status_code not in (200, 201):
                logger.warning(f"Apify {resp.status_code}: {resp.text[:200]}")
                continue
            got = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Apify request failed: {type(e).__name__}: {e}")
            continue
        for it in (got if isinstance(got, list) else []):
            if isinstance(it, dict) and not it.get("noResults"):
                items_by_id.setdefault(str(it.get("id") or it.get("url") or len(items_by_id)), it)
    items = list(items_by_id.values())
    shown = _already_shown()
    posts: list[dict] = []
    for it in items:
        author = (it.get("author") or {}).get("userName") or it.get("user", {}).get("screen_name") or ""
        if author and author.lower() != handle.lstrip("@").lower():
            continue  # search results can include replies from others
        if it.get("isRetweet") or it.get("retweeted"):
            continue
        # Owner, 23 Oct 2026: his own original posts only. Replies ("Sure, but 18-29 year olds...", "I wrote about
        # this here:") and retweets were outranking his chart posts on likes.
        if it.get("isReply") or it.get("inReplyToId") or it.get("inReplyToStatusId"):
            continue
        text = (it.get("text") or it.get("full_text") or "").strip()
        if text.startswith("RT @") or text.startswith("@"):
            continue
        if (it.get("isQuote") or it.get("quoted_status_id") or it.get("quotedStatusId")) and len(text) < 100:
            continue  # a one-line reaction to someone else's post, not a post of his own
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
        url_ = it.get("url") or it.get("twitterUrl") or ""
        if _post_key(url_) in shown:
            continue  # already in a sent edition: never the same post twice
        posts.append({
            "text": text,
            "url": url_,
            "likes": likes,
            "date": created.strftime("%Y-%m-%d") if created else "",
            "_ts": created.timestamp() if created else 0,
        })
    # Newest first (owner, 23 Oct 2026): "Recent posts" means recent, not most liked over five days.
    posts.sort(key=lambda p: -p["_ts"])
    for p_ in posts:
        p_.pop("_ts", None)
    logger.info(f"own posts: {len(items)} items from Apify (2 queries), {len(posts)} kept, newest {limit} used")
    return posts[:limit]


def _post_key(url: str) -> str:
    """Status id for an X url, else the url itself (LinkedIn urls are stable)."""
    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1) if m else (url or "").rstrip("/")


def _already_shown(editions: int = 10) -> set:
    """Post keys that appeared in the Recent posts block of the last `editions` SENT editions."""
    try:
        import drafts
        out: set = set()
        for row in drafts.list_recent(editions + 5):
            if row.get("status") != "sent":
                continue
            d = drafts.get(row["date"]) or {}
            for p_ in ((d.get("json") or {}).get("_own_posts") or []):
                if isinstance(p_, dict) and p_.get("url"):
                    out.add(_post_key(p_["url"]))
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"already-shown lookup failed: {type(e).__name__}: {e}")
        return set()


def fetch_own_linkedin_posts(profile_url: str = LINKEDIN_URL, days: int = DAYS,
                             limit: int = LINKEDIN_LIMIT, min_likes: int = MIN_LIKES) -> list[dict]:
    """The owner's LinkedIn posts from the last `days` days, most reactions first."""
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key or not profile_url:
        return []
    try:
        from collectors.linkedin_apify import _run_actor  # pulse/scripts is on sys.path via paths
        items = _run_actor(api_key, [profile_url], "week", 10)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Apify LinkedIn request failed: {type(e).__name__}: {e}")
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days)
    shown = _already_shown()
    posts: list[dict] = []
    for it in items:
        if not isinstance(it, dict) or it.get("repostId") or it.get("repost"):
            continue  # a bare repost is not the owner's own post
        text = (it.get("content") or "").strip()
        if not text:
            text = ((it.get("article") or {}).get("title") or "").strip()
        if not text:
            continue
        ts = (it.get("postedAt") or {}).get("timestamp")
        created = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
        if created and created < since:
            continue
        eng = it.get("engagement") or {}
        likes = int(eng.get("likes") or 0)
        if likes < min_likes:
            continue
        if _post_key(it.get("linkedinUrl") or "") in shown:
            continue
        posts.append({
            "text": text,
            "url": it.get("linkedinUrl") or "",
            "likes": likes,
            "date": created.strftime("%Y-%m-%d") if created else "",
            "platform": "linkedin",
            "_ts": created.timestamp() if created else 0,
        })
    posts.sort(key=lambda p: -p["_ts"])          # newest first (owner, 23 Oct 2026)
    for p_ in posts:
        p_.pop("_ts", None)
    logger.info(f"own LinkedIn posts: {len(items)} items from Apify, {len(posts)} kept, newest {limit} used")
    return posts[:limit]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for p in fetch_own_posts():
        print(p["likes"], p["date"], p["url"], "—", p["text"][:80])
    for p in fetch_own_linkedin_posts():
        print("LinkedIn", p["likes"], p["date"], p["url"], "—", p["text"][:80])
