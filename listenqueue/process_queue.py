#!/usr/bin/env python3
"""Process the listen queue: extract text, generate audio, update RSS feed.

Run this on a schedule (e.g., every 30 min via cron or launchd) or on-demand.
"""

import logging
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

# Add parent dir so we can import from the package
sys.path.insert(0, str(Path(__file__).parent))

from queue_db import get_pending, mark_done, mark_error
from extract import extract_from_url
from tts import synthesize
from feed_generator import generate_feed
from text_prep import prepare_for_tts

# Pulse pipeline DB — already-enriched article bodies live here. The Pulse
# enricher runs Browserbase (paywall-aware) + archive.ph fallback, so its
# body content is usually higher fidelity than what ListenQueue can
# re-extract on its own — especially for FT/WSJ/NYT/Bloomberg.
PULSE_DB_PATH = Path(
    "/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"
)
# Below this length, treat the pulse body as a teaser/snippet and prefer
# fresh extraction. The Pulse enricher caps bodies at 8000 chars, so an
# enriched article will typically sit between 1500 and 8000.
MIN_ENRICHED_CHARS = 1500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("listenqueue")


def _generate_intro(title: str, text: str, source: str = "") -> str:
    """Use Claude Haiku to generate a brief spoken intro for the episode."""
    try:
        import anthropic
        client = anthropic.Anthropic()

        # Always extract author/publication from the text itself — never use
        # queue source field ("gmail", "telegram", "cli") as the author
        source_info = ""
        first_lines = text[:500]
        # Try "By Author Name" or "By Author from Publication"
        by_match = re.search(r'[Bb]y ([^.\n]+?)(?:\.|$|\n)', first_lines)
        if by_match:
            source_info = by_match.group(1).strip()
        # If source is just "gmail" or "telegram", don't use it
        if source in ("gmail", "telegram", "cli", ""):
            pass  # Already handled above
        elif source_info:
            pass  # Already found from text
        else:
            source_info = source

        today = datetime.now().strftime("%B %d, %Y")

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": (
                f"Write a brief spoken intro (2-3 sentences) for a podcast episode. "
                f"Format: 'This is [Title]' — then add 'by [Author]' ONLY if a real person's name is provided "
                f"in the Author field below. If the Author field says '(none)' or is empty, do NOT add any author attribution at all. "
                f"Do NOT extract a phrase from the article text and use it as an author name. "
                f"Then add 'published in [Publication]' if known, then the date. "
                f"Then one sentence summarizing the main argument. "
                f"Do NOT say 'Today we're discussing' or 'Welcome to the podcast' or 'On today's episode'. "
                f"Just state the facts directly. Write it as spoken aloud — no quotes, no markdown.\n\n"
                f"Title: {title}\n"
                f"Author: {source_info if source_info else '(none)'}\n"
                f"Date: {today}\n"
                f"First 500 words: {' '.join(text.split()[:500])}"
            )}],
        )
        intro = resp.content[0].text.strip()
        logger.info(f"  Generated intro: {intro[:80]}...")
        return intro
    except Exception as e:
        logger.warning(f"  Intro generation failed: {e}")
        # Fallback: simple intro
        if source:
            return f"{title}. By {source}."
        return f"{title}."


