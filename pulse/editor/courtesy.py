"""Courtesy email to free Housing at Noon readers who keep hitting the upgrade wall (owner, 21 Oct 2026).

Owner's instruction: "The courtesy email to repeat wall hitters with the links they tried and a time-limited discount.
Remember let's include a gift link there." and "If the people hitting the wall are from a corporate address, we'll send
a different email. It is focused on all the things for corporate readers: the expensing language, group discounts, a
prewritten email they can forward, and anything else you can think of that would sell a corporate reader."

Revised the same evening (owner, 21 Oct 2026): "Those emails are mostly fine, but they're pretty impersonal and curt. I
want them to be much warmer and friendlier. Instead of 'Hi' it could be 'Hi there' and then thank them for reading. We
should send this on the same day that they clicked so we should be doing this every day. We can say, 'In today's Housing
at Noon.' You should remind them what Housing at Noon is and you can say, 'As a courtesy here's the article you
clicked.' You don't need to say the piece from ft.com. Just give them the link to the piece. Make it look nice."

Who gets it: by default, free-tier subscribers with one or more walled clicks in TODAY's edition (--days 0,
--min-clicks 1; the noon-courtesy.timer runs it Mon-Fri at 5:20 pm ET), after dropping mail-security link scanners
(click_report.scanner_emails, per edition), premium subscribers, anyone no longer subscribed, the owner's own addresses,
and anyone already in courtesy_sent (one email per person, ever). Corporate readers come first, so they get the scarce
gift links. Each email lists up to five of the articles the person clicked, most recent first, with the article's own
headline as the link text (pulse.db title, else the page's og:title, else the theme), a gift link for FT, WSJ and
Bloomberg when gift_links.py can make one, and a 20%-off-the-first-year Stripe promotion code NOON<MMDD> valid 7 days.
Every real send is BCC'd to the owner (paths.OWNER_EMAIL), because Resend sends do not appear in his Gmail Sent folder;
--test-to goes to him directly and carries no BCC. Every test and send is logged to ~/work/noon/logs/courtesy_sends.jsonl.

Two kinds, chosen by the address (classify): personal mailboxes (Gmail, iCloud, university addresses and so on) get the
short note; every other domain gets the corporate note (expensing, invoice on request, team pricing, a note to forward
to a manager). Both are styled like the edition: logo masthead, article cards, a discount callout with a button.

    courtesy.py                                  # preview (default) of today's clicks into ~/work/noon/clicks/courtesy_preview
    courtesy.py --days 7 --preview DIR           # preview the last 7 days into DIR: one HTML per recipient + previews.json
    courtesy.py --test-to aziz@home-economics.us --days 7  # one sample of each kind to the owner, subject [TEST]
    courtesy.py --create-code                    # create (idempotently) today's Stripe promotion code, print it
    courtesy.py --send --create-code --telegram  # the daily job: real send, then a Telegram summary via tgnotify

Nothing is sent without --test-to or --send, and --send refuses to run without a verified active code. With no one to
write to, --send creates no code and only reports.
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
    itself, and the preview shows the gift wording over the plain link. On a real send it is called once per
    reader and article, never reused from an earlier reader in the same run, so FT's three-view limit is counted
    per reader (gift_links reuses its own cached link without opening the browser)."""
    if _gift_link is None:
        return None
    if cache.get("_preview"):
        try:
            return url if _gift_eligible and _gift_eligible(url) else None
        except Exception:  # noqa: BLE001
            return None
    try:
        g = _gift_link(url) or None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"gift link failed for {url}: {e}")
        g = None
    cache.setdefault("_used", []).append((url, g))
    return g


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

