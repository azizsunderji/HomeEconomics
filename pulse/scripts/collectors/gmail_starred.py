"""Fetch recent starred Gmail emails with AI summaries.

Returns the 3 most recent starred emails with Haiku-generated summaries
for display in the daily briefing. Uses the same OAuth pattern as gmail.py.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import anthropic
import httpx

from collectors.gmail import _get_all_access_tokens

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def get_starred_emails(max_results: int = 3) -> list[dict]:
    """Fetch up to 3 starred Gmail messages and summarize each with Haiku.

    Returns list of dicts: [{"subject", "from", "date", "summary", "url"}]
    """
    access_tokens = _get_all_access_tokens()
    if not access_tokens:
        logger.warning("No Gmail access tokens — skipping starred emails")
        return []

    # Use the first account
    access_token = access_tokens[0]
    headers = {"Authorization": f"Bearer {access_token}"}

    # Fetch starred messages
    try:
        resp = httpx.get(
            f"{GMAIL_API}/messages",
            headers=headers,
            params={"q": "is:starred", "maxResults": max_results},
            timeout=30,
        )
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
    except Exception as e:
        logger.error(f"Failed to list starred messages: {e}")
        return []

    if not messages:
        logger.info("No starred emails found")
        return []

    results = []
    for msg_ref in messages[:max_results]:
        try:
            msg_resp = httpx.get(
                f"{GMAIL_API}/messages/{msg_ref['id']}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                timeout=15,
            )
            msg_resp.raise_for_status()
            msg = msg_resp.json()

            msg_headers = msg.get("payload", {}).get("headers", [])
            subject = _extract_header(msg_headers, "Subject")
            sender = _extract_header(msg_headers, "From")
            date_str = _extract_header(msg_headers, "Date")
            snippet = msg.get("snippet", "")
            message_id = msg_ref["id"]

            gmail_url = f"https://mail.google.com/mail/u/0/#inbox/{message_id}"

            results.append({
                "subject": subject,
                "from": sender,
                "date": date_str,
                "snippet": snippet,
                "url": gmail_url,
            })
        except Exception as e:
            logger.warning(f"Error fetching starred message {msg_ref['id']}: {e}")
            continue

    if not results:
        return []

    # Summarize each email with Haiku
    try:
        client = anthropic.Anthropic()
        for item in results:
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                    messages=[{"role": "user", "content": (
                        f"Summarize this email in 1-2 sentences. Be concise and informative.\n\n"
                        f"Subject: {item['subject']}\n"
                        f"From: {item['from']}\n"
                        f"Snippet: {item['snippet']}"
                    )}],
                )
                item["summary"] = resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"Haiku summary failed for '{item['subject'][:40]}': {e}")
                item["summary"] = item["snippet"]
    except Exception as e:
        logger.warning(f"Anthropic client init failed: {e}")
        for item in results:
            item["summary"] = item["snippet"]

    # Clean up — remove snippet from final output
    for item in results:
        item.pop("snippet", None)

    logger.info(f"Starred emails: {len(results)} fetched and summarized")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    starred = get_starred_emails()
    for s in starred:
        print(f"  {s['from'][:40]}: {s['subject'][:50]}")
        print(f"    {s['summary'][:100]}")
    print(f"\nTotal: {len(starred)} starred emails")
