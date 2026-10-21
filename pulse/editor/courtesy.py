"""Courtesy email to free Housing at Noon readers who keep hitting the upgrade wall (owner, 21 Oct 2026).

Owner's instruction: "The courtesy email to repeat wall hitters with the links they tried and a time-limited discount.
Remember let's include a gift link there." and "If the people hitting the wall are from a corporate address, we'll send
a different email. It is focused on all the things for corporate readers: the expensing language, group discounts, a
prewritten email they can forward, and anything else you can think of that would sell a corporate reader."

Who gets it: free-tier subscribers with --min-clicks (default 2) or more walled clicks in the last --days (default 7)
editions, after dropping mail-security link scanners (click_report.scanner_emails, per edition), premium subscribers,
anyone no longer subscribed, the owner's own addresses, and anyone already in courtesy_sent (once per person). Each
email lists up to five of the sources the person tried to open, most recent first, with a gift link for FT, WSJ and
Bloomberg when gift_links.py can make one, and a 20%-off-the-first-year Stripe promotion code valid for 7 days.

Two kinds, chosen by the address (classify): personal mailboxes (Gmail, iCloud, university addresses and so on) get the
short note; every other domain gets the corporate note (expensing, invoice on request, team pricing, a note to forward
to a manager).

    courtesy.py                                  # preview (default) into ~/work/noon/clicks/courtesy_preview
    courtesy.py --days 7 --preview DIR           # preview into DIR: one HTML per recipient + previews.json
    courtesy.py --min-clicks 1 --preview DIR     # lower threshold, to see the copy on more real data
    courtesy.py --test-to aziz@home-economics.us # one sample of each kind to the owner, subject [TEST]
    courtesy.py --create-code                    # create (idempotently) today's Stripe promotion code, print it
    courtesy.py --send --create-code             # real send; or --send --code NOON1021 with an existing active code

Nothing is sent without --test-to or --send, and --send refuses to run without a verified active code.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html as _html
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import paths  # noqa: E402,F401  (loads the environment and puts pulse/scripts on sys.path)
import clicks  # noqa: E402
import click_report  # noqa: E402
import drafts  # noqa: E402
import render  # noqa: E402
from delivery.subscribers import get_subscribers, make_unsubscribe_url  # noqa: E402

try:  # written separately (FT, WSJ, Bloomberg gift links from the owner's subscriptions); optional
    from gift_links import gift_link as _gift_link
except Exception:  # noqa: BLE001
    _gift_link = None
try:  # side-effect-free check of whether a URL is one gift_links can mint (used by previews)
    from gift_links import classify as _gift_eligible
except Exception:  # noqa: BLE001
    _gift_eligible = None

logger = logging.getLogger("noon.courtesy")
ET = ZoneInfo("America/New_York")

FROM = "Aziz Sunderji <aziz@home-economics.us>"
REPLY_TO = "aziz@home-economics.us"
UPGRADE_URL = "https://homeeconomics.us/noon/upgrade?src=courtesy"
PLACEHOLDER_CODE = "NOON1021"   # previews only, so the copy can be approved before any Stripe object exists
PREVIEW_UNSUB = "https://homeeconomics.us/api/pulse/unsubscribe?u=PREVIEW"  # a real link in a preview would work
MONTHLY_USD, ANNUAL_USD = 49, 490   # Stripe prices on 21 Oct 2026 (STRIPE_PRICE_PULSE_MONTHLY / _ANNUAL)
MAX_PIECES = 5
OWN_DOMAINS = ("home-economics.us", "homeeconomics.us")
DEFAULT_PREVIEW_DIR = Path.home() / "work" / "noon" / "clicks" / "courtesy_preview"

# Consumer mailbox providers: an address here (or at a .edu / .ac.<country> domain) gets the personal email; every
# other domain is treated as a workplace and gets the corporate email. "name.*" matches any country ending
# (yahoo.co.uk, hotmail.fr, gmx.de). Vanity domains (jane@janedoe.com) cannot be told apart from small firms and
# fall into corporate.
CONSUMER_MAILBOXES = frozenset({
    "gmail.com", "googlemail.com", "yahoo.*", "ymail.com", "rocketmail.com", "hotmail.*", "outlook.*", "live.*",
    "msn.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "protonmail.ch", "pm.me",
    "comcast.net", "att.net", "verizon.net", "sbcglobal.net", "cox.net", "charter.net", "earthlink.net", "mail.com",
    "gmx.*", "fastmail.com", "fastmail.fm", "hey.com", "zoho.com", "zohomail.com",
})
# Personal vanity domains that would otherwise look corporate (owner confirmed each one; add as they turn up).
PERSONAL_DOMAINS = {"aeckinger.com", "jakobschnitzer.de"}

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_VERSION = "2024-06-20"   # pinned so coupon/promotion-code parameters keep this shape
COUPON_ID = "NOONCOURTESY20"
COUPON_NAME = "Housing at Noon courtesy 20%"


# ---------------------------------------------------------------- classification

def classify(email: str) -> str:
    """'personal' for consumer mailboxes and academic addresses, otherwise 'corporate'."""
    domain = email.rsplit("@", 1)[-1].lower().strip()
    if domain in PERSONAL_DOMAINS or domain.endswith(".edu") or re.search(r"\.ac\.[a-z]{2}$", domain):
        return "personal"
    first = domain.split(".", 1)[0]
    if domain in CONSUMER_MAILBOXES or f"{first}.*" in CONSUMER_MAILBOXES:
        return "personal"
    return "corporate"


# ---------------------------------------------------------------- selection

SENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS courtesy_sent (
  email     TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,
  edition   TEXT NOT NULL,
  sent_at   TEXT NOT NULL,
  code      TEXT,
  resend_id TEXT
);
"""