def _clean_and_extract(raw_text: str, title_hint: str = "") -> dict:
    """Use Haiku to extract metadata, and clean the article body.

    For short articles (<5000 words), Haiku cleans the body too.
    For long articles, Haiku only extracts metadata and text_prep handles the body,
    since Haiku's output token limit can't fit the full body in a JSON response.

    Returns {"title", "author", "publication", "date", "body"}.
    """
    word_count = len(raw_text.split())
    is_long = word_count > 5000

    try:
        import anthropic
        client = anthropic.Anthropic()

        if is_long:
            # Long article: only extract metadata from the first ~2000 words
            # Body cleanup handled by text_prep.py
            snippet = ' '.join(raw_text.split()[:2000])
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{"role": "user", "content": (
                    "Extract metadata from this article text. Return ONLY a JSON object with these fields:\n"
                    '- "title": the article title\n'
                    '- "author": the author\'s full name, or empty string "" if not explicitly stated. '
                    "Do NOT guess or infer the author — only include a name if it appears as a byline.\n"
                    '- "publication": the publication/newsletter name, or empty string "" if not found\n'
                    '- "date": the publication date if found, or empty string\n'
                    "Do NOT include a body field.\n\n"
                    f"Title hint: {title_hint}\n\nFirst ~2000 words:\n{snippet}"
                )}],
            )

            import json as _json
            response_text = resp.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            result = _json.loads(response_text)

            # Use text_prep for body cleanup on the full text
            cleaned_body = prepare_for_tts(raw_text)
            result["body"] = cleaned_body

            logger.info(f"  Haiku metadata (long article): title='{result.get('title','')[:40]}', "
                         f"author='{result.get('author','')[:30]}', "
                         f"body={len(cleaned_body.split())} words (text_prep cleaned)")
            return result

        else:
            # Short article: Haiku does both metadata and body cleanup
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=16000,
                messages=[{"role": "user", "content": (
                    "Extract the article from this raw web/email text. Return ONLY a JSON object with these fields:\n"
                    '- "title": the article title\n'
                    '- "author": the author\'s full name, or empty string "" if not explicitly stated in the text. '
                    "Do NOT guess or infer the author — only include a name if it appears as a byline.\n"
                    '- "publication": the publication/newsletter name, or empty string "" if not found\n'
                    '- "date": the publication date if found, or empty string\n'
                    '- "body": the clean article body text — REMOVE all navigation, share buttons, ads, copyright, '
                    "subscribe CTAs, image captions, photo credits, UI elements, footer junk, repeated title/author, "
                    "HTML/CSS fragments, references/bibliography, social media links. "
                    "KEEP the COMPLETE article body — every paragraph, section heading, quote, and data point. "
                    "Do NOT summarize or shorten the body. Return it in full.\n\n"
                    f"Title hint: {title_hint}\n\nRaw text:\n{raw_text}"
                )}],
            )

            import json as _json
            response_text = resp.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            result = _json.loads(response_text)
            logger.info(f"  Haiku cleaned: title='{result.get('title','')[:40]}', "
                         f"author='{result.get('author','')[:30]}', "
                         f"body={len(result.get('body','').split())} words")
            return result

    except Exception as e:
        logger.warning(f"  Haiku cleanup failed: {e}")
        return {"title": title_hint, "author": "", "publication": "", "date": "", "body": prepare_for_tts(raw_text)}