# Look of the edition (pulse/scripts/delivery/email_lunch.py): white page, 600px column, gray logo top left, system
# sans stack, ink #3D3733, links #0BB4FF, #F6F7F3 boxes, cream #DADFCE as the only rule colour. No bold anywhere.
INK, MUTED, LINK, LIGHT, CREAM, WHITE = "#3D3733", "#8A8580", "#0BB4FF", "#F6F7F3", "#DADFCE", "#FFFFFF"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "'SFMono-Regular', Menlo, Consolas, 'Liberation Mono', monospace"
LOGO_URL = "https://noon.homeeconomics.us/images/logo-email-black.png"   # full-strength #3D3733 (owner, 22 Oct 2026)
LOGO_WIDTH = 120
P = f'<p style="margin:0 0 18px 0;font-family:{FONT};font-size:16px;line-height:1.5;color:{INK};">'
A = f'style="color:{LINK};text-decoration:none;"'
SIGNATURE = (f'<div style="margin:28px 0 0 0;font-family:{FONT};font-size:15px;line-height:1.55;color:{INK};">'
             f'Aziz Sunderji<br><a href="https://www.homeeconomics.us" {A}>www.homeeconomics.us</a><br>'
             f'<a href="https://fantastical.app/azizsunderji/my-template" {A}>Schedule meeting</a></div>')
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


# The article's own headline is the link text (owner, 21 Oct 2026: "Just give them the link to the piece"). The edition
# keeps no source titles (its anchor texts are verbs such as "reported"), so the headline comes from, in order: the
# collector's title for the same URL in pulse.db (RSS and newsletter items carry the publisher's headline), the page's
# og:title / <title> fetched at build time with a short timeout, and finally the theme title from the edition.
TITLE_TIMEOUT_S = 5
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/124.0 Safari/537.36")
_JUNK_TITLE = re.compile(r"subscribe|just a moment|access denied|are you a robot|attention required|not found|"
                         r"log ?in|sign ?in|sign up|error|verify|captcha|^\W*$|^(x|twitter|home)$", re.I)
_SITE_NAMES = {"financial times", "ft", "wsj", "the wall street journal", "wall street journal", "bloomberg",
               "bloomberg.com", "reuters", "the new york times", "nytimes.com", "the washington post", "cnbc",
               "axios", "politico", "the economist", "marketwatch", "barron's", "yahoo finance", "fortune",
               "business insider", "substack", "realtor.com", "redfin", "zillow", "nar", "census.gov"}
_title_cache: dict[str, str | None] = {}


def _clean_title(t: str, host: str) -> str | None:
    t = re.sub(r"\s+", " ", _html.unescape(t or "")).strip()
    # drop a trailing " | Site" / " - Site" when the last segment is the publication's name
    m = re.match(r"^(.*\S)\s+[|\-–—:]\s+([^|\-–—:]{2,40})$", t)
    if m:
        tail = m.group(2).strip().lower()
        site = re.sub(r"[^a-z]", "", host.split(".")[-2] if host.count(".") >= 1 else host)
        if tail in _SITE_NAMES or (site and site in re.sub(r"[^a-z]", "", tail)):
            t = m.group(1).strip()
    if len(t) < 12 or len(t) > 200 or _JUNK_TITLE.search(t):
        return None
    return t


def _db_title(url: str, host: str) -> str | None:
    path = os.environ.get("PULSE_DB")
    if not path or not os.path.exists(path):
        return None
    base = url.split("#")[0].split("?")[0]
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT title FROM items WHERE url IN (?,?,?,?) ORDER BY id DESC LIMIT 5",
                           (url, base, base.rstrip("/"), base.rstrip("/") + "/")).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pulse.db title lookup failed for {url}: {e}")
        return None
    for (t,) in rows:
        c = _clean_title(t, host)
        if c:
            return c
    return None


