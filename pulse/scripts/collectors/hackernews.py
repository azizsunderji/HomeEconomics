"""Hacker News collector using the Firebase/Algolia APIs.

Uses the HN Algolia search API for keyword-filtered stories.
No API key needed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

import httpx

from collectors import PulseItem
from config import HN_MIN_SCORE, HN_KEYWORDS

logger = logging.getLogger(__name__)

HN_SEARCH_API = "https://hn.algolia.com/api/v1"


def _search_stories(query: str, min_points: int = 20, hours_back: int = 24) -> list[dict]:
    """Search HN stories via Algolia API."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp())
    url = f"{HN_SEARCH_API}/search"
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"points>={min_points},created_at_i>={cutoff}",
        "hitsPerPage": 30,
    }

    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("hits", [])


def _get_item(item_id: int) -> dict:
    """Get a single HN item (for top-level comment context)."""
    url = f"{HN_SEARCH_API}/items/{item_id}"
    resp = httpx.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def collect(
    keywords: list[str] | None = None,
    min_score: int = HN_MIN_SCORE,
    hours_back: int = 24,
) -> list[PulseItem]:
    """Collect recent HN stories matching housing/economics keywords.

    Returns list of PulseItem objects.
    """
    keywords = keywords or HN_KEYWORDS
    items = []
    seen_ids = set()

    for keyword in keywords:
        try:
            stories = _search_stories(keyword, min_points=min_score, hours_back=hours_back)

            for story in stories:
                story_id = story.get("objectID", "")
                if story_id in seen_ids:
                    continue
                seen_ids.add(story_id)

                # Parse created_at
                published = None
                created_at_i = story.get("created_at_i")
                if created_at_i:
                    published = datetime.fromtimestamp(created_at_i, tz=timezone.utc)

                url = story.get("url", "")
                if not url:
                    url = f"https://news.ycombinator.com/item?id={story_id}"

                # Grab top comments for context (up to 3)
                body_parts = []
                if story.get("story_text"):
                    body_parts.append(story["story_text"][:1000])

                try:
                    detail = _get_item(int(story_id))
                    children = detail.get("children", [])[:3]
                    for child in children:
                        text = child.get("text", "")
                        if text:
                            body_parts.append(f"[Comment] {text[:500]}")
                except Exception:
                    pass  # Comments are nice-to-have, not essential

                item = PulseItem(
                    source="hackernews",
                    source_id=f"hn_{story_id}",
                    url=url,
                    title=story.get("title", ""),
                    body="\n\n".join(body_parts)[:3000],
                    author=story.get("author", ""),
                    published_at=published,
                    score=story.get("points", 0),
                    num_comments=story.get("num_comments", 0),
                    engagement_raw={
                        "points": story.get("points", 0),
                        "search_keyword": keyword,
                    },
                )
                items.append(item)

            logger.info(f"HN '{keyword}': {len(stories)} stories found")

        except Exception as e:
            logger.error(f"Error searching HN for '{keyword}': {e}")
            continue

    logger.info(f"Hacker News total: {len(items)} items from {len(keywords)} keywords")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:10]:
        print(f"[{item.score:>4}] {item.title[:70]}")
    print(f"\nTotal: {len(results)} items")