def _conn():
    conn = clicks.connect()
    conn.executescript(SENT_SCHEMA)
    return conn


def _gift(url: str, cache: dict) -> str | None:
    """Gift URL, or None. Every gift_link() call counts as a hand-out (an FT link allows three views) and a new
    link spends a monthly credit, so previews (cache["_preview"]) never call it: an eligible URL comes back as
    itself, and the preview shows the gift wording over the plain link."""
    if _gift_link is None:
        return None
    if cache.get("_preview"):
        try:
            return url if _gift_eligible and _gift_eligible(url) else None
        except Exception:  # noqa: BLE001
            return None
    if url not in cache:
        try:
            cache[url] = _gift_link(url) or None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gift link failed for {url}: {e}")
            cache[url] = None
    return cache[url]


def candidates(days: int = 7, min_clicks: int = 2, today: str | None = None) -> tuple[list[dict], dict]:
    """People to write to, and a dict of counts for the summary (who was dropped and why)."""
    today = today or drafts.today_et()
    since = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).date().isoformat()
    conn = _conn()
    rows = conn.execute("SELECT * FROM events WHERE edition BETWEEN ? AND ?", (since, today)).fetchall()
    by_ed: dict[str, list] = defaultdict(list)
    for r in rows:
        by_ed[r["edition"]].append(r)

    hits: dict[str, list] = defaultdict(list)   # email -> [(created_at, edition, link_id)]
    for ed, ev in by_ed.items():
        scanners = click_report.scanner_emails(ev)
        for r in ev:
            if (r["type"] == "email.clicked" and r["wall"] and (r["tier"] or "free") == "free"
                    and r["email"] and r["email"] not in scanners):
                hits[r["email"]].append((r["created_at"], ed, r["link_id"]))

    subs = get_subscribers()
    fallback = len(subs) == 1 and subs[0].get("user_id") is None
    by_email = {s["email"].lower(): s for s in subs}
    already = {r["email"] for r in conn.execute("SELECT email FROM courtesy_sent")}
    stats = {"wall_people": len(hits), "below_threshold": 0, "owner": 0, "premium": 0, "not_subscribed": 0,
             "already_sent": 0, "no_sources": 0, "subscriber_list_fallback": fallback}

    sources: dict[str, dict] = {}
    out = []
    for email, hs in hits.items():
        if len(hs) < min_clicks:
            stats["below_threshold"] += 1; continue
        if email == paths.OWNER_EMAIL.lower() or email.rsplit("@", 1)[-1] in OWN_DOMAINS:
            stats["owner"] += 1; continue
        sub = by_email.get(email)
        if not fallback and sub is None:
            stats["not_subscribed"] += 1; continue
        if sub and sub.get("premium"):
            stats["premium"] += 1; continue
        if email in already:
            stats["already_sent"] += 1; continue
        pieces, seen = [], set()
        for _ts, ed, lid in sorted(hs, reverse=True):
            if ed not in sources:
                sources[ed] = click_report._edition_sources(ed)
            s = sources[ed].get(lid or "")
            if not s or s["url"] in seen:
                continue
            seen.add(s["url"])
            pieces.append({"edition": ed, "theme": s.get("theme") or "", "text": s.get("text") or "",
                           "host": s.get("host") or "", "url": s["url"]})
            if len(pieces) == MAX_PIECES:
                break
        if not pieces:
            stats["no_sources"] += 1; continue
        out.append({"email": email, "kind": classify(email), "user_id": (sub or {}).get("user_id"),
                    "wall_clicks": len(hs), "pieces": pieces})
    # Corporate readers first (owner, 21 Oct 2026: "prioritize corporate readers because our gift links run out ...
    # their conversion rate is likely to be much higher"). Gift links are minted in this order, so when an FT
    # article's three views or Bloomberg's five a month run out, corporate readers got them.
    out.sort(key=lambda p: (p["kind"] != "corporate", -p["wall_clicks"], p["email"]))
    return out, stats


