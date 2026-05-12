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

from playwright.async_api import async_playwright

try:
    import browser_cookie3
except ImportError:
    # Only needed for local Chrome-cookie mode; cloud mode uses Browserbase.
    browser_cookie3 = None

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
    """Find recent RSS/news items to enrich with full article text."""
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute("""
        SELECT id, url, title, body, source, feed_name, relevance_score
        FROM items
        WHERE source = 'rss'
          AND collected_at >= ?
          AND url != ''
          AND url NOT LIKE 'https://t.co/%'
          AND url NOT LIKE 'https://x.com/%'
          AND url NOT LIKE 'https://news.google.com/%'
          AND platform_tags NOT LIKE '%Journals%'
        ORDER BY COALESCE(relevance_score, 0) DESC
        LIMIT ?
    """, (cutoff, limit)).fetchall()
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
    # Try common article containers first. Bloomberg-specific selectors added
    # because the generic 'article-body' / 'story-body' didn't match their
    # actual markup, causing 478/515 Bloomberg articles to return only the
    # 654-char OpenGraph meta description.
    for pattern in [
        r'<article[^>]*>(.*?)</article>',
        # Bloomberg-specific: data-component attributes
        r'<div[^>]*\bdata-component="body"[^>]*>(.*?)</div>\s*<div[^>]*\bdata-component="(?:footer|recommended)"',
        r'<div[^>]*\bclass="[^"]*\bbody-content\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*\bclass="[^"]*\bbody-copy\b[^"]*"[^>]*>(.*?)</div>',
        r'<section[^>]*\bclass="[^"]*\bbody-(?:content|copy)\b[^"]*"[^>]*>(.*?)</section>',
        # Generic patterns (existing)
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


import os

BROWSERBASE_API_KEY = os.environ.get("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID = os.environ.get("BROWSERBASE_PROJECT_ID", "")
# Optional context_id for persistent paywall cookies (WSJ/FT/NYT/etc.).
# Created via Browserbase API; the user logs into each site once via the
# Browserbase live-view, cookies persist for months.
BROWSERBASE_CONTEXT_ID = os.environ.get("BROWSERBASE_CONTEXT_ID", "")


def _create_browserbase_session():
    """Create a new Browserbase session with the configured project + context.

    Returns the session object (has .id and .connect_url). Returns None if
    Browserbase isn't configured — caller falls back to local Playwright.
    """
    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        return None
    try:
        from browserbase import Browserbase
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
        kwargs = {
            "project_id": BROWSERBASE_PROJECT_ID,
            # `api_timeout` is the SESSION lifetime in seconds (not to be
            # confused with `timeout` which is the HTTP request timeout for
            # the create call). Default session is ~5 min — too short for a
            # 150-article batch. Bump to 30 min.
            "api_timeout": 1800,
            "keep_alive": True,
        }
        if BROWSERBASE_CONTEXT_ID:
            # Persist cookies/storage across sessions — enables paywall auth
            kwargs["browser_settings"] = {
                "context": {"id": BROWSERBASE_CONTEXT_ID, "persist": True}
            }
        session = bb.sessions.create(**kwargs)
        return session
    except Exception as e:
        logger.warning(f"Browserbase session creation failed: {e}")
        return None


async def _connect_browser(p) -> tuple[object, str]:
    """Connect to a remote (Browserbase) or local browser. Returns (browser, mode).

    Mode is one of: "browserbase", "local-fallback".

    Primary path: Browserbase. Hosted Chrome with residential proxies + stealth
    posture. Handles paywall bot-detection that local Playwright trips
    (Bloomberg confirmed working, expect similar for WSJ/FT/NYT once auth
    cookies are stored in a Browserbase context).

    Fallback: local headless Chrome + cookie injection. Used if Browserbase is
    misconfigured/unreachable. Same fragile behavior as before but at least
    keeps pulse running.
    """
    session = _create_browserbase_session()
    if session is not None:
        try:
            browser = await p.chromium.connect_over_cdp(session.connect_url)
            logger.info(f"Connected to Browserbase session {session.id} "
                        f"(context_id={'set' if BROWSERBASE_CONTEXT_ID else 'none'})")
            return browser, "browserbase"
        except Exception as e:
            logger.warning(f"Browserbase CDP connect failed: {e}; falling back to local")

    # Local fallback
    logger.warning("Using local headless Chrome with cookie injection (Browserbase unavailable)")
    browser = await p.chromium.launch(
        headless=True,
        channel="chrome",
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    return browser, "local-fallback"


async def _enrich_batch(items: list[dict], dry_run: bool = False) -> dict[str, str]:
    """Fetch full text for a batch of items. Returns {item_id: full_text}."""
    results: dict[str, str] = {}

    async with async_playwright() as p:
        browser, mode = await _connect_browser(p)

        for item in items:
            url = item["url"]
            item_id = item["id"]
            title = item.get("title", "")[:60]

            parsed = urlparse(url)
            domain = parsed.netloc.lower().lstrip("www.")
            if any(domain == skip or domain.endswith("." + skip) for skip in SKIP_DOMAINS):
                logger.info(f"  SKIP {domain}: {title}")
                continue

            page = None
            context = None
            try:
                if mode == "browserbase":
                    # Use the persistent auth context but spawn a fresh page per
                    # URL — sharing a single page across many gotos caused a
                    # navigation cascade where a Bloomberg redirect to
                    # chrome-error:// poisoned the page state for subsequent
                    # URLs ("interrupted by another navigation"). A new page
                    # per URL is fully isolated.
                    context = browser.contexts[0]
                    page = await context.new_page()
                else:
                    # Local fallback: spawn per-URL context with cookies
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

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                # Browserbase sometimes needs longer for JS-rendered content to
                # stabilize (Bloomberg, FT). 4s is the sweet spot per testing.
                await page.wait_for_timeout(4000 if mode == "browserbase" else 2000)

                html = await page.content()

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
            finally:
                # Always close the per-URL page to release the slot.
                # For local mode also close the per-URL context.
                if page is not None:
                    try: await page.close()
                    except Exception: pass
                if mode != "browserbase" and context is not None:
                    try: await context.close()
                    except Exception: pass

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
    parser.add_argument("--limit", type=int, default=150)
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