def _extract_via_browserbase(url: str) -> dict:
    """Extract article text via Browserbase — hosted Chrome with residential
    proxies, stealth posture, and persistent paywall-auth cookies.

    This is the same path Pulse uses for enrichment. Much higher fidelity
    than local Playwright (which gets bot-detected) and doesn't require the
    user to have Chrome running locally. Costs Browserbase credits per
    session but reliably gets full text for WSJ / FT / NYT / Bloomberg /
    The Atlantic / The New Yorker / etc.

    Requires env vars BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, and
    optionally BROWSERBASE_CONTEXT_ID (for persistent paywall cookies).
    Returns {title, text, url} or {..., error: <msg>} on failure.
    """
    api_key = os.environ.get("BROWSERBASE_API_KEY", "")
    project_id = os.environ.get("BROWSERBASE_PROJECT_ID", "")
    context_id = os.environ.get("BROWSERBASE_CONTEXT_ID", "")
    if not api_key or not project_id:
        logger.warning("  Browserbase: API key / project ID not set in env — skipping")
        return {"title": "", "text": "", "url": url, "error": "browserbase env not set"}

    # Reuse Pulse's HTML→article-text extractor. It has site-specific
    # selectors (Bloomberg, WSJ, etc.) we don't want to duplicate.
    try:
        import sys as _sys
        _pulse_scripts = "/Users/azizsunderji/Dropbox/Home Economics/HomeEconomics/pulse"
        if _pulse_scripts not in _sys.path:
            _sys.path.insert(0, _pulse_scripts)
        from enrich_articles import _extract_article_text as _pulse_extract
    except Exception as e:
        logger.warning(f"  Browserbase: failed to import pulse extractor: {e}")
        return {"title": "", "text": "", "url": url, "error": f"pulse import: {e}"}

    try:
        import asyncio
        from browserbase import Browserbase
        from playwright.async_api import async_playwright
        from urllib.parse import urlparse as _up, urlunparse as _uu

        async def _fetch() -> dict:
            bb = Browserbase(api_key=api_key)
            session_kwargs = {"project_id": project_id, "keep_alive": True}
            if context_id:
                session_kwargs["browser_settings"] = {
                    "context": {"id": context_id, "persist": True}
                }
            session = bb.sessions.create(**session_kwargs)
            logger.info(f"  Browserbase session {session.id} created "
                        f"(context_id={'set' if context_id else 'none'})")
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(session.connect_url)
                try:
                    context = browser.contexts[0]
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    # Browserbase needs longer than local for JS-heavy sites
                    await page.wait_for_timeout(4000)
                    title = await page.title()
                    html = await page.content()
                    text = _pulse_extract(html)
                    # Thin? Try archive.ph cached snapshot in the same session.
                    if len(text) < 500:
                        parsed = _up(url)
                        clean_url = _uu((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                        archive_search = f"https://archive.ph/newest/{clean_url}"
                        logger.info(f"  Browserbase: thin ({len(text)}c), trying archive.ph…")
                        try:
                            arch_page = await context.new_page()
                            await arch_page.goto(archive_search, wait_until="domcontentloaded", timeout=25000)
                            await arch_page.wait_for_timeout(3500)
                            final_url = arch_page.url
                            if "archive.ph" in final_url:
                                body_check = await arch_page.evaluate(
                                    "document.body ? document.body.innerText.slice(0,400) : ''"
                                )
                                bc = (body_check or "").lower()
                                if ("no archived versions" not in bc
                                    and "no snapshots" not in bc
                                    and "captcha" not in bc):
                                    arch_html = await arch_page.content()
                                    arch_text = _pulse_extract(arch_html)
                                    if len(arch_text) >= 500:
                                        text = arch_text
                                        if not title:
                                            title = await arch_page.title()
                                        logger.info(f"  Browserbase+archive.ph: {len(text)}c")
                            try: await arch_page.close()
                            except Exception: pass
                        except Exception as e:
                            logger.warning(f"  archive.ph (via Browserbase) failed: {e}")
                    return {"title": title, "text": text}
                finally:
                    try: await browser.close()
                    except Exception: pass

        result = asyncio.run(_fetch())
        text = result.get("text", "") or ""
        if text and len(text.split()) > 50:
            logger.info(f"  Browserbase extraction succeeded: {len(text.split())} words")
            return {"title": result.get("title", ""), "text": text, "url": url}
        return {"title": "", "text": "", "url": url,
                "error": f"Browserbase returned too little text ({len(text.split())} words)"}
    except Exception as e:
        logger.warning(f"  Browserbase extraction failed: {type(e).__name__}: {e}")
        return {"title": "", "text": "", "url": url, "error": str(e)}


def _extract_via_playwright(url: str) -> dict:
    """Extract article text via playwright with Chrome cookies (handles paywalls).

    Uses browser_cookie3 to read the user's live Chrome cookies (decrypted via
    macOS Keychain) and injects them into a headless Chromium session.
    No separate Chrome debug port required.
    """
    try:
        import asyncio
        import browser_cookie3
        from playwright.async_api import async_playwright
        from urllib.parse import urlparse as _up

        parsed = _up(url)
        domain = parsed.netloc
        if domain.startswith('www.'):
            domain = '.' + domain[4:]  # www.nytimes.com -> .nytimes.com
        else:
            domain = '.' + domain

        _chrome_base = "/Users/azizsunderji/Library/Application Support/Google/Chrome"
        _profiles = ["Default"] + [f"Profile {i}" for i in range(1, 6)]
        seen, raw_cookies = set(), []
        for _profile in _profiles:
            try:
                _jar = browser_cookie3.chrome(
                    domain_name=domain,
                    cookie_file=f"{_chrome_base}/{_profile}/Cookies"
                )
                for c in _jar:
                    key = (c.name, c.domain)
                    if key not in seen:
                        seen.add(key)
                        raw_cookies.append(c)
            except Exception:
                pass
        cookies = [
            {'name': c.name, 'value': c.value, 'domain': c.domain,
             'path': c.path, 'secure': bool(c.secure), 'httpOnly': False}
            for c in raw_cookies
        ]
        logger.info(f"  Playwright: loaded {len(cookies)} cookies for {domain}")

        async def _fetch():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, channel='chrome')
                context = await browser.new_context(
                    user_agent=(
                        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/147.0.0.0 Safari/537.36'
                    )
                )
                await context.add_cookies(cookies)
                page = await context.new_page()
                await page.goto(url, wait_until='load', timeout=25000)
                await page.wait_for_timeout(2000)
                title = await page.title()
                try:
                    paras = await page.query_selector_all('article p')
                    if paras:
                        texts = [await para.inner_text() for para in paras]
                        text = '\n\n'.join(t for t in texts if t.strip())
                    else:
                        text = await page.inner_text('body')
                except Exception:
                    text = await page.inner_text('body')
                await browser.close()
                return title, text

        title, text = asyncio.run(_fetch())

        if text and len(text.split()) > 50:
            logger.info(f"  Playwright extraction succeeded: {len(text.split())} words")
            return {"title": title, "text": text, "url": url}
        return {"title": "", "text": "", "url": url, "error": "Playwright returned too little text"}

    except Exception as e:
        logger.warning(f"  Playwright extraction failed: {e}")
        return {"title": "", "text": "", "url": url, "error": str(e)}


def _extract_via_chrome(url: str) -> dict:
    """Extract article text via Chrome DevTools Protocol.

    Connects to a running Chrome instance with --remote-debugging-port=9222.
    The Chrome must be logged into paywalled sites for this to work.
    """
    try:
        import json, time
        import httpx
        import websocket

        CDP = "http://localhost:9222"

        # Check if Chrome debug is available
        try:
            httpx.get(f"{CDP}/json/version", timeout=3)
        except Exception:
            logger.warning("  Chrome debug port not available (port 9222)")
            return {"title": "", "text": "", "url": url, "error": "Chrome debug not running"}

        # Open a new tab with the URL
        resp = httpx.put(f"{CDP}/json/new?{url}", timeout=15)
        tab = resp.json()
        ws_url = tab.get("webSocketDebuggerUrl", "")
        tab_id = tab.get("id", "")

        if not ws_url:
            return {"title": "", "text": "", "url": url, "error": "No WebSocket URL from CDP"}

        # Wait for page to load — longer for JS-heavy sites like Bloomberg
        from urllib.parse import urlparse as _urlparse
        domain = _urlparse(url).netloc.lower()
        wait_time = 15 if "bloomberg.com" in domain else 10
        time.sleep(wait_time)

        # Extract text via WebSocket
        ws = websocket.create_connection(ws_url)
        cmd = {"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": """(function(){
                var a = document.querySelector('article') || document.querySelector('[role="main"]');
                var title = document.title;
                var text = a ? a.innerText : document.body.innerText;
                return JSON.stringify({title: title, text: text});
            })()""",
            "returnByValue": True
        }}
        ws.send(json.dumps(cmd))
        result = json.loads(ws.recv())
        raw = result.get("result", {}).get("result", {}).get("value", "{}")
        data = json.loads(raw)
        ws.close()

        # Close the tab
        httpx.get(f"{CDP}/json/close/{tab_id}", timeout=5)

        title = data.get("title", "")
        text = data.get("text", "")

        if text and len(text.split()) > 50:
            logger.info(f"  Chrome extraction succeeded: {len(text.split())} words")
            return {"title": title, "text": text, "url": url}
        return {"title": "", "text": "", "url": url, "error": "Chrome extraction returned too little text"}

    except Exception as e:
        logger.warning(f"  Chrome extraction failed: {e}")
        return {"title": "", "text": "", "url": url, "error": str(e)}


