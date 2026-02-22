"""Bluesky collector using the AT Protocol public API.

Two collection strategies:
1. Follow specific housing/econ accounts via getAuthorFeed (no auth needed)
2. Search posts via searchPosts (requires auth — BLUESKY_HANDLE + BLUESKY_APP_PASSWORD)

The public search API was restricted in late 2025, so account-following
is the reliable primary method.
"""

from __future__ import annotations

import os
import logging
import time
from datetime import datetime, timezone, timedelta

import httpx

from collectors import PulseItem
from config import (
    BLUESKY_SEARCH_TERMS, BLUESKY_MAX_PER_QUERY,
    BLUESKY_ACCOUNTS,
)

logger = logging.getLogger(__name__)

BSKY_PUBLIC_API = "https://public.api.bsky.app"
BSKY_AUTH_API = "https://bsky.social"
REQUEST_DELAY = 1.0  # seconds between requests


def _create_session() -> tuple[str, str] | None:
    """Authenticate with Bluesky and return (access_token, did)."""
    handle = os.environ.get("BLUESKY_HANDLE", "")
    password = os.environ.get("BLUESKY_APP_PASSWORD", "")

    if not handle or not password:
        return None

    try:
        resp = httpx.post(
            f"{BSKY_AUTH_API}/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["accessJwt"], data["did"]
    except Exception as e:
        logger.warning(f"Bluesky authentication failed: {e}")
        return None


def _get_author_feed(actor: str, limit: int = 30) -> list[dict]:
    """Get recent posts from a Bluesky account (public, no auth needed)."""
    url = f"{BSKY_PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed"
    params = {"actor": actor, "limit": min(limit, 100), "filter": "posts_and_author_threads"}

    resp = httpx.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("feed", [])


def _search_posts(query: str, limit: int = 25, auth_token: str = "") -> list[dict]:
    """Search Bluesky posts. Requires auth token (public search returns 403)."""
    if auth_token:
        base_url = BSKY_AUTH_API
        headers = {"Authorization": f"Bearer {auth_token}"}
    else:
        base_url = BSKY_PUBLIC_API
        headers = {}

    url = f"{base_url}/xrpc/app.bsky.feed.searchPosts"
    params = {"q": query, "limit": min(limit, 100), "sort": "top"}

    resp = httpx.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("posts", [])


def _parse_post(post_data: dict, source_tag: str = "") -> PulseItem | None:
    """Parse a Bluesky feed item into a PulseItem."""
    post = post_data.get("post", post_data)  # Handle both feed items and raw posts
    record = post.get("record", {})
    author_info = post.get("author", {})
    uri = post.get("uri", "")

    if not uri or not record.get("text"):
        return None

    handle = author_info.get("handle", "")
    rkey = uri.split("/")[-1] if "/" in uri else ""
    web_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else uri

    # Parse datetime
    created_at_str = record.get("createdAt", "")
    published = None
    if created_at_str:
        try:
            published = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    # Skip posts older than 3 days
    if published and published < datetime.now(timezone.utc) - timedelta(days=3):
        return None

    like_count = post.get("likeCount", 0) or 0
    repost_count = post.get("repostCount", 0) or 0
    reply_count = post.get("replyCount", 0) or 0

    return PulseItem(
        source="bluesky",
        source_id=f"bsky_{hash(uri) & 0xFFFFFFFF:08x}",
        url=web_url,
        title=record.get("text", "")[:200],
        body=record.get("text", ""),
        author=f"@{handle}" if handle else author_info.get("displayName", ""),
        published_at=published,
        score=like_count + repost_count,
        num_comments=reply_count,
        engagement_raw={
            "likes": like_count,
            "reposts": repost_count,
            "replies": reply_count,
            "source_tag": source_tag,
        },
    )


def collect(
    accounts: list[str] | None = None,
    search_terms: list[str] | None = None,
    max_per_query: int = BLUESKY_MAX_PER_QUERY,
) -> list[PulseItem]:
    """Collect recent Bluesky posts from followed accounts + search.

    Primary: getAuthorFeed for each account (no auth, always works).
    Secondary: searchPosts if BLUESKY credentials are set.
    """
    accounts = accounts or BLUESKY_ACCOUNTS
    search_terms = search_terms or BLUESKY_SEARCH_TERMS
    items = []
    seen_uris = set()

    def _add_item(item: PulseItem | None):
        if item and item.source_id not in seen_uris:
            seen_uris.add(item.source_id)
            items.append(item)

    # Strategy 1: Follow accounts (public API, always works)
    for account in accounts:
        try:
            feed = _get_author_feed(account, limit=max_per_query)
            count = 0
            for feed_item in feed:
                item = _parse_post(feed_item, source_tag=f"account:{account}")
                if item:
                    _add_item(item)
                    count += 1
            logger.info(f"Bluesky @{account}: {count} posts")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            logger.error(f"Error fetching Bluesky @{account}: {e}")
            continue

    # Strategy 2: Search (requires auth)
    auth_token = ""
    session = _create_session()
    if session:
        auth_token, _ = session
        logger.info("Bluesky: authenticated — running search queries")

        for term in search_terms:
            try:
                posts = _search_posts(term, limit=max_per_query, auth_token=auth_token)
                count = 0
                for post in posts:
                    item = _parse_post(post, source_tag=f"search:{term}")
                    if item:
                        _add_item(item)
                        count += 1
                logger.info(f"Bluesky search '{term}': {count} posts")
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                logger.error(f"Error searching Bluesky for '{term}': {e}")
                continue
    else:
        logger.info("Bluesky: no auth credentials — skipping search (set BLUESKY_HANDLE + BLUESKY_APP_PASSWORD)")

    logger.info(f"Bluesky total: {len(items)} items from {len(accounts)} accounts + {'search' if auth_token else 'no search'}")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:15]:
        print(f"[{item.score:>4} pts, {item.num_comments} replies] {item.author}: {item.title[:70]}")
    print(f"\nTotal: {len(results)} items")
