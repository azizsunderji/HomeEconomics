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
DEAD_LOG = Path.home() / "work" / "noon" / "logs" / "dead_addresses.jsonl"


def _hdr() -> dict:
    key = (os.environ.get("CLERK_SECRET_KEY") or "").strip()
    if not key:
        raise RuntimeError("CLERK_SECRET_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def unsubscribe_complainer(email: str, dry_run: bool = False, reason: str = "spam-complaint", log: Path | None = None) -> dict:
    """Mark the Clerk user with this email unsubscribed. Returns a small result dict (also appended to `log`, default LOG).
    `reason` is stored as pulseNewsletter.unsubReason ("spam-complaint" from the webhook, "dead-address" for bounces)."""
    email = (email or "").strip().lower()
    log = log or LOG
    res = {"email": email, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": reason}
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
                    pn.update({"subscribed": False, "unsubscribedAt": res["at"], "unsubReason": reason})
                    r2 = hc.patch(f"{CLERK}/users/{u['id']}/metadata", headers=_hdr(),
                                  json={"public_metadata": {"pulseNewsletter": pn}})
                    r2.raise_for_status()
                    res["result"] = "unsubscribed"
                res["user_id"] = u["id"]
    except Exception as e:  # noqa: BLE001
        res["result"] = f"error: {type(e).__name__}: {str(e)[:160]}"
    logger.info(f"{reason} {email}: {res['result']}")
    if not dry_run:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(json.dumps(res) + "\n")
    return res


def unsubscribe_dead_address(email: str, dry_run: bool = False) -> dict:
    """Unsubscribe an address that cannot receive mail (mailbox full, no MX, permanent refusal); logs to DEAD_LOG."""
    return unsubscribe_complainer(email, dry_run=dry_run, reason="dead-address", log=DEAD_LOG)


def is_promap_event(ev: dict) -> bool:
    """promapcomplaint1: a ProMap email (sender @thepromap.com or a Resend tag kind promap_*), not a Housing at Noon edition"""
    d = ev.get("data") or {}
    tags = d.get("tags") or {}
    if isinstance(tags, list):
        tags = {t.get("name"): t.get("value") for t in tags if isinstance(t, dict)}
    return str(tags.get("kind") or "").startswith("promap") or "thepromap.com" in str(d.get("from") or "").lower()


def stop_promap_emails(email: str, dry_run: bool = False) -> dict:
    """promapcomplaint1: stamp Clerk privateMetadata.promapEmailsOff (what the ProMap unsubscribe link writes); Housing at Noon untouched"""
    email = (email or "").strip().lower()
    res = {"email": email, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reason": "promap-spam-complaint"}
    try:
        with httpx.Client(timeout=20) as hc:
            r = hc.get(f"{CLERK}/users", headers=_hdr(), params={"email_address": [email], "limit": 5})
            r.raise_for_status()
            users = [u for u in r.json() if any((e.get("email_address") or "").lower() == email for e in u.get("email_addresses", []))]
            if not users:
                res["result"] = "no Clerk user"
            elif (users[0].get("private_metadata") or {}).get("promapEmailsOff"):
                res["result"] = "already off ProMap emails"; res["user_id"] = users[0]["id"]
            elif dry_run:
                res["result"] = "dry-run would stop ProMap emails"; res["user_id"] = users[0]["id"]
            else:
                r2 = hc.patch(f"{CLERK}/users/{users[0]['id']}/metadata", headers=_hdr(), json={"private_metadata": {"promapEmailsOff": res["at"]}})
                r2.raise_for_status()
                res["result"] = "ProMap emails stopped"; res["user_id"] = users[0]["id"]
    except Exception as e:  # noqa: BLE001
        res["result"] = f"error: {type(e).__name__}: {str(e)[:160]}"
    logger.info(f"promap spam complaint {email}: {res['result']}")
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
    promap = is_promap_event(ev)   # promapcomplaint1
    res = stop_promap_emails(email) if promap else unsubscribe_complainer(email)
    try:
        import subprocess
        tg = Path.home() / ".local" / "bin" / "tgnotify"
        if tg.exists():
            msg = (f"ProMap: {email} marked a ProMap email as spam. They will get no more ProMap emails; their Housing at Noon subscription is unchanged."
                   if promap else f"Housing at Noon: {email} marked the edition as spam → {res['result']}")
            subprocess.run([str(tg), msg], check=False, timeout=20)
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
    rows = conn.execute("SELECT lower(email), raw FROM events WHERE type='email.complained' AND email IS NOT NULL "
                        "AND created_at >= datetime('now', ?)", (f"-{days} days",)).fetchall()
    rows = sorted({(e,) for e, raw in rows if not is_promap_event(json.loads(raw or "{}"))})   # promapcomplaint1: ProMap complaints are not Noon
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