def _fetch_title(url: str, host: str = "") -> str | None:
    """og:title (else twitter:title, else <title>) of the page, or None on any error or a paywall/robot title."""
    host = host or (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "text/html,*/*;q=0.5",
                                                   "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=TITLE_TIMEOUT_S) as r:
            if "html" not in (r.headers.get("Content-Type") or "").lower():
                return None
            page = r.read(400_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except Exception:  # noqa: BLE001
        return None
    for pat in (r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:title["\']',
                r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)',
                r"<title[^>]*>([^<]+)</title>"):
        m = re.search(pat, page, re.I)
        if m:
            c = _clean_title(m.group(1), host)
            if c:
                return c
    return None


def _headline(p: dict) -> str | None:
    url = p["url"]
    if url not in _title_cache:
        host = p.get("host") or (urllib.parse.urlsplit(url).hostname or "").removeprefix("www.")
        _title_cache[url] = _db_title(url, host) or _fetch_title(url, host)
    return _title_cache[url]


def _label(p: dict) -> str:
    m = re.search(r"freedomforum\.org/dfp/pdf\d+/[A-Z]{2}_([A-Z]+)\.pdf", p["url"])
    if m:
        day = datetime.strptime(p["edition"], "%Y-%m-%d").strftime("%B %-d")
        return f"The front page of {FRONT_PAGES.get(m.group(1), 'a newspaper')} for {day} (a PDF)"
    m = re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/", p["url"])
    if m:   # X's page title is "Name (@handle) on X"
        t = _fetch_title(p["url"], "x.com")
        who = t[:-5] if t and t.endswith(" on X") else f"@{m.group(1)}"
        return f"A post by {who} on X"
    # anchor texts in the edition are usually a verb ("reported", "framed"), so they are a last resort
    return _headline(p) or p["theme"] or (p["text"] if len(p["text"]) >= 25 else "") or p["host"]


GIFT_NOTE = "This is a gift link, so it opens without a subscription."


def _table(inner: str, td_style: str, margin: str = "0", bg: str | None = None, cls: str = "") -> str:
    """One-cell layout table (tables survive Outlook and Gmail where div padding and radius do not)."""
    bga = f' bgcolor="{bg}"' if bg else ""
    bgs = f"background-color:{bg};" if bg else ""
    ca = f' class="{cls}"' if cls else ""
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:separate;margin:{margin};"><tr><td{bga}{ca} style="{bgs}{td_style}">{inner}'
            f'</td></tr></table>')


def _gift_wanted(url: str) -> bool:
    """True when this URL is one we would normally gift (FT, WSJ, Bloomberg)."""
    try:
        return bool(_gift_eligible and _gift_eligible(url))
    except Exception:  # noqa: BLE001
        return False


def _piece_lines(pieces: list[dict], gift_cache: dict) -> tuple[str, str, int, bool]:
    """Card list (HTML), plain-text list, how many gift links were used, and whether any article that should
    have had a gift link went out without one (then the email drops the courtesy wording: Aziz, 22 Oct 2026)."""
    cards, items_t, n_gift, missing = [], [], 0, False
    for p in pieces:
        g = _gift(p["url"], gift_cache)
        if not g and _gift_wanted(p["url"]):
            missing = True
        href = g or p["url"]
        label = _label(p)
        note_h = note_t = ""
        if g:
            n_gift += 1
            note_h = (f'<div style="margin:6px 0 0 0;font-family:{FONT};font-size:13px;line-height:1.4;'
                      f'color:{MUTED};">{_esc(GIFT_NOTE)}</div>')
            note_t = f"\n  {GIFT_NOTE}"
        inner = (f'<a href="{_esc(href)}" style="color:{LINK};text-decoration:none;font-family:{FONT};'
                 f'font-size:17px;line-height:1.4;">{_esc(label)}</a>{note_h}')
        cards.append(_table(inner, "border-radius:6px;padding:14px 16px;", "0 0 10px 0", LIGHT))
        items_t.append(f"  {label}\n  {href}{note_t}")
    return (f'<div style="margin:0 0 26px 0;">{"".join(cards)}</div>', "\n\n".join(items_t), n_gift, missing)


def _noun(pieces: list[dict]) -> str:
    n = len(pieces)
    fronts = sum(1 for p in pieces if "freedomforum" in p["url"])
    posts = sum(1 for p in pieces if re.match(r"https?://(?:www\.)?(?:x\.com|twitter\.com|bsky\.app)/", p["url"]))
    if fronts == n:
        return "front page" if n == 1 else "front pages"
    if posts == n:
        return "post" if n == 1 else "posts"
    if fronts or posts:
        return "link" if n == 1 else "links"
    return "article" if n == 1 else "articles"


def _when(pieces: list[dict], today: str) -> str:
    """"today's Housing at Noon" for today's edition, the weekday for another single edition, else the past week."""
    eds = sorted({p["edition"] for p in pieces})
    if eds == [today]:
        return "today's Housing at Noon"
    if len(eds) == 1:
        return f"{datetime.strptime(eds[0], '%Y-%m-%d').strftime('%A')}'s Housing at Noon"
    return "Housing at Noon this past week"


# Owner, 22 Oct 2026: conversational, "thanks" not "thank you", contractions, the reminder in parentheses.
THANKS = "I wanted to say thanks for reading Housing at Noon"
REMINDER = ("(my weekday email about the housing market, which sorts the day's housing news, data releases and "
            "research into themes).")


def _opening(pieces: list[dict], today: str, plain: bool = False) -> str:
    n = len(pieces)
    what = ("a link that is reserved for premium readers" if n == 1
            else f"{_NUM.get(n, str(n))} links that are reserved for premium readers")
    here = "here's the" if n == 1 else "here are the"
    if plain:   # a gift link ran out: ordinary link, no "courtesy" claim (Aziz, 22 Oct 2026)
        return f"In {_when(pieces, today)}, you clicked {what}. {here[0].upper() + here[1:]} {_noun(pieces)} you clicked:"
    return f"In {_when(pieces, today)}, you clicked {what}. As a courtesy, {here} {_noun(pieces)} you clicked:"


def subject_for(pieces: list[dict], today: str) -> str:
    return f"The {_noun(pieces)} you clicked in {_when(pieces, today)}"


# Owner, 22 Oct 2026: "I'd encourage you to sign up for the premium edition, which has [you only got X of X themes]
# every theme of the day, and then a direct link to every source." No "daily chart". Then the 20% offer.
_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
          11: "eleven", 12: "twelve"}


