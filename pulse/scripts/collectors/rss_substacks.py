"""Competitor Substack collector.

Fetches RSS feeds from a curated list of economics/housing Substacks.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser

from collectors import PulseItem
from config import COMPETITOR_SUBSTACKS

logger = logging.getLogger(__name__)


def _parse_date(entry: dict) -> Optional[datetime]:
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
    substacks: list[tuple[str, str]] | None = None,
    max_per_feed: int = 5,
    max_age_hours: int = 72,  # Substacks publish less frequently
) -> list[PulseItem]:
    """Fetch recent posts from competitor Substacks.

    Returns list of PulseItem objects.
    """
    substacks = substacks or COMPETITOR_SUBSTACKS
    items = []
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    for name, feed_url in substacks:
        try:
            parsed = feedparser.parse(feed_url)

            if parsed.bozo and not parsed.entries:
                logger.warning(f"Feed error for '{name}': {parsed.bozo_exception}")
                continue

            for entry in parsed.entries[:max_per_feed]:
                url = entry.get("link", "")
                if not url:
                    continue

                published = _parse_date(entry)
                if published and published.timestamp() < cutoff:
                    continue

                # Extract body — Substacks usually provide full HTML in content
                body = ""
                if "content" in entry and entry["content"]:
                    import re
                    body = re.sub(r"<[^>]+>", "", entry["content"][0].get("value", "")).strip()[:3000]
                elif "summary" in entry:
                    import re
                    body = re.sub(r"<[^>]+>", "", entry["summary"]).strip()[:3000]

                item = PulseItem(
                    source="substack",
                    source_id=f"sub_{hash(url) & 0xFFFFFFFF:08x}",
                    url=url,
                    title=entry.get("title", "").strip(),
                    body=body,
                    author=name,
                    published_at=published,
                    feed_name=name,
                    feed_priority="competitor",
                    platform_tags=["competitor_substack"],
                )
                items.append(item)

            logger.info(f"Substack '{name}': {len(parsed.entries[:max_per_feed])} entries")

        except Exception as e:
            logger.warning(f"Error fetching Substack '{name}': {e}")
            continue

    logger.info(f"Substacks total: {len(items)} items from {len(substacks)} feeds")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:10]:
        print(f"{item.author:>30}: {item.title[:60]}")
    print(f"\nTotal: {len(results)} items")