def _extract_via_archive(url: str) -> dict:
    """Last-resort extraction via archive.ph cached version.

    Uses Chrome CDP to search archive.ph (which blocks automated HTTP requests)
    and then loads the cached page to extract the full article text.
    Only used when both HTTP and Chrome CDP direct extraction fail.
    """
    try:
        import json, time
        import httpx
        import websocket

        CDP = "http://localhost:9222"

        # Check if Chrome debug is available
        try:
            httpx.get(f"{CDP}/json/version", timeout=3)
        except Exception:
            logger.warning("  Chrome debug port not available for archive.ph")
            return {"title": "", "text": "", "url": url, "error": "Chrome debug not running"}

        # Step 1: Search archive.ph for cached versions of this URL
        # Strip query params (access tokens, tracking) — archive.ph indexes base URLs
        from urllib.parse import urlparse as _ap_urlparse, urlunparse as _ap_urlunparse
        parsed = _ap_urlparse(url)
        clean_url = _ap_urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        search_url = f"https://archive.ph/{clean_url}"
        logger.info(f"  Searching archive.ph via Chrome: {search_url}")

        resp = httpx.put(f"{CDP}/json/new?{search_url}", timeout=15)
        tab = resp.json()
        ws_url = tab.get("webSocketDebuggerUrl", "")
        tab_id = tab.get("id", "")

        if not ws_url:
            return {"title": "", "text": "", "url": url, "error": "No WebSocket URL from CDP"}

        time.sleep(12)

        # Find the link to the newest archived version
        ws = websocket.create_connection(ws_url)
        cmd = {"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": """(function(){
                var links = document.querySelectorAll('a[href*="archive.ph/"]');
                for (var i = 0; i < links.length; i++) {
                    var href = links[i].href;
                    // Match archive.ph/XXXXX (short hash links to archived pages)
                    if (href.match(/archive\\.ph\\/[A-Za-z0-9]{4,6}$/)) {
                        return href;
                    }
                }
                return '';
            })()""",
            "returnByValue": True
        }}
        ws.send(json.dumps(cmd))
        result = json.loads(ws.recv())
        archive_url = result.get("result", {}).get("result", {}).get("value", "")
        ws.close()

        # Close search tab
        httpx.get(f"{CDP}/json/close/{tab_id}", timeout=5)

        if not archive_url:
            logger.info("  No archived version found on archive.ph")
            return {"title": "", "text": "", "url": url, "error": "No archived version found"}

        logger.info(f"  Found archived version: {archive_url}")

        # Step 2: Load the archived page and extract article text
        time.sleep(2)
        resp2 = httpx.put(f"{CDP}/json/new?{archive_url}", timeout=15)
        tab2 = resp2.json()
        ws_url2 = tab2.get("webSocketDebuggerUrl", "")
        tab_id2 = tab2.get("id", "")

        if not ws_url2:
            return {"title": "", "text": "", "url": url, "error": "No WebSocket URL for archive page"}

        time.sleep(12)

        ws2 = websocket.create_connection(ws_url2)
        cmd2 = {"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": """(function(){
                var a = document.querySelector('article') || document.querySelector('[role="main"]');
                var title = document.title;
                var text = a ? a.innerText : document.body.innerText;
                return JSON.stringify({title: title, text: text});
            })()""",
            "returnByValue": True
        }}
        ws2.send(json.dumps(cmd2))
        result2 = json.loads(ws2.recv())
        raw = result2.get("result", {}).get("result", {}).get("value", "{}")
        data = json.loads(raw)
        ws2.close()

        httpx.get(f"{CDP}/json/close/{tab_id2}", timeout=5)

        title = data.get("title", "")
        text = data.get("text", "")

        if text and len(text.split()) > 50:
            logger.info(f"  archive.ph extraction succeeded: {len(text.split())} words")
            return {"title": title, "text": text, "url": url}
        return {"title": "", "text": "", "url": url, "error": "archive.ph returned too little text"}

    except Exception as e:
        logger.warning(f"  archive.ph extraction failed: {e}")
        return {"title": "", "text": "", "url": url, "error": str(e)}