def _theme_counts(edition: str) -> tuple[int, int] | None:
    """(themes in the free edition, themes in the premium edition) for that day's draft, or None."""
    try:
        from delivery import email_lunch as el
        d = drafts.get(edition)
        obj = (d or {}).get("json") if isinstance((d or {}).get("json"), dict) else (d or {})
        entries = [e for e in (obj.get("entries") or []) if isinstance(e, dict)]
        entries.sort(key=lambda e: (e.get("rank") is None, e.get("rank", 10**6)))
        shown, withheld = el._split_entries(entries, "free")
        if shown and withheld:
            return len(shown), len(shown) + len(withheld)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"theme counts for {edition}: {e}")
    return None


def premium_line(pieces: list[dict], today: str) -> str:
    eds = sorted({p["edition"] for p in pieces})
    counts = _theme_counts(eds[-1]) if eds else None
    if counts:
        free_n, all_n = counts
        day = "today's" if eds[-1] == today else datetime.strptime(eds[-1], "%Y-%m-%d").strftime("%A's")
        got = f" ({day} free edition carried {free_n} of the {all_n})"   # digits for both, so "5 of the 14" reads evenly
    else:
        got = ""
    return (f"I'd encourage you to sign up for the premium edition, which has every theme of the day{got} and a direct "
            f"link to every source. I'd love to offer you 20% off your first year.")


