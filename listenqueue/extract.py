"""Extract readable text from URLs and emails."""

import logging
import re
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Simple readability-style extraction using Mozilla's Readability via a
# lightweight approach: fetch HTML, strip tags, clean up whitespace.
# For paywalled sites, we fall back to the raw text we can get.

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities, keeping paragraph structure."""
    # Remove script/style blocks
    html = re.sub(r'<(script|style|nav|footer|header)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Convert block elements to newlines
    html = re.sub(r'</(p|div|h[1-6]|li|tr|br|blockquote)>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    # Strip remaining tags
    html = re.sub(r'<[^>]+>', '', html)
    # Decode common entities
    for entity, char in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' '),
                         ('&mdash;', '—'), ('&ndash;', '–'), ('&rsquo;', "'"),
                         ('&lsquo;', "'"), ('&rdquo;', '"'), ('&ldquo;', '"')]:
        html = html.replace(entity, char)
    # Collapse whitespace but preserve paragraph breaks
    lines = [' '.join(line.split()) for line in html.split('\n')]
    text = '\n'.join(line for line in lines if line.strip())
    return text.strip()


def _extract_article_text(html: str) -> str:
    """Try to extract the main article content from HTML."""
    # Look for common article containers
    for pattern in [
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*article[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*story-body[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*post-content[^"]*"[^>]*>(.*?)</div>',
    ]:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _strip_html(match.group(1))
    # Fallback: strip the whole page
    return _strip_html(html)


def _extract_title(html: str) -> str:
    """Extract title from HTML."""
    # Try og:title first
    match = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html, re.IGNORECASE)
    if match:
        return match.group(1)
    # Fall back to <title>
    match = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL | re.IGNORECASE)
    if match:
        return _strip_html(match.group(1)).strip()
    return ""


def extract_from_url(url: str) -> dict:
    """Extract title and text content from a URL.

    Returns {"title": str, "text": str, "url": str}.
    """
    try:
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        html = resp.text

        title = _extract_title(html)
        text = _extract_article_text(html)

        if len(text) < 100:
            # Article extraction failed, try full page
            text = _strip_html(html)

        # Remove common web junk
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            ll = line.strip().lower()
            if any(j in ll for j in ['share this', 'subscribe', 'sign up', 'newsletter',
                                      'cookie', 'privacy policy', 'terms of service',
                                      'advertisement', 'skip to content', 'read more at']):
                continue
            if ll.startswith('http') and len(ll.split()) == 1:
                continue
            if ll in ('share', 'like', 'comment', 'restack', 'tweet', 'save'):
                continue
            cleaned.append(line)
        text = '\n'.join(cleaned).strip()

        return {"title": title, "text": text, "url": url}

    except Exception as e:
        logger.error(f"Failed to extract from {url}: {e}")
        return {"title": "", "text": "", "url": url, "error": str(e)}


def extract_from_email(subject: str, body: str, sender: str = "") -> dict:
    """Extract text content from an email.

    Returns {"title": str, "text": str}.
    """
    # Clean up HTML email body if needed
    if '<' in body and '>' in body:
        text = _strip_html(body)
    else:
        text = body

    intro = f"Email from {sender}. Subject: {subject}.\n\n" if sender else f"{subject}.\n\n"
    return {"title": subject, "text": intro + text}
