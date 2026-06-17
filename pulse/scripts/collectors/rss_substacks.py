"""Competitor Substack collector.

Fetches RSS feeds from a curated list of economics/housing Substacks.

Two fetch paths:
  1. Browserbase (preferred when BROWSERBASE_API_KEY is set). Substack.com's
     CDN aggressively 403s GitHub Actions IP ranges even with a browser UA.
     Routing through Browserbase's residential-IP sessions recovers ~9
     newsletters/day we'd otherwise lose in production.
  2. httpx (fallback for local dev or when BB is unavailable). Same browser
     UA as before.

A single BB session is opened for the entire substack batch and reused
across all feeds — far cheaper than per-feed session creation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx

from collectors import PulseItem
from config import COMPETITOR_SUBSTACKS

logger = logging.getLogger(__name__)

# Browser-like UA — substack.com and several publisher domains 403 anything
# that looks like a script/bot. The plain `feedparser.parse(url)` path uses
# python-feedparser/X.X as the UA and gets silently blocked by ~25 of our
# substack feeds. Routing through httpx with this UA recovers them.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_XML_BAD_ENTITY_RE = re.compile(
    r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([A-Za-z][A-Za-z0-9]*);"
)
_XML_BARE_AMP_RE = re.compile(r"&(?!#?\w+;)")


def _sanitize_xml_entities(b: bytes) -> bytes:
    """Replace undefined named entities and bare `&` so an XML parser
    that doesn't ship the HTML DTD can still parse the document."""
    try:
        text = b.decode("utf-8", errors="replace")
    except Exception:
        return b
    text = _XML_BAD_ENTITY_RE.sub(r"&amp;\1;", text)
    text = _XML_BARE_AMP_RE.sub("&amp;", text)
    return text.encode("utf-8", errors="replace")


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


async def _fetch_via_browserbase(urls: list[str]) -> dict[str, bytes]:
    """Open ONE Browserbase session, fetch every substack RSS URL through
    the browser context's HTTP client, return raw bytes per URL.

    Bypasses substack.com's CDN-level IP block on GitHub Actions runners.
    Missing keys = failed fetch (logged); caller falls back to httpx.
    """
    out: dict[str, bytes] = {}
    try:
        from playwright.async_api import async_playwright
        # Reuse enrich_articles' session helper to keep BB config in one place.
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from enrich_articles import _create_browserbase_session
    except Exception as e:
        logger.warning(f"Browserbase substack import failed: {e}")
        return out

    session = _create_browserbase_session()
    if session is None:
        return out

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(session.connect_url)
            ctx = browser.contexts[0]
            req = ctx.request
            for url in urls:
                try:
                    resp = await req.get(
                        url,
                        headers={
                            "User-Agent": _UA,
                            "Accept": (
                                "application/rss+xml, application/atom+xml, "
                                "application/xml;q=0.9, */*;q=0.8"
                            ),
                        },
                        timeout=20000,
                    )
                    if resp.status == 200:
                        out[url] = await resp.body()
                    else:
                        logger.info(
                            f"BB substack '{url}': HTTP {resp.status}"
                        )
                except Exception as e:
                    logger.warning(
                        f"BB substack '{url}' fetch error: {type(e).__name__}: {e}"
                    )
            try:
                await browser.close()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"BB substack session failed: {e}")
    return out


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

    # Browserbase batch pre-fetch: substack.com 403s GitHub Actions IPs even
    # with a browser UA. We open ONE BB session and fetch all feeds through
    # it; anything that succeeds skips the httpx path below.
    bb_bytes: dict[str, bytes] = {}
    if os.environ.get("BROWSERBASE_API_KEY"):
        try:
            urls_to_fetch = [feed_url for _, feed_url in substacks]
            bb_bytes = asyncio.run(_fetch_via_browserbase(urls_to_fetch))
            logger.info(
                f"Browserbase substack pass: {len(bb_bytes)} of "
                f"{len(urls_to_fetch)} feeds fetched"
            )
        except Exception as e:
            logger.warning(f"Browserbase substack pass crashed: {e}")

    for name, feed_url in substacks:
        try:
            raw_bytes = bb_bytes.get(feed_url)

            # httpx fallback for any URL Browserbase didn't get.
            if raw_bytes is None:
                try:
                    r = httpx.get(
                        feed_url,
                        timeout=20,
                        follow_redirects=True,
                        headers={
                            "User-Agent": _UA,
                            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                        },
                    )
                except Exception as fetch_err:
                    logger.warning(f"Substack fetch failed for '{name}': {fetch_err}")
                    continue

                if r.status_code != 200:
                    logger.warning(f"Substack '{name}' returned HTTP {r.status_code}")
                    continue

                raw_bytes = r.content
            parsed = feedparser.parse(raw_bytes)

            # Retry with entity sanitization if first pass bozo'd and got nothing.
            if parsed.bozo and not parsed.entries:
                sanitized = _sanitize_xml_entities(raw_bytes)
                if sanitized != raw_bytes:
                    parsed_retry = feedparser.parse(sanitized)
                    if parsed_retry.entries:
                        logger.info(
                            f"Substack '{name}' recovered after entity sanitization: "
                            f"{len(parsed_retry.entries)} entries"
                        )
                        parsed = parsed_retry

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
                    body = re.sub(r"<[^>]+>", "", entry["content"][0].get("value", "")).strip()[:3000]
                elif "summary" in entry:
                    body = re.sub(r"<[^>]+>", "", entry["summary"]).strip()[:3000]

                item = PulseItem(
                    source="substack",
                    # Deterministic MD5 — Python's hash() is randomized per-process
                    # and was producing different source_ids for the same URL each
                    # run, silently bypassing UNIQUE dedup and creating duplicates.
                    source_id=f"sub_{hashlib.md5(url.encode()).hexdigest()[:12]}",
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
