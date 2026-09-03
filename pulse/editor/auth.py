"""Owner-only auth: a password login that sets a signed cookie, plus a
per-day magic link used in the 'draft ready' email. Both are HMACs over
NOON_SECRET; nothing is stored server-side."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta

COOKIE = "noon_session"
SESSION_DAYS = 60


def _secret() -> bytes:
    s = os.environ.get("NOON_SECRET", "")
    if len(s) < 32:
        raise RuntimeError("NOON_SECRET missing or too short (set it in ~/.noon_env)")
    return s.encode()


def _sig(msg: str) -> str:
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


def password_ok(candidate: str) -> bool:
    expected = os.environ.get("NOON_PASSWORD", "")
    return bool(expected) and secrets.compare_digest(candidate, expected)


def make_session() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_sig(f'session:{exp}')}"


def session_ok(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < time.time():
        return False
    return secrets.compare_digest(sig, _sig(f"session:{exp_s}"))


def magic_token(date: str) -> str:
    return _sig(f"magic:{date}")


def magic_ok(date: str, token: str, today: str) -> bool:
    """Valid for the draft's day and the two days after (the link lives in
    an email; a stale one should not work forever)."""
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        t = datetime.strptime(today, "%Y-%m-%d")
    except ValueError:
        return False
    if not (timedelta(days=-2) <= (t - d) <= timedelta(days=2)):
        return False
    return secrets.compare_digest(token, magic_token(date))