# ---------------------------------------------------------------- copy

INK, GREY, LINK = "#3D3733", "#7F7570", "#1a73e8"
P = f'<p style="margin:0 0 16px 0;">'
SIGNATURE = ('<div>Aziz Sunderji<br><a href="https://www.homeeconomics.us" style="color:' + LINK + ';">'
             'www.homeeconomics.us</a><br><a href="https://fantastical.app/azizsunderji/my-template" style="color:'
             + LINK + ';">Schedule meeting</a></div>')
SIGNATURE_TEXT = "Aziz Sunderji\nwww.homeeconomics.us\nSchedule meeting: https://fantastical.app/azizsunderji/my-template"


def _esc(s: str) -> str:
    return _html.escape(s or "", quote=True)


def _end_date(code_expires: datetime) -> str:
    return code_expires.astimezone(ET).strftime("%A, %B %-d")


def _code_expiry(days: int, now: datetime | None = None) -> datetime:
    """11:59 pm Eastern on the last day, so "valid through <weekday, date>" is exactly true."""
    now = (now or datetime.now(ET)).astimezone(ET)
    return (now + timedelta(days=days)).replace(hour=23, minute=59, second=0, microsecond=0)


# Newspaper front-page images in the edition link to Freedom Forum's daily PDFs (cdn.freedomforum.org/dfp/pdfDD/
# XX_PAPER.pdf); they carry no theme and no anchor text, so they are labelled by paper.
FRONT_PAGES = {"WSJ": "The Wall Street Journal", "NYT": "The New York Times", "WP": "The Washington Post",
               "USAT": "USA Today", "LAT": "the Los Angeles Times", "FT": "the Financial Times"}
_NUM = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _label(p: dict) -> str:
    m = re.search(r"freedomforum\.org/dfp/pdf\d+/[A-Z]{2}_([A-Z]+)\.pdf", p["url"])
    if m:
        day = datetime.strptime(p["edition"], "%Y-%m-%d").strftime("%B %-d")
        return f"The front page of {FRONT_PAGES.get(m.group(1), 'a newspaper')} for {day} (a PDF)"
    # anchor texts in the edition are usually a verb ("reported", "framed"), so the theme is the better label
    if len(p["text"]) >= 25:
        return p["text"]
    return p["theme"] or p["text"] or p["host"]


