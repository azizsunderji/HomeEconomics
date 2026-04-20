#!/usr/bin/env python3
"""Fetch full article text for RSS/news items in pulse.db using playwright + Chrome cookies.

Runs locally before synthesis. Updates the body field for items that only have
RSS-teaser-level text (< 500 chars). Uses the user's logged-in Chrome session so
paywalled articles (NYT, WSJ, FT, Bloomberg) are fully accessible.

Usage:
    python enrich_articles.py [--hours 36] [--limit 60] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import browser_cookie3
from playwright.async_api import async_playwright

DB_PATH = Path(__file__).parent / "data" / "pulse.db"
LOG_PATH = Path("/tmp/pulse_enrich.log")

_CHROME_BASE = "/Users/azizsunderji/Library/Application Support/Google/Chrome"
_CHROME_PROFILES = ["Default"] + [f"Profile {i}" for i in range(1, 6)]

# Sites known to block headless even with valid cookies — skip them
SKIP_DOMAINS = {
    "google.com", "google.news.com", "t.co", "twitter.com", "x.com",
    "linkedin.com", "facebook.com",
}

# Minimum body length to consider "already enriched"
MIN_BODY_LEN = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_PATH), mode="a"),
    ],
)
logger = logging.getLogger("enrich")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _get_items_to_enrich(conn: sqlite3.Connection, hours: int, limit: int) -> list[dict]:
    """Find recent RSS/news items with thin body text."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute("""
        SELECT id, url, title, body, source, feed_name, relevance_score
        FROM items
        WHERE source IN ('rss', 'google_news')
          AND collected_at >= ?
          AND (body IS NULL OR length(body) < ?)
          AND url != ''
          AND url NOT LIKE 'https://t.co/%'
        ORDER BY COALESCE(relevance_score, 0) DESC
        LIMIT ?
    """, (cutoff, MIN_BODY_LEN, limit)).fetchall()
    return [dict(r) for r in rows]


def _get_cookies(url: str) -> list[dict]:
    """Load Chrome cookies for the given URL across all profiles."""
    parsed = urlparse(url)
    domain = parsed.netloc
    if domain.startswith("www."):
        domain = "." + domain[4:]
    else:
        domain = "." + domain

    seen, raw_cookies = set(), []
    for profile in _CHROME_PROFILES:
        try:
            jar = browser_cookie3.chrome(
                domain_name=domain,
                cookie_file=f"{_CHROME_BASE}/{profile}/Cookies",
            )
            for c in jar:
                key = (c.name, c.domain)
                if key not in seen:
                    seen.add(key)
                    raw_cookies.append(c)
        except Exception:
            pass

    return [
        {"name": c.name, "value": c.value, "domain": c.domain,
         "path": c.path, "secure": bool(c.secure), "httpOnly": False}
        for c in raw_cookies
    ]


def _extract_article_text(html: str) -> str:
    """Extract main article body from rendered HTML."""
    # Strip script/style/nav/header/footer blocks
    html = re.sub(
        r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>",
        "", html, flags=re.DOTALL | re.IGNORECASE,
    )
    # Try common article containers first
    for pattern in [
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*\bclass="[^"]*\barticle-body\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*\bclass="[^"]*\bstory-body\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*\bclass="[^"]*\bpost-content\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*\bclass="[^"]*\barticle__body\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*\bclass="[^"]*\bcontent-body\b[^"]*"[^>]*>(.*?)</div>',
        r'<main[^>]*>(.*?)</main>',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 200:
            html = m.group(1)
            break

    # Convert block elements to newlines, strip tags
    html = re.sub(r"</(p|div|h[1-6]|li|blockquote)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)

    # Decode common HTML entities
    for entity, char in [
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
        ("&#39;", "'"), ("&nbsp;", " "), ("&mdash;", "—"), ("&ndash;", "–"),
        ("&rsquo;", "'"), ("&lsquo;", "'"), ("&rdquo;", '"'), ("&ldquo;", '"'),
    ]:
        html = html.replace(entity, char)

    # Collapse whitespace
    lines = [" ".join(line.split()) for line in html.split("\n")]
    text = "\n".join(line for line in lines if line.strip())
    return text.strip()


async def _enrich_batch(items: list[dict], dry_run: bool = False) -> dict[str, str]:
    """Fetch full text for a batch of items. Returns {item_id: full_text}."""
    results: dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        for item in items:
            url = item["url"]
            item_id = item["id"]
            title = item.get("title", "")[:60]

            parsed = urlparse(url)
            domain = parsed.netloc.lower().lstrip("www.")
            if any(skip in domain for skip in SKIP_DOMAINS):
                logger.info(f"  SKIP {domain}: {title}")
                continue

            try:
                cookies = _get_cookies(url)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/147.0.0.0 Safari/537.36"
                    ),
                    java_script_enabled=True,
                )
                if cookies:
                    await context.add_cookies(cookies)

                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)

                html = await page.content()
                await context.close()

                text = _extract_article_text(html)

                if len(text) < 200:
                    logger.info(f"  THIN ({len(text)}c) {domain}: {title}")
                    continue

                # Cap at 8000 chars — enough for synthesis, not a memory hog
                text = text[:8000]
                results[item_id] = text
                logger.info(f"  OK ({len(text)}c) {domain}: {title}")

                # Brief pause between requests to the same domain
                await asyncio.sleep(1.5)

            except Exception as e:
                logger.warning(f"  FAIL {url[:60]}: {e}")

        await browser.close()

    return results


def _update_db(conn: sqlite3.Connection, enriched: dict[str, str]) -> int:
    """Write enriched bodies back to pulse.db."""
    updated = 0
    for item_id, text in enriched.items():
        conn.execute(
            "UPDATE items SET body = ? WHERE id = ?",
            (text, item_id),
        )
        updated += 1
    conn.commit()
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=36)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logger.info(f"=== PULSE ARTICLE ENRICHMENT (last {args.hours}h, limit {args.limit}) ===")
    t0 = time.time()

    conn = _get_db()
    items = _get_items_to_enrich(conn, args.hours, args.limit)
    logger.info(f"Found {len(items)} items needing enrichment")

    if not items:
        logger.info("Nothing to enrich.")
        return

    enriched = asyncio.run(_enrich_batch(items, dry_run=args.dry_run))

    if args.dry_run:
        logger.info(f"Dry run — would update {len(enriched)} items")
    else:
        updated = _update_db(conn, enriched)
        logger.info(f"Updated {updated} items in pulse.db")

    elapsed = time.time() - t0
    logger.info(f"Enrichment complete in {elapsed:.0f}s — {len(enriched)}/{len(items)} items enriched")


if __name__ == "__main__":
    main()
