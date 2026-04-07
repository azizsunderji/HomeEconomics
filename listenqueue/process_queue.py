#!/usr/bin/env python3
"""Process the listen queue: extract text, generate audio, update RSS feed.

Run this on a schedule (e.g., every 30 min via cron or launchd) or on-demand.
"""

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add parent dir so we can import from the package
sys.path.insert(0, str(Path(__file__).parent))

from queue_db import get_pending, mark_done, mark_error
from extract import extract_from_url
from tts import synthesize
from feed_generator import generate_feed
from text_prep import prepare_for_tts

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

        # Wait for page to load
        time.sleep(10)

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
                # Extract text from URL
                extracted = extract_from_url(url)
                if extracted.get("error") or not extracted.get("text"):
                    # Fallback: try Chrome browser extraction for paywalled sites
                    logger.info(f"  HTTP extraction failed, trying Chrome...")
                    chrome_text = _extract_via_chrome(url)
                    if chrome_text and len(chrome_text.get("text", "")) > 100:
                        extracted = chrome_text
                        logger.info(f"  Chrome extraction succeeded: {len(extracted['text'].split())} words")
                    else:
                        mark_error(item_id, extracted.get("error", "No text extracted"))
                        logger.warning(f"  Extraction failed: {extracted.get('error', 'empty')}")
                        continue
                title = extracted["title"] or title
                text = extracted["text"]
                logger.info(f"  Extracted: '{title[:50]}' ({len(text.split())} words)")

            # Use Haiku to clean the text and extract metadata in one pass
            cleaned = _clean_and_extract(text, title_hint=title)
            title = cleaned.get("title") or title
            author_from_text = cleaned.get("author", "")
            if cleaned.get("publication"):
                author_from_text = f"{author_from_text} from {cleaned['publication']}" if author_from_text else cleaned["publication"]
            body_text_raw = cleaned.get("body", text)

            # Generate a spoken intro with Claude
            intro = _generate_intro(title, body_text_raw, author_from_text)

            # Mark the split between intro and body so TTS can insert silence
            full_text = f"{intro}\n\n===INTRO_END===\n\n{body_text_raw}"

            # Generate audio
            audio_path, duration = synthesize(full_text, title, item_id)
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
