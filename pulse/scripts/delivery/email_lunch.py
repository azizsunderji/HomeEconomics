"""News at Noon — email renderer (v4b briefing dict -> HTML).

Replaces the "Pulse"-era template in delivery/email_briefing.py for the
daily brief now called "News at Noon". Renders
from `briefing["entries"]` (the canonical ranked list produced by
v4b_runner) rather than the `conversation_themes` back-mapping.

Design rules (owner's brief, 2026-09):
  * White background everywhere. Body text #3D3733, links #0BB4FF.
  * No dark horizontal rules, no bordered boxes. Sections separate by
    whitespace. The only permitted separator colour is #F6F7F3 at 1px.
  * Masthead: Home Economics logo (LOGO_URL, ~140px, left-aligned) above
    the "News at Noon" text title, then the date. A wordmark image can
    replace the text title later via WORDMARK_URL. No small-caps kicker.
  * "Today's Themes": one synthesis paragraph at the top (never bullets),
    under its own section heading.
  * Numbered entries: title, summary with markdown links, one line of
    light source pills. No heat badges, no "Triggered by", no topic tags.
  * Compact four-up front-pages row with one sentence-cased headline each.
  * Free / premium tiering (see FREE_ENTRY_COUNT, UPGRADE_URL). In the
    free edition the gap between the withheld block and "On the Front
    Pages" is twice the standard section gap (WALL_GAP).
  * Every top-level section is separated by SECTION_GAP pixels.
  * Paper of the Day; then "From Home Economics" as a top-level heading
    with three subsections: Recent Publications (Substack feed), Tools
    (Pro Map, list-shaped so more can be added), and Home Economics in
    the News (press mentions; omitted entirely when there are none).
  * Footer is one light line. No cost line, no URL-audit strip, no logs link.
  * Unsubscribe block is appended by v4b_runner._lunch_footer at send
    time — not here.

Typography: the brand's Oracle typeface cannot be embedded in email (no
reliable @font-face support in Gmail / Outlook), so the template uses the
system font stack. Inline styles throughout; a single <style> block carries
only the mobile media query.

Public entry point:
    render_lunch_html(briefing, tier="premium") -> (html, top_entry_title, entry_count)
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

# Reuse the pieces of the old template that work: markdown-link rendering,
# HTML escaping, and the front-pages sidecar loader (same URLs / paths).
from delivery.email_briefing import _esc, _md_links, _load_front_pages_json

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────

TITLE = "News at Noon"
PUBLISHER = "Home Economics"

# Masthead logo: black PNG on transparent, ~14 KB, deployed 2026-09-02
# (HEAD -> 200, image/png, 14,596 bytes). Rendered ~140px wide, top-left.
LOGO_URL = "https://homeeconomics.us/logo-email.png"
LOGO_WIDTH = 100

# Wordmark slot. When a "News at Noon" wordmark graphic exists, set this to
# its URL and the masthead renders it as an <img> (WORDMARK_WIDTH px wide,
# alt=TITLE) in place of the <h1> text title. Leave empty for the text title.
WORDMARK_URL = ""
WORDMARK_WIDTH = 320

# Vertical rhythm. SECTION_GAP separates every top-level section (was a
# mix of 26/34/36px; raised ~50% and made uniform). WALL_GAP is the larger
# gap between the free edition's "N more in the premium edition" block and
# "On the Front Pages".
SECTION_GAP = 54
WALL_GAP = 72  # gap between the free-tier withheld box and On the Front Pages
# Gap between a subsection heading block and the next subsection inside
# "From Home Economics".
SUBSECTION_GAP = 28

# Free tier: when no entry carries a `tier` key, the top FREE_ENTRY_COUNT
# entries by rank are shown in the free edition and the rest are listed
# by title only under "N more in the premium edition".
FREE_ENTRY_COUNT = 5

# Upgrade page. The path still says /pulse/ and will be renamed later;
# keep every reference to it going through this constant.
UPGRADE_URL = "https://homeeconomics.us/pulse/upgrade"

# Pro Map page. HEAD-checked 2026-09-02: /promap -> 200; /pro-map, /tools,
# /pro, /map -> 404 on homeeconomics.us.
PRO_MAP_URL = "https://homeeconomics.us/promap"

# Home Economics publications feed. Resolved 2026-09-02:
#   https://home-economics.substack.com/feed  302-> https://homeeconomics.substack.com/feed
#   https://www.home-economics.us/feed        301-> https://home-economics.us/feed/ (WordPress; stale, last post Dec 2025)
# The Substack feed is the one that lists current posts.
HE_FEED_URL = "https://homeeconomics.substack.com/feed"
HE_FEED_TIMEOUT = 10
HE_FEED_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 NewsAtNoon/1.0")
HE_PUBLICATIONS_COUNT = 5
# pulse/scripts/delivery/he_publications.json (tracked, so GitHub Actions has it when Substack blocks the fetch)
# Live cache: written on every successful fetch; gitignored (pulse/data/).
HE_PUBLICATIONS_LIVE_CACHE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "he_publications.json"
)
# Tracked snapshot: read-only fallback shipped in the repo (refresh with
# scripts/refresh_he_publications.py); never written at render time.
HE_PUBLICATIONS_CACHE = (
    Path(__file__).resolve().parent / "he_publications.json"
)

# Palette (only these background colours may appear in the rendered HTML).
WHITE = "#FFFFFF"
LIGHT = "#F6F7F3"
INK = "#3D3733"
BLUE = "#0BB4FF"
MUTED = "#888888"

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

# ── Source pill canonical casing ────────────────────────────────────────
# Keyed by lowercase name. Feed names in the pipeline arrive in many
# casings and with section suffixes ("WSJ Tech", "FT Front Page",
# "Fortune | FORTUNE", "Calculatedrisk", "slowboring.com"); this map plus
# _canonical_source() collapses them to one display name each.
SOURCE_CANON = {
    "ft": "FT",
    "financial times": "FT",
    "wsj": "WSJ",
    "wall street journal": "WSJ",
    "the wall street journal": "WSJ",
    "nyt": "NYT",
    "new york times": "NYT",
    "the new york times": "NYT",
    "cnbc": "CNBC",
    "costar": "CoStar",
    "housingwire": "HousingWire",
    "housing wire": "HousingWire",
    "la times": "LA Times",
    "los angeles times": "LA Times",
    "latimes": "LA Times",
    "bloomberg": "Bloomberg",
    "calculated risk": "Calculated Risk",
    "calculatedrisk": "Calculated Risk",
    "slow boring": "Slow Boring",
    "slowboring": "Slow Boring",
    "fortune": "Fortune",
    "reuters": "Reuters",
    "axios": "Axios",
    "politico": "Politico",
    "the atlantic": "The Atlantic",
    "atlantic": "The Atlantic",
    "substack": "Substack",
    "twitter": "X",
    "x": "X",
    "twitter/x": "X",
    "bluesky": "Bluesky",
    "hacker news": "Hacker News",
    "hackernews": "Hacker News",
    "hn": "Hacker News",
    "reddit": "Reddit",
    "resiclub": "ResiClub",
    "the overshoot": "The Overshoot",
    "theovershoot": "The Overshoot",
    "u.s. census bureau": "Census Bureau",
    "census bureau": "Census Bureau",
    "city journal": "City Journal",
    "washington post": "Washington Post",
    "the washington post": "Washington Post",
}

# Platform keys from entry["sources"] (collector type -> pill label).
# "rss" is omitted: the named outlets in news_outlets already cover it.
PLATFORM_PILL = {
    "twitter": "X",
    "bluesky": "Bluesky",
    "substack": "Substack",
    "hackernews": "Hacker News",
    "reddit": "Reddit",
    "gmail": "Newsletter",
}


def _canonical_source(name: str) -> str:
    """Map a raw feed/outlet name to its canonical display casing.

    Order of attempts: exact lowercase match; strip an "Alert: " prefix;
    take the outlet half of "Author - Outlet" / "Outlet | OUTLET"; drop a
    bare domain suffix; then a leading-token match ("WSJ Tech" -> WSJ).
    Default preserves the given casing (never .title()).
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low in SOURCE_CANON:
        return SOURCE_CANON[low]
    if low.startswith("alert:"):
        low = low[len("alert:"):].strip()
        raw = raw[len("alert:"):].strip()
    if " - " in low:                      # "Matthew Yglesias - Slow Boring"
        low = low.rsplit(" - ", 1)[1].strip()
        raw = raw.rsplit(" - ", 1)[1].strip()
    if " | " in low:                      # "Fortune | FORTUNE"
        low = low.split(" | ", 1)[0].strip()
        raw = raw.split(" | ", 1)[0].strip()
    low_nodomain = re.sub(r"\.(com|co|org|net|us|io)$", "", low)
    if low_nodomain in SOURCE_CANON:
        return SOURCE_CANON[low_nodomain]
    # "Alert: Rebecca Picciotto Wsj" -> last token; "WSJ Tech" -> first token
    tokens = low_nodomain.split()
    if tokens:
        if tokens[0] in SOURCE_CANON and tokens[0] not in ("the", "x"):
            return SOURCE_CANON[tokens[0]]
        if tokens[-1] in SOURCE_CANON and tokens[-1] not in ("the", "x"):
            return SOURCE_CANON[tokens[-1]]
    return raw