def _discount(code: str, end: str) -> tuple[str, str]:
    """Soft callout: the offer, the code (monospace, letter-spaced, not bold), the prices, a button and the plain URL."""
    m, a = MONTHLY_USD * 0.8, ANNUAL_USD * 0.8
    prices = (f"That makes it ${m:.2f} a month instead of ${MONTHLY_USD} for twelve months, or ${a:,.0f} instead of "
              f"${ANNUAL_USD} on the annual plan. The code works through {end}.")
    t = (f"20% off your first year\n"
         f"Enter this code at checkout: {code}\n"
         f"{prices}\n"
         f"Upgrade: {UPGRADE_URL}")
    code_span = (f'<span style="display:inline-block;font-family:{MONO};font-size:19px;letter-spacing:0.14em;'
                 f'color:{INK};background-color:{LIGHT};padding:4px 10px;border-radius:4px;white-space:nowrap;">'
                 f'{_esc(code)}</span>')
    button = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
              f'<td bgcolor="{LINK}" style="background-color:{LINK};border-radius:6px;">'
              f'<a href="{UPGRADE_URL}" target="_blank" style="display:inline-block;padding:12px 24px;'
              f'font-family:{FONT};font-size:16px;line-height:1.2;color:{WHITE};text-decoration:none;'
              f'border-radius:6px;">Upgrade with 20% off</a></td></tr></table>')
    inner = (f'<div style="margin:0 0 12px 0;font-family:{FONT};font-size:18px;line-height:1.35;color:{INK};">'
             f'20% off your first year</div>'
             f'<div style="margin:0 0 6px 0;font-family:{FONT};font-size:15px;line-height:1.5;color:{INK};">'
             f'Enter this code at checkout:</div>'
             f'<div style="margin:0 0 16px 0;">{code_span}</div>'
             f'<div style="margin:0 0 18px 0;font-family:{FONT};font-size:15px;line-height:1.5;color:{INK};">'
             f'{_esc(prices)}</div>'
             f'{button}'
             f'<div class="hn-url" style="margin:10px 0 0 0;font-family:{FONT};font-size:13px;line-height:1.4;word-break:break-all;">'
             f'<a href="{UPGRADE_URL}" style="color:{MUTED};text-decoration:none;">{UPGRADE_URL}</a></div>')
    h = _table(inner, f"border:1px solid {CREAM};border-radius:8px;padding:20px 20px 18px 20px;", "0 0 26px 0", cls="hn-box")
    return h, t


CLOSING = "Thanks again for reading. If you have a question or a suggestion, just reply. It comes straight to me."

MOBILE_CSS = ("<style>@media only screen and (max-width:480px){.hn-col{padding:20px 16px 8px 16px !important;}"
              ".hn-col img{width:110px !important;}.hn-box{padding:18px 14px 16px 14px !important;}"
              ".hn-url{font-size:12px !important;}}</style>")


def _wrap(body_h: str, unsub: str | None) -> str:
    logo = (f'<img src="{LOGO_URL}" alt="Home Economics" width="{LOGO_WIDTH}" style="display:block;width:{LOGO_WIDTH}px;'
            f'max-width:100%;height:auto;border:0;margin:0 0 30px 0;">')
    doc = (f'<!doctype html><html><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width, initial-scale=1">{MOBILE_CSS}</head>'
           f'<body style="margin:0;padding:0;background-color:{WHITE};font-family:{FONT};">'
           f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{WHITE}" '
           f'style="background-color:{WHITE};"><tr><td align="center" style="padding:0;">'
           f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
           f'style="width:100%;max-width:600px;"><tr><td class="hn-col" align="left" '
           f'style="padding:32px 24px 8px 24px;font-family:{FONT};font-size:16px;line-height:1.5;color:{INK};">'
           f'{logo}{body_h}</td></tr></table></td></tr></table></body></html>')
    return render.with_footer(doc, unsub)


def _intro(person: dict, today: str, plain: bool = False) -> tuple[str, str]:
    opening = _opening(person["pieces"], today, plain)
    h = f"{P}Hi there,</p>{P}{_esc(THANKS)} {_esc(REMINDER)}</p>{P}{_esc(opening)}</p>"   # one sentence
    t = f"Hi there,\n\n{THANKS} {REMINDER}\n\n{opening}"
    return h, t


