"""The owner's own X posts, for the "Recent posts" subsection.

Reads the items table (source='twitter', author=OWN_HANDLE) and ranks by
likes from the scraper's engagement_raw JSON. Nothing is fetched here; the
posts only exist if the account is on a scraped X list.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

OWN_HANDLE = "@azizsunderji"
OWN_POSTS_DAYS = 5
OWN_POSTS_LIMIT = 3
OWN_POSTS_MIN_LIKES = 1


def load_own_posts(conn: sqlite3.Connection, handle: str = OWN_HANDLE, days: int = OWN_POSTS_DAYS,
                   limit: int = OWN_POSTS_LIMIT, min_likes: int = OWN_POSTS_MIN_LIKES) -> list[dict]:
    """[{text, url, likes, date}] for the owner's posts in the window, most
    liked first. Returns [] when the account is not in the corpus."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = conn.execute(
            "SELECT url, title, body, published_at, engagement_raw FROM items "
            "WHERE source='twitter' AND lower(author)=lower(?) AND published_at >= ? "
            "ORDER BY published_at DESC LIMIT 200", (handle, since)).fetchall()
    except sqlite3.Error:
        return []
    posts = []
    for url, title, body, published_at, raw in rows:
        likes = 0
        try:
            likes = int((json.loads(raw) if raw else {}).get("likes") or 0)
        except (ValueError, TypeError, json.JSONDecodeError):
            pass
        if likes < min_likes:
            continue
        text = (body or title or "").strip().replace("\n", " ")
        if not text or not url:
            continue
        posts.append({"text": text, "url": url, "likes": likes,
                      "date": (published_at or "")[:10]})
    posts.sort(key=lambda p: (-p["likes"], p["date"]), reverse=False)
    return posts[:limit]
