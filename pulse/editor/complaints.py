"""Spam complaints -> unsubscribe (owner, 25 Oct 2026).

When Resend reports `email.complained` (the recipient marked the edition as spam, relayed through the mailbox
provider's feedback loop), the person is unsubscribed in Clerk at once: pulseNewsletter.subscribed = false,
unsubscribedAt = now, unsubReason = "spam-complaint". Sending again to someone who complained damages
deliverability for every other reader. Called from app.resend_hook in a thread; also usable as a CLI backfill:

    complaints.py --backfill [--days 30] [--dry-run]   unsubscribe every complainer in clicks.db not yet handled
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

logger = logging.getLogger("noon.complaints")
CLERK = "https://api.clerk.com/v1"
CLICKS_DB = Path(os.environ.get("NOON_CLICKS_DB", str(Path.home() / "work" / "noon" / "clicks.db")))
LOG = Path.home() / "work" / "noon" / "logs" / "complaints.jsonl"


def _hdr() -> dict:
    key = (os.environ.get("CLERK_SECRET_KEY") or "").strip()
    if not key:
        raise RuntimeError("CLERK_SECRET_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def unsubscribe_complainer(email: str, dry_run: bool = False) -> dict:
    """Mark the Clerk user with this email unsubscribed. Returns a small result dict (also appended to LOG)."""
    email = (email or "").strip().lower()
    res = {"email": email, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": "spam-complaint"}
    try:
        with httpx.Client(timeout=20) as hc:
            r = hc.get(f"{CLERK}/users", headers=_hdr(), params={"email_address": [email], "limit": 5})
            r.raise_for_status()
            users = [u for u in r.json() if any((e.get("email_address") or "").lower() == email for e in u.get("email_addresses", []))]
            if not users:
                res["result"] = "no Clerk user"
            else:
                u = users[0]
                pn = dict(((u.get("public_metadata") or {}).get("pulseNewsletter")) or {})
                if pn.get("subscribed") is False:
                    res["result"] = "already unsubscribed"
                elif dry_run:
                    res["result"] = "dry-run would unsubscribe"
                else:
                    pn.update({"subscribed": False, "unsubscribedAt": res["at"], "unsubReason": "spam-complaint"})
                    r2 = hc.patch(f"{CLERK}/users/{u['id']}/metadata", headers=_hdr(),
                                  json={"public_metadata": {"pulseNewsletter": pn}})
                    r2.raise_for_status()
                    res["result"] = "unsubscribed"
                res["user_id"] = u["id"]
    except Exception as e:  # noqa: BLE001
        res["result"] = f"error: {type(e).__name__}: {str(e)[:160]}"
    logger.info(f"complaint {email}: {res['result']}")
    if not dry_run:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps(res) + "\n")
    return res


def handle_webhook_event(body: bytes) -> dict | None:
    """Called by the Resend webhook after the event is stored: unsubscribe on a complaint, else nothing."""
    try:
        ev = json.loads(body)
    except ValueError:
        return None
    if ev.get("type") != "email.complained":
        return None
    to = (ev.get("data") or {}).get("to") or []
    email = to[0] if isinstance(to, list) and to else (to if isinstance(to, str) else "")
    if not email:
        return None
    res = unsubscribe_complainer(email)
    try:
        import subprocess
        tg = Path.home() / ".local" / "bin" / "tgnotify"
        if tg.exists():
            subprocess.run([str(tg), f"Housing at Noon: {email} marked the edition as spam → {res['result']}"], check=False, timeout=20)
    except Exception:  # noqa: BLE001
        pass
    return res


def _handled() -> set:
    if not LOG.exists():
        return set()
    out = set()
    for line in LOG.read_text().splitlines():
        try:
            d = json.loads(line)
            if d.get("result") in ("unsubscribed", "already unsubscribed", "no Clerk user"):
                out.add(d["email"])
        except ValueError:
            continue
    return out


def backfill(days: int = 30, dry_run: bool = False) -> list[dict]:
    conn = sqlite3.connect(CLICKS_DB)
    rows = conn.execute("SELECT DISTINCT lower(email) FROM events WHERE type='email.complained' AND email IS NOT NULL "
                        "AND created_at >= datetime('now', ?)", (f"-{days} days",)).fetchall()
    done = _handled()
    out = []
    for (email,) in rows:
        if email in done:
            continue
        out.append(unsubscribe_complainer(email, dry_run=dry_run))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--backfill" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
        for r in backfill(days, dry_run="--dry-run" in sys.argv):
            print(r)
    else:
        print(__doc__)
