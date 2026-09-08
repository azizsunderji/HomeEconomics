"""Morning signup report for the owner: who joined, who upgraded, who left,
and where signups come from.

Run daily at 07:00 America/New_York by noon-report.timer (see systemd/).
Reads the Clerk user list (the same list the send uses) and emails a short
HTML summary to NOON_OWNER_EMAIL via Resend, Reply-To the owner like the
edition itself.

Clerk metadata read here (written by the portal):
    publicMetadata.pulseNewsletter = {subscribed, since, unsubscribedAt?, source?}
    publicMetadata.tools = {pulse: bool, pulseSince?: ISO}
`source` is the `?src=` tag the reader arrived with (or `ref:<host>` when
there was no tag); it is written at free signup and by the Stripe webhook at
upgrade if no source is set yet. `tools.pulseSince` is written by the webhook
the first time tools.pulse turns on (from 2026-09-08); premium subscriptions
made before that carry no timestamp and never show in the 24-hour or 7-day
premium counts.

CLI:
    python signup_report.py                 # last 24 h, send to the owner
    python signup_report.py --hours 72      # wider window
    python signup_report.py --to me@x.com   # override the recipient
    python signup_report.py --dry-run       # write the HTML, print its path, do not send
"""
from __future__ import annotations

import argparse
import html
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

import paths
from delivery.subscribers import (
    CLERK_API_BASE,
    CLERK_MAX_USERS,
    CLERK_PAGE_SIZE,
    _primary_email,
)

logger = logging.getLogger("noon.report")

ET = ZoneInfo("America/New_York")
EMAIL_FROM = "News at Noon <pulse@home-economics.us>"
INK = "#3D3733"
MUTED = "#8A8580"
FONT = "-apple-system, BlinkMacSystemFont, 'Helvetica Neue', Helvetica, Arial, sans-serif"
HEAD_FONT = "Georgia, 'Times New Roman', serif"
UNTAGGED = "(untagged)"


# --------------------------------------------------------------------------
# Clerk
# --------------------------------------------------------------------------

