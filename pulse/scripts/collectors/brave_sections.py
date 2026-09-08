"""Section backstop via the Brave Search API (added 2026-09-08).

Dow Jones publishes no RSS feed for the Journal's real estate section, so WSJ
real estate coverage reached the corpus only through Google Alerts on nine
named reporters (about ten items a fortnight). This collector asks Brave
Search each run for the newest articles under a section path and stores them
as ordinary `rss` items (same source_id scheme as rss_feeds, so an article the
NYT Real Estate feed already captured is a duplicate, not a second row).

Queries live in SECTIONS: (feed name, Brave query, URL pattern the result must
match). Needs BRAVE_API_KEY (GitHub secret; the pulse-daily workflow exports
it). Without the key the collector logs one line and returns nothing.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone

import httpx

from collectors import PulseItem, record_collector_error

logger = logging.getLogger(__name__)

SECTIONS: list[tuple[str, str, str]] = [
    ("WSJ > Real Estate (via search)", "site:wsj.com/real-estate", r"^https?://(www\.)?wsj\.com/real-estate/"),
    ("WSJ > Nicole Friedman (via search)", '"Nicole Friedman" site:wsj.com', r"^https?://(www\.)?wsj\.com/"),
    ("NYT > Real Estate (via search)", "site:nytimes.com realestate", r"^https?://(www\.)?nytimes\.com/\d{4}/\d{2}/\d{2}/realestate/"),
]
NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
WEB_URL = "https://api.search.brave.com/res/v1/web/search"
FRESHNESS = "pw"  # past week; the source_id dedupes repeats across runs
COUNT = 20


def _parse_age(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _search(client: httpx.Client, query: str) -> list[dict]:
    """News endpoint first; web endpoint if the plan lacks news. Returns result dicts."""
    params = {"q": query, "count": COUNT, "freshness": FRESHNESS, "search_lang": "en", "country": "us"}
    r = client.get(NEWS_URL, params=params)
    if r.status_code == 200:
        return list((r.json().get("results") or []))
    if r.status_code in (401, 403, 422, 429):
        raise RuntimeError(f"news search HTTP {r.status_code}: {r.text[:120]}")
    r = client.get(WEB_URL, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"web search HTTP {r.status_code}: {r.text[:120]}")
    return list(((r.json().get("web") or {}).get("results") or []))


def collect() -> list[PulseItem]:
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        logger.info("brave_sections: BRAVE_API_KEY not set; skipping")
        return []
    items: list[PulseItem] = []
    seen: set[str] = set()
    headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": key}
    with httpx.Client(timeout=25, headers=headers) as client:
        for feed_name, query, pattern in SECTIONS:
            rx = re.compile(pattern, re.I)
            try:
                results = _search(client, query)
            except Exception as e:  # noqa: BLE001
                record_collector_error("rss", e, context=f"brave={feed_name}")
                logger.warning(f"brave_sections '{feed_name}': {e}")
                continue
            kept = 0
            for res in results:
                url = (res.get("url") or "").split("?")[0].split("#")[0]
                if not url or not rx.match(url) or url in seen:
                    continue
                seen.add(url)
                title = re.sub(r"<[^>]+>", "", res.get("title") or "").strip()
                desc = re.sub(r"<[^>]+>", "", res.get("description") or "").strip()
                if not title:
                    continue
                items.append(PulseItem(
                    source="rss",
                    source_id=f"rss_{hashlib.md5(url.encode()).hexdigest()[:12]}",
                    url=url,
                    title=title,
                    body=desc[:2000],
                    author="",
                    published_at=_parse_age(res.get("page_age")),
                    feed_name=feed_name,
                    feed_priority="high",
                ))
                kept += 1
            logger.info(f"brave_sections '{feed_name}': {len(results)} results, {kept} matched the section")
    logger.info(f"brave_sections total: {len(items)} items")
    return items