def _normalize_url(url: str) -> str:
    """Normalize a URL for matching against pulse.db.

    Strips fragment, trailing slash, and common tracking query params
    (utm_*, ref, partner, etc.) so a bookmarklet click matches the
    canonical URL the Pulse enricher saw.
    """
    if not url:
        return ""
    try:
        p = urlparse(url)
    except Exception:
        return url
    # Drop fragment + tracking params
    if p.query:
        keep = []
        for kv in p.query.split("&"):
            key = kv.split("=", 1)[0].lower()
            if key.startswith("utm_"):
                continue
            if key in {"ref", "partner", "source", "rss", "campaign_id",
                       "campaign", "fbclid", "gclid", "mc_cid", "mc_eid"}:
                continue
            keep.append(kv)
        query = "&".join(keep)
    else:
        query = ""
    path = (p.path or "").rstrip("/")
    return urlunparse((p.scheme, p.netloc, path, "", query, ""))


def _lookup_enriched_body(url: str) -> dict | None:
    """Look up the URL in pulse.db and return enriched body + metadata if available.

    Matches on normalized URL. Returns None if no usable enriched body found.
    """
    if not PULSE_DB_PATH.exists():
        return None
    if not url:
        return None
    normalized = _normalize_url(url)
    if not normalized:
        return None
    try:
        conn = sqlite3.connect(f"file:{PULSE_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        logger.warning(f"  pulse.db open failed: {e}")
        return None
    try:
        # Try exact URL first, then normalized variants. Pulse stores some
        # URLs with redirect prefixes (paragraph.com, mail.google.com), so
        # also match by suffix on the canonical URL.
        rows = conn.execute(
            "SELECT id, url, title, author, body, source, collected_at "
            "FROM items WHERE url = ? OR url = ? ORDER BY length(body) DESC LIMIT 5",
            (url, normalized),
        ).fetchall()
        if not rows:
            # Fallback: substring match on the normalized URL within the
            # stored URL (catches paragraph.com / Gmail tracker redirects
            # whose `?url=<encoded>` parameter contains the real link).
            from urllib.parse import quote
            rows = conn.execute(
                "SELECT id, url, title, author, body, source, collected_at "
                "FROM items WHERE url LIKE ? OR url LIKE ? "
                "ORDER BY length(body) DESC LIMIT 5",
                (f"%{normalized}%", f"%{quote(normalized, safe='')}%"),
            ).fetchall()
        for r in rows:
            body = r["body"] or ""
            if len(body) >= MIN_ENRICHED_CHARS:
                logger.info(
                    f"  Pulse enriched body found: id={r['id']} "
                    f"src={r['source']} body={len(body)}c "
                    f"({len(body.split())} words)"
                )
                return {
                    "title": r["title"] or "",
                    "text": body,
                    "url": url,
                    "_pulse_item_id": r["id"],
                    "_pulse_source": r["source"],
                }
        return None
    except Exception as e:
        logger.warning(f"  pulse.db lookup failed: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def process_all():
    """Process all pending queue items."""
    pending = get_pending()
    if not pending:
        logger.info("No pending items in queue")
        return

    logger.info(f"Processing {len(pending)} pending items")
    processed = 0

    for item in pending:
        item_id = item["id"]
        url = item["url"]
        logger.info(f"Processing [{item_id}]: {url[:80]}")

        try:
            # Check if text was already provided (e.g., from email body)
            text = item.get("text_content", "").strip()
            title = item.get("title", "") or "Untitled"

            if text and len(text) > 100:
                logger.info(f"  Using pre-extracted text: '{title[:50]}' ({len(text.split())} words)")
            else:
                # Step 1: check pulse.db for an already-enriched body. Pulse's
                # enricher runs Browserbase with paywall cookies + archive.ph
                # fallback, so for any URL Pulse has already seen, its body
                # is more reliable than re-running our own HTTP / Playwright
                # / Chrome-CDP / archive.ph chain.
                extracted = _lookup_enriched_body(url) or {}
                if extracted.get("text"):
                    title = extracted.get("title") or title
                    text = extracted["text"]
                    logger.info(
                        f"  Using Pulse-enriched body: '{title[:50]}' "
                        f"({len(text.split())} words)"
                    )
                else:
                    # Step 2: Browserbase — Pulse's primary enrichment path.
                    # Hosted Chrome with residential proxies, stealth posture,
                    # and persistent paywall cookies. High fidelity on
                    # WSJ/FT/NYT/Bloomberg/Atlantic where local Playwright
                    # trips bot detection. Already has an archive.ph
                    # fallback wired into the same session.
                    logger.info(f"  No Pulse-enriched body — trying Browserbase…")
                    extracted = _extract_via_browserbase(url)
                # Minimum word count to consider extraction successful
                MIN_WORDS = 300

                if extracted.get("error") or len(extracted.get("text", "").split()) < MIN_WORDS:
                    # Step 3: cheap HTTP extraction — works on non-paywalled sites
                    # and is essentially free, so worth trying before the slower
                    # local-Chrome fallbacks.
                    logger.info(f"  Browserbase insufficient ({len(extracted.get('text', '').split())} words), trying HTTP…")
                    extracted = extract_from_url(url)

                if extracted.get("error") or len(extracted.get("text", "").split()) < MIN_WORDS:
                    # Fallback 1: playwright + Chrome cookies (handles paywalled sites)
                    logger.info(f"  HTTP extraction insufficient ({len(extracted.get('text', '').split())} words), trying playwright...")
                    pw_text = _extract_via_playwright(url)
                    if pw_text and len(pw_text.get("text", "").split()) >= MIN_WORDS:
                        extracted = pw_text
                        logger.info(f"  Playwright extraction succeeded: {len(extracted['text'].split())} words")
                    else:
                        # Fallback 2: Chrome CDP (if running with --remote-debugging-port=9222)
                        pw_words = len(pw_text.get("text", "").split()) if pw_text else 0
                        logger.info(f"  Playwright insufficient ({pw_words} words), trying Chrome CDP...")
                        chrome_text = _extract_via_chrome(url)
                        if chrome_text and len(chrome_text.get("text", "").split()) >= MIN_WORDS:
                            extracted = chrome_text
                            logger.info(f"  Chrome CDP succeeded: {len(extracted['text'].split())} words")
                        else:
                            # Fallback 3: archive.ph
                            chrome_words = len(chrome_text.get("text", "").split()) if chrome_text else 0
                            logger.info(f"  Chrome CDP insufficient ({chrome_words} words), trying archive.ph...")
                            archive_text = _extract_via_archive(url)
                            if archive_text and len(archive_text.get("text", "").split()) >= MIN_WORDS:
                                extracted = archive_text
                                logger.info(f"  archive.ph succeeded: {len(extracted['text'].split())} words")
                            else:
                                mark_error(item_id, extracted.get("error", "No text extracted"))
                                logger.warning(f"  All extraction methods failed: {extracted.get('error', 'empty')}")
                                continue
                title = extracted["title"] or title
                text = extracted["text"]
                logger.info(f"  Extracted: '{title[:50]}' ({len(text.split())} words)")

            # Use Haiku to clean the text and extract metadata in one pass
            cleaned = _clean_and_extract(text, title_hint=title)
            title = cleaned.get("title") or title
            author_from_text = cleaned.get("author", "")
            publication = cleaned.get("publication", "")

            # Infer publication from URL if Haiku couldn't find it
            if not publication and url:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower().replace("www.", "")
                DOMAIN_TO_PUB = {
                    "ft.com": "The Financial Times",
                    "nytimes.com": "The New York Times",
                    "wsj.com": "The Wall Street Journal",
                    "economist.com": "The Economist",
                    "newyorker.com": "The New Yorker",
                    "washingtonpost.com": "The Washington Post",
                    "bloomberg.com": "Bloomberg",
                    "theatlantic.com": "The Atlantic",
                    "vox.com": "Vox",
                    "theverge.com": "The Verge",
                    "wired.com": "Wired",
                    "reuters.com": "Reuters",
                    "theguardian.com": "The Guardian",
                    "latimes.com": "The Los Angeles Times",
                    "politico.com": "Politico",
                    "axios.com": "Axios",
                    "semafor.com": "Semafor",
                    "paulgraham.com": "Paul Graham",
                    "archive.ph": "",  # don't attribute to archive
                }
                publication = DOMAIN_TO_PUB.get(domain, "")

            if publication:
                author_from_text = f"{author_from_text} from {publication}" if author_from_text else publication
            body_text_raw = cleaned.get("body", text)

            # Generate a spoken intro with Claude
            intro = _generate_intro(title, body_text_raw, author_from_text)

            # Mark the split between intro and body so TTS can insert silence
            full_text = f"{intro}\n\n===INTRO_END===\n\n{body_text_raw}"

            # Generate audio
            # Rotate voices for variety
            VOICES = ["nova", "echo"]
            voice = VOICES[item_id % len(VOICES)]
            audio_path, duration = synthesize(full_text, title, item_id, voice=voice)
            logger.info(f"  Audio: {audio_path} (~{duration}s)")

            # Mark done
            mark_done(item_id, audio_path, duration, text)
            processed += 1

        except Exception as e:
            mark_error(item_id, str(e))
            logger.error(f"  Failed: {e}")

    # Regenerate RSS feed
    if processed > 0:
        feed_path = generate_feed()
        logger.info(f"RSS feed updated: {feed_path} ({processed} new items)")
    else:
        logger.info("No items processed successfully")


if __name__ == "__main__":
    process_all()
