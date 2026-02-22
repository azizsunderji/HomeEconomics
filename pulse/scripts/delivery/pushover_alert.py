"""Pushover push notifications for high-signal alerts.

Only fires when: convergence score 4+ platforms AND relevant data exists
AND story broke within 3 hours. Expected frequency: 1-2x per month.
"""

from __future__ import annotations

import logging
import os

import httpx

from config import CONVERGENCE_ALERT_THRESHOLD

logger = logging.getLogger(__name__)

PUSHOVER_API = "https://api.pushover.net/1/messages.json"


def send_alert(
    title: str,
    message: str,
    url: str = "",
    priority: int = 0,
) -> bool:
    """Send a Pushover push notification.

    Priority levels:
        -2: no notification/alert
        -1: quiet notification
         0: normal priority (default)
         1: high priority (bypass quiet hours)
         2: emergency (requires acknowledgement)

    Returns True if sent successfully.
    """
    token = os.environ.get("PUSHOVER_TOKEN", "")
    user = os.environ.get("PUSHOVER_USER", "")

    if not token or not user:
        logger.warning("PUSHOVER_TOKEN or PUSHOVER_USER not set — skipping alert")
        return False

    payload = {
        "token": token,
        "user": user,
        "title": title[:250],
        "message": message[:1024],
        "priority": priority,
        "sound": "pushover",
    }

    if url:
        payload["url"] = url[:512]
        payload["url_title"] = "View story"

    try:
        resp = httpx.post(PUSHOVER_API, data=payload, timeout=15)
        resp.raise_for_status()
        logger.info(f"Pushover alert sent: {title[:50]}")
        return True
    except Exception as e:
        logger.error(f"Pushover alert failed: {e}")
        return False


def check_and_alert(convergence_results: list[dict]) -> int:
    """Check convergence results for alert-worthy stories.

    Returns number of alerts sent.
    """
    alerts_sent = 0

    for result in convergence_results:
        if not result.get("is_alert_worthy", False):
            continue

        platforms = ", ".join(result.get("platforms", []))
        title = f"🔥 {result['label']}: {result['platform_count']} platforms"
        message = (
            f"Trending across {platforms}\n"
            f"{result['total_items']} items, avg relevance {result['avg_relevance']}\n"
            f"Convergence score: {result['convergence_score']:.1f}"
        )

        top_items = result.get("top_items", [])
        url = top_items[0].get("url", "") if top_items else ""

        if send_alert(title, message, url=url, priority=0):
            alerts_sent += 1

    return alerts_sent
