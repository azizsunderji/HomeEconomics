"""Sending: test copies to the owner, and the noon send (shadow or
subscribers). Subscriber mode is a copy of v4b_runner.send_lunch_to_subscribers
so the two stay behaviourally identical."""
from __future__ import annotations

import logging
import os
import time

import httpx

import paths
import render
from delivery.subscribers import get_subscribers, make_unsubscribe_url

logger = logging.getLogger("noon.sender")
RESEND_BATCH_LIMIT = 100


def _post_resend(api_key: str, url: str, payload) -> bool:
    last_error = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=30,
            )
            if resp.status_code in (200, 201):
                return True
            logger.warning(f"Resend {resp.status_code} on attempt {attempt + 1}/3: {resp.text[:300]}")
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code in (401, 403):
                break
        except Exception as e:  # noqa: BLE001
            last_error = f"{type(e).__name__}: {e}"
            logger.warning(f"Resend request failed on attempt {attempt + 1}/3: {last_error}")
        if attempt < 2:
            time.sleep(5 * (attempt + 1))
    logger.error(f"Resend failed after retries: {last_error}")
    return False


def _api_key() -> str:
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        raise RuntimeError("RESEND_API_KEY not set")
    return key


def send_test(draft: dict, tier: str, to: str | None = None) -> bool:
    """One copy of the chosen tier to the owner, subject marked as a test."""
    to = to or paths.OWNER_EMAIL
    html = render.preview(draft, tier)
    subject = f"[TEST – {tier.upper()}] {render.subject(draft['date'])}"
    ok = _post_resend(_api_key(), "https://api.resend.com/emails",
                      {"from": render.EMAIL_FROM, "to": [to], "subject": subject, "html": html})
    logger.info(f"test send ({tier}) to {to}: {'ok' if ok else 'FAILED'}")
    return ok


def send_final(draft: dict) -> tuple[bool, str]:
    """The noon send. Returns (ok, log line). Mode from NOON_SEND_MODE."""
    if paths.SEND_MODE == "subscribers":
        return _send_subscribers(draft)
    tier = paths.SHADOW_TIER if paths.SHADOW_TIER in ("free", "premium") else "free"
    html = render.preview(draft, tier)
    ok = _post_resend(_api_key(), "https://api.resend.com/emails",
                      {"from": render.EMAIL_FROM, "to": [paths.OWNER_EMAIL],
                       "subject": render.subject(draft["date"]), "html": html})
    line = f"shadow send ({tier}) to {paths.OWNER_EMAIL}: {'ok' if ok else 'FAILED'}"
    logger.info(line)
    return ok, line


def _send_subscribers(draft: dict) -> tuple[bool, str]:
    api_key = _api_key()
    subscribers = get_subscribers()
    premium_html, free_html, _top = render.render_variants(draft)
    subject = render.subject(draft["date"])
    emails: list[dict] = []
    n_premium = n_free = 0
    for sub in subscribers:
        premium = bool(sub.get("premium"))
        unsub_url = make_unsubscribe_url(sub["user_id"]) if sub.get("user_id") else None
        if sub.get("user_id") and not unsub_url:
            logger.warning(f"no unsubscribe URL for user {sub['user_id']} (PULSE_UNSUB_SECRET missing?)")
        msg = {"from": render.EMAIL_FROM, "to": [sub["email"]], "subject": subject,
               "html": render.with_footer(premium_html if premium else free_html, unsub_url)}
        if unsub_url:
            msg["headers"] = {"List-Unsubscribe": f"<{unsub_url}>",
                              "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if premium:
            n_premium += 1
        else:
            n_free += 1
        emails.append(msg)
    chunks = [emails[i:i + RESEND_BATCH_LIMIT] for i in range(0, len(emails), RESEND_BATCH_LIMIT)]
    sent = 0
    for chunk in chunks:
        if _post_resend(api_key, "https://api.resend.com/emails/batch", chunk):
            sent += len(chunk)
        else:
            logger.error(f"batch of {len(chunk)} failed")
    line = f"subscriber send: {sent}/{len(emails)} ({n_premium} premium, {n_free} free)"
    logger.info(line)
    return sent > 0, line


def send_notification(date: str, magic_url: str, shown: int, total: int) -> bool:
    """'Your draft is ready' email to the owner with the one-tap edit link."""
    label = render.date_label(date)
    html = (
        '<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:17px;'
        'line-height:1.5;color:#3D3733;max-width:560px;margin:0 auto;padding:24px 16px;">'
        f'<p style="margin:0 0 16px 0;">The News at Noon draft for {label} is ready. '
        f'It has {total} themes; free readers currently get {shown}.</p>'
        f'<p style="margin:0 0 24px 0;"><a href="{magic_url}" style="display:inline-block;'
        'background:#0BB4FF;color:#ffffff;text-decoration:none;padding:12px 20px;'
        'font-size:16px;">Edit today&rsquo;s edition</a></p>'
        '<p style="margin:0;font-size:14px;color:#888888;">It sends at 12:15 ET whether or not you edit it. '
        'Open the editor and press Hold if it should not go out today.</p></div>'
    )
    ok = _post_resend(_api_key(), "https://api.resend.com/emails",
                      {"from": render.EMAIL_FROM, "to": [paths.OWNER_EMAIL],
                       "subject": f"Draft ready: {render.subject(date)}", "html": html})
    logger.info(f"draft-ready notification for {date}: {'ok' if ok else 'FAILED'}")
    return ok