def personal_email(person: dict, code: str, end: str, unsub: str | None, gift_cache: dict, today: str) -> dict:
    lh, lt, n_gift, no_gift = _piece_lines(person["pieces"], gift_cache)
    dh, dt = _discount(code, end)
    ih, it = _intro(person, today, plain=no_gift)
    pl = premium_line(person["pieces"], today)
    body_h = f"{ih}{lh}{P}{_esc(pl)}</p>{dh}{P}{_esc(CLOSING)}</p>{SIGNATURE}"
    text = f"{it}\n\n{lt}\n\n{pl}\n\n{dt}\n\n{CLOSING}\n\n{SIGNATURE_TEXT}\n"
    return {"subject": subject_for(person["pieces"], today), "html": _wrap(body_h, unsub),
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
        "I'd like to expense a subscription to Housing at Noon, a daily email on the US housing market from Aziz "
        "Sunderji at Home Economics. Each weekday at noon Eastern it sorts the day's housing news, data releases and "
        "research into themes, with a direct link to every source. It'd replace the time I now spend scanning a dozen "
        "sources each day.",
        "",
        f"It costs ${MONTHLY_USD} a month or ${ANNUAL_USD} a year, and the monthly plan can be cancelled at any time. There's a "
        "team rate for three or more people, if others here would find it useful.",
        "",
        "Would it be OK for me to expense it?",
        "",
        "[Your name]",
    ]
    text = "\n".join("> " + l if l else ">" for l in lines)
    body = [l for l in lines if l]
    inner = "".join(f'<div style="margin:0 0 {0 if i == len(body) - 1 else 10}px 0;">{_esc(l)}</div>'
                    for i, l in enumerate(body))
    h = (f'<div style="margin:0 0 26px 0;padding:2px 0 2px 16px;border-left:3px solid {CREAM};font-family:{FONT};'
         f'font-size:15px;line-height:1.5;color:{INK};">{inner}</div>')
    return h, text


EXPENSE = ("If you read Housing at Noon for work, your employer may well pay for it. Subscriptions to research "
           "services are usually reimbursable as a business expense. Every payment comes with a Stripe receipt you can "
           "attach to an expense report, and if your finance team needs a proper invoice with your company's name and "
           "address, I'm happy to send one.")
TEAM = ("If three or more people on your team would read it, I offer team pricing with a single invoice for the group. "
        "Reply with the number of people and I'll send you a quote.")
FORWARD = "If it helps, here's a short note you could send to your manager:"


def corporate_email(person: dict, code: str, end: str, unsub: str | None, gift_cache: dict, today: str) -> dict:
    lh, lt, n_gift, no_gift = _piece_lines(person["pieces"], gift_cache)
    dh, dt = _discount(code, end)
    ih, it = _intro(person, today, plain=no_gift)
    nh, nt = _manager_note()
    pl = premium_line(person["pieces"], today)
    body_h = (f"{ih}{lh}{P}{_esc(pl)}</p>{dh}{P}{_esc(EXPENSE)}</p>{P}{_esc(TEAM)}</p>"
              f"{P}{_esc(FORWARD)}</p>{nh}{P}{_esc(CLOSING)}</p>{SIGNATURE}")
    text = (f"{it}\n\n{lt}\n\n{pl}\n\n{dt}\n\n{EXPENSE}\n\n{TEAM}\n\n{FORWARD}\n\n{nt}\n\n"
            f"{CLOSING}\n\n{SIGNATURE_TEXT}\n")
    return {"subject": subject_for(person["pieces"], today), "html": _wrap(body_h, unsub),
            "text": text + _text_footer(unsub), "gift_links_used": n_gift}


def _text_footer(unsub: str | None) -> str:
    parts = ([f"Unsubscribe: {unsub}"] if unsub else []) + [render.PULSE_POSTAL_ADDRESS]
    return "\n--\nYou're receiving this because you subscribe to Housing at Noon.\n" + "\n".join(parts) + "\n"