def _piece_lines(pieces: list[dict], gift_cache: dict) -> tuple[str, str, int]:
    """HTML list, plain-text list, and how many gift links were used."""
    items_h, items_t, n_gift = [], [], 0
    for p in pieces:
        g = _gift(p["url"], gift_cache)
        href = g or p["url"]
        label = _label(p)
        where = f", the piece from {p['host']}" if p["host"] and "freedomforum" not in p["host"] else ""
        gift_t = ""
        if g:
            n_gift += 1
            gift_t = " This one is a gift link, so it opens without a subscription."
        items_h.append(f'<div style="margin:0 0 12px 0;">&bull; <a href="{_esc(href)}" style="color:{LINK};">'
                       f'{_esc(label)}</a>{_esc(where)}.{_esc(gift_t)}</div>')
        items_t.append(f"• {label}{where}.{gift_t}\n  {href}")
    return "".join(items_h) + '<div style="height:4px;"></div>', "\n\n".join(items_t), n_gift


def _opening(pieces: list[dict]) -> str:
    n = len(pieces)
    eds = sorted({p["edition"] for p in pieces})
    when = (f"In {datetime.strptime(eds[0], '%Y-%m-%d').strftime('%A')}'s Housing at Noon"
            if len(eds) == 1 else "In Housing at Noon this past week")
    what = "one of the sources" if n == 1 else f"{_NUM.get(n, str(n))} of the sources"
    here = "Here it is, so you can read it." if n == 1 else "Here they are, so you can read them."
    return f"{when}, you tried to open {what}, and the free edition sent you to the upgrade page instead. {here}"


def _discount(code: str, end: str) -> tuple[str, str]:
    m = MONTHLY_USD * 0.8
    a = ANNUAL_USD * 0.8
    t = (f"If you would like to try it, the code {code} takes 20% off your first year: ${m:.2f} a month instead of "
         f"${MONTHLY_USD} for twelve months, or ${a:,.0f} instead of ${ANNUAL_USD} for a year on the annual plan. "
         f"The code works through {end}, and you can enter it at checkout here: {UPGRADE_URL}")
    h = (f"If you would like to try it, the code {_esc(code)} takes 20% off your first year: ${m:.2f} a month instead "
         f"of ${MONTHLY_USD} for twelve months, or ${a:,.0f} instead of ${ANNUAL_USD} for a year on the annual plan. "
         f"The code works through {_esc(end)}, and you can enter it at checkout here: "
         f'<a href="{UPGRADE_URL}" style="color:{LINK};">{UPGRADE_URL}</a>')
    return h, t


PREMIUM_LINE = ("The premium edition has every theme of the day, a direct link to every source, and the daily chart.")


def _wrap(body_h: str, unsub: str | None) -> str:
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
           f'</head><body style="margin:0;padding:0;background:#ffffff;">'
           f'<div style="max-width:600px;margin:0 auto;padding:24px 20px;font-family:-apple-system,Helvetica,Arial,'
           f'sans-serif;font-size:16px;line-height:1.5;color:{INK};">{body_h}</div></body></html>')
    return render.with_footer(doc, unsub)


def personal_email(person: dict, code: str, end: str, unsub: str | None, gift_cache: dict) -> dict:
    lh, lt, n_gift = _piece_lines(person["pieces"], gift_cache)
    dh, dt = _discount(code, end)
    opening = _opening(person["pieces"])
    body_h = (f"{P}Hi,</p>{P}{_esc(opening)}</p>{lh}{P}{_esc(PREMIUM_LINE)}</p>{P}{dh}</p>"
              f"{P}If you have a question, reply to this email. It comes straight to me.</p>{SIGNATURE}")
    text = (f"Hi,\n\n{opening}\n\n{lt}\n\n{PREMIUM_LINE}\n\n{dt}\n\n"
            f"If you have a question, reply to this email. It comes straight to me.\n\n{SIGNATURE_TEXT}\n")
    return {"subject": "The articles you tried to open in Housing at Noon", "html": _wrap(body_h, unsub),
            "text": text + _text_footer(unsub), "gift_links_used": n_gift}


