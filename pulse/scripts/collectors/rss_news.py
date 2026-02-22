"""Google News RSS collector.

Uses Google News RSS endpoints directly with feedparser.
No API key needed — Google News RSS is free and public.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone

import feedparser

from collectors import PulseItem
from config import GOOGLE_NEWS_QUERIES

logger = logging.getLogger(__name__)

GNEWS_RSS_BASE = "https://news.google.com/rss/search"


def _build_url(query: str, hl: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    """Build a Google News RSS search URL."""
    params = urllib.parse.urlencode({
        "q": query,
        "hl": hl,
        "gl": gl,
        "ceid": ceid,
    })
    return f"{GNEWS_RSS_BASE}?{params}"


def _parse_date(entry: dict):
    """Try to parse a feedparser entry's date."""
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
            except (ValueError, OverflowError):
                continue
    return None


def collect(
    queries: list[str] | None = None,
    max_per_query: int = 15,
) -> list[PulseItem]:
    """Collect recent Google News results for housing/economics queries.

    Returns list of PulseItem objects.
    """
    queries = queries or GOOGLE_NEWS_QUERIES
    items = []
    seen_urls = set()

    for query in queries:
        try:
            url = _build_url(query)
            feed = feedparser.parse(url)

            if feed.bozo and not feed.entries:
                logger.warning(f"Google News RSS error for '{query}': {feed.bozo_exception}")
                continue

            for entry in feed.entries[:max_per_query]:
                link = entry.get("link", "")
                if not link or link in seen_urls:
                    continue
                seen_urls.add(link)

                published = _parse_date(entry)

                # Extract source name — Google News includes it in the title
                title = entry.get("title", "").strip()
                source_name = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title = parts[0].strip()
                    source_name = parts[1].strip()

                summary = entry.get("summary", "")
                summary = re.sub(r"<[^>]+>", "", summary).strip()

                item = PulseItem(
                    source="google_news",
                    source_id=f"gnews_{hash(link) & 0xFFFFFFFF:08x}",
                    url=link,
                    title=title,
                    body=summary[:2000],
                    author=source_name,
                    published_at=published,
                    engagement_raw={"search_query": query},
                    platform_tags=[query],
                )
                items.append(item)

            logger.info(f"Google News '{query}': {len(feed.entries)} entries")

        except Exception as e:
            logger.error(f"Error searching Google News for '{query}': {e}")
            continue

    logger.info(f"Google News total: {len(items)} items from {len(queries)} queries")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:10]:
        print(f"[{item.author:>25}] {item.title[:70]}")
    print(f"\nTotal: {len(results)} items")