def fetch_clerk_users() -> list[dict]:
    """All Clerk users as raw dicts (the helper in delivery.subscribers keeps
    only email/user_id/premium, and this report needs the timestamps and the
    source). Raises on any failure: a report with wrong numbers is worse than
    no report, and the timer logs the traceback."""
    secret = (os.environ.get("CLERK_SECRET_KEY") or "").strip()
    if not secret:
        raise RuntimeError("CLERK_SECRET_KEY not set")
    users: list[dict] = []
    offset = 0
    with httpx.Client(timeout=30) as client:
        while offset < CLERK_MAX_USERS:
            resp = client.get(
                f"{CLERK_API_BASE}/users",
                params={"limit": CLERK_PAGE_SIZE, "offset": offset},
                headers={"Authorization": f"Bearer {secret}"},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Clerk GET /users returned {resp.status_code}: {resp.text[:200]}")
            page = resp.json()
            if not isinstance(page, list):
                raise RuntimeError(f"Clerk GET /users returned non-list payload: {str(page)[:200]}")
            users.extend(u for u in page if isinstance(u, dict))
            if len(page) < CLERK_PAGE_SIZE:
                break
            offset += CLERK_PAGE_SIZE
    return users


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def summarise_user(u: dict) -> dict | None:
    """Flatten one Clerk user into the fields the report needs, or None when
    the user has never touched News at Noon (no pulseNewsletter and no
    tools.pulse)."""
    meta = u.get("public_metadata") if isinstance(u.get("public_metadata"), dict) else {}
    pn = meta.get("pulseNewsletter") if isinstance(meta.get("pulseNewsletter"), dict) else None
    tools = meta.get("tools") if isinstance(meta.get("tools"), dict) else {}
    premium = tools.get("pulse") is True
    if pn is None and not premium:
        return None
    pn = pn or {}
    source = pn.get("source")
    if not isinstance(source, str) or not source.strip():
        source = None
    created = u.get("created_at")
    return {
        "user_id": u.get("id"),
        "email": _primary_email(u) or "(no email)",
        "subscribed": pn.get("subscribed") is True,
        "since": _parse_iso(pn.get("since")),
        "unsubscribed_at": _parse_iso(pn.get("unsubscribedAt")),
        "source": source,
        "premium": premium,
        "premium_since": _parse_iso(tools.get("pulseSince")),
        "created_at": (datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                       if isinstance(created, (int, float)) else None),
    }


# --------------------------------------------------------------------------
# Report data
# --------------------------------------------------------------------------

def build_report(users: list[dict], hours: float, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    window = now - timedelta(hours=hours)
    week = now - timedelta(days=7)
    rows = [r for r in (summarise_user(u) for u in users) if r]

    def in_window(t: datetime | None, start: datetime) -> bool:
        return t is not None and start <= t <= now + timedelta(minutes=5)

    signups = sorted((r for r in rows if r["subscribed"] and in_window(r["since"], window)),
                     key=lambda r: r["since"], reverse=True)
    premium = sorted((r for r in rows if r["premium"] and in_window(r["premium_since"], window)),
                     key=lambda r: r["premium_since"], reverse=True)
    unsubs = sorted((r for r in rows if not r["subscribed"] and in_window(r["unsubscribed_at"], window)),
                    key=lambda r: r["unsubscribed_at"], reverse=True)

    current = [r for r in rows if r["subscribed"]]
    n_premium_now = sum(1 for r in current if r["premium"])
    # Premium users who have stopped the email but are still billed.
    premium_not_receiving = sum(1 for r in rows if r["premium"] and not r["subscribed"])

    week_signups = [r for r in rows if in_window(r["since"], week)]
    week_unsubs = [r for r in rows if in_window(r["unsubscribed_at"], week)]
    week_premium = [r for r in rows if r["premium"] and in_window(r["premium_since"], week)]

    by_source_week = Counter((r["source"] or UNTAGGED) for r in week_signups)
    by_source_all = Counter((r["source"] or UNTAGGED) for r in rows if r["since"] is not None)
    sources = sorted(set(by_source_week) | set(by_source_all),
                     key=lambda s: (s == UNTAGGED, -by_source_all[s], -by_source_week[s], s))

    return {
        "now": now,
        "hours": hours,
        "signups": signups,
        "premium": premium,
        "unsubs": unsubs,
        "n_subscribers": len(current),
        "n_premium": n_premium_now,
        "n_free": len(current) - n_premium_now,
        "premium_not_receiving": premium_not_receiving,
        "week_signups": len(week_signups),
        "week_unsubs": len(week_unsubs),
        "week_premium": len(week_premium),
        "by_source": [(s, by_source_week.get(s, 0), by_source_all.get(s, 0)) for s in sources],
        "n_users": len(users),
        "n_rows": len(rows),
    }


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def fmt_time(t: datetime | None) -> str:
    if t is None:
        return "—"
    local = t.astimezone(ET)
    return local.strftime("%a %-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def fmt_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return "—"
    secs = max(0, int((end - start).total_seconds()))
    if secs < 3600:
        n = max(1, secs // 60)
        return f"{n} minute{'s' if n != 1 else ''}"
    if secs < 86400 * 2:
        n = secs // 3600
        return f"{n} hour{'s' if n != 1 else ''}"
    n = secs // 86400
    return f"{n} day{'s' if n != 1 else ''}"


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _head(text: str) -> str:
    return (f'<p style="margin:36px 0 12px 0;font-family:{HEAD_FONT};font-size:20px;'
            f'line-height:1.3;color:{INK};">{_e(text)}</p>')


def _none() -> str:
    return f'<p style="margin:0;font-size:15px;line-height:1.5;color:{INK};">None.</p>'


def _table(headers: list[str], rows: list[list[str]], align_right: set[int] = frozenset()) -> str:
    cell = (f"font-family:{FONT};font-size:14px;line-height:1.45;color:{INK};"
            "padding:4px 16px 4px 0;vertical-align:top;")
    head = (f"font-family:{FONT};font-size:11px;letter-spacing:0.08em;text-transform:uppercase;"
            f"color:{MUTED};padding:0 16px 6px 0;vertical-align:bottom;")
    out = ['<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
           'style="border-collapse:collapse;width:100%;">']
    out.append("<tr>")
    for i, h in enumerate(headers):
        ta = "right" if i in align_right else "left"
        out.append(f'<td style="{head}text-align:{ta};">{_e(h)}</td>')
    out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        for i, v in enumerate(r):
            ta = "right" if i in align_right else "left"
            last = "padding-right:0;" if i == len(r) - 1 else ""
            # Emails may break anywhere; times and durations stay on one line.
            wrap = "word-break:break-all;" if i == 0 else "white-space:nowrap;"
            out.append(f'<td style="{cell}{last}text-align:{ta};{wrap}">{_e(v)}</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _window_label(hours: float) -> str:
    if abs(hours - 24) < 0.01:
        return "the last 24 hours"
    if hours < 48 and float(hours).is_integer():
        return "the last hour" if int(hours) == 1 else f"the last {int(hours)} hours"
    days = hours / 24
    return f"the last {days:g} days"


def render_html(rep: dict) -> str:
    now_local = rep["now"].astimezone(ET)
    win = _window_label(rep["hours"])
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
        f'<body style="margin:0;padding:0;background:#ffffff;">'
        f'<div style="max-width:560px;margin:0 auto;padding:28px 20px 40px 20px;font-family:{FONT};color:{INK};">',
        f'<p style="margin:0;font-family:{HEAD_FONT};font-size:24px;line-height:1.25;color:{INK};">News at Noon signups</p>',
        f'<p style="margin:6px 0 0 0;font-size:14px;line-height:1.5;color:{MUTED};">'
        f'{_e(now_local.strftime("%A, %B %-d, %Y, %-I:%M %p ET").replace("AM ET", "am ET").replace("PM ET", "pm ET"))}'
        f' &middot; covering {_e(win)}</p>',
    ]

    # (a) free signups
    parts.append(_head(f"New free signups in {win}"))
    if rep["signups"]:
        parts.append(_table(
            ["Email", "Time (ET)", "Source"],
            [[r["email"], fmt_time(r["since"]), (r["source"] or UNTAGGED) + (" · premium" if r["premium"] else "")]
             for r in rep["signups"]],
        ))
    else:
        parts.append(_none())

    # (b) premium
    parts.append(_head(f"New premium subscriptions in {win}"))
    if rep["premium"]:
        parts.append(_table(
            ["Email", "Time (ET)", "Source"],
            [[r["email"], fmt_time(r["premium_since"]), r["source"] or UNTAGGED] for r in rep["premium"]],
        ))
    else:
        parts.append(_none())
    parts.append(
        f'<p style="margin:8px 0 0 0;font-size:12px;line-height:1.5;color:{MUTED};">'
        "Times are tools.pulseSince, written by the Stripe webhook when the subscription first turns on "
        "(from 8 September 2026). Earlier premium subscriptions carry no timestamp and are counted "
        "only in the totals.</p>"
    )

    # (c) unsubscribes
    parts.append(_head(f"Unsubscribes in {win}"))
    if rep["unsubs"]:
        parts.append(_table(
            ["Email", "Time (ET)", "Subscribed for", "Source"],
            [[r["email"], fmt_time(r["unsubscribed_at"]), fmt_duration(r["since"], r["unsubscribed_at"]),
              r["source"] or UNTAGGED] for r in rep["unsubs"]],
        ))
    else:
        parts.append(_none())

    # (d) totals
    parts.append(_head("Totals"))
    line = f"font-size:15px;line-height:1.55;color:{INK};margin:0;"
    extra = ""
    if rep["premium_not_receiving"]:
        n = rep["premium_not_receiving"]
        extra = (f" A further {n} premium account{'s' if n != 1 else ''} "
                 f"{'have' if n != 1 else 'has'} stopped the email but {'are' if n != 1 else 'is'} still billed.")
    parts.append(
        f'<p style="{line}">Subscribers now: {rep["n_subscribers"]} '
        f'({rep["n_free"]} free, {rep["n_premium"]} premium).{_e(extra)}</p>'
        f'<p style="{line}">Last 7 days: {rep["week_signups"]} signup{"s" if rep["week_signups"] != 1 else ""}, '
        f'{rep["week_premium"]} premium, '
        f'{rep["week_unsubs"]} unsubscribe{"s" if rep["week_unsubs"] != 1 else ""}.</p>'
    )
    parts.append(f'<p style="margin:20px 0 8px 0;font-size:15px;line-height:1.5;color:{INK};">Signups by source</p>')
    if rep["by_source"]:
        parts.append(_table(
            ["Source", "Last 7 days", "Since launch"],
            [[s, str(w), str(a)] for s, w, a in rep["by_source"]],
            align_right={1, 2},
        ))
    else:
        parts.append(_none())

    parts.append(
        f'<p style="margin:36px 0 0 0;font-size:12px;line-height:1.5;color:{MUTED};">'
        f'Clerk: {rep["n_users"]} accounts, {rep["n_rows"]} with a News at Noon record. '
        "Source is the ?src= tag on the link the reader arrived with, or ref:&lt;site&gt; when there was none; "
        "addresses that signed up before tagging began are (untagged).</p>"
    )
    parts.append("</div></body></html>")
    return "".join(parts)


def subject_line(now: datetime | None = None) -> str:
    local = (now or datetime.now(timezone.utc)).astimezone(ET)
    return f"News at Noon signups: {local.strftime('%A, %B %-d')}"


# --------------------------------------------------------------------------
# Send
# --------------------------------------------------------------------------

def send(html_body: str, to: str, subject: str) -> bool:
    from sender import REPLY_TO, _api_key, _post_resend
    return _post_resend(_api_key(), "https://api.resend.com/emails",
                        {"from": EMAIL_FROM, "to": [to], "subject": subject,
                         "html": html_body, "reply_to": REPLY_TO})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hours", type=float, default=24, help="window for the new/unsubscribed sections (default 24)")
    ap.add_argument("--to", default=None, help="recipient (default NOON_OWNER_EMAIL)")
    ap.add_argument("--dry-run", action="store_true", help="write the HTML and print its path instead of sending")
    ap.add_argument("--out", default=None, help="where to write the HTML in --dry-run (default under the log dir)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    users = fetch_clerk_users()
    rep = build_report(users, args.hours)
    body = render_html(rep)
    subject = subject_line(rep["now"])
    logger.info(
        f"report: {len(rep['signups'])} signups, {len(rep['premium'])} premium, {len(rep['unsubs'])} unsubscribes "
        f"in {args.hours:g} h; {rep['n_subscribers']} subscribers now ({rep['n_premium']} premium)"
    )

    if args.dry_run:
        out = Path(args.out) if args.out else paths.LOG_DIR / "signup_report_latest.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(out)
        return 0

    to = args.to or paths.OWNER_EMAIL
    ok = send(body, to, subject)
    logger.info(f"signup report to {to}: {'ok' if ok else 'FAILED'} ({subject})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