# Corporate template. Research on how research products and paid newsletters word "expense it" and the note to a
# manager (21 Oct 2026), drawn on for structure, not wording:
#   - Newcomer, "How to Expense Newcomer" (newcomer.co/p/how-to-expense-newcomer): ask the manager first; say what the
#     publication covers and how it helps your work.
#   - Culpium, "Expense Culpium to Your Company" (culpium.com/p/expense-culpium-to-your-company) and What Did OpenAI
#     Do This Week, expense template (whatdidopenaidothisweek.beehiiv.com/p/template-how-to-expense-your-subscription):
#     a short note with the price and billing period, that it can be cancelled at any time, and one direct question at
#     the end; group discount for several seats.
#   - Stratechery FAQ (via search) and STAT / Kitces group plans: team subscriptions on request, one point of contact
#     and one payment for the group, a custom invoice for the finance team.
# Kept: (1) "subscriptions to research services are often reimbursable as a business expense" plus a receipt and an
# invoice on request; (2) team pricing for three or more people, one invoice; (3) a forwardable note that gives what it
# is, the time it saves, the price, that it can be cancelled, and a one-line ask.

def _manager_note() -> tuple[str, str]:
    lines = [
        "Subject: Housing at Noon subscription",
        "",
        "Hi [name],",
        "",
        "I would like to expense a subscription to Housing at Noon, a daily email on the US housing market from Aziz "
        "Sunderji at Home Economics. Each weekday at noon Eastern it sorts the day's housing news, data releases and "
        "research into themes, with a direct link to every source. It replaces the time I now spend scanning a dozen "
        "sources each day.",
        "",
        f"It costs ${MONTHLY_USD} a month or ${ANNUAL_USD} a year, and it can be cancelled at any time. There is a "
        "team rate for three or more people, if others here would find it useful.",
        "",
        "Would it be all right for me to expense it?",
        "",
        "[Your name]",
    ]
    text = "\n".join("> " + l if l else ">" for l in lines)
    inner = "".join(f'<div style="margin:0 0 12px 0;">{_esc(l)}</div>' for l in lines if l)
    h = (f'<div style="margin:0 0 18px 0;padding:4px 0 4px 16px;border-left:3px solid #DADFCE;color:{INK};">'
         f'{inner}</div>')
    return h, text


def corporate_email(person: dict, code: str, end: str, unsub: str | None, gift_cache: dict) -> dict:
    lh, lt, n_gift = _piece_lines(person["pieces"], gift_cache)
    dh, dt = _discount(code, end)
    opening = _opening(person["pieces"])
    expense = ("If you read Housing at Noon for work, your employer may pay for it. Subscriptions to research services "
               "are often reimbursable as a business expense. Every payment comes with a receipt from Stripe that you "
               "can attach to an expense report, and if your finance team needs a formal invoice with your company's "
               "name and address, reply to this email and I will send one.")
    team = ("If three or more people on your team would read it, I offer team pricing with a single invoice for the "
            "group. Reply with the number of people and I will send you a quote.")
    forward = "If it helps, here is a short note you can copy and send to your manager:"
    nh, nt = _manager_note()
    body_h = (f"{P}Hi,</p>{P}{_esc(opening)}</p>{lh}{P}{_esc(PREMIUM_LINE)}</p>{P}{_esc(expense)}</p>"
              f"{P}{_esc(team)}</p>{P}{dh}</p>{P}{_esc(forward)}</p>{nh}"
              f"{P}If you have a question, reply to this email. It comes straight to me.</p>{SIGNATURE}")
    text = (f"Hi,\n\n{opening}\n\n{lt}\n\n{PREMIUM_LINE}\n\n{expense}\n\n{team}\n\n{dt}\n\n{forward}\n\n{nt}\n\n"
            f"If you have a question, reply to this email. It comes straight to me.\n\n{SIGNATURE_TEXT}\n")
    return {"subject": "The articles you tried to open in Housing at Noon", "html": _wrap(body_h, unsub),
            "text": text + _text_footer(unsub), "gift_links_used": n_gift}


