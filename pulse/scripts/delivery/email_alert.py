"""Lightweight email alert via Resend — for pipeline health issues.

Separate from email_briefing.py: that renders the full daily briefing; this
sends short plain-text alerts when something upstream is broken (Gmail token
expiring, Twitter collection failing, etc.).
"""

from __future__ import annotations

import logging
import os

import httpx

from config import EMAIL_TO, EMAIL_FROM

logger = logging.getLogger(__name__)


def send_alert_email(subject: str, body: str) -> bool:
    """Send a short plain-text alert via Resend. Returns True if sent."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.warning("RESEND_API_KEY not set — cannot send alert email")
        return False

    # Escape newlines into <br> so plain body still reads in HTML
    html_body = body.replace("\n", "<br>")
    payload = {
        "from": EMAIL_FROM,
        "to": [EMAIL_TO],
        "subject": f"[Pulse alert] {subject}"[:200],
        "html": f"<pre style='font-family: -apple-system, monospace; white-space: pre-wrap;'>{html_body}</pre>",
    }
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Alert email sent: {subject[:60]}")
        return True
    except Exception as e:
        logger.error(f"Alert email failed: {e}")
        return False
