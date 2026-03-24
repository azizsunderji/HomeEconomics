"""Fetch recent press mentions of Aziz Sunderji / Home Economics.

Searches Google News RSS for mentions over the past week.
Returns a list of dicts for the "Aziz in the News" email section.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser

logger = logging.getLogger(__name__)

GNEWS_RSS_BASE = "https://news.google.com/rss/search"

# Queries to find press mentions
MENTION_QUERIES = [
    '"Aziz Sunderji"',
    'site:home-economics.us -site:substack.com',
]


def _build_url(query: str, when: str = "7d") -> str:
    """Build a Google News RSS search URL with time filter."""
    params = urllib.parse.urlencode({
        "q": f"{query} when:{when}",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    return f"{GNEWS_RSS_BASE}?{params}"


def _parse_date(entry: dict) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
            except (ValueError, OverflowError):
                continue
    return None


def _extract_source(title: str) -> tuple[str, str]:
    """Extract source name from Google News title format: 'Headline - Source Name'."""
    match = re.match(r'^(.+)\s+-\s+(.+)$', title)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return title, ""


def get_press_mentions() -> list[dict]:
    """Fetch press mentions from the past week.

    Returns list of dicts: [{"headline", "source", "url", "date"}]
    """
    seen_urls = set()
    results = []

    for query in MENTION_QUERIES:
        url = _build_url(query)
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logger.warning(f"Press mentions feed error for '{query}': {e}")
            continue

        for entry in feed.entries[:20]:
            link = entry.get("link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            raw_title = entry.get("title", "")
            headline, source = _extract_source(raw_title)
            pub_date = _parse_date(entry)

            # Skip self-published content (our own Substack)
            if "home-economics.us" in link.lower() or "home economics" in source.lower():
                continue

            results.append({
                "headline": headline,
                "source": source,
                "url": link,
                "date": pub_date.strftime("%b %d") if pub_date else "",
            })

    # Dedupe by headline similarity (first 50 chars)
    deduped = []
    seen_headlines = set()
    for item in results:
        key = item["headline"][:50].lower()
        if key not in seen_headlines:
            seen_headlines.add(key)
            deduped.append(item)

    logger.info(f"Press mentions: {len(deduped)} found (from {len(results)} raw)")
    return deduped


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mentions = get_press_mentions()
    for m in mentions:
        print(f"  [{m['date']}] {m['source']}: {m['headline'][:60]}")
    print(f"\nTotal: {len(mentions)} mentions")