def _text_footer(unsub: str | None) -> str:
    parts = ([f"Unsubscribe: {unsub}"] if unsub else []) + [render.PULSE_POSTAL_ADDRESS]
    return "\n--\nYou're receiving this because you subscribe to Housing at Noon.\n" + "\n".join(parts) + "\n"


def build(person: dict, code: str, end: str, unsub: str | None, gift_cache: dict) -> dict:
    fn = corporate_email if person["kind"] == "corporate" else personal_email
    return fn(person, code, end, unsub, gift_cache)


# ---------------------------------------------------------------- Stripe promotion code

class StripeError(RuntimeError):
    pass


def _stripe(method: str, path: str, params: dict | None = None) -> dict:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise StripeError("STRIPE_SECRET_KEY is not set in ~/.noon_env")
    data = urllib.parse.urlencode(params or {}, doseq=True).encode() if method == "POST" else None
    url = STRIPE_API + path + ("?" + urllib.parse.urlencode(params or {}, doseq=True) if method == "GET" and params else "")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode(),
        "Stripe-Version": STRIPE_VERSION, "User-Agent": "noon-courtesy/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except ValueError:
            msg = body
        raise StripeError(f"Stripe {method} {path} returned HTTP {e.code}: {msg[:400]}") from None


def ensure_code(pct: int = 20, days: int = 7, now: datetime | None = None) -> dict:
    """Create (idempotently) the courtesy coupon and today's promotion code NOON<MMDD>. Returns
    {code, id, expires_at (datetime), created (bool)}. Raises StripeError with a readable message."""
    now = (now or datetime.now(ET)).astimezone(ET)
    coupon_id = COUPON_ID if pct == 20 else f"NOONCOURTESY{pct}"
    try:
        coupon = _stripe("GET", f"/coupons/{coupon_id}")
    except StripeError as e:
        if "No such coupon" not in str(e):
            raise
        price = _stripe("GET", f"/prices/{os.environ.get('STRIPE_PRICE_PULSE_MONTHLY', '')}")
        coupon = _stripe("POST", "/coupons", {
            "id": coupon_id, "name": COUPON_NAME if pct == 20 else f"Housing at Noon courtesy {pct}%",
            "percent_off": pct, "duration": "repeating", "duration_in_months": 12,
            "applies_to[products][]": [price["product"]]})   # Housing at Noon only, not the Pro Map
    code = f"NOON{now.strftime('%m%d')}"
    existing = _stripe("GET", "/promotion_codes", {"code": code, "limit": 10}).get("data") or []
    for pc in existing:
        cp = pc.get("coupon")
        cp_id = cp.get("id") if isinstance(cp, dict) else cp
        if pc.get("active") and cp_id == coupon["id"] and (pc.get("expires_at") or 0) > now.timestamp():
            return {"code": code, "id": pc["id"], "expires_at": datetime.fromtimestamp(pc["expires_at"], ET),
                    "created": False}
    if existing:
        raise StripeError(f"promotion code {code} already exists but is inactive, expired or on another coupon")
    expires = _code_expiry(days, now)
    pc = _stripe("POST", "/promotion_codes", {
        "coupon": coupon["id"], "code": code, "expires_at": int(expires.timestamp()), "max_redemptions": 200,
        "restrictions[first_time_transaction]": "true"})
    return {"code": code, "id": pc["id"], "expires_at": expires, "created": True}


