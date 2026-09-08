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

# Brave does not index wsj.com at all (site:wsj.com returns nothing from either index,
# 2026-09-08); the Journal's real estate section comes from the droplet mirror's
# Google News feed instead (pulse/editor/mirror_feeds.py, GNEWS_SEARCHES).
SECTIONS: list[tuple[str, list[str], str]] = [
    ("NYT > Real Estate (via search)",
     ["site:nytimes.com realestate", "site:nytimes.com/realestate"],
     r"^https?://(www\.)?nytimes\.com/\d{4}/\d{2}/\d{2}/realestate/"),
]
NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
WEB_URL = "https://api.search.brave.com/res/v1/web/search"
FRESHNESS = "pw"  # past week; the source_id dedupes repeats across runs
COUNT = 20
MAX_AGE_DAYS = 7
# Brave drops the "www." that the RSS feeds carry; the source_id is md5(url), so the
# host must match the feed's form or the same article is stored twice (seen 2026-09-08).
CANONICAL_HOST = {"nytimes.com": "www.nytimes.com", "wsj.com": "www.wsj.com"}


def _parse_age(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _search(client: httpx.Client, query: str) -> list[dict]:
    """News index first, then the web index whenever news gives nothing (paywalled
    publishers such as wsj.com are thin in the news index). Returns result dicts,
    each tagged with "_index"."""
    base = {"q": query, "count": COUNT, "search_lang": "en", "country": "us"}
    attempts = [(NEWS_URL, "news", True), (WEB_URL, "web", True), (NEWS_URL, "news-nofresh", False), (WEB_URL, "web-nofresh", False)]
    for url, label, fresh in attempts:
        params = dict(base, freshness=FRESHNESS) if fresh else base
        r = client.get(url, params=params)
        if r.status_code in (401, 403):
            raise RuntimeError(f"{label} search HTTP {r.status_code}: {r.text[:120]}")
        if r.status_code != 200:
            continue
        j = r.json()
        rows = j.get("results") if url == NEWS_URL else (j.get("web") or {}).get("results")
        if rows:
            return [dict(x, _index=label) for x in rows]
    return []


def collect() -> list[PulseItem]:
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        logger.info("brave_sections: BRAVE_API_KEY not set; skipping")
        return []
    items: list[PulseItem] = []
    seen: set[str] = set()
    headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": key}
    with httpx.Client(timeout=25, headers=headers) as client:
        for feed_name, queries, pattern in SECTIONS:
            rx = re.compile(pattern, re.I)
            results: list[dict] = []
            for query in queries:
                try:
                    got = _search(client, query)
                except Exception as e:  # noqa: BLE001
                    record_collector_error("rss", e, context=f"brave={feed_name}")
                    logger.warning(f"brave_sections '{feed_name}' [{query}]: {e}")
                    continue
                matched = [x for x in got if rx.match((x.get("url") or "").split("?")[0])]
                logger.info(f"brave_sections '{feed_name}' [{query}]: {len(got)} results "
                            f"({got[0]['_index'] if got else '-'}), {len(matched)} in section")
                results.extend(got)
            kept = 0
            for res in results:
                url = (res.get("url") or "").split("?")[0].split("#")[0]
                for bare, www in CANONICAL_HOST.items():
                    url = re.sub(rf"^(https?://){bare}/", rf"\1{www}/", url)
                if not url or not rx.match(url) or url in seen:
                    continue
                age = _parse_age(res.get("page_age"))
                if age is not None and (datetime.now(timezone.utc) - age).days > MAX_AGE_DAYS:
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
