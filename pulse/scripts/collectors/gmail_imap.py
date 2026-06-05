"""IMAP collector for the personal Gmail inbox (aziz.sunderji@gmail.com).

The Workspace inbox (aziz@home-economics.us) uses OAuth via gmail.py — the
Internal OAuth app type works for that account.

The personal Gmail account can't use OAuth from this codebase: the Internal
OAuth app type only authorizes Workspace accounts. (Trying to refresh the
personal token throws HTTP 400 / invalid_grant.) So we use IMAP with an
app-specific password instead.

Environment:
    GMAIL_IMAP_USER     — full email address, e.g. aziz.sunderji@gmail.com
    GMAIL_IMAP_PASSWORD — 16-char app password from
                          https://myaccount.google.com/apppasswords

Returned items match the schema produced by gmail.py so they store + flow
through the rest of the pipeline identically (source='gmail' for normal
mail, 'substack' for Substack newsletters; the same junk/allowlist
filters apply).
"""

from __future__ import annotations

import base64
import email
import imaplib
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

# Make sibling imports work whether this is called directly or via run_pipeline
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from collectors import PulseItem
from collectors.gmail import (
    _extract_primary_url,
    _thread_id_to_gmail_url_for_account,
)
from config import (
    GMAIL_AI_HEADLINE_SENDERS,
    GMAIL_JUNK_SENDER_PATTERNS,
    GMAIL_JUNK_TITLE_PATTERNS,
    GMAIL_NEWSLETTER_SENDERS,
    INSTITUTIONAL_SENDER_ALLOWLIST,
)

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


# ── Header decoding ─────────────────────────────────────────────────────

def _decode_header(value: str) -> str:
    """Decode an RFC 2047 encoded-word header (e.g. =?utf-8?B?...?=)."""
    if not value:
        return ""
    parts = []
    try:
        for chunk, enc in decode_header(value):
            if isinstance(chunk, bytes):
                try:
                    parts.append(chunk.decode(enc or "utf-8", errors="replace"))
                except Exception:
                    parts.append(chunk.decode("utf-8", errors="replace"))
            else:
                parts.append(chunk)
    except Exception:
        return value
    return "".join(parts).strip()


# ── Body extraction (IMAP returns full RFC822, so we walk the parsed
#    email.message.Message object — different code path than gmail.py
#    which walks a Gmail-API JSON payload). ────────────────────────────

def _extract_text_body(msg: email.message.Message) -> str:
    """Plain-text body. Prefers text/plain over stripped text/html."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
                    except Exception:
                        return payload.decode("utf-8", errors="replace")
        # Fallback to first text/html stripped of tags
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                    except Exception:
                        html = payload.decode("utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
        return ""
    # Non-multipart
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    try:
        charset = msg.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")
    except Exception:
        text = payload.decode("utf-8", errors="replace")
    if msg.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return text


def _extract_html_body_imap(msg: email.message.Message) -> str:
    """Raw HTML body if present (used for URL extraction)."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                try:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
                except Exception:
                    return payload.decode("utf-8", errors="replace")
        return ""
    if msg.get_content_type() == "text/html":
        payload = msg.get_payload(decode=True)
        if payload:
            try:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
            except Exception:
                return payload.decode("utf-8", errors="replace")
    return ""


# ── Filter (mirrors gmail.py's filter so personal-Gmail items behave
#    identically to Workspace items). ──────────────────────────────────

def _should_drop(sender: str, subject: str) -> bool:
    sender_lower = (sender or "").lower()
    title_lower = (subject or "").lower()

    # Skip Pulse's own emails
    if "onboarding@resend.dev" in sender_lower or "pulse@home-economics" in sender_lower:
        return True
    # Skip forwards/replies of Pulse
    if (
        "fwd: pulse:" in title_lower
        or "fw: pulse:" in title_lower
        or "re: pulse:" in title_lower
    ):
        return True
    # Skip Aziz's own outbound
    if "aziz@home-economics.us" in sender_lower or "aziz.sunderji@gmail.com" in sender_lower:
        return True

    # Allowlist wins — if matched, keep
    if any(p in sender_lower for p in INSTITUTIONAL_SENDER_ALLOWLIST):
        return False
    if any(p in sender_lower for p in GMAIL_NEWSLETTER_SENDERS):
        return False
    if any(p in sender_lower for p in GMAIL_AI_HEADLINE_SENDERS):
        return False

    # Junk filters (only when not allowlisted)
    if any(p in sender_lower for p in GMAIL_JUNK_SENDER_PATTERNS):
        return True
    if any(p in title_lower for p in GMAIL_JUNK_TITLE_PATTERNS):
        return True
    return False