def verify_code(code: str) -> dict:
    """An existing promotion code passed with --code: must exist, be active and not expired."""
    data = _stripe("GET", "/promotion_codes", {"code": code, "active": "true", "limit": 10}).get("data") or []
    now = datetime.now(timezone.utc).timestamp()
    for pc in data:
        if pc.get("expires_at") is None or pc["expires_at"] > now:
            exp = datetime.fromtimestamp(pc["expires_at"], ET) if pc.get("expires_at") else None
            return {"code": code, "id": pc["id"], "expires_at": exp, "created": False}
    raise StripeError(f"no active, unexpired promotion code {code} in Stripe")


# ---------------------------------------------------------------- sending

def _resend(url: str, payload, idem: str | None = None) -> dict:
    import httpx
    headers = {"Authorization": f"Bearer {os.environ.get('RESEND_API_KEY', '')}", "Content-Type": "application/json"}
    if idem:
        headers["Idempotency-Key"] = idem   # a retried batch is not sent twice
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code in (200, 201):
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (400, 401, 403, 409, 422):
                break
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"Resend failed: {last}")


def _message(person: dict, mail: dict, to: str, subject: str, edition: str, unsub: str | None) -> dict:
    msg = {"from": FROM, "to": [to], "reply_to": REPLY_TO, "subject": subject, "html": mail["html"],
           "text": mail["text"],
           # tier=courtesy keeps clicks on this email out of the edition's free-tier wall counts in click_report
           "tags": [{"name": "kind", "value": "courtesy"}, {"name": "edition", "value": edition},
                    {"name": "tier", "value": "courtesy"}]}
    if unsub:
        msg["headers"] = {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    return msg


# ---------------------------------------------------------------- CLI

def _summary(people: list[dict], stats: dict, min_clicks: int, days: int) -> str:
    n_p = sum(1 for p in people if p["kind"] == "personal")
    lines = [f"courtesy candidates ({min_clicks}+ walled clicks in {days} days): {len(people)} "
             f"({n_p} personal, {len(people) - n_p} corporate)",
             f"  free readers who hit the wall at all: {stats['wall_people']}; dropped: below threshold "
             f"{stats['below_threshold']}, owner {stats['owner']}, premium {stats['premium']}, no longer subscribed "
             f"{stats['not_subscribed']}, already sent {stats['already_sent']}, no matching source {stats['no_sources']}"]
    if stats["subscriber_list_fallback"]:
        lines.append("  WARNING: the Clerk subscriber list fell back to the owner only, so premium and unsubscribed "
                     "readers could not be excluded")
    lines.append(f"  {'kind':<10}{'clicks':>7}{'pieces':>7}  domain")
    for p in people:
        lines.append(f"  {p['kind']:<10}{p['wall_clicks']:>7}{len(p['pieces']):>7}  {p['email'].rsplit('@', 1)[-1]}")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-clicks", type=int, default=2)
    ap.add_argument("--preview", metavar="DIR", default=None)
    ap.add_argument("--test-to", metavar="EMAIL", default=None)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--create-code", action="store_true")
    ap.add_argument("--code", default=None, help="an existing active promotion code to use instead of creating one")
    ap.add_argument("--pct", type=int, default=20)
    ap.add_argument("--valid-days", type=int, default=7)
    a = ap.parse_args()
    if a.send and a.test_to:
        print("choose one of --send or --test-to"); return 2

    today = drafts.today_et()
    people, stats = candidates(a.days, a.min_clicks, today)
    print(_summary(people, stats, a.min_clicks, a.days))

    promo = None
    if a.create_code:
        try:
            promo = ensure_code(a.pct, a.valid_days)
            print(f"promotion code {promo['code']} ({'created' if promo['created'] else 'already existed'}), "
                  f"valid through {promo['expires_at']:%A %B %-d %Y %-I:%M %p} ET")
        except StripeError as e:
            print(f"COULD NOT CREATE THE STRIPE CODE: {e}")
            if "permission" in str(e).lower() or "403" in str(e):
                print("  The restricted key needs write access to Coupons and Promotion codes "
                      "(Stripe dashboard > Developers > API keys > edit the restricted key).")
            return 1
    elif a.code:
        try:
            promo = verify_code(a.code)
        except StripeError as e:
            print(f"CODE CHECK FAILED: {e}"); return 1

    if a.send and not promo:
        print("refusing to send: run with --create-code, or pass an existing active code with --code"); return 2
    if a.send and stats["subscriber_list_fallback"]:
        print("refusing to send: the subscriber list could not be read from Clerk"); return 2

    code = promo["code"] if promo else PLACEHOLDER_CODE
    expires = (promo["expires_at"] if promo and promo.get("expires_at") else _code_expiry(a.valid_days))
    end = _end_date(expires)
    gift_cache: dict = {} if (a.test_to or a.send) else {"_preview": True}   # previews never mint or hand out gift links
    if _gift_link is None:
        print("gift_links.py not found: every source gets its plain URL")

    if a.test_to:
        sent = 0
        for kind in ("personal", "corporate"):
            p = next((x for x in people if x["kind"] == kind), None)
            if not p:
                print(f"no eligible {kind} reader to use as the sample"); continue
            mail = build(p, code, end, None, gift_cache)
            res = _resend("https://api.resend.com/emails",
                          _message(p, mail, a.test_to, f"[TEST {kind}] {mail['subject']}", today, None))
            sent += 1
            print(f"test {kind} (data from {p['email']}) sent to {a.test_to}: {res.get('id')}")
        return 0 if sent else 1

    if a.send:
        conn = _conn()
        msgs, built = [], []
        for p in people:
            unsub = make_unsubscribe_url(p["user_id"]) if p.get("user_id") else None
            if not unsub:
                print(f"skipping {p['email']}: no unsubscribe link (no Clerk user id or PULSE_UNSUB_SECRET)"); continue
            mail = build(p, code, end, unsub, gift_cache)
            msgs.append(_message(p, mail, p["email"], mail["subject"], today, unsub))
            built.append(p)
        for i in range(0, len(msgs), 100):
            chunk, ppl = msgs[i:i + 100], built[i:i + 100]
            idem = "courtesy-" + hashlib.sha1("|".join(p["email"] for p in ppl).encode()).hexdigest()[:24]
            res = _resend("https://api.resend.com/emails/batch", chunk, idem)
            ids = [d.get("id") for d in (res.get("data") or [])]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with conn:
                for p, rid in zip(ppl, ids + [None] * (len(ppl) - len(ids))):
                    conn.execute("INSERT OR REPLACE INTO courtesy_sent VALUES (?,?,?,?,?,?)",
                                 (p["email"], p["kind"], today, now, code, rid))
                    print(f"sent {p['kind']:<9} {p['email']}  {len(p['pieces'])} pieces  resend {rid}")
        print(f"sent {len(built)} courtesy emails with code {code}")
        return 0

    out = Path(a.preview) if a.preview else DEFAULT_PREVIEW_DIR
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, p in enumerate(people, 1):
        mail = build(p, code, end, PREVIEW_UNSUB, gift_cache)
        stem = f"{i:02d}_{p['kind']}_{re.sub(r'[^a-z0-9]+', '_', p['email'].lower())}"
        (out / f"{stem}.html").write_text(mail["html"])
        (out / f"{stem}.txt").write_text(f"Subject: {mail['subject']}\n\n{mail['text']}")
        meta.append({"email": p["email"], "kind": p["kind"], "wall_clicks": p["wall_clicks"], "pieces": p["pieces"],
                     "gift_links_used": mail["gift_links_used"], "file": f"{stem}.html"})
    (out / "previews.json").write_text(json.dumps({"generated": datetime.now(ET).isoformat(timespec="seconds"),
                                                    "min_clicks": a.min_clicks, "days": a.days, "code": code,
                                                    "code_ends": end, "stats": stats, "people": meta}, indent=2))
    print(f"wrote {len(meta)} previews to {out} (code shown: {code}{", a placeholder" if not promo else ""}; nothing sent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
