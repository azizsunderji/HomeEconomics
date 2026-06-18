"""OPML-based RSS feed collector.

Parses the HomeEconomicsRSS.opml file and fetches all feeds.
Uses feedparser for RSS/Atom parsing.
"""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import time

import feedparser

from collectors import PulseItem, record_collector_error

logger = logging.getLogger(__name__)

# Look for OPML: new data/ location first, then legacy repo root, then Dropbox
_DATA_OPML = Path(__file__).parent.parent.parent / "data" / "Feeds.opml"
_REPO_OPML = Path(__file__).parent.parent.parent / "HomeEconomicsRSS.opml"
_DROPBOX_OPML = Path("/Users/azizsunderji/Dropbox/Home Economics/RSSFeeds/HomeEconomicsRSS.opml")
DEFAULT_OPML_PATH = str(
    _DATA_OPML if _DATA_OPML.exists() else
    _REPO_OPML if _REPO_OPML.exists() else _DROPBOX_OPML
)


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
    max_per_feed: int = 25,
    max_age_hours: int = 72,
) -> list[PulseItem]:
    """Fetch all feeds from OPML and collect recent entries.

    Returns list of PulseItem objects.
    """
    feeds = parse_opml(opml_path)
    items = []
    seen_urls = set()
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    logger.info(f"Parsed {len(feeds)} feeds from OPML")

    # Strip tracking/position query params so the SAME article in multiple feed
    # slots (WSJ's lead_pos1..5, utm_*, ref, etc.) dedupes to one item.
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    def _canonical_url(u: str) -> str:
        try:
            parts = urlsplit(u)
            kept = [
                (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
                if not (
                    k.lower().startswith(("utm_", "mc_", "fbclid", "gclid", "ref"))
                    or k.lower() in {"mod", "source", "campaign_id", "share", "share_id"}
                )
            ]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))
        except Exception:
            return u

    # ──────────────────────────────────────────────────────────────────
    # Why we don't call `feedparser.parse(url)` directly:
    # feedparser's built-in HTTP fetcher silently fails on some publisher
    # endpoints (e.g. seattletimes.com returns "mismatched tag" / 0
    # entries even though the body parses fine when fetched with httpx).
    # We route every feed through httpx with a real UA + follow_redirects,
    # then pass the bytes to feedparser. That fixed Seattle Times +
    # several other publisher feeds going dark in the pipeline.
    #
    # Brookings (and a handful of other XML feeds) ship malformed entity
    # references like `&hellip;` that the XML parser flags as undefined.
    # _sanitize_xml_entities() converts those bad `&…;` sequences to
    # safe HTML-numeric escapes so feedparser can keep going.
    # ──────────────────────────────────────────────────────────────────
    import httpx as _httpx
    import re as _re_rss
    # Browser-like UA — seattletimes.com and a handful of other publishers
    # 403 anything that looks like a script/bot. Real desktop UA gets through.
    _UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    _XML_BAD_ENTITY_RE = _re_rss.compile(
        r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([A-Za-z][A-Za-z0-9]*);"
    )
    _XML_BARE_AMP_RE = _re_rss.compile(r"&(?!#?\w+;)")

    def _sanitize_xml_entities(b: bytes) -> bytes:
        """Replace undefined named entities and bare `&` so an XML parser
        that doesn't ship the HTML DTD can still parse the document.

        - `&hellip;` / `&nbsp;` / other HTML-only entities → `&amp;hellip;`
          etc. (escape the ampersand so the parser sees the text intact)
        - bare `&` not followed by a known entity → `&amp;`
        Both transformations are conservative: known XML entities (amp,
        lt, gt, quot, apos) and numeric entities (`&#123;`, `&#x7B;`)
        pass through untouched.
        """
        try:
            text = b.decode("utf-8", errors="replace")
        except Exception:
            return b
        text = _XML_BAD_ENTITY_RE.sub(r"&amp;\1;", text)
        text = _XML_BARE_AMP_RE.sub("&amp;", text)
        return text.encode("utf-8", errors="replace")

    for feed_info in feeds:
        try:
            # Fetch via httpx (lets us swap UA, follow redirects, and
            # handle quirks consistently across feeds).
            try:
                r = _httpx.get(
                    feed_info["url"],
                    timeout=20,
                    follow_redirects=True,
                    headers={"User-Agent": _UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"},
                )
            except Exception as fetch_err:
                record_collector_error("rss", fetch_err, context=f"feed={feed_info['title']}")
                logger.warning(
                    f"Feed fetch failed for '{feed_info['title']}': {fetch_err}"
                )
                continue
            if r.status_code != 200:
                record_collector_error(
                    "rss",
                    RuntimeError(f"HTTP {r.status_code}"),
                    context=f"feed={feed_info['title']}",
                )
                logger.warning(
                    f"Feed '{feed_info['title']}' returned HTTP {r.status_code}"
                )
                continue

            raw_bytes = r.content
            parsed = feedparser.parse(raw_bytes)

            # Retry with entity sanitization if the first pass bozo'd
            # AND returned no entries (typical XML-undefined-entity case).
            if parsed.bozo and not parsed.entries:
                sanitized = _sanitize_xml_entities(raw_bytes)
                if sanitized != raw_bytes:
                    parsed_retry = feedparser.parse(sanitized)
                    if parsed_retry.entries:
                        logger.info(
                            f"Feed '{feed_info['title']}' recovered after "
                            f"entity sanitization: {len(parsed_retry.entries)} entries"
                        )
                        parsed = parsed_retry

            if parsed.bozo and not parsed.entries:
                record_collector_error(
                    "rss",
                    parsed.bozo_exception or RuntimeError("feed parse failed"),
                    context=f"feed={feed_info['title']}",
                )
                logger.warning(f"Feed error for '{feed_info['title']}': {parsed.bozo_exception}")
                continue

            count = 0
            for entry in parsed.entries[:max_per_feed]:
                raw_url = entry.get("link", "")
                url = _canonical_url(raw_url)
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
                    # MD5-based source_id is deterministic across runs. Python's
                    # built-in hash() is randomized per-process (GitHub Actions
                    # spins up a fresh interpreter every run), producing a
                    # different ID for the same URL each day — which silently
                    # bypassed the UNIQUE constraint and created thousands of
                    # duplicate rows of the same articles.
                    source_id=f"rss_{hashlib.md5(url.encode()).hexdigest()[:12]}",
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
            record_collector_error("rss", e, context=f"feed={feed_info['title']}")
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
