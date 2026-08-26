"""Clerk-backed subscriber list for the Pulse daily email.

Recipients are Clerk users (the auth provider for the Home Economics
portal) with `public_metadata.pulseNewsletter.subscribed === true`.
Premium recipients additionally have `public_metadata.tools.pulse ===
true` and receive the email with working external links; free
recipients get the upgrade-wall variant.

HARD SAFETY GUARANTEE: get_subscribers() can never raise and can never
return an empty list. On ANY failure (missing CLERK_SECRET_KEY, network
error, unexpected response schema, zero subscribed users) it logs
loudly and returns the single-recipient fallback

    [{"email": "aziz@home-economics.us", "user_id": None, "premium": True}]

so the daily send degrades to today's behavior instead of breaking.

Smoke test (prints redacted emails):

    CLERK_SECRET_KEY=sk_... python pulse/scripts/delivery/subscribers.py
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

CLERK_API_BASE = "https://api.clerk.com/v1"
CLERK_PAGE_SIZE = 100
CLERK_MAX_USERS = 10_000  # hard cap on pagination (safety against loops)

UNSUBSCRIBE_BASE = "https://homeeconomics.us/api/pulse/unsubscribe"

# The fallback recipient — today's single-recipient behavior. Returned
# (as a fresh copy) whenever the Clerk fetch fails in any way.
FALLBACK_SUBSCRIBER = {
    "email": "aziz@home-economics.us",
    "user_id": None,
    "premium": True,
}


def _fallback(reason: str) -> list[dict]:
    logger.error(
        "SUBSCRIBER FETCH FAILED — falling back to single recipient "
        f"aziz@home-economics.us. Reason: {reason}"
    )
    return [dict(FALLBACK_SUBSCRIBER)]


def _primary_email(user: dict) -> str:
    """Resolve a Clerk user object's primary email address.

    Clerk user shape: `primary_email_address_id` points into the
    `email_addresses` list ([{id, email_address, ...}]). Falls back to
    the first listed address if the primary id doesn't resolve."""
    addresses = user.get("email_addresses") or []
    primary_id = user.get("primary_email_address_id")
    for addr in addresses:
        if isinstance(addr, dict) and addr.get("id") == primary_id:
            return (addr.get("email_address") or "").strip()
    # Primary id missing/unmatched — fall back to first address.
    for addr in addresses:
        if isinstance(addr, dict) and addr.get("email_address"):
            return (addr.get("email_address") or "").strip()
    return ""


def get_subscribers() -> list[dict]:
    """Fetch Pulse newsletter subscribers from Clerk.

    Returns [{email: str, user_id: str, premium: bool}, ...]. Never
    raises; never returns an empty list (see module docstring)."""
    secret = (os.environ.get("CLERK_SECRET_KEY") or "").strip()
    if not secret:
        return _fallback("CLERK_SECRET_KEY not set")

    users: list[dict] = []
    offset = 0
    try:
        with httpx.Client(timeout=30) as client:
            while offset < CLERK_MAX_USERS:
                resp = client.get(
                    f"{CLERK_API_BASE}/users",
                    params={"limit": CLERK_PAGE_SIZE, "offset": offset},
                    headers={"Authorization": f"Bearer {secret}"},
                )
                if resp.status_code != 200:
                    return _fallback(
                        f"Clerk GET /users returned {resp.status_code}: "
                        f"{resp.text[:200]}"
                    )
                page = resp.json()
                if not isinstance(page, list):
                    return _fallback(
                        f"Clerk GET /users returned non-list payload: "
                        f"{str(page)[:200]}"
                    )
                users.extend(u for u in page if isinstance(u, dict))
                if len(page) < CLERK_PAGE_SIZE:
                    break
                offset += CLERK_PAGE_SIZE
    except Exception as e:
        return _fallback(f"Clerk request failed: {type(e).__name__}: {e}")

    subscribers: list[dict] = []
    skipped_no_email = 0
    try:
        for u in users:
            meta = u.get("public_metadata")
            if not isinstance(meta, dict):
                continue
            newsletter = meta.get("pulseNewsletter")
            if not isinstance(newsletter, dict):
                continue
            if newsletter.get("subscribed") is not True:
                continue
            email = _primary_email(u)
            if not email or "@" not in email:
                skipped_no_email += 1
                logger.warning(
                    f"subscribed Clerk user {u.get('id')} has no resolvable "
                    f"email address — skipping"
                )
                continue
            tools = meta.get("tools")
            premium = isinstance(tools, dict) and tools.get("pulse") is True
            subscribers.append({
                "email": email,
                "user_id": u.get("id"),
                "premium": premium,
            })
    except Exception as e:
        return _fallback(f"unexpected Clerk user schema: {type(e).__name__}: {e}")

    if not subscribers:
        return _fallback(
            f"Clerk returned {len(users)} users but 0 with "
            f"pulseNewsletter.subscribed=true "
            f"({skipped_no_email} subscribed-but-no-email)"
        )

    n_premium = sum(1 for s in subscribers if s["premium"])
    logger.info(
        f"Clerk subscribers: {len(subscribers)} total "
        f"({n_premium} premium, {len(subscribers) - n_premium} free; "
        f"scanned {len(users)} users, skipped {skipped_no_email} without email)"
    )
    return subscribers


def make_unsubscribe_url(user_id: str) -> str | None:
    """Per-recipient one-click unsubscribe URL.

    token = hex(HMAC_SHA256(key=PULSE_UNSUB_SECRET, message=clerkUserId)).
    Returns None when the secret or user_id is missing — the caller must
    then omit the List-Unsubscribe header rather than send a bad link."""
    secret = (os.environ.get("PULSE_UNSUB_SECRET") or "").strip()
    if not secret or not user_id:
        return None
    token = hmac.new(
        secret.encode("utf-8"), user_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{UNSUBSCRIBE_BASE}?u={quote(user_id, safe='')}&t={token}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    subs = get_subscribers()
    print(f"{len(subs)} subscriber(s):")
    for s in subs:
        local, _, domain = s["email"].partition("@")
        redacted = f"{local[:3]}...@{domain}"
        print(f"  {redacted:40s} premium={s['premium']} "
              f"user_id={'<none>' if s['user_id'] is None else s['user_id']}")