def build(person: dict, code: str, end: str, unsub: str | None, gift_cache: dict, today: str | None = None) -> dict:
    fn = corporate_email if person["kind"] == "corporate" else personal_email
    return fn(person, code, end, unsub, gift_cache, today or drafts.today_et())


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
        # The price ids are "sensitive" in Vercel and cannot be pulled, so find the Housing at Noon product by name.
        prods = [p["product"] for p in _stripe("GET", "/prices", {"active": "true", "limit": 100, "expand[]": "data.product"})["data"]
                 if "noon" in (p["product"].get("name") or "").lower()]
        if not prods:
            raise StripeError("no active Stripe price whose product name contains 'Noon'")
        coupon = _stripe("POST", "/coupons", {
            "id": coupon_id, "name": COUPON_NAME if pct == 20 else f"Housing at Noon courtesy {pct}%",
            "percent_off": pct, "duration": "repeating", "duration_in_months": 12,
            "applies_to[products][]": sorted({p["id"] for p in prods})})   # Housing at Noon only, not the Pro Map
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

def _window(days: int) -> str:
    return "today's edition" if days == 0 else f"the last {days} days"


def _summary(people: list[dict], stats: dict, min_clicks: int, days: int) -> str:
    n_p = sum(1 for p in people if p["kind"] == "personal")
    lines = [f"courtesy candidates ({min_clicks}+ walled clicks in {_window(days)}): {len(people)} "
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


SEND_LOG = Path(os.environ.get("NOON_LOG_DIR", str(Path.home() / "work" / "noon" / "logs"))) / "courtesy_sends.jsonl"


def _log(record: dict) -> None:
    """One JSON line per test or real send, so tests are on record next to the real sends."""
    try:
        SEND_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SEND_LOG.open("a") as f:
            f.write(json.dumps({"at": datetime.now(ET).isoformat(timespec="seconds"), **record}) + "\n")
    except OSError as e:
        logger.warning(f"could not write {SEND_LOG}: {e}")


def _quota_line() -> str:
    """Gift-link credits left per host, as last seen by gift_links.py (FT 20 and Bloomberg 5 a month; WSJ unknown)."""
    try:
        import gift_links
        con = gift_links._db()
        rows = {r["host"]: dict(r) for r in con.execute("SELECT * FROM gift_state")}
        con.close()
    except Exception as e:  # noqa: BLE001
        return f"gift quota unreadable ({type(e).__name__})"
    parts = []
    for host, name in (("ft", "FT"), ("bloomberg", "Bloomberg"), ("wsj", "WSJ")):
        r = rows.get(host) or {}
        if r.get("remaining") is not None:
            s = f"{name} {r['remaining']}" + (f"/{r['allowance']}" if r.get("allowance") else "") + " left"
        else:
            s = f"{name} quota not shown"
        if r.get("paused_until"):
            s += f" (paused: {r.get('pause_reason') or 'unknown'})"
        parts.append(s)
    return "; ".join(parts)


def _telegram(text: str) -> None:
    import subprocess
    try:
        subprocess.run([str(Path.home() / ".local" / "bin" / "tgnotify"), text], timeout=60, check=False,
                       capture_output=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tgnotify failed: {e}")


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=0, help="0 = today's edition only (the daily send)")
    ap.add_argument("--min-clicks", type=int, default=1)
    ap.add_argument("--preview", metavar="DIR", default=None)
    ap.add_argument("--test-to", metavar="EMAIL", default=None)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--create-code", action="store_true")
    ap.add_argument("--code", default=None, help="an existing active promotion code to use instead of creating one")
    ap.add_argument("--pct", type=int, default=20)
    ap.add_argument("--valid-days", type=int, default=7)
    ap.add_argument("--telegram", action="store_true", help="post a short summary to Aziz via ~/.local/bin/tgnotify")
    a = ap.parse_args()
    if a.send and a.test_to:
        print("choose one of --send or --test-to"); return 2

    today = drafts.today_et()
    tg = (lambda s: _telegram(s)) if a.telegram else (lambda s: None)
    people, stats = candidates(a.days, a.min_clicks, today)
    print(_summary(people, stats, a.min_clicks, a.days))

    if a.send and not people:
        msg = (f"Courtesy emails {today}: none to send. {stats['wall_people']} free readers hit the wall in "
               f"{_window(a.days)}; already sent {stats['already_sent']}, premium {stats['premium']}, owner "
               f"{stats['owner']}. No promotion code created.")
        print(msg); tg(msg)
        return 0

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
            tg(f"Courtesy emails {today}: NOT SENT, the Stripe code failed: {str(e)[:300]}")
            return 1
    elif a.code:
        try:
            promo = verify_code(a.code)
        except StripeError as e:
            print(f"CODE CHECK FAILED: {e}"); tg(f"Courtesy emails {today}: NOT SENT, code check failed: {e}")
            return 1

    if a.send and not promo:
        print("refusing to send: run with --create-code, or pass an existing active code with --code"); return 2
    if a.send and stats["subscriber_list_fallback"]:
        msg = "refusing to send: the subscriber list could not be read from Clerk"
        print(msg); tg(f"Courtesy emails {today}: NOT SENT, {msg}")
        return 2

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
            mail = build(p, code, end, None, gift_cache, today)
            res = _resend("https://api.resend.com/emails",
                          _message(p, mail, a.test_to, f"[TEST {kind}] {mail['subject']}", today, None))
            sent += 1
            _log({"mode": "test", "kind": kind, "to": a.test_to, "data_from": p["email"], "code": code,
                  "pieces": len(p["pieces"]), "gift_links": mail["gift_links_used"], "resend_id": res.get("id")})
            print(f"test {kind} (data from {p['email']}) sent to {a.test_to}: {res.get('id')}")
        return 0 if sent else 1

    if a.send:
        conn = _conn()
        msgs, built, gifts = [], [], []
        for p in people:
            unsub = make_unsubscribe_url(p["user_id"]) if p.get("user_id") else None
            if not unsub:
                print(f"skipping {p['email']}: no unsubscribe link (no Clerk user id or PULSE_UNSUB_SECRET)"); continue
            mail = build(p, code, end, unsub, gift_cache, today)
            m = _message(p, mail, p["email"], mail["subject"], today, unsub)
            m["bcc"] = [paths.OWNER_EMAIL]   # owner, 21 Oct 2026: a copy of every real courtesy email in his inbox
            msgs.append(m)
            built.append(p)
            gifts.append(mail["gift_links_used"])
        n_sent = 0
        for i in range(0, len(msgs), 100):
            chunk, ppl, gl = msgs[i:i + 100], built[i:i + 100], gifts[i:i + 100]
            idem = "courtesy-" + hashlib.sha1("|".join(p["email"] for p in ppl).encode()).hexdigest()[:24]
            res = _resend("https://api.resend.com/emails/batch", chunk, idem)
            ids = [d.get("id") for d in (res.get("data") or [])]
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with conn:
                for p, rid, g in zip(ppl, ids + [None] * (len(ppl) - len(ids)), gl):
                    conn.execute("INSERT OR REPLACE INTO courtesy_sent VALUES (?,?,?,?,?,?)",
                                 (p["email"], p["kind"], today, now, code, rid))
                    _log({"mode": "send", "kind": p["kind"], "to": p["email"], "code": code,
                          "pieces": len(p["pieces"]), "gift_links": g, "resend_id": rid})
                    print(f"sent {p['kind']:<9} {p['email']}  {len(p['pieces'])} pieces  resend {rid}")
                    n_sent += 1
        n_corp = sum(1 for p in built if p["kind"] == "corporate")
        print(f"sent {n_sent} courtesy emails with code {code}")
        tg(f"Courtesy emails {today}: sent {n_sent} ({n_sent - n_corp} personal, {n_corp} corporate), "
           f"{sum(gifts)} gift links used, code {code} through {end}. Gift quota: {_quota_line()}. "
           f"Copies are BCC'd to {paths.OWNER_EMAIL}.")
        return 0

    out = Path(a.preview) if a.preview else DEFAULT_PREVIEW_DIR
    out.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, p in enumerate(people, 1):
        mail = build(p, code, end, PREVIEW_UNSUB, gift_cache, today)
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