def _entry_pills(entry: dict) -> list[str]:
    """Ordered, de-duplicated pill labels for one entry."""
    labels: list[str] = []
    seen: set[str] = set()
    for outlet in entry.get("news_outlets") or []:
        label = _canonical_source(outlet)
        if label and label.lower() not in seen:
            seen.add(label.lower())
            labels.append(label)
    sources = entry.get("sources") or {}
    if isinstance(sources, dict):
        for key in sources:
            label = PLATFORM_PILL.get(str(key).lower())
            if label and label.lower() not in seen:
                seen.add(label.lower())
                labels.append(label)
    return labels


# ── Headline sentence-casing ───────────────────────────────────────────
# Words to keep capitalised when converting an ALL-CAPS print headline to
# sentence case. Deliberately short; supplemented at render time by every
# capitalised word found in the mixed-case headlines of the same day.
_PROPER = {
    "u.s.", "u.k.", "u.n.", "e.u.", "d.c.", "n.y.", "l.a.",
    "us", "uk", "eu", "un", "nyc", "gop", "fed", "fbi", "cia", "epa",
    "fema", "nasa", "irs", "sec", "doj", "ai", "gdp", "ceo", "covid",
    "trump", "biden", "harris", "vance", "newsom", "adams", "congress",
    "senate", "house", "america", "american", "americans", "washington",
    "new", "york", "california", "texas", "florida", "los", "angeles",
    "houston", "chicago", "china", "chinese", "russia", "russian", "iran",
    "iranian", "israel", "israeli", "gaza", "ukraine", "ukrainian",
    "europe", "european", "mexico", "canada", "japan", "japanese",
    "india", "taiwan", "korea", "supreme", "court", "white", "pentagon",
    "democrats", "democrat", "republicans", "republican", "wall", "street",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
    "labor", "day", "christmas", "thanksgiving",
}


