"""OPML-based RSS feed collector.

Parses the HomeEconomicsRSS.opml file and fetches all feeds.
Uses feedparser for RSS/Atom parsing.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import time

import feedparser

from collectors import PulseItem

logger = logging.getLogger(__name__)

DEFAULT_OPML_PATH = "/Users/azizsunderji/Dropbox/Home Economics/RSSFeeds/HomeEconomicsRSS.opml"


def parse_opml(opml_path: str = DEFAULT_OPML_PATH) -> list[dict]:
    """Parse OPML file into a list of feed dicts with priority info."""
    tree = ET.parse(opml_path)
    root = tree.getroot()
    feeds = []

    def _process_outline(outline, folder: str = ""):
        xml_url = outline.get("xmlUrl", "").strip()
        if xml_url:
            feeds.append({
                "url": xml_url,
                "title": outline.get("title", outline.get("text", "")),
                "html_url": outline.get("htmlUrl", ""),
                "folder": folder,
                "priority": "high" if folder == "HighPriority" else
                           "journal" if folder == "Journals" else "normal",
            })
        # Recurse into child outlines (folders)
        folder_name = outline.get("title", outline.get("text", folder))
        for child in outline:
            _process_outline(child, folder=folder_name)

    body = root.find(".//body")
    if body is not None:
        for outline in body:
            _process_outline(outline)

    return feeds


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
    opml_path: str = DEFAULT_OPML_PATH,
    max_per_feed: int = 10,
    max_age_hours: int = 48,
) -> list[PulseItem]:
    """Fetch all feeds from OPML and collect recent entries.

    Returns list of PulseItem objects.
    """
    feeds = parse_opml(opml_path)
    items = []
    seen_urls = set()
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    logger.info(f"Parsed {len(feeds)} feeds from OPML")

    for feed_info in feeds:
        try:
            parsed = feedparser.parse(feed_info["url"])

            if parsed.bozo and not parsed.entries:
                logger.warning(f"Feed error for '{feed_info['title']}': {parsed.bozo_exception}")
                continue

            count = 0
            for entry in parsed.entries[:max_per_feed]:
                url = entry.get("link", "")
                if not url or url in seen_urls:
                    continue

                published = _parse_date(entry)
                # Skip entries older than max_age_hours
                if published and published.timestamp() < cutoff:
                    continue

                seen_urls.add(url)

                # Extract body text
                body = ""
                if "summary" in entry:
                    import re
                    body = re.sub(r"<[^>]+>", "", entry["summary"]).strip()[:2000]
                elif "content" in entry and entry["content"]:
                    import re
                    body = re.sub(r"<[^>]+>", "", entry["content"][0].get("value", "")).strip()[:2000]

                item = PulseItem(
                    source="rss",
                    source_id=f"rss_{hash(url) & 0xFFFFFFFF:08x}",
                    url=url,
                    title=entry.get("title", "").strip(),
                    body=body,
                    author=entry.get("author", ""),
                    published_at=published,
                    feed_name=feed_info["title"],
                    feed_priority=feed_info["priority"],
                    platform_tags=[feed_info["folder"]] if feed_info["folder"] else [],
                )
                items.append(item)
                count += 1

            if count > 0:
                logger.debug(f"Feed '{feed_info['title']}': {count} entries")

        except Exception as e:
            logger.warning(f"Error fetching feed '{feed_info['title']}': {e}")
            continue

    logger.info(f"RSS feeds total: {len(items)} items from {len(feeds)} feeds")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:15]:
        prio = f"[{item.feed_priority:>7}]" if item.feed_priority else ""
        print(f"{prio} {item.feed_name[:25]:>25}: {item.title[:60]}")
    print(f"\nTotal: {len(results)} items")
