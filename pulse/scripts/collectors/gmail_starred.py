"""Fetch random starred Gmail emails with AI summaries.

Returns 5 randomly selected starred emails with Haiku-generated summaries
for display in the daily briefing. Uses the same OAuth pattern as gmail.py.
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timezone

import anthropic
import httpx

from collectors.gmail import _get_all_access_tokens, _thread_id_to_gmail_url_for_account

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a header value by name."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def get_starred_emails(pick: int = 5, pool_size: int = 50) -> list[dict]:
    """Fetch starred Gmail messages and randomly select 5 to summarize.

    Fetches up to pool_size starred messages, randomly picks `pick` of them,
    then summarizes each with Haiku.

    Returns list of dicts: [{"subject", "from", "date", "summary", "url"}]
    """
    access_tokens = _get_all_access_tokens()
    if not access_tokens:
        logger.warning("No Gmail access tokens — skipping starred emails")
        return []

    # Collect starred messages from ALL accounts
    all_candidates = []  # list of (msg_ref, access_token, email_address)

    for access_token in access_tokens:
        acct_headers = {"Authorization": f"Bearer {access_token}"}

        # Get this account's email address for deep linking
        try:
            profile_resp = httpx.get(
                f"{GMAIL_API}/profile", headers=acct_headers, timeout=15,
            )
            profile_resp.raise_for_status()
            email_address = profile_resp.json().get("emailAddress", "")
        except Exception as e:
            logger.warning(f"Could not get profile for account: {e}")
            email_address = ""

        # Fetch starred messages for this account
        try:
            resp = httpx.get(
                f"{GMAIL_API}/messages",
                headers=acct_headers,
                params={"q": "is:starred", "maxResults": pool_size},
                timeout=30,
            )
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
        except Exception as e:
            logger.warning(f"Failed to list starred messages for {email_address}: {e}")
            continue

        for msg in messages:
            all_candidates.append((msg, access_token, email_address))

        logger.info(f"Starred emails from {email_address}: {len(messages)} found")

    if not all_candidates:
        logger.info("No starred emails found across any account")
        return []

    # Dedupe by threadId across all accounts
    seen_threads = set()
    unique_candidates = []
    for msg, token, email in all_candidates:
        tid = msg.get("threadId", msg["id"])
        if tid not in seen_threads:
            seen_threads.add(tid)
            unique_candidates.append((msg, token, email))

    # Randomly select from the deduplicated pool
    selected = random.sample(unique_candidates, min(pick, len(unique_candidates)))
    logger.info(f"Starred emails: picked {len(selected)} from {len(unique_candidates)} unique threads ({len(all_candidates)} total)")

    results = []
    for msg_ref, access_token, email_address in selected:
        try:
            acct_headers = {"Authorization": f"Bearer {access_token}"}
            msg_resp = httpx.get(
                f"{GMAIL_API}/messages/{msg_ref['id']}",
                headers=acct_headers,
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
            thread_id = msg.get("threadId", msg_ref.get("threadId", ""))

            # Build Gmail deep link with authuser for the correct account
            gmail_url = _thread_id_to_gmail_url_for_account(thread_id, email_address)

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