def _sentence_case_headline(text: str, proper: set[str] | None = None) -> str:
    """Convert an ALL-CAPS print headline to sentence case.

    Fixes the old _normalize_headline_caps, which .title()-cased every
    word ("A Bond Sell-Off Jolts Borrowers Across The Globe"). Here only
    the first letter, dotted acronyms (U.S.), and known proper nouns keep
    their capital. Mixed-case headlines are returned untouched.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    if sum(1 for c in letters if c.isupper()) / len(letters) < 0.95:
        return text[:1].upper() + text[1:]
    known = set(_PROPER)
    if proper:
        known |= {p.lower() for p in proper}
    out_words = []
    for i, word in enumerate(text.split(" ")):
        core = word.lower()
        bare = re.sub(r"^[^a-z0-9.]+|[^a-z0-9.]+$", "", core)
        if re.fullmatch(r"(?:[a-z]\.){2,}", bare):          # u.s. -> U.S.
            fixed = core.upper()
        elif bare in known:
            # Capitalise each hyphenated part: "sell-off" stays lower,
            # "al-qaeda" (if known) -> "Al-Qaeda".
            fixed = "-".join(p[:1].upper() + p[1:] for p in core.split("-"))
        elif bare == "i":
            fixed = "I"
        else:
            fixed = core
        if i == 0:
            fixed = fixed[:1].upper() + fixed[1:]
        out_words.append(fixed)
    return " ".join(out_words)


def _collect_proper_nouns(front_pages: dict) -> set[str]:
    """Capitalised words from the day's mixed-case headlines (excluding the
    first word of each), used as extra proper-noun hints."""
    found: set[str] = set()
    for paper in (front_pages or {}).values():
        for h in (paper or {}).get("headlines") or []:
            t = (h.get("text") or "").strip()
            letters = [c for c in t if c.isalpha()]
            if not letters or sum(c.isupper() for c in letters) / len(letters) > 0.7:
                continue                      # ALL CAPS: nothing to learn
            words = [w for w in t.split(" ")[1:] if re.search(r"[A-Za-z]", w)]
            if not words:
                continue
            cap_share = sum(1 for w in words if re.sub(r"^[^A-Za-z]+", "", w)[:1].isupper()) / len(words)
            if cap_share > 0.5:
                continue                      # Title Case (WSJ style): every word is capitalised, no signal
            for w in words:
                bare = re.sub(r"^[^A-Za-z0-9.]+|[^A-Za-z0-9.]+$", "", w)
                if bare[:1].isupper() and len(bare) > 1:
                    found.add(bare.lower())
    return found


# ── Home Economics publications ────────────────────────────────────────

def _parse_feed(xml_text: str) -> list[dict]:
    """Parse RSS 2.0 or Atom into [{title, url, date}] (date = 'Sep 1, 2026')."""
    root = ET.fromstring(xml_text)
    items: list[dict] = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    rss_items = list(root.iter("item"))
    if rss_items:
        for it in rss_items:
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            date_str = ""
            if pub:
                try:
                    date_str = parsedate_to_datetime(pub).strftime("%b %-d, %Y")
                except Exception:
                    date_str = pub[:16]
            if title and link:
                items.append({"title": title, "url": link, "date": date_str})
    else:
        for it in root.findall("atom:entry", ns):
            title = (it.findtext("atom:title", default="", namespaces=ns) or "").strip()
            link_el = it.find("atom:link", ns)
            link = (link_el.get("href") if link_el is not None else "") or ""
            pub = (it.findtext("atom:published", default="", namespaces=ns)
                   or it.findtext("atom:updated", default="", namespaces=ns) or "")
            date_str = ""
            if pub:
                try:
                    date_str = datetime.fromisoformat(pub.replace("Z", "+00:00")).strftime("%b %-d, %Y")
                except Exception:
                    date_str = pub[:10]
            if title and link:
                items.append({"title": title, "url": link, "date": date_str})
    return items


def _fetch_feed_text() -> str:
    """GET HE_FEED_URL and return the body. Substack answers httpx (any
    User-Agent) with 403 but serves curl normally — a TLS/HTTP-fingerprint
    check on their side — so curl is tried first and httpx is the fallback."""
    try:
        r = subprocess.run(
            ["curl", "-sSL", "--max-time", str(HE_FEED_TIMEOUT), "-A", HE_FEED_UA,
             "-H", "Accept: application/rss+xml, application/xml, text/xml, */*", HE_FEED_URL],
            capture_output=True, text=True, timeout=HE_FEED_TIMEOUT + 2,
        )
        if r.returncode == 0 and r.stdout.lstrip().startswith("<"):
            return r.stdout
        logger.warning(f"curl feed fetch failed rc={r.returncode}: {r.stderr[:200]}")
    except Exception as e:
        logger.warning(f"curl feed fetch failed: {e}")
    resp = httpx.get(HE_FEED_URL, timeout=HE_FEED_TIMEOUT, follow_redirects=True,
                     headers={"User-Agent": HE_FEED_UA,
                              "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    resp.raise_for_status()
    return resp.text


def load_he_publications(limit: int = HE_PUBLICATIONS_COUNT) -> list[dict]:
    """Latest Home Economics posts. Fetches HE_FEED_URL (10s timeout),
    caches the parsed list to HE_PUBLICATIONS_CACHE, and falls back to the
    cache on any failure. Returns [] when neither is available."""
    try:
        items = _parse_feed(_fetch_feed_text())
        if items:
            try:
                HE_PUBLICATIONS_LIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
                HE_PUBLICATIONS_LIVE_CACHE.write_text(json.dumps(
                    {"fetched_at": datetime.now(timezone.utc).isoformat(),
                     "feed": HE_FEED_URL, "items": items[:20]},
                    indent=2, ensure_ascii=False))
            except Exception as e:
                logger.warning(f"could not write publications cache: {e}")
            return items[:limit]
    except Exception as e:
        logger.warning(f"HE feed fetch failed ({HE_FEED_URL}): {e}")
    for cache in (HE_PUBLICATIONS_LIVE_CACHE, HE_PUBLICATIONS_CACHE):
        try:
            if cache.exists():
                cached = json.loads(cache.read_text())
                items = (cached.get("items") or [])[:limit]
                if items:
                    return items
        except Exception as e:
            logger.warning(f"could not read publications cache {cache}: {e}")
    return []


# ── Small building blocks ───────────────────────────────────────────────

def _spacer(height: int = 24) -> str:
    return (f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td height="{height}" style="height:{height}px; line-height:{height}px; '
            f'font-size:1px;">&nbsp;</td></tr></table>\n')


def _kicker(text: str) -> str:
    """Section heading: small, letter-spaced, muted. No rule above it."""
    return (f'<div style="font-family:{FONT}; font-size:12px; letter-spacing:2px; '
            f'text-transform:uppercase; color:{MUTED}; margin:0 0 14px 0;">'
            f'{_esc(text)}</div>\n')


def _heading(text: str) -> str:
    """Top-level heading (e.g. "From Home Economics"): larger than the
    section kickers and the entry titles, ink-coloured, bold."""
    return (f'<div style="font-family:{FONT}; font-size:24px; line-height:1.2; '
            f'font-weight:700; letter-spacing:-0.3px; color:{INK}; margin:0 0 20px 0;">'
            f'{_esc(text)}</div>\n')


def _subkicker(text: str) -> str:
    """Subsection heading under a _heading: same style as _kicker but a
    touch smaller, so the hierarchy reads heading > subsection > body."""
    return (f'<div style="font-family:{FONT}; font-size:11px; letter-spacing:2px; '
            f'text-transform:uppercase; color:{MUTED}; margin:0 0 10px 0;">'
            f'{_esc(text)}</div>\n')


def _pill(label: str) -> str:
    return (f'<span style="display:inline-block; background-color:{LIGHT}; '
            f'color:{INK}; font-size:12px; line-height:18px; padding:2px 9px; '
            f'border-radius:11px; margin:0 6px 6px 0; white-space:nowrap;">'
            f'{_esc(label)}</span>')


def _button(text: str, url: str) -> str:
    return (f'<a href="{url}" target="_blank" style="display:inline-block; '
            f'background-color:{BLUE}; color:{WHITE}; padding:9px 16px; '
            f'border-radius:4px; font-size:14px; font-weight:600; '
            f'text-decoration:none;">{_esc(text)}</a>')


BODY_LINK_STYLE = (
    f"color:{INK}; text-decoration:none; border-bottom:2px solid {BLUE}; padding-bottom:1px;"
)


# Reporting verbs. When a markdown link's anchor text contains one of these,
# only the verb stays linked ("Ned Resnikoff [argued](url)", "Alex Stapp
# [made](url) a parallel point") — the owner's house style.
_LINK_VERBS = {
    "argued", "argues", "reported", "reports", "noted", "notes", "wrote", "writes", "said",
    "says", "made", "makes", "found", "finds", "warned", "warns", "showed", "shows", "pointed",
    "points", "called", "calls", "added", "adds", "estimated", "estimates", "projected",
    "projects", "told", "posted", "posts", "tweeted", "explained", "explains", "published",
    "publishes", "released", "releases", "announced", "announces", "flagged", "flags",
    "highlighted", "highlights", "observed", "observes", "predicted", "predicts", "suggested",
    "suggests", "cited", "cites", "described", "describes", "framed", "frames", "put", "puts",
    "responded", "responds", "replied", "replies", "countered", "counters", "pushed",
    "pushes", "asked", "asks", "questioned", "questions", "documented", "documents",
    "tracked", "tracks", "calculated", "calculates", "confirmed", "confirms", "claimed",
    "claims", "declared", "declares", "concluded", "concludes", "surveyed", "measured",
    "detailed", "details", "outlined", "outlines", "shared", "shares", "laid", "lays",
    "quoted", "quotes", "signed", "signs", "vetoed", "vetoes", "filed", "files", "sued",
    "sues", "settled", "settles", "ruled", "rules", "blocked", "blocks", "approved",
    "approves", "passed", "passes", "introduced", "introduces", "unveiled", "unveils",
}


def _is_verb(word: str) -> bool:
    return re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", word).lower() in _LINK_VERBS


def _narrow_link_anchors(text: str) -> str:
    """House style: only the reporting verb carries the link.

    1. '[Ned Resnikoff argued](url) that' -> 'Ned Resnikoff [argued](url) that'
       (verb inside the anchor: keep only the verb).
    2. '[Nicholas Miller and Justin Lahart](url) quoted' ->
       'Nicholas Miller and Justin Lahart [quoted](url)'
       (no verb inside: the word right after the anchor is a verb).
    3. 'The MBA reported … [applications up 0.8%](url)' ->
       'The MBA [reported](url) … applications up 0.8%'
       (no verb inside or after: move the link back to the nearest
       reporting verb earlier in the same sentence, if it is not already
       linked).
    Anchors with no reachable verb are left untouched. Markdown in, markdown out."""
    anchor = r'(?:[^\[\]]|\[[^\]]*\])+'
    link_re = re.compile(rf'\[({anchor})\]\(([^)]+)\)')
    text = str(text)
    out = []
    pos = 0
    for m in link_re.finditer(text):
        inner, url = m.group(1), m.group(2)
        words = inner.split(" ")
        # case 1: verb inside the anchor
        idx = next((i for i, w in enumerate(words) if _is_verb(w)), None)
        if idx is not None and len(words) >= 2:
            before, after = " ".join(words[:idx]), " ".join(words[idx + 1:])
            rep = f"[{words[idx]}]({url})"
            rep = (before + " " + rep) if before else rep
            rep = (rep + " " + after) if after else rep
            out.append(text[pos:m.start()] + rep)
            pos = m.end()
            continue
        if idx is not None:          # single-word verb anchor: already right
            continue
        # case 2: verb immediately after the anchor (allow one small word between)
        tail = text[m.end():]
        mt = re.match(r"(\s+)((?:[a-z]{1,3}\s+)?)([A-Za-z]+)", tail)
        if mt and _is_verb(mt.group(3)):
            rep = inner + mt.group(1) + mt.group(2) + f"[{mt.group(3)}]({url})"
            out.append(text[pos:m.start()] + rep)
            pos = m.end() + mt.end()
            continue
        # case 3: nearest verb earlier in the same sentence, not inside another link
        head = text[pos:m.start()]
        sent_start = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("\n"))
        window = head[sent_start + 1:] if sent_start >= 0 else head
        cands = [mm for mm in re.finditer(r"[A-Za-z]+", window) if _is_verb(mm.group(0))]
        cands = [mm for mm in cands if "[" not in window[max(0, mm.start() - 1):mm.start()]]
        if cands:
            v = cands[-1]
            base = len(head) - len(window)
            vs, ve = base + v.start(), base + v.end()
            new_head = head[:vs] + f"[{head[vs:ve]}]({url})" + head[ve:]
            out.append(new_head + inner)
            pos = m.end()
            continue
        # nothing reachable: leave the link as written
    out.append(text[pos:])
    return "".join(out)


def _body_links(text: str) -> str:
    """_md_links() with every anchor restyled for body copy: ink-coloured,
    underlined with a 2px rule, never blue and never visited-purple. Keeps
    href/target, drops the incoming style. Anchors are first narrowed to
    the reporting verb (see _narrow_link_anchors)."""
    html = _md_links(_narrow_link_anchors(text))
    return re.sub(
        r'<a\s+href="([^"]+)"[^>]*>',
        lambda m: f'<a href="{m.group(1)}" target="_blank" style="{BODY_LINK_STYLE}">',
        html,
    )


def _link(text: str, url: str, color: str = BLUE, weight: str = "normal") -> str:
    return (f'<a href="{url}" target="_blank" style="color:{color}; '
            f'text-decoration:none; font-weight:{weight};">{_esc(text)}</a>')


def _format_date(date_str: str) -> str:
    """'2026-09-02' -> 'Wednesday, September 2, 2026'."""
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except Exception:
        d = datetime.now(timezone.utc)
    return d.strftime("%A, %B %-d, %Y")


# ── Intro paragraph ────────────────────────────────────────────────────

INTRO_MAX_CHARS = 320
INTRO_MAX_SENTENCES = 3


# Abbreviations that end with a period but do not end a sentence. A
# lowercase word after one of these is left alone.
_ABBREV = {
    "gov", "sen", "rep", "mr", "mrs", "ms", "dr", "prof", "st", "mt", "no", "vs", "inc", "co",
    "corp", "ltd", "jr", "sr", "u.s", "u.k", "e.g", "i.e", "etc", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "fig", "dept", "est", "approx",
    "a.m", "p.m", "gen", "lt", "col", "sgt", "capt", "ave", "blvd", "rd",
}


def _fix_sentence_starts(text: str) -> str:
    """Capitalise a lowercase word that begins a sentence: at the start of
    the text, after a paragraph break, or after '. ', '! ', '? ' when the
    preceding token is not a known abbreviation. Markdown links are left
    intact (the word inside '[' is handled like any other word)."""
    if not text:
        return text

    def _cap_first(seg: str) -> str:
        m = re.match(r"^(\s*\[?)([a-z])", seg)
        return seg[:m.end(1)] + m.group(2).upper() + seg[m.end():] if m else seg

    out_paras = []
    for para in text.split("\n\n"):
        para = _cap_first(para)

        def _after_punct(m):
            prev = m.group(1).lower().rstrip(".")
            if prev in _ABBREV or len(prev) <= 1:
                return m.group(0)
            return m.group(1) + m.group(2) + m.group(3) + m.group(4).upper()

        para = re.sub(r"([A-Za-z.]+)([.!?])(\s+\[?)([a-z])", _after_punct, para)
        out_paras.append(para)
    return "\n\n".join(out_paras)


def _shorten_intro(text: str) -> str:
    """Keep the first INTRO_MAX_SENTENCES sentences, stopping early once
    INTRO_MAX_CHARS is reached, always on a sentence boundary. The first
    sentence is always kept even if it alone exceeds the cap."""
    sents = [t for t in re.split(r"(?<=[.!?])\s+", text.strip()) if t]
    out: list[str] = []
    for sent in sents[:INTRO_MAX_SENTENCES]:
        if out and len(" ".join(out)) + 1 + len(sent) > INTRO_MAX_CHARS:
            break
        out.append(sent)
    return " ".join(out) if out else text.strip()


def _intro_text(briefing: dict, entries: list[dict]) -> tuple[str, str]:
    """Return (text, source_label). Priority: `intro` (editor-written) ->
    `pulse` if it is prose (or a dict/list carrying a prose field) ->
    `conversation_pulse` (the v1 synthesis paragraph) -> one sentence
    composed from the top three entry titles."""
    intro = briefing.get("intro")
    if isinstance(intro, str) and intro.strip():
        return intro.strip(), "intro"

    def _prose_from(val):
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for k in ("summary", "text", "paragraph", "prose", "pulse"):
                v = val.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        if isinstance(val, list):
            for v in val:
                p = _prose_from(v)
                if p:
                    return p
        return ""

    p = _prose_from(briefing.get("pulse"))
    if p:
        return _shorten_intro(p), "pulse"
    p = _prose_from(briefing.get("conversation_pulse"))
    if p:
        return _shorten_intro(p), "conversation_pulse"
    titles = [e.get("title", "").strip().rstrip(".") for e in entries[:3] if e.get("title")]
    if not titles:
        return "", "none"
    if len(titles) == 1:
        joined = titles[0]
    elif len(titles) == 2:
        joined = f"{titles[0]}, and {titles[1][:1].lower() + titles[1][1:]}"
    else:
        joined = (f"{titles[0]}; {titles[1][:1].lower() + titles[1][1:]}; "
                  f"and {titles[2][:1].lower() + titles[2][1:]}")
    return f"Today: {joined}.", "composed"


# ── Tiering ─────────────────────────────────────────────────────────────

def _split_entries(entries: list[dict], tier: str) -> tuple[list[dict], list[dict]]:
    """(shown, withheld) for the requested tier."""
    if tier != "free":
        return entries, []
    if any("tier" in e for e in entries):
        shown = [e for e in entries if e.get("tier") != "premium"]
        withheld = [e for e in entries if e.get("tier") == "premium"]
        return shown, withheld
    return entries[:FREE_ENTRY_COUNT], entries[FREE_ENTRY_COUNT:]


# ── Main renderer ───────────────────────────────────────────────────────

def render_lunch_html(briefing: dict, tier: str = "premium") -> tuple[str, str, int]:
    """Render a v4b briefing as the News at Noon email.

    Returns (html, top_entry_title, entry_count) where entry_count is the
    number of entries rendered in full for this tier. Mirrors the return
    shape of email_briefing.render_briefing_html so it can drop into
    v3_1_runner._render_variants-style code.
    """
    tier = "free" if str(tier).lower() == "free" else "premium"

    entries = [e for e in (briefing.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: (e.get("rank") is None, e.get("rank", 10**6)))
    shown, withheld = _split_entries(entries, tier)
    total = len(entries)
    top_title = (shown[0].get("title") if shown else (entries[0].get("title") if entries else "")) or ""

    date_line = _format_date(briefing.get("date") or "")
    intro, _intro_src = _intro_text(briefing, entries)
    paper = briefing.get("paper_of_the_day") or None
    press_mentions = briefing.get("_press_mentions") or []

    body_text = f"font-family:{FONT}; font-size:17px; line-height:1.6; color:{INK};"

    # Title: wordmark image when WORDMARK_URL is set, otherwise the text <h1>.
    if WORDMARK_URL:
        title_html = (f'<img src="{WORDMARK_URL}" alt="{_esc(TITLE)}" width="{WORDMARK_WIDTH}" '
                      f'style="display:block; width:{WORDMARK_WIDTH}px; max-width:100%; '
                      f'height:auto; margin:0 0 8px 0;">')
    else:
        title_html = (f'<h1 style="font-family:{FONT}; font-size:36px; line-height:1.1; '
                      f'font-weight:700; letter-spacing:-0.5px; color:{INK}; margin:0 0 8px 0;">'
                      f'{_esc(TITLE)}</h1>')

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(TITLE)}</title>
<style>
/* Mobile: stack the four front-page cells into a single column. */
@media screen and (max-width: 600px) {{
  .fp-row {{ display: block !important; }}
  .fp-cell {{ display: block !important; width: 100% !important; padding: 0 0 18px 0 !important; }}
  .fp-cell img {{ max-width: 100% !important; }}
}}
</style>
</head>
<body style="margin:0; padding:0; background-color:{WHITE};">
<center>
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="{WHITE}" style="background-color:{WHITE};">
<tr><td align="center">
<table cellpadding="0" cellspacing="0" width="100%" style="max-width:600px; width:100%; {body_text}">
<tr><td style="padding:28px 24px 32px 24px;">

<!-- MASTHEAD: logo (top-left), title, date. No small-caps publisher line. -->
<!-- Oracle (the brand typeface) cannot be embedded in email; system stack used. -->
<div style="margin:0 0 {SECTION_GAP}px 0;"><img src="{LOGO_URL}" alt="{_esc(PUBLISHER)}" width="{LOGO_WIDTH}" style="display:block; width:{LOGO_WIDTH}px; max-width:100%; height:auto;"></div>
<!-- WORDMARK SLOT: set WORDMARK_URL (module constant) and the text <h1> is
     replaced by <img src=WORDMARK_URL alt=TITLE width=WORDMARK_WIDTH>. -->
{title_html}
<div style="font-family:{FONT}; font-size:15px; color:{MUTED}; margin:0;">{_esc(date_line)}</div>
""")

    # ── Free-edition banner ──
    banner_html = ""
    if tier == "free":
        parts.append(_spacer(22))
        n_withheld = len(withheld)
        if n_withheld:
            clause = (f" Links are disabled, and {n_withheld} of today&rsquo;s {total} "
                      f"themes are only in the premium edition.")
        else:
            clause = " Links are disabled in this edition."
        banner_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td bgcolor="{LIGHT}" style="background-color:{LIGHT}; padding:14px 16px; '
            f'border-radius:6px; font-family:{FONT}; font-size:14px; line-height:1.5; color:{INK};">'
            f'You&rsquo;re reading the free edition of {_esc(TITLE)}.{clause} '
            f'{_link("Upgrade →", UPGRADE_URL, weight="600")}'
            f'</td></tr></table>\n'
        )
        parts.append(banner_html)

    # ── Today's Themes (synthesis paragraph) ──
    if intro:
        parts.append(_spacer(SECTION_GAP))
        parts.append(_kicker("Today’s Themes"))
        parts.append(
            f'<p style="{body_text} font-size:18px; line-height:1.65; margin:0;">'
            f'{_body_links(_fix_sentence_starts(intro))}</p>\n'
        )

    # ── Entries ──
    if shown:
        parts.append(_spacer(SECTION_GAP))
        for i, e in enumerate(shown, start=1):
            num = e.get("rank") if isinstance(e.get("rank"), int) else i
            title = (e.get("title") or "").strip()
            title = title[:1].upper() + title[1:]
            summary = _fix_sentence_starts((e.get("summary") or "").strip())
            pills = _entry_pills(e)
            pills_html = ""
            if pills:
                pills_html = (f'<div style="margin:10px 0 0 0; line-height:24px;">'
                              f'{"".join(_pill(p) for p in pills)}</div>')
            # Last entry carries no bottom padding so the following section
            # gap is exactly SECTION_GAP (or WALL_GAP) and nothing more.
            pad_bottom = 0 if i == len(shown) else 40
            parts.append(
                f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td style="padding:0 0 {pad_bottom}px 0;">'
                f'<table cellpadding="0" cellspacing="0" style="margin:0 0 8px 0;"><tr>'
                f'<td style="font-family:{FONT}; font-size:19px; line-height:1.3; font-weight:700; '
                f'color:{BLUE}; padding:0 12px 0 0; vertical-align:top; white-space:nowrap;">{num}</td>'
                f'<td style="font-family:{FONT}; font-size:19px; line-height:1.3; font-weight:700; '
                f'color:{INK}; padding:0; vertical-align:top;">{_esc(title)}</td>'
                f'</tr></table>'
                f'<div style="{body_text}">{_body_links(summary)}</div>'
                f'{pills_html}'
                f'</td></tr></table>\n'
            )

    # ── Withheld entries (free tier) ──
    if tier == "free" and withheld:
        n = len(withheld)
        items = "".join(
            f'<div style="{body_text} font-size:16px; margin:0 0 8px 0;">'
            f'{_esc((w.get("title") or "").strip())}</div>'
            for w in withheld
        )
        parts.append(_spacer(36))
        parts.append(
            f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td bgcolor="{LIGHT}" style="background-color:{LIGHT}; padding:18px 18px 14px 18px; border-radius:6px;">'
            f'{_kicker("More in the premium edition")}'
            f'{items}'
            f'<div style="margin:12px 0 0 0; font-family:{FONT}; font-size:14px;">'
            f'{_link("Upgrade →", UPGRADE_URL, weight="600")}</div>'
            f'</td></tr></table>\n'
        )

    # ── Front pages ──
    front = _load_front_pages_json()
    _cb = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    _et_now = datetime.now(timezone.utc) - timedelta(hours=4)  # ET ≈ UTC-4 (same as old renderer)
    _ff_day = _et_now.day
    papers = [
        ("nyt", "The New York Times",      "NY_NYT"),
        ("wsj", "The Wall Street Journal", "NY_WSJ"),
        ("lat", "Los Angeles Times",       "CA_LAT"),
        ("hc",  "Houston Chronicle",       "TX_HC"),
    ]
    proper = _collect_proper_nouns(front) if front else set()
    cells = []
    for slug, label, ff_slug in papers:
        pdata = (front or {}).get(slug) or {}
        heads = pdata.get("headlines") or []
        head = next((h for h in heads if (h.get("text") or "").strip()), None)
        head_html = ""
        if head:
            text = _esc(_sentence_case_headline((head.get("text") or "").strip(), proper))
            url = head.get("article_url") or ""
            inner = (f'<a href="{url}" target="_blank" style="color:{INK}; text-decoration:none;">{text}</a>'
                     if url else text)
            head_html = (f'<div style="font-family:{FONT}; font-size:13px; line-height:1.4; '
                         f'color:{INK}; margin:6px 0 0 0;">{inner}</div>')
        pdf_url = f"https://cdn.freedomforum.org/dfp/pdf{_ff_day}/{ff_slug}.pdf"
        img_url = f"https://home-economics.us/pulse-screenshots/{slug}.jpg?v={_cb}"
        short = {"nyt": "NYT", "wsj": "WSJ", "lat": "LA Times", "hc": "Houston Chronicle"}[slug]
        cells.append(
            f'<td class="fp-cell" width="25%" valign="top" style="padding:0 6px;">'
            f'<a href="{pdf_url}" target="_blank">'
            f'<img src="{img_url}" alt="{_esc(label)} front page" width="120" '
            f'style="display:block; width:100%; max-width:120px; height:auto;"></a>'
            f'<div style="font-family:{FONT}; font-size:11px; letter-spacing:1.5px; '
            f'text-transform:uppercase; color:{MUTED}; margin:8px 0 0 0;">{_esc(short)}</div>'
            f'{head_html}'
            f'</td>'
        )
    # Free edition: the withheld block sits right above this heading; give it
    # twice the standard gap so the wall reads as the end of the entries.
    parts.append(_spacer(WALL_GAP if (tier == "free" and withheld) else SECTION_GAP))
    parts.append(_kicker("On the Front Pages"))
    parts.append(
        f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 -6px;">'
        f'<tr class="fp-row">{"".join(cells)}</tr></table>\n'
    )

    # ── Paper of the Day ──
    if isinstance(paper, dict) and paper.get("title"):
        p_url = paper.get("url") or "#"
        meta = _esc(paper.get("publication") or "")
        if paper.get("date"):
            meta += (" &middot; " if meta else "") + _esc(paper["date"])
        key = (paper.get("key_finding") or "").strip()
        parts.append(_spacer(SECTION_GAP))
        parts.append(_kicker("Paper of the Day"))
        parts.append(
            f'<div style="font-family:{FONT}; font-size:19px; line-height:1.3; font-weight:700; margin:0 0 4px 0;">'
            f'<a href="{p_url}" target="_blank" style="color:{INK}; text-decoration:none;">{_esc(paper["title"])}</a></div>'
            f'<div style="font-family:{FONT}; font-size:14px; color:{INK}; margin:0 0 2px 0;">{_esc(paper.get("authors") or "")}</div>'
            f'<div style="font-family:{FONT}; font-size:13px; color:{MUTED}; margin:0 0 12px 0;">{meta}</div>'
            + (f'<div style="{body_text} font-weight:600; margin:0 0 10px 0;">{_esc(key)}</div>' if key else "")
            + f'<div style="{body_text} margin:0 0 16px 0;">{_body_links(paper.get("summary") or "")}</div>'
            f'<div>{_button("Read the paper →", p_url)}</div>\n'
        )

    # ── From Home Economics (top-level heading with three subsections) ──
    pubs = load_he_publications()
    parts.append(_spacer(SECTION_GAP))
    parts.append(_heading("From Home Economics"))
    subsections: list[str] = []

    # 1. Recent Publications (latest Substack posts). Omitted if the feed
    #    and cache are both unavailable.
    if pubs:
        rows = []
        for pub in pubs:
            date_bit = (f'<span style="color:{MUTED}; font-size:13px; margin-left:8px;">{_esc(pub.get("date") or "")}</span>'
                        if pub.get("date") else "")
            rows.append(
                f'<div style="{body_text} font-size:16px; margin:0 0 8px 0;">'
                f'{_link(pub.get("title") or "", pub.get("url") or "#", color=INK, weight="600")}{date_bit}</div>\n'
            )
        subsections.append(_subkicker("Recent Publications") + "".join(rows))

    # 2. Tools. One paragraph per tool: (name, blurb, cta_text, url). Add
    #    more tuples here as tools are launched.
    tools = [
        ("Pro Map",
         "The Pro Map puts Home Economics&rsquo; county- and ZIP-level housing data on one "
         "interactive map, from prices and rents to migration and permits.",
         "Explore the Pro Map →", PRO_MAP_URL),
    ]
    tool_rows = [
        f'<p style="{body_text} font-size:15px; margin:0 0 10px 0;">'
        f'{blurb} {_link(cta, url, weight="600")}</p>\n'
        for _name, blurb, cta, url in tools
    ]
    subsections.append(_subkicker("Tools") + "".join(tool_rows))

    # 3. Home Economics in the News (press mentions). Omitted entirely when
    #    the briefing carries none; nothing is fabricated.
    if press_mentions:
        rows = []
        for m in press_mentions[:10]:
            url = m.get("url") or ""
            src = (m.get("source") or "").strip()
            head = (m.get("headline") or "").strip()
            date_str = (m.get("date") or "").strip()
            src_html = _link(src, url, weight="600") if url else f'<span style="color:{BLUE}; font-weight:600;">{_esc(src)}</span>'
            head_html = _link(head, url, color=INK) if url else _esc(head)
            date_html = f'<span style="color:{MUTED}; font-size:13px;"> ({_esc(date_str)})</span>' if date_str else ""
            rows.append(
                f'<div style="{body_text} font-size:16px; margin:0 0 8px 0;">'
                f'<span style="font-size:13px;">{src_html}</span> {head_html}{date_html}</div>\n'
            )
        subsections.append(_subkicker("Home Economics in the News") + "".join(rows))

    parts.append(_spacer(SUBSECTION_GAP).join(subsections))

    # ── Free-edition banner, repeated at the bottom ──
    if tier == "free" and banner_html:
        parts.append(_spacer(SECTION_GAP))
        parts.append(banner_html)

    # ── Footer ──
    parts.append(_spacer(SECTION_GAP))
    parts.append(
        f'<div style="font-family:{FONT}; font-size:13px; color:{MUTED}; text-align:center;">'
        f'{_esc(TITLE)} &middot; {_esc(PUBLISHER)}</div>\n'
    )

    parts.append("""
</td></tr>
</table>
</td></tr>
</table>
</center>
</body>
</html>""")

    html = "".join(parts)
    return html, top_title, len(shown)


__all__ = [
    "render_lunch_html", "load_he_publications", "FREE_ENTRY_COUNT",
    "UPGRADE_URL", "PRO_MAP_URL", "HE_FEED_URL", "SOURCE_CANON",
    "TITLE", "LOGO_URL", "WORDMARK_URL", "SECTION_GAP", "WALL_GAP",
]