# ── Main collector ──────────────────────────────────────────────────────

def collect_personal_gmail_imap(
    hours_back: int = 24, max_results: int = 250
) -> list[PulseItem]:
    """Pull items from the personal Gmail inbox via IMAP.

    Returns an empty list (no error) when GMAIL_IMAP_PASSWORD isn't
    configured — lets the rest of the pipeline keep running.
    """
    user = os.environ.get("GMAIL_IMAP_USER", "")
    pw = os.environ.get("GMAIL_IMAP_PASSWORD", "")
    if not user or not pw:
        logger.warning(
            "GMAIL_IMAP_USER / GMAIL_IMAP_PASSWORD not set — skipping personal Gmail"
        )
        return []

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    except Exception as e:
        logger.error(f"IMAP connect to {IMAP_HOST}:{IMAP_PORT} failed: {e}")
        return []

    try:
        try:
            mail.login(user, pw)
        except imaplib.IMAP4.error as e:
            logger.error(
                f"IMAP login failed for {user}: {e}. "
                f"Check the app password at https://myaccount.google.com/apppasswords"
            )
            return []

        # Read-only so we don't mark messages as read
        status, _ = mail.select("INBOX", readonly=True)
        if status != "OK":
            logger.error(f"IMAP SELECT INBOX failed: {status}")
            return []

        since_dt = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        since_str = since_dt.strftime("%d-%b-%Y")
        status, data = mail.search(None, f'(SINCE "{since_str}")')
        if status != "OK" or not data or not data[0]:
            logger.info(f"IMAP search returned no messages since {since_str}")
            return []

        msg_ids = data[0].split()
        # Take the most recent up to the cap
        msg_ids = msg_ids[-max_results:]
        logger.info(
            f"IMAP {user}: {len(msg_ids)} candidate messages since {since_str}"
        )

        items: list[PulseItem] = []
        dropped = 0

        for mid in msg_ids:
            try:
                status, raw_data = mail.fetch(mid, "(RFC822)")
                if status != "OK" or not raw_data or not raw_data[0]:
                    continue
                raw_bytes = raw_data[0][1]
                if not raw_bytes or not isinstance(raw_bytes, (bytes, bytearray)):
                    continue
                msg = email.message_from_bytes(raw_bytes)

                subject = _decode_header(msg.get("Subject", ""))
                sender = _decode_header(msg.get("From", ""))
                date_str = msg.get("Date", "")
                message_id = msg.get("Message-ID", "").strip("<>")

                if _should_drop(sender, subject):
                    dropped += 1
                    continue

                # Parse published date
                published: Optional[datetime] = None
                if date_str:
                    try:
                        published = parsedate_to_datetime(date_str)
                        if published and published.tzinfo is None:
                            published = published.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass

                body = _extract_text_body(msg)[:5000]
                html_body = _extract_html_body_imap(msg)
                article_url = _extract_primary_url(html_body, body)
                gmail_url = _thread_id_to_gmail_url_for_account(
                    message_id or mid.decode("ascii", errors="replace"),
                    user,
                )

                # Substack detection (same as OAuth collector)
                sender_lower = sender.lower()
                is_substack = "substack.com" in sender_lower
                author_name = sender
                if is_substack and sender:
                    m = re.match(r'"?([^"<]+)"?\s*<', sender)
                    if m:
                        author_name = m.group(1).strip()

                # source_id: stable per-message ID prefixed so it can't
                # collide with the OAuth collector's source_ids.
                src_id = f"imap_{user.split('@')[0]}_{message_id or mid.decode('ascii','replace')}"

                items.append(PulseItem(
                    source="substack" if is_substack else "gmail",
                    source_id=src_id,
                    url=article_url or gmail_url,
                    title=subject,
                    body=body,
                    author=author_name,
                    published_at=published,
                    platform_tags=(
                        ["newsletter_substack"] if is_substack
                        else ["email", "newsletter"]
                    ),
                    feed_priority="newsletter" if is_substack else "",
                    engagement_raw={"gmail_url": gmail_url, "imap_account": user},
                ))
            except Exception as e:
                logger.warning(f"IMAP fetch failed for mid={mid!r}: {e}")
                continue

        logger.info(
            f"IMAP {user}: kept {len(items)} items, dropped {dropped} by filters"
        )
        return items
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass
