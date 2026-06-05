#!/usr/bin/env python3
"""Resolve and enrich t.co links inside high-relevance tweet bodies.

A tweet often quotes a study/article with a t.co link to the underlying source.
Without resolving that link, Sonnet only sees the tweeter's cherry-picked
excerpt and can miss the actual source's framing (see the @ProducerCities /
"Black homeownership rate backtracks" failure).

What this does:
1. Pull tweets with body containing 't.co/...' and relevance_score >= threshold
2. Resolve each t.co URL by following HTTP redirects to the final destination
3. Skip links that resolve to social media, image hosts, video sites
4. Fetch the destination article via Browserbase
5. Append a "[Linked article: <title>] <excerpt>" block to the tweet's body

Runs after enrich_articles.py in the cloud workflow.

Usage:
    python enrich_tweet_links.py [--hours 24] [--limit 60] [--min-relevance 50]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

DB_PATH = Path(
    os.environ.get(
        "PULSE_DB",
        "/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"
        if Path("/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db").exists()
        else str(Path(__file__).parent / "data" / "pulse.db"),
    )
)
LOG_PATH = Path("/tmp/pulse_enrich_tweet_links.log")

# Domains we DON'T want to enrich — they're either useless (image hosts,
# self-references) or actively hostile (auth-gated social media).
SKIP_RESOLVED_DOMAINS = {
    "twitter.com", "x.com", "t.co", "pic.twitter.com",
    "instagram.com", "facebook.com", "linkedin.com",
    "youtube.com", "youtu.be", "tiktok.com",
    "imgur.com", "i.redd.it", "v.redd.it",
}

# Don't bother resolving t.co URLs we've already seen this run
_resolved_cache: dict[str, str | None] = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_PATH), mode="a"),
    ],
)
logger = logging.getLogger("enrich_tweet_links")


def _resolve_tco(short_url: str, timeout: int = 8) -> str | None:
    """Follow HTTP redirects from a t.co URL to the final destination.

    Returns the resolved URL or None on failure.
    """
    if short_url in _resolved_cache:
        return _resolved_cache[short_url]
    try:
        req = urllib.request.Request(
            short_url,
            method="HEAD",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 Chrome/130.0.0.0"},
        )
        # urllib follows redirects by default — final URL is in response.url
        with urllib.request.urlopen(req, timeout=timeout) as r:
            final = r.url
    except urllib.error.HTTPError as e:
        # Some endpoints reject HEAD but the redirect chain still happened
        final = e.url if hasattr(e, "url") else None
    except Exception as e:
        logger.debug(f"resolve fail {short_url}: {e}")
        final = None
    _resolved_cache[short_url] = final
    return final


def _extract_tco_urls(body: str) -> list[str]:
    """Find all https://t.co/... URLs in a tweet body."""
    return re.findall(r"https?://t\.co/[A-Za-z0-9]+", body or "")


def _should_enrich(resolved_url: str) -> bool:
    """Decide whether a resolved URL is worth Browserbase-fetching."""
    if not resolved_url:
        return False
    try:
        domain = urlparse(resolved_url).netloc.lower().lstrip("www.")
    except Exception:
        return False
    if not domain:
        return False
    if any(domain == d or domain.endswith("." + d) for d in SKIP_RESOLVED_DOMAINS):
        return False
    return True


# ---- Browserbase setup (mirrors enrich_articles.py) ----

BROWSERBASE_API_KEY = os.environ.get("BROWSERBASE_API_KEY", "")
BROWSERBASE_PROJECT_ID = os.environ.get("BROWSERBASE_PROJECT_ID", "")
BROWSERBASE_CONTEXT_ID = os.environ.get("BROWSERBASE_CONTEXT_ID", "")


def _create_browserbase_session():
    if not BROWSERBASE_API_KEY or not BROWSERBASE_PROJECT_ID:
        return None
    try:
        from browserbase import Browserbase
        bb = Browserbase(api_key=BROWSERBASE_API_KEY)
        kwargs = {"project_id": BROWSERBASE_PROJECT_ID, "api_timeout": 1800, "keep_alive": True}
        if BROWSERBASE_CONTEXT_ID:
            kwargs["browser_settings"] = {"context": {"id": BROWSERBASE_CONTEXT_ID, "persist": True}}
        return bb.sessions.create(**kwargs)
    except Exception as e:
        logger.warning(f"Browserbase session creation failed: {e}")
        return None


