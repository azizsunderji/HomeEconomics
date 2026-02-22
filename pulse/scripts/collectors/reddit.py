"""Reddit collector using public .json endpoints (no auth required).

Collects hot posts from housing/economics subreddits with top comments.
No OAuth credentials needed — uses Reddit's public JSON API.
Rate limited to ~10 requests/minute (6.5s between requests).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests as req_lib

from collectors import PulseItem
from config import (
    REDDIT_SUBREDDITS, REDDIT_MIN_SCORE, REDDIT_MAX_PER_SUB,
    REDDIT_MIN_COMMENTS, REDDIT_REQUEST_DELAY,
)

logger = logging.getLogger(__name__)

USER_AGENT = "HomeEconomicsPulse/1.0 (research; contact@home-economics.us)"
HEADERS = {"User-Agent": USER_AGENT}
TOP_COMMENTS_COUNT = 10
MAX_COMMENT_CHARS = 500


def _fetch_json(url: str, params: dict | None = None) -> dict:
    """Fetch a Reddit .json endpoint with rate limiting."""
    resp = req_lib.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _get_subreddit_posts(sub_name: str, max_posts: int = 15) -> list[dict]:
    """Fetch hot posts from a subreddit via .json endpoint."""
    url = f"https://www.reddit.com/r/{sub_name}/hot.json"
    data = _fetch_json(url, params={"limit": max_posts, "raw_json": 1})
    posts = []
    for child in data.get("data", {}).get("children", []):
        if child.get("kind") == "t3":
            posts.append(child["data"])
    return posts


def _get_post_comments(sub_name: str, post_id: str, limit: int = TOP_COMMENTS_COUNT) -> list[dict]:
    """Fetch top comments for a post via .json endpoint."""
    url = f"https://www.reddit.com/r/{sub_name}/comments/{post_id}.json"
    data = _fetch_json(url, params={"sort": "top", "limit": limit, "raw_json": 1})

    comments = []
    if len(data) >= 2:
        for child in data[1].get("data", {}).get("children", []):
            if child.get("kind") == "t1":
                c = child["data"]
                comments.append({
                    "body": (c.get("body") or "")[:MAX_COMMENT_CHARS],
                    "score": c.get("score", 0),
                    "author": c.get("author", "[deleted]"),
                })
    return comments[:limit]


def collect(
    subreddits: Optional[list[str]] = None,
    min_score: int = REDDIT_MIN_SCORE,
    max_per_sub: int = REDDIT_MAX_PER_SUB,
    min_comments: int = REDDIT_MIN_COMMENTS,
) -> list[PulseItem]:
    """Collect recent posts + comments from target subreddits.

    Returns list of PulseItem objects.
    """
    subreddits = subreddits or REDDIT_SUBREDDITS
    items = []

    for sub_name in subreddits:
        try:
            posts = _get_subreddit_posts(sub_name, max_posts=max_per_sub)
            time.sleep(REDDIT_REQUEST_DELAY)

            collected = 0
            for post in posts:
                if post.get("score", 0) < min_score:
                    continue

                post_id = post.get("id", "")
                num_comments = post.get("num_comments", 0)

                # Fetch top comments for posts with enough discussion
                top_comments = []
                if num_comments >= min_comments:
                    try:
                        top_comments = _get_post_comments(sub_name, post_id)
                        time.sleep(REDDIT_REQUEST_DELAY)
                    except Exception as e:
                        logger.warning(f"Failed to fetch comments for {post_id} in r/{sub_name}: {e}")

                # Build body: self-text + top comments with scores
                body = (post.get("selftext") or "")[:2000]
                if top_comments:
                    comment_lines = []
                    for c in top_comments:
                        comment_lines.append(
                            f"[{c['score']:+d}] u/{c['author']}: {c['body']}"
                        )
                    body += "\n\n--- Top Comments ---\n" + "\n---\n".join(comment_lines)

                created_utc = post.get("created_utc", 0)
                published = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else None

                item = PulseItem(
                    source="reddit",
                    source_id=f"reddit_{post_id}",
                    url=f"https://reddit.com{post.get('permalink', '')}",
                    title=post.get("title", ""),
                    body=body,
                    author=post.get("author", "[deleted]"),
                    published_at=published,
                    score=post.get("score", 0),
                    num_comments=num_comments,
                    engagement_raw={
                        "upvote_ratio": post.get("upvote_ratio", 0),
                        "is_self": post.get("is_self", False),
                        "comment_scores": [c["score"] for c in top_comments],
                        "is_conversation": num_comments >= min_comments,
                    },
                    subreddit=sub_name,
                    platform_tags=[post.get("link_flair_text")] if post.get("link_flair_text") else [],
                )
                items.append(item)
                collected += 1

                if collected >= max_per_sub:
                    break

            logger.info(f"r/{sub_name}: collected {collected} posts")

        except req_lib.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 429:
                logger.warning(f"r/{sub_name}: rate limited, backing off 30s")
                time.sleep(30)
            else:
                logger.error(f"Error collecting from r/{sub_name}: {e}")
            continue
        except Exception as e:
            logger.error(f"Error collecting from r/{sub_name}: {e}")
            continue

    conversations = sum(1 for i in items if i.engagement_raw.get("is_conversation"))
    logger.info(
        f"Reddit total: {len(items)} items from {len(subreddits)} subreddits "
        f"({conversations} with active comments)"
    )
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:5]:
        conv = "CONV" if item.engagement_raw.get("is_conversation") else "link"
        print(f"[{item.score:>5}] [{conv}] r/{item.subreddit}: {item.title[:80]}")
    print(f"\nTotal: {len(results)} items")
