"""Gmail inbox collector.

Scans inbox for newsletters, research reports, and data alerts.
Requires GMAIL_CREDENTIALS (base64-encoded OAuth JSON) and GMAIL_TOKEN env vars.
Uses the Gmail API with read-only scope.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from collectors import PulseItem
from config import GMAIL_SENDER_WHITELIST, GMAIL_LABELS, GMAIL_MAX_RESULTS

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _refresh_access_token(token_data: dict) -> Optional[str]:
    """Exchange a refresh token for an access token."""
    refresh_token = token_data.get("refresh_token")
    client_id = token_data.get("client_id", "")
    client_secret = token_data.get("client_secret", "")

    if refresh_token and client_id and client_secret:
        resp = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("access_token")
        logger.error(f"Gmail token refresh failed: {resp.status_code}")
        return None
    return token_data.get("access_token")


def _get_all_access_tokens() -> list[str]:
    """Get access tokens for all configured Gmail accounts.

    Supports GMAIL_TOKEN (single account) and GMAIL_TOKENS (JSON array of accounts).
    """
    tokens = []

    # Single account
    single = os.environ.get("GMAIL_TOKEN", "")
    if single:
        try:
            data = json.loads(single)
        except json.JSONDecodeError:
            try:
                data = json.loads(base64.b64decode(single))
            except Exception:
                data = None
        if data:
            tok = _refresh_access_token(data)
            if tok:
                tokens.append(tok)

    # Multiple accounts
    multi = os.environ.get("GMAIL_TOKENS", "")
    if multi:
        try:
            accounts = json.loads(multi)
        except json.JSONDecodeError:
            try:
                accounts = json.loads(base64.b64decode(multi))
            except Exception:
                accounts = []
        for acct in accounts:
            tok = _refresh_access_token(acct)
            if tok:
                tokens.append(tok)

    if not tokens:
        logger.warning("No GMAIL_TOKEN or GMAIL_TOKENS set — skipping Gmail collection")

    return tokens


def _list_messages(
    access_token: str,
    query: str,
    max_results: int = 50,
) -> list[dict]:
    """List messages matching a Gmail query."""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"q": query, "maxResults": max_results}
    resp = httpx.get(f"{GMAIL_API}/messages", headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("messages", [])


def _get_message(access_token: str, msg_id: str) -> dict:
    """Get full message by ID."""
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"format": "full"}
    resp = httpx.get(f"{GMAIL_API}/messages/{msg_id}", headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_html_body(payload: dict) -> str:
    """Extract raw HTML body from Gmail message payload (for URL extraction)."""
    body_data = payload.get("body", {}).get("data", "")
    if body_data:
        try:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        except Exception:
            pass

    parts = payload.get("parts", [])
    for part in parts:
        mime = part.get("mimeType", "")
        if mime == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                except Exception:
                    pass
        if "parts" in part:
            result = _extract_html_body(part)
            if result:
                return result
    return ""


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    # Try direct body
    body_data = payload.get("body", {}).get("data", "")
    if body_data:
        try:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        except Exception:
            pass

    # Try parts
    parts = payload.get("parts", [])
    for part in parts:
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                except Exception:
                    pass
        elif mime == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                try:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    # Strip HTML tags
                    return re.sub(r"<[^>]+>", " ", html).strip()
                except Exception:
                    pass
        # Recurse into multipart
        if "parts" in part:
            result = _extract_body(part)
            if result:
                return result

    return ""


# Domains to skip when extracting article URLs from emails
_SKIP_URL_PATTERNS = [
    r"unsubscribe", r"manage.preferences", r"email-preferences",
    r"list-unsubscribe", r"mailchimp\.com", r"sendgrid\.net",
    r"tracking\.", r"click\.", r"open\.", r"pixel\.",
    r"facebook\.com", r"twitter\.com", r"linkedin\.com/share",
    r"instagram\.com", r"youtube\.com", r"mailto:",
    r"google\.com/maps", r"maps\.google", r"apple\.com/maps",
    r"mail\.google\.com", r"calendar\.google",
    r"play\.google\.com", r"apps\.apple\.com",
    r"zoom\.us/j/", r"teams\.microsoft\.com",
    r"\.(png|jpg|jpeg|gif|svg|ico|css|js|woff|woff2)(\?|$)",
    r"fonts\.", r"cdnjs\.", r"cdn\.", r"cdn-cgi/",
    r"beehiiv\.com/cdn", r"media\.", r"images\.",
]
_SKIP_URL_RE = re.compile("|".join(_SKIP_URL_PATTERNS), re.IGNORECASE)

# Domains that signal high-value article links
_GOOD_DOMAINS = [
    "substack.com", "apricitas.io", "theovershoot.co", "noahpinion.blog",
    "calculatedrisk.substack.com", "aei.org", "brookings.edu", "nber.org",
    "federalreserve.gov", "bls.gov", "census.gov", "freddiemac.com",
    "fanniemae.com", "nar.realtor", "redfin.com", "zillow.com",
    "goldmansachs.com", "jpmorgan.com", "gs.com",
    "bloomberg.com", "wsj.com", "nytimes.com", "ft.com",
    "reuters.com", "cnbc.com", "axios.com",
    "construction-physics.com", "globalhousingwatch.org",
    "resiclub.com",
]


def _extract_primary_url(html_body: str, plain_body: str) -> str:
    """Extract the most relevant article URL from an email body.

    Strategy:
    1. Pull all href URLs from HTML body
    2. Pull bare URLs from plain text body
    3. Filter out junk (unsubscribe, tracking, social, images)
    4. Score remaining by domain quality
    5. Return the best one, or empty string
    """
    urls = []

    # Extract from HTML href attributes
    if html_body:
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_body, re.IGNORECASE)
        urls.extend(hrefs)

    # Extract from plain text (bare URLs)
    if plain_body:
        bare = re.findall(r'https?://[^\s<>\[\]"\')\]]+', plain_body)
        urls.extend(bare)

    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for u in urls:
        u = u.strip().rstrip(".,;:)")
        if u not in seen and len(u) > 15:
            seen.add(u)
            unique_urls.append(u)

    # Filter out junk
    filtered = [u for u in unique_urls if not _SKIP_URL_RE.search(u)]

    if not filtered:
        return ""

    # Score by domain quality — good domains get priority
    def _score(url: str) -> int:
        for domain in _GOOD_DOMAINS:
            if domain in url.lower():
                return 2
        # Substack redirect links are common in newsletter emails
        if "substack.com/redirect" in url:
            return 1
        return 0

    # Sort by score (descending), then by position (first = likely most prominent)
    scored = sorted(enumerate(filtered), key=lambda x: (-_score(x[1]), x[0]))

    return scored[0][1] if scored else ""


def collect(
    sender_whitelist: list[str] | None = None,
    max_results: int = GMAIL_MAX_RESULTS,
    hours_back: int = 24,
) -> list[PulseItem]:
    """Collect recent emails from whitelisted senders.

    Returns list of PulseItem objects.
    """
    access_tokens = _get_all_access_tokens()
    if not access_tokens:
        return []

    # Build Gmail search query — pull everything, let the classifier decide relevance
    after_date = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).strftime("%Y/%m/%d")
    query = f"after:{after_date} in:anywhere -category:social -category:promotions -category:forums"

    items = []

    for acct_idx, access_token in enumerate(access_tokens):
        try:
            messages = _list_messages(access_token, query, max_results=max_results)
            logger.info(f"Gmail account {acct_idx + 1}: found {len(messages)} messages")

            for msg_ref in messages:
                try:
                    msg = _get_message(access_token, msg_ref["id"])
                    payload = msg.get("payload", {})
                    headers = payload.get("headers", [])

                    subject = _extract_header(headers, "Subject")
                    sender = _extract_header(headers, "From")
                    date_str = _extract_header(headers, "Date")

                    # Parse date
                    published = None
                    if date_str:
                        from email.utils import parsedate_to_datetime
                        try:
                            published = parsedate_to_datetime(date_str)
                            if published.tzinfo is None:
                                published = published.replace(tzinfo=timezone.utc)
                        except Exception:
                            pass

                    body = _extract_body(payload)[:5000]

                    # Extract the primary article URL from the email
                    html_body = _extract_html_body(payload)
                    article_url = _extract_primary_url(html_body, body)
                    gmail_url = f"https://mail.google.com/mail/#all/{msg_ref['id']}"

                    item = PulseItem(
                        source="gmail",
                        source_id=f"gmail_{acct_idx}_{msg_ref['id']}",
                        url=article_url or gmail_url,
                        title=subject,
                        body=body,
                        author=sender,
                        published_at=published,
                        platform_tags=["email", "newsletter"],
                        engagement_raw={"gmail_url": gmail_url},
                    )
                    items.append(item)

                except Exception as e:
                    logger.warning(f"Error processing Gmail message {msg_ref['id']}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error listing Gmail messages (account {acct_idx + 1}): {e}")

    logger.info(f"Gmail total: {len(items)} items from {len(access_tokens)} accounts")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = collect()
    for item in results[:10]:
        print(f"{item.author[:40]:>40}: {item.title[:50]}")
    print(f"\nTotal: {len(results)} items")