def _extract_text(html: str) -> tuple[str, str]:
    """Return (title, body_text) from an HTML page."""
    # Title
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = (m.group(1).strip() if m else "").replace("&amp;", "&").replace("&#39;", "'")

    # Strip scripts/styles
    html = re.sub(r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>",
                  "", html, flags=re.DOTALL | re.IGNORECASE)
    # Try article container
    for pattern in [
        r"<article[^>]*>(.*?)</article>",
        r'<div[^>]*\bclass="[^"]*\barticle-body\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*\bclass="[^"]*\bstory-body\b[^"]*"[^>]*>(.*?)</div>',
        r"<main[^>]*>(.*?)</main>",
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 200:
            html = m.group(1)
            break
    # Convert blocks to newlines, strip tags
    html = re.sub(r"</(p|div|h[1-6]|li|blockquote)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    for entity, ch in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                       ("&#39;", "'"), ("&nbsp;", " "), ("&mdash;", "—")]:
        html = html.replace(entity, ch)
    lines = [" ".join(line.split()) for line in html.split("\n")]
    body = "\n".join(line for line in lines if line.strip()).strip()
    return title, body


async def _fetch_page(context, url: str) -> tuple[str, str]:
    """Fetch a URL via Browserbase; return (title, body) or empty strings on fail."""
    page = None
    try:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3500)
        html = await page.content()
        return _extract_text(html)
    except Exception as e:
        logger.warning(f"  page fetch fail {url[:80]}: {type(e).__name__}")
        return "", ""
    finally:
        if page is not None:
            try: await page.close()
            except Exception: pass


# ---- Main pipeline ----

async def _run(hours: int, limit: int, min_relevance: int, dry_run: bool):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cutoff = f"datetime('now', '-{hours} hours')"
    rows = conn.execute(
        f"""
        SELECT id, url, body, title, relevance_score, author
        FROM items
        WHERE source = 'twitter'
          AND body LIKE '%t.co/%'
          AND collected_at > {cutoff}
          AND (relevance_score IS NULL OR relevance_score >= ?)
        ORDER BY relevance_score DESC NULLS LAST
        LIMIT ?
        """,
        (min_relevance, limit),
    ).fetchall()

    logger.info(f"=== TWEET-LINK ENRICHMENT (last {hours}h, limit {limit}, min_relevance {min_relevance}) ===")
    logger.info(f"Found {len(rows)} candidate tweets with t.co links")

    if not rows:
        return

    bb_session = _create_browserbase_session()
    if bb_session is None:
        logger.warning("Browserbase unavailable; aborting (this script requires Browserbase)")
        return

    appended = 0
    skipped = 0
    failed = 0
    seen_resolved = set()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(bb_session.connect_url)
        context = browser.contexts[0]

        for item in rows:
            tco_urls = _extract_tco_urls(item["body"])
            if not tco_urls:
                continue

            # Resolve each t.co
            resolved = []
            for short in tco_urls:
                final = _resolve_tco(short)
                if final and _should_enrich(final):
                    if final in seen_resolved:
                        continue  # already enriched this link via another tweet
                    seen_resolved.add(final)
                    resolved.append(final)

            if not resolved:
                skipped += 1
                continue

            # Fetch first useful resolved URL (most tweets link 1 substantive URL)
            target = resolved[0]
            title, body_text = await _fetch_page(context, target)
            if len(body_text) < 200:
                logger.info(f"  THIN linked {target[:60]}")
                failed += 1
                continue

            # Append to tweet body
            excerpt = body_text[:3000]
            new_body = (item["body"] or "") + (
                f"\n\n[Linked article from tweet — {target}]\n"
                f"Title: {title or '(no title)'}\n{excerpt}"
            )

            if not dry_run:
                conn.execute(
                    "UPDATE items SET body = ? WHERE id = ?",
                    (new_body, item["id"]),
                )
                conn.commit()

            logger.info(f"  APPENDED ({len(excerpt)}c) {item['author']}: {(item['title'] or '')[:60]} ← {target[:60]}")
            appended += 1

        await browser.close()

    logger.info(f"\n=== DONE — appended={appended}  failed={failed}  skipped(all-social-or-unresolvable)={skipped} ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--min-relevance", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(_run(args.hours, args.limit, args.min_relevance, args.dry_run))


if __name__ == "__main__":
    main()
