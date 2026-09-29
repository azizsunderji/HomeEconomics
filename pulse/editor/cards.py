"""Social image cards for a Housing at Noon edition: Instagram and X carousels, and a
LinkedIn document PDF.

Owner's rules (Aziz, 28-29 Sep 2026):
  * One card per FREE theme, in free-edition order, and every theme fits on ONE card. No
    continuation cards.
  * "Text size must be constant" (29 Sep): the body is always 36 px. There is no smaller
    fallback size; nothing is ever set below 36 px and nothing is clipped.
  * The title sits on ONE line (29 Sep): 54 px where it fits the width, else stepped down
    2 px at a time to no less than 40 px. A generated title that does not fit at 40 px is
    shortened by the condensing model to the characters that fit (logged).
  * Layout (29 Sep): the theme's number as a 96 px bold blue numeral at top left, the small
    Home Economics logo at top right on the same row, vertically centred on the numeral;
    48 px top padding; the footer holds only "Housing at Noon · <date>", 40 px from the
    bottom edge. Source pills (as in the email) sit just above the footer on every card.
  * When a theme's text is longer than the card holds, the builder condenses it with
    Claude to fit. The condensed text keeps the theme's meaning, every number and every
    attribution, and at most two paragraphs.
  * "I want a way to edit the text" (29 Sep): the owner can replace a card's title and body
    on /cards/<date> (table card_overrides in the drafts DB, via drafts.py). An override is
    used instead of the generated text, without any Claude call, in every later render
    (including the one after the noon send). Position 0 holds an edited CTA description.
  * The set ends with a standalone call-to-action card (large logo, "Housing at Noon",
    one line of description, homeeconomics.us/noon). Instagram's cap of 10 includes it,
    so at most 9 themes; beyond that the last themes are dropped (logged), never the CTA.

How a theme is fitted:
  1. Title size: the largest of 54, 52 ... 40 px at which the title fits on one line.
  2. Budget. The card is rendered with this theme's own title and pills and a filler body
     (the theme's words, two paragraphs), and the longest filler that fits at 36 px is
     the character budget.
  3. If the visible text (links reduced to their anchor words) is within the budget and
     fits, it is used unchanged. Otherwise it is condensed:
     a. Facts to keep are extracted from the visible text, less its pointer sentences
        ("My X post on this is here"): every number, percentage, dollar figure and date
        (regexes), every @handle and "On X,"-style platform lead-in (regexes), every source
        pill the text names, and every other named source or person (Claude Haiku).
     b. Claude Sonnet condenses the text, given the facts list, a target of 85% of the
        budget and a hard ceiling of the budget, keeping the author's commentary.
     c. A reply passes when every fact is present and the whole text fits at 36 px.
        Otherwise up to 3 repair rounds follow, each naming the missing facts and the
        excess measured on the card. If the latest draft still does not fit at 36 px,
        up to 2 more repair rounds follow (5 in all) with a target of 75% of the budget.
     d. Last resort, if no draft fits at 36 px: the model is asked once to drop its least
        important sentence(s) while keeping every listed fact (WARNING logged).
  4. A condensed text is never set smaller. The draft that fits at 36 px with the fewest
     missing facts is used; a WARNING names anything missing. Only if no draft fits at all
     does the last guard cut at a sentence end at 36 px (WARNING); nothing is clipped.
Results are cached in NOON_CARDS_CACHE (default ~/work/noon/cards_cache.json), keyed by
sha1(PROMPT_VERSION + theme markdown + budget), so re-rendering the same draft gives the
same cards at no cost; bumping PROMPT_VERSION retires every cached text. Each
condensation, its fidelity (facts kept / total, repair rounds) and the token usage and
cost per model are logged.

Files: `Housing at Noon YYYY-MM-DD card1.png` … `cardK.png` (the themes, then the CTA
card; stale higher-numbered cards from an earlier render of the same date are removed),
`Housing at Noon YYYY-MM-DD carousel.pdf` (all cards, 1080x1350 px pages), in
NOON_CARDS_DIR (default NOON_PDF_DIR/cards), mirrored to NOON_PDF_DROPBOX_DIR/cards, and
`Housing at Noon YYYY-MM-DD cards.json` (what each card shows, its budget and title size;
read by the editor's card panel; not mirrored).

    python cards.py            # today's draft
    python cards.py --date 2026-09-04 --out /tmp/cards
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
import shutil
from datetime import datetime, timezone
from pathlib import Path

import paths  # noqa: F401  (sys.path setup)
import drafts

logger = logging.getLogger("noon.cards")

PDF_DIR = Path(os.environ.get("NOON_PDF_DIR", str(Path.home() / "work" / "noon" / "pdf")))
CARDS_DIR = Path(os.environ.get("NOON_CARDS_DIR", str(PDF_DIR / "cards")))
DROPBOX_DIR = os.environ.get("NOON_PDF_DROPBOX_DIR", "")
CACHE_PATH = Path(os.environ.get("NOON_CARDS_CACHE", str(Path.home() / "work" / "noon" / "cards_cache.json")))
SIGNUP = "homeeconomics.us/noon"
W, H = 1080, 1350
MAX_CARDS = 10          # Instagram's carousel limit, CTA card included
BODY_PX = 36               # constant (owner, 29 Sep 2026); there is no smaller fallback
TITLE_PXS = tuple(range(54, 39, -2))   # 54 ... 40: the largest that keeps the title on one line
TITLE_MIN_PX = TITLE_PXS[-1]
MARGIN = 80
TOP_PAD = 48               # above the numeral row (owner, 29 Sep 2026)
BOTTOM_PAD = 40            # below the footer line
CTA_DESC = "A daily brief on the U.S. housing market, free every weekday at noon ET"
# Sonnet condenses and repairs; Haiku extracts names (owner's choice, 28 Sep 2026).
# Condensation moved from claude-sonnet-5 to claude-sonnet-5-5 (owner, 29 Sep 2026).
# NOON_CARDS_MODEL=claude-sonnet-5 switches back.
CONDENSE_MODEL = os.environ.get("NOON_CARDS_MODEL", "claude-sonnet-5-5")
EXTRACT_MODEL = os.environ.get("NOON_CARDS_EXTRACT_MODEL", "claude-haiku-4-5")
PRICE_PER_MTOK = {"claude-sonnet-5-5": (2.00, 10.00), "claude-sonnet-5": (2.00, 10.00),
                  "claude-haiku-4-5": (1.00, 5.00)}  # in, out USD
# Part of the cache key: bump it whenever the prompts, the checks or the model change, so
# texts made under older rules are not reused.
PROMPT_VERSION = "cards-v8-2026-09-29-sonnet55"
# The facts extraction did not change in v7, so its cache entries (and Haiku calls) are kept.
FACTS_VERSION = "cards-v5-2026-09-28-facts"
TARGET_SHARE = 0.85       # target length as a share of the budget; the budget is the ceiling
FIT_TARGET_SHARE = 0.75   # target in the extra repair rounds for a draft that does not fit
MAX_REPAIRS = 3
MAX_FIT_REPAIRS = 5       # repair rounds in all when the latest draft still does not fit

INK, MUTED, BLUE, CREAM, LIGHT = "#3D3733", "#7F7570", "#0BB4FF", "#F6F7F3", "#DADFCE"
SANS = '"ABC Oracle Edu", "Helvetica Neue", Helvetica, Arial, sans-serif'
STATIC = Path(__file__).resolve().parent / "static"
LOGO_SVG = STATIC / "he-large-black.svg"
LOGO_PNG = STATIC / "he-large-black.png"

NUM_WORDS = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
             "Eleven", "Twelve"]

_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)")
# An image dropped in from the editor (![caption](url) on its own line) is left
# out of the cards.
_IMG_LINE = re.compile(r"^[ \t]*!\[[^\]\n]*\]\(https?://[^\s)]+\)[ \t]*$", re.M)


def _no_images(md: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _IMG_LINE.sub("", str(md or "")))


def plain(md: str) -> str:
    """Markdown -> plain text (links to their text, no markup). Used by xpost.py."""
    t = _LINK_RE.sub(r"\1", _no_images(md))
    t = re.sub(r"[*_`]+", "", t)
    return t.replace("\r", "").strip()


def date_label(date: str) -> str:
    return datetime.strptime(date[:10], "%Y-%m-%d").strftime("%A, %B %-d, %Y")


def _esc(s: str) -> str:
    return _html.escape(str(s or ""), quote=False)


_ABBREV = re.compile(r"(?:\b(?:[A-Z]\.){1,3}|\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|No|Inc|Co|Corp|Gov|Sen|Rep|Jan|Feb|"
                     r"Mar|Apr|Aug|Sept|Sep|Oct|Nov|Dec|approx|est)\.)$")
_SENT_BREAK = re.compile(r"[.?!][\"”’)]?\s+(?=[\"“‘(]?[A-Z0-9])")


def sentences(text: str) -> list[str]:
    """Plain text -> sentences (paragraph breaks always end a sentence). Initialisms such
    as 'U.S.' and common abbreviations do not end one."""
    out: list[str] = []
    for para in [p.strip() for p in str(text or "").split("\n\n") if p.strip()]:
        para = " ".join(para.split())
        start = 0
        for m in _SENT_BREAK.finditer(para):
            end = m.start() + len(m.group(0).rstrip())
            if _ABBREV.search(para[start:m.start() + 1]):
                continue
            out.append(para[start:end].strip())
            start = m.end()
        if para[start:].strip():
            out.append(para[start:].strip())
    return out


def _para_sentences(text: str) -> list[list[str]]:
    return [sentences(p) for p in text.split("\n\n") if p.strip()]


def hook_line(draft: dict) -> tuple[str, bool]:
    """(first sentence of the standfirst, whether it came from the first theme's title).
    No longer used by the cards; kept for xpost.py."""
    s = sentences(plain(draft.get("intro") or ""))
    if s:
        return s[0], False
    entries = [e for e in (draft.get("entries") or []) if (e.get("title") or "").strip()]
    return ((entries[0]["title"].strip(), True) if entries else ("Housing at Noon", False))


# ── the free edition's themes, as the email shows them ──────────────────

def free_themes(draft: dict) -> list[dict]:
    """[{pos, num, title, text, md, pills}] for the free edition, in its order: the same
    entry set, title casing, sentence-start fixes, link narrowing ("On X," before handles)
    and pills as email_lunch.render_lunch_html(tier="free"). `pos` is the position in the
    free edition (1..n, used for "Theme One"); `num` is the entry's rank; `text` is the
    visible text (links reduced to their anchor words, paragraphs separated by a blank
    line); `md` is the summary markdown."""
    from delivery import email_lunch as el
    entries = [e for e in (draft.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: (e.get("rank") is None, e.get("rank", 10**6)))
    shown, _withheld = el._split_entries(entries, "free")
    out = []
    for i, e in enumerate(shown, start=1):
        num = e.get("rank") if isinstance(e.get("rank"), int) else i
        title = (e.get("title") or "").strip()
        title = title[:1].upper() + title[1:]
        md = _no_images(e.get("summary") or "").strip()
        body = el._body_links(el._fix_sentence_starts(md))
        paras = []
        for p in re.split(r"(?:<br\s*/?>\s*){2,}", body):
            t = _html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", " ", p)))
            t = " ".join(t.split())
            if t:
                paras.append(t)
        text = "\n\n".join(paras)
        if re.search(r"https?://", text):
            logger.warning(f"theme {num}: a bare URL is in the card text")
        # A cleaned draft stores its pills at ingest (links.clean_draft). They are rebuilt
        # here with the same rule, so a display name added to source_names.json later
        # (e.g. "Kevinerdmann" -> "Kevin Erdmann") also reaches cards of earlier drafts.
        if isinstance(e.get("_pills"), list):
            import links
            pills = links.outlets_for(e)
        else:
            pills = el._entry_pills(e)
        out.append({"pos": i, "num": num, "title": title, "text": text, "md": md,
                    "pills": pills})
    return out


def theme_word(pos: int) -> str:
    return f"Theme {NUM_WORDS[pos - 1]}" if 1 <= pos <= len(NUM_WORDS) else f"Theme {pos}"


# ── HTML ────────────────────────────────────────────────────────────────

def _data_uri(path: Path) -> str:
    kind = "svg+xml" if path.suffix == ".svg" else "png"
    return f"data:image/{kind};base64," + base64.b64encode(path.read_bytes()).decode()


_LOGO_CACHE: dict[str, str] = {}


def _logo() -> str:
    if "u" not in _LOGO_CACHE:
        p = LOGO_SVG if LOGO_SVG.is_file() else LOGO_PNG
        _LOGO_CACHE["u"] = _data_uri(p) if p.is_file() else "https://homeeconomics.us/logo-email.png"
    return _LOGO_CACHE["u"]


def _base_css() -> str:
    m = MARGIN
    return f"""
<style>
  html, body {{ margin:0; padding:0; background:{CREAM}; }}
  body {{ width:{W}px; height:{H}px; overflow:hidden; color:{INK}; font-family:{SANS};
          -webkit-font-smoothing:antialiased; font-kerning:normal; }}
  .card {{ box-sizing:border-box; width:{W}px; height:{H}px; padding:{TOP_PAD}px {m}px {BOTTOM_PAD}px {m}px;
           display:flex; flex-direction:column; overflow:hidden; background:{CREAM}; }}
  /* numeral top left, small logo top right, centred on the numeral (owner, 29 Sep 2026) */
  .top {{ flex:none; display:flex; justify-content:space-between; align-items:center; margin:0 0 22px 0; }}
  .eb {{ color:{BLUE}; font-weight:700; font-size:96px; line-height:0.9; letter-spacing:-0.03em; }}
  .top img {{ height:52px; width:auto; display:block; }}
  /* one line (owner, 29 Sep 2026); .wrap only for an owner's title too long even at 40 px */
  .tt {{ flex:none; font-weight:500; font-size:54px; line-height:1.08; letter-spacing:-0.03em;
         margin:0 0 40px 0; white-space:nowrap; }}
  .tt.wrap {{ white-space:normal; text-wrap:balance; }}
  .body {{ flex:none; line-height:1.35; letter-spacing:-0.005em; }}
  .body p {{ margin:0 0 0.72em 0; hyphens:manual; }}
  .body p:last-child {{ margin-bottom:0; }}
  .pills {{ margin:auto 0 0 0; padding-top:40px; display:flex; flex-wrap:wrap; gap:14px; flex:none; }}
  .pills + .foot {{ margin-top:0; }}
  .pill {{ background:{LIGHT}; color:{INK}; font-size:28px; line-height:1.2; padding:9px 22px 10px;
           border-radius:999px; white-space:nowrap; }}
  .foot {{ margin-top:auto; padding-top:40px; flex:none; white-space:nowrap; font-size:28px; line-height:1;
           color:{MUTED}; }}
  /* the closing call-to-action card */
  .cta {{ justify-content:center; padding:{m}px 96px; background:{BLUE}; }}
  .cta .logo {{ width:560px; height:auto; display:block; margin:0 0 84px 0; }}
  .cta h1 {{ margin:0; font-weight:500; font-size:104px; line-height:1.0; letter-spacing:-0.04em; }}
  .cta .desc {{ margin:36px 0 0 0; font-size:42px; line-height:1.3; letter-spacing:-0.01em; max-width:840px;
                text-wrap:balance; }}
  .cta .addr {{ margin:72px 0 0 0; align-self:flex-start; background:{CREAM}; color:{INK}; font-weight:500;
                font-size:48px; letter-spacing:-0.02em; line-height:1; padding:30px 40px 32px;
                border-radius:18px; }}
  .cta .date {{ margin-top:auto; font-size:28px; color:{MUTED}; }}
</style>
"""


def card_html(theme: dict, date: str, text: str, *, px: int = BODY_PX) -> str:
    """One theme card: numeral and small logo, title (one line at theme["title_px"], default
    54 px), body (paragraphs separated by a blank line), pills, footer line."""
    paras = [" ".join(p.split()) for p in str(text or "").split("\n\n") if p.strip()]
    # a paragraph never ends on a one-word line: its last two words are bound together
    body = "".join(f"<p>{_esc(_bind_last(p))}</p>" for p in paras)
    pills = ""
    if theme.get("pills"):
        pills = '<div class="pills">' + "".join(
            f'<span class="pill">{_esc(p)}</span>' for p in theme["pills"][:6]) + "</div>"
    foot = f'<div class="foot">Housing at Noon · {_esc(date_label(date))}</div>'
    tpx = int(theme.get("title_px") or TITLE_PXS[0])
    tcls = "tt wrap" if theme.get("title_wrap") else "tt"
    return (f'<div class="card"><div class="top"><div class="eb">{theme["pos"]}</div>'
            f'<img src="{_logo()}" alt="Home Economics"></div>'
            f'<div class="{tcls}" style="font-size:{tpx}px">{_esc(theme["title"])}</div>'
            f'<div class="body" style="font-size:{px}px">{body}</div>{pills}{foot}</div>')


def _bind_last(p: str) -> str:
    i = p.rfind(" ")
    return p if i < 0 or len(p) - i > 30 else p[:i] + "\u00a0" + p[i + 1:]


def cta_html(date: str, desc: str | None = None) -> str:
    """The closing sign-up card; `desc` is the owner's edited description line, if any."""
    return (f'<div class="card cta"><img class="logo" src="{_logo()}" alt="Home Economics">'
            f'<h1>Housing at Noon</h1>'
            f'<div class="desc">{_esc(" ".join((desc or CTA_DESC).split()))}</div>'
            f'<div class="addr">{SIGNUP}</div></div>')


def _doc(inner: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">{_base_css()}</head>'
            f'<body>{inner}</body></html>')


# ── measuring ───────────────────────────────────────────────────────────

class _Fitter:
    """Measures card HTML in one Playwright page (body swapped in place, no reloads)."""

    def __init__(self, page):
        self.page = page
        page.set_content(_doc(), wait_until="load")
        page.evaluate("() => document.fonts.ready")

    def fits(self, html: str) -> bool:
        return not self.page.evaluate("""(h) => {
          document.body.innerHTML = h;
          const c = document.querySelector('.card');
          if (c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1) return true;
          const f = document.querySelector('.foot');
          if (f && f.scrollWidth > f.clientWidth + 1) return true;
          return false; }""", html)

    def title_one_line(self, theme: dict, date: str, px: int) -> bool:
        """Whether the title fits on one line at `px`."""
        return bool(self.page.evaluate("""(h) => {
          document.body.innerHTML = h;
          const t = document.querySelector('.tt');
          return t.scrollWidth <= t.clientWidth + 1; }""",
            card_html(dict(theme, title_px=px, title_wrap=False), date, "")))

    def title_px(self, theme: dict, date: str) -> int | None:
        """The largest of TITLE_PXS at which the title is one line, else None."""
        return next((p for p in TITLE_PXS if self.title_one_line(theme, date, p)), None)

    def title_capacity(self, theme: dict, date: str) -> int:
        """Characters of the title (a prefix ending at a word) that fit on one line at 40 px."""
        t = theme["title"]
        lo, hi = 0, len(t)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.title_one_line(dict(theme, title=t[:mid].rstrip()), date, TITLE_MIN_PX):
                lo = mid
            else:
                hi = mid - 1
        return lo

    def budget(self, theme: dict, date: str, px: int = BODY_PX) -> int:
        """Characters of body text that fit on this theme's card at `px`, measured with the
        theme's own title and pills and a two-paragraph filler made of its own words."""
        words = theme["text"].split() or ["housing"]
        filler_words: list[str] = []
        while len(" ".join(filler_words)) < 6000:
            filler_words.extend(words)

        def filler(n: int) -> str:
            s = " ".join(filler_words)[:n].rsplit(" ", 1)[0]
            cut = int(len(s) * 0.55)
            sp = s.find(" ", cut)
            return s if sp < 0 else s[:sp] + "\n\n" + s[sp + 1:]

        lo, hi = 0, 6000
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.fits(card_html(theme, date, filler(mid), px=px)):
                lo = mid
            else:
                hi = mid - 1
        return len(filler(lo)) if lo else 0


def _capacity(fitter: _Fitter, theme: dict, date: str, text: str, px: int) -> int:
    """How many characters of `text` (a prefix, ending at a word) fit on the card at `px`.
    Used only to tell the model the exact excess; the text itself is never cut here."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cut = text[:mid].rsplit(" ", 1)[0] if mid < len(text) else text
        if fitter.fits(card_html(theme, date, cut, px=px)):
            lo = mid
        else:
            hi = mid - 1
    return len(text[:lo].rsplit(" ", 1)[0]) if lo < len(text) else lo


def _outcome(fitter: _Fitter, theme: dict, date: str, text: str) -> tuple[str, int | None]:
    """(text as the card would show it, px): (text, 36) when the whole text fits at 36 px,
    else (the last-guard cut at 36 px, None). There is no smaller size (owner, 29 Sep 2026)."""
    if fitter.fits(card_html(theme, date, text, px=BODY_PX)):
        return text, BODY_PX
    return _cut_to_fit(fitter, theme, date, text, BODY_PX), None


def _cut_to_fit(fitter: _Fitter, theme: dict, date: str, text: str, px: int) -> str:
    """Longest prefix of whole sentences (paragraphs kept) that fits at `px`."""
    units = [(pi, s) for pi, p in enumerate(_para_sentences(text)) for s in p]

    def join(k: int) -> str:
        paras: dict[int, list[str]] = {}
        for pi, s in units[:k]:
            paras.setdefault(pi, []).append(s)
        return "\n\n".join(" ".join(v) for v in paras.values())

    lo, hi = 1, len(units)
    best = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if fitter.fits(card_html(theme, date, join(mid), px=px)):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return join(best)


# ── facts to keep (owner, 28 Sep 2026: never lose a number or an attribution) ──

_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DATE_RE = re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*\d{{4}})?(?!\d)"
                      rf"|\b{_MONTH}\.?\s+\d{{4}}(?!\d)")
# a figure: optional currency sign, digits with separators, optional unit (%, K, bn, million ...)
_NUM_RE = re.compile(r"(?<![\w.])[$€£]?\d+(?:[.,]\d+)*"
                     r"(?:\s?(?:%|percent\b|(?:[KkMBT]|bn|tn)\b|(?:thousand|million|billion|trillion)\b))?")
_HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{1,30}(?![A-Za-z0-9_])")
_LEADIN_RE = re.compile(r"\b[Oo]n (X|LinkedIn|Bluesky|Threads|Substack|Reddit|YouTube|TikTok|Facebook|"
                        r"Instagram|Mastodon)\b")
_FIRST_PERSON = re.compile(r"(?<![\w’'])(?:I|I['’](?:m|ve|d|ll)|[Mm]y|me|[Mm]yself)(?![\w’'])")


def _norm_num(s: str) -> str:
    return re.sub(r"\s?percent\b", "%", s.replace(" ", " "))


def _context(text: str, start: int, end: int, span: int = 45) -> str:
    a, b = max(0, start - span), min(len(text), end + span)
    s = text[a:b].replace("\n", " ")
    if a > 0:
        s = "…" + s.split(" ", 1)[-1]
    if b < len(text):
        s = s.rsplit(" ", 1)[0] + "…"
    return s


def regex_facts(text: str) -> list[dict]:
    """Numbers, dates and @handles in the visible text, each once, in order of first
    appearance, with a few words of context."""
    facts: list[dict] = []
    seen: set[str] = set()
    taken: list[tuple[int, int]] = []

    def add(kind, value, m):
        v = value.strip()
        if v and v not in seen:
            seen.add(v)
            facts.append({"kind": kind, "value": v, "context": _context(text, m.start(), m.end()),
                          "at": m.start()})

    for m in _DATE_RE.finditer(text):
        taken.append((m.start(), m.end()))
        add("date", m.group(0), m)
    for m in _NUM_RE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue  # the day or year of a date already listed
        add("number", m.group(0), m)
    for m in _HANDLE_RE.finditer(text):
        add("handle", m.group(0), m)
    facts.sort(key=lambda f: f["at"])
    for f in facts:
        f.pop("at", None)
    return facts


def _has_name(name: str, text: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", text, re.I) is not None


def _has_fact(f: dict, text: str) -> bool:
    k, v = f["kind"], f["value"]
    if k in ("number", "date"):
        return re.search(r"(?<![\d.,])" + re.escape(_norm_num(v)) + r"(?![.,]?\d)", _norm_num(text)) is not None
    if k == "handle":
        return re.search(re.escape(v) + r"(?![A-Za-z0-9_])", text) is not None
    if k == "name":
        return _has_name(v, text)
    if k == "commentary":
        if f.get("first_person") and not _FIRST_PERSON.search(text):
            return False
        kws = f.get("keywords") or []
        low = {w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 5}
        return sum(1 for w in kws if w[:5] in low) >= min(2, len(kws))
    return v in text


def missing_facts(facts: list[dict], text: str) -> list[dict]:
    return [f for f in facts if not _has_fact(f, text)]


def _fact_label(f: dict) -> str:
    if f["kind"] == "commentary":
        return "the author's commentary"
    return f["value"]


# A sentence that only points elsewhere ("My X post on this is here", "the map below") is
# not commentary the card can carry.
_POINTER = re.compile(r"\b(?:here|below|above)\s*[.)]?\s*$|\bsee my\b|\bmy (?:X|Substack|LinkedIn|Bluesky|"
                      r"Threads) post\b|\bI wrote about\b|\bI(?:'ve|’ve| have) written\b|\bthe map below\b", re.I)
_STOP = set("""about above after again against along among another around because become been before
being below between both could does doing during each either every first from further have having
here hers herself himself into itself just least less like might more most much must myself never
other ought ours ourselves over same shall should since some such than that their theirs them
themselves then there these they this those though through under until upon very were what when
where which while whom whose will with within without would your yours yourself yourselves also
still even really quite rather indeed these those thing things lots something""".split())


def commentary_fact(text: str, names: list[str], handles: set[str]) -> dict | None:
    """The author's commentary, if any: sentences in the first person (pointer sentences
    aside), and a last paragraph that names no source or handle (an unattributed closing
    remark such as "Indeed, Staten Island is much less dense ..."). Checked in a condensed
    text by its distinctive words (at least two must survive, compared on their first five
    letters) and, when the original view was in the first person, by first-person wording.
    The first-person check is not applied when the only first-person words were in pointer
    sentences, so the model is never pushed to invent a first-person line."""
    paras = _para_sentences(text)
    if not paras:
        return None
    sents: list[str] = []
    for p in paras:
        sents += [s for s in p if _FIRST_PERSON.search(s) and not _POINTER.search(s)]
    last = " ".join(s for s in paras[-1] if not _POINTER.search(s))
    if (len(paras) > 1 and last and not any(_has_name(n, last) for n in names)
            and not any(h in last for h in handles)):
        sents += [s for s in paras[-1] if not _POINTER.search(s) and s not in sents]
    if not sents:
        return None
    body = " ".join(sents)
    rest = text
    for s in sents:
        rest = rest.replace(s, " ")
    rest_stems = {w[:5] for w in re.findall(r"[a-z]+", rest.lower()) if len(w) >= 5}
    words = [w for w in dict.fromkeys(re.findall(r"[a-z]+", body.lower())) if len(w) >= 5 and w not in _STOP]
    kws = [w for w in words if w[:5] not in rest_stems][:8] or words[:8]
    fp = any(_FIRST_PERSON.search(s) for s in sents)
    return {"kind": "commentary", "value": "the author's commentary",
            "context": " ".join(body.split()[:10]) + " …", "keywords": kws, "first_person": fp}


_NAMES_PROMPT = (
    "Below is a passage from a newsletter about the U.S. housing market. List every named "
    "source or person in it: people; news outlets, publications, newsletters and blogs; firms, "
    "banks, brokerages and data providers; government agencies and bodies; think tanks and "
    "universities; and social platforms named as the place where something was said (such as "
    "X, LinkedIn or Bluesky). Do not list places, laws, programs, products or indexes, and do "
    "not list the newsletter's own author (\"I\") or a platform mentioned only as the place of "
    "the author's own post (\"see my X post\").\n"
    "Give each entity on its own, by its name only, spelled as in the passage: from "
    "\"Calculated Risk's Bill McBride\" list \"Calculated Risk\" and \"Bill McBride\"; from "
    "\"Census and HUD data\" list \"Census\" and \"HUD\"; from \"the September NAHB survey\" list "
    "\"NAHB\"; from \"HousingWire's coverage\" list \"HousingWire\". A person named in full and "
    "later by surname appears once, in full. Reply with a JSON array of strings and nothing "
    "else.\n\nPassage:\n")


def _parse_names(reply: str) -> list[str]:
    m = re.search(r"\[.*\]", reply or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return []
    return [str(x).strip() for x in arr if isinstance(x, str) and str(x).strip()]


def _clean_names(names: list[str], text: str, handles: set[str]) -> list[str]:
    """Names that really occur in the text, without handles, duplicates, or a name that
    only repeats part of a longer one ("Erdmann" when "Kevin Erdmann" is listed)."""
    out: list[str] = []
    # a possessive pair ("Calculated Risk's Bill McBride") is two names
    names = [p for n in names for p in re.split(r"['’]s\s+", n)]
    for n in names:
        n = re.sub(r"['’]s?$", "", n.strip(" .,;:\"“”"))
        if not n or n.startswith("@") or n in handles or not _has_name(n, text):
            continue
        if any(n.lower() == o.lower() for o in out):
            continue
        out.append(n)
    # a surname alone is dropped when the full name is listed ("Erdmann" / "Kevin Erdmann");
    # "FT" and "FT Property" are both kept
    return [n for n in out if not any(o != n and o.lower().endswith(" " + n.lower()) for o in out)]


# ── condensing with Claude ──────────────────────────────────────────────

_SYSTEM = ("You condense sections of Housing at Noon, a daily brief on the U.S. housing market, "
           "so that each section fits on one social-media card. You shorten wording; you never "
           "add facts, opinions or emphasis, and you never drop a fact you are told to keep.")


def _facts_block(facts: list[dict]) -> str:
    lines = []
    for f in facts:
        if f["kind"] in ("number", "date"):
            lines.append(f"- {f['value']}   (in: \"{f['context']}\")")
        elif f["kind"] == "handle":
            lines.append(f"- {f['value']}   (handle; keep it exactly)")
        elif f["kind"] == "name":
            lines.append(f"- {f['value']}   (source or person; keep the name as written)")
        elif f["kind"] == "commentary":
            lines.append(f"- the author's own commentary, which begins \"{f['context']}\" (keep its "
                         "point, condensed, in the author's own words"
                         + (", in the first person" if f.get("first_person") else "")
                         + ", as the last paragraph)")
    return "\n".join(lines)


def _prompt(theme: dict, budget: int, facts: list[dict]) -> str:
    target = int(budget * TARGET_SHARE)
    orig = len(theme["text"])
    return (
        "Condense the theme below so it fits on one social-media card.\n\n"
        f"Length: aim for about {target} characters in total, counting spaces and punctuation. "
        f"The hard limit is {budget} characters; a reply over {budget} cannot be used. The "
        f"original is {orig} characters, so about {max(0, 100 - round(100 * target / max(1, orig)))}% "
        "of it must go.\n\n"
        "Facts that must all appear in your text, each written exactly as listed (numbers and "
        "dates character for character, names in full):\n"
        f"{_facts_block(facts)}\n\n"
        "Rules:\n"
        "- Every listed fact must appear. To make room, cut restatement, background, adjectives, "
        "examples that carry no listed fact, and connecting phrases; merge sentences; use short "
        "attributions (\"per HousingWire\", \"Kevin Erdmann notes\").\n"
        "- Keep each number with what it measures and with its source. Do not change any figure "
        "or who said it.\n"
        "- The author's own commentary (passages in the first person, and an unattributed closing "
        "remark) is the most distinctive part of the brief. Keep it, condensed, in the author's "
        "own words, as the last paragraph. Leave out only a sentence that points to something not "
        "on the card (\"the map below\", \"see my post\").\n"
        "- Do not add any statement, opinion or first-person wording that is not in the original.\n"
        "- At most 2 paragraphs separated by one blank line (the reported facts first, the "
        "author's commentary, if any, second).\n"
        "- Plain text only: no links, markdown, headings, bullets, bold or italics.\n"
        "- Write in the brief's register: measured, precise, restrained.\n"
        "- Return only the condensed text.\n\n"
        f"Theme title (context only; do not repeat it): {theme['title']}\n\n"
        f"Theme text:\n{theme['text']}"
    )


def _repair_prompt(draft: str, budget: int, missing: list[dict], fits36: bool,
                   capacity: int | None = None, share: float = TARGET_SHARE) -> str:
    """`capacity`: characters of this draft that fit on the card at 36 px, measured by
    rendering (paragraph breaks and line ends make it lower than the budget). `share`:
    the target as a share of the budget (0.75 in the extra rounds for a draft that does
    not fit)."""
    limit = budget if fits36 or capacity is None else min(budget, capacity)
    target = int(limit * (TARGET_SHARE if limit == budget else 0.95))
    if share < TARGET_SHARE:
        target = min(target, int(budget * share))
    parts = ["That draft needs changes before it can be used:"]
    if len(draft) > limit:
        why = ("" if limit == budget else
               f" (measured on the card: only the first {limit} characters of this draft fit, because "
               "paragraph breaks and line ends take room)")
        parts.append(f"- It is {len(draft)} characters; the limit is {limit}{why}, so at least "
                     f"{len(draft) - limit} characters must go (aim for about {target}).")
    elif not fits36:
        parts.append(f"- It is {len(draft)} characters but does not fit on the card; shorten it by "
                     f"about {max(30, len(draft) - int(budget * share))} characters.")
    if missing:
        parts.append("- These facts from the original are missing and must be restored, exactly as "
                     "written:\n" + _facts_block(missing))
    listing = "\n".join(f"[{len(x)}] {x}" for para in _para_sentences(draft) for x in para)
    parts.append(f"\nYour sentences, with their lengths in characters:\n{listing}\n")
    parts.append("Revise the draft: restore every missing fact, then shorten by merging sentences and "
                 "cutting wording that carries no listed fact. Do not drop any listed fact to save "
                 "space, keep the author's commentary, and add nothing that is not in the original. At most 2 "
                 "paragraphs. Return "
                 "only the revised text, without the bracketed lengths.")
    return "\n".join(parts)


def _drop_prompt(draft: str, capacity: int, facts: list[dict]) -> str:
    """Last resort (owner, 29 Sep 2026): the draft still does not fit at 36 px after every
    repair round, so the model deletes its least important sentence(s), keeping every fact."""
    need = max(30, len(draft) - capacity)
    listing = "\n".join(f"[{len(x)}] {x}" for para in _para_sentences(draft) for x in para)
    return ("The draft still does not fit on the card: measured on the card, only the first "
            f"{capacity} of its {len(draft)} characters fit, so at least {need} characters must go.\n\n"
            "As a last step, delete the least important sentence or sentences: those that carry "
            "none of the facts listed below, or the fewest of them. Every listed fact must still "
            "appear, exactly as written:\n"
            f"{_facts_block(facts)}\n\n"
            f"Your sentences, with their lengths in characters:\n{listing}\n\n"
            "Do not rewrite the sentences you keep beyond what joining them needs, keep the "
            "author's commentary, and add nothing. At most 2 paragraphs. Return only the revised "
            "text, without the bracketed lengths.")


_TITLE_PROMPT = (
    "Shorten this headline from a daily brief on the U.S. housing market to at most {n} "
    "characters, counting spaces. Keep its meaning and its key terms (names, numbers, places). "
    "Plain, measured wording; keep the original's capitalisation style; no final period. "
    "Return only the headline.\n\nHeadline: {title}\n\nThe section it heads (context only):\n{text}")


def _clean_reply(t: str) -> str:
    t = _LINK_RE.sub(r"\1", str(t or "")).replace("\r", "")
    t = re.sub(r"^\s*\[\d+\]\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*(?:#+\s*|[-*•]\s+)", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|`)", "", t)
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", t.strip()) if p.strip()]
    if len(paras) > 2:
        paras = [paras[0], " ".join(paras[1:])]
    return "\n\n".join(paras)


class _Condenser:
    def __init__(self):
        self.client = None
        self.usage: dict[str, dict[str, int]] = {}
        self.cache = self._load()
        self.dirty = False

    @staticmethod
    def _load() -> dict:
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:  # noqa: BLE001
            return {}

    def save(self) -> None:
        if not self.dirty:
            return
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False, sort_keys=True))
        tmp.replace(CACHE_PATH)
        self.dirty = False

    @staticmethod
    def key(theme: dict, budget: int) -> str:
        return hashlib.sha1((PROMPT_VERSION + "\x00" + theme["md"] + "\x00" + str(budget))
                            .encode("utf-8")).hexdigest()

    def _call(self, model: str, messages: list, system: str | None = None, max_tokens: int = 4000) -> str:
        import anthropic
        if self.client is None:
            self.client = anthropic.Anthropic()
        kw = dict(model=model, max_tokens=max_tokens, messages=messages)
        if system:
            kw["system"] = system
        if model.startswith("claude-sonnet-5-5"):
            # Sonnet 5.5 rejects thinking "disabled" (400). "between_tools" is its lowest
            # setting: no up-front thinking; accepted at effort low/medium/high only.
            kw["thinking"] = {"type": "between_tools"}
            kw["output_config"] = {"effort": "high"}
        elif model.startswith("claude-sonnet-5"):
            kw["thinking"] = {"type": "disabled"}
        resp = self.client.messages.create(**kw)
        u = self.usage.setdefault(model, {"calls": 0, "input": 0, "output": 0})
        u["calls"] += 1
        u["input"] += resp.usage.input_tokens
        u["output"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"{model} declined")
        return "".join(b.text for b in resp.content if b.type == "text")

    def facts(self, theme: dict) -> tuple[list[dict], bool]:
        """(facts to keep, whether the name extraction worked). Cached per theme text."""
        fk = "facts:" + hashlib.sha1((FACTS_VERSION + "\x00" + theme["md"]).encode("utf-8")).hexdigest()
        hit = self.cache.get(fk)
        if hit and isinstance(hit.get("facts"), list):
            return hit["facts"], True
        # facts come from the text without its pointer sentences ("My X post on this is
        # here", "I wrote about this on Substack here"): the card leaves those out, so a
        # name or number found only there must not be demanded
        text = "\n\n".join(" ".join(s for s in p if not _POINTER.search(s))
                           for p in _para_sentences(theme["text"]))
        facts = regex_facts(text)
        handles = {f["value"] for f in facts if f["kind"] == "handle"}
        ok = True
        try:
            names = _parse_names(self._call(EXTRACT_MODEL, [{"role": "user", "content": _NAMES_PROMPT + text}],
                                            max_tokens=1000))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"theme {theme['num']}: name extraction failed ({e}); numbers and handles only")
            names, ok = [], False
        # platform lead-ins ("On X,", "On LinkedIn,") are attributions; found by regex so
        # they do not depend on the model
        names = names + [m.group(1) for m in _LEADIN_RE.finditer(text)]
        # the source pills (the cited outlets) are attributions whenever the text names them
        names = names + [p for p in (theme.get("pills") or []) if _has_name(p, text)]
        kept_names = _clean_names(names, text, handles)
        for n in kept_names:
            m = re.search(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])", text, re.I)
            facts.append({"kind": "name", "value": n,
                          "context": _context(text, m.start(), m.end()) if m else ""})
        c = commentary_fact(theme["text"], kept_names, handles)
        if c:
            facts.append(c)
        if ok:
            self.cache[fk] = {"facts": facts, "model": EXTRACT_MODEL, "title": theme["title"],
                              "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.dirty = True
        return facts, ok

    def condense(self, theme: dict, budget: int, fits36, capacity=None, outcome=None) -> dict:
        """{text, how, facts, missing, rounds, ok, last_resort}. `fits36(text)` renders the
        card at 36 px; `capacity(text)` gives how many of the text's characters fit on it;
        `outcome(text)` gives (text as the card would show it, 36 or None if it had to be cut).
        A draft passes when every fact is present and the whole text fits at 36 px. Otherwise
        up to MAX_REPAIRS repair rounds follow; if the latest draft still does not fit, up to
        MAX_FIT_REPAIRS rounds in all, at FIT_TARGET_SHARE of the budget; if no draft fits at
        all, one last request drops the least important sentence(s). The draft that fits with
        the fewest missing facts is returned; the text is never cut here."""
        k = self.key(theme, budget)
        hit = self.cache.get(k)
        if hit and hit.get("text"):
            facts = hit.get("facts") or []
            return {"text": hit["text"], "how": "cache", "facts": facts,
                    "missing": missing_facts(facts, hit["text"]), "rounds": hit.get("rounds", 0),
                    "ok": hit.get("ok", False), "last_resort": hit.get("last_resort", False)}
        last_resort = False
        try:
            facts, names_ok = self.facts(theme)
            messages = [{"role": "user", "content": _prompt(theme, budget, facts)}]
            drafts_: list[dict] = []
            rounds = 0
            while True:
                out = _clean_reply(self._call(CONDENSE_MODEL, messages, system=_SYSTEM))
                d = {"text": out, "miss": missing_facts(facts, out), "fit": bool(fits36(out))}
                drafts_.append(d)
                if not d["miss"] and d["fit"]:
                    break
                if rounds >= MAX_FIT_REPAIRS or (rounds >= MAX_REPAIRS and d["fit"]):
                    break
                rounds += 1
                share = FIT_TARGET_SHARE if rounds > MAX_REPAIRS else TARGET_SHARE
                cap = capacity(out) if (capacity is not None and not d["fit"]) else None
                logger.info(f"theme {theme['pos']}: repair {rounds}: {len(out)} chars (budget {budget}"
                            + (f", {cap} fit on the card" if cap is not None else "") + "), "
                            f"missing {[_fact_label(f) for f in d['miss']]}, fits at {BODY_PX} px: {d['fit']}"
                            + (f", target {int(share * 100)}% of the budget" if share != TARGET_SHARE else ""))
                # only the latest draft is sent back (not every earlier one): the repair
                # request carries all that is needed, and the input stays small
                messages = [messages[0], {"role": "assistant", "content": out},
                            {"role": "user", "content": _repair_prompt(out, budget, d["miss"], d["fit"], cap, share)}]
            if not any(d["fit"] for d in drafts_) and capacity is not None:
                # last resort: drop the least important sentence(s) of the most complete draft
                idx = min(range(len(drafts_)), key=lambda i: (len(drafts_[i]["miss"]), -i))
                base = drafts_[idx]["text"]
                cap = capacity(base)
                logger.warning(f"theme {theme['pos']} (entry {theme['num']}, {theme['title']!r}): no draft "
                               f"fits at {BODY_PX} px after {rounds} repair round(s); last resort: asking "
                               f"the model to drop its least important sentence(s) ({len(base)} chars, "
                               f"{cap} fit)")
                out = _clean_reply(self._call(CONDENSE_MODEL, [
                    messages[0], {"role": "assistant", "content": base},
                    {"role": "user", "content": _drop_prompt(base, cap, facts)}], system=_SYSTEM))
                drafts_.append({"text": out, "miss": missing_facts(facts, out), "fit": bool(fits36(out))})
                last_resort = True
        except Exception as e:  # noqa: BLE001  (no key, network, refusal): never block the cards
            logger.warning(f"theme {theme['num']}: condensation failed ({e}); the full text is used and "
                           "cut at a sentence end if it does not fit")
            return {"text": theme["text"], "how": "not condensed (error)", "facts": [], "missing": [],
                    "rounds": 0, "ok": False, "last_resort": False}
        order = {id(d): i for i, d in enumerate(drafts_)}
        passing = [d for d in drafts_ if not d["miss"] and d["fit"]]
        fitting = [d for d in drafts_ if d["fit"]]
        if passing:
            best = passing[-1]
        elif fitting:  # fewest missing facts, then the latest
            best = min(fitting, key=lambda d: (len(d["miss"]), -order[id(d)]))
        elif outcome is not None:
            # none fits whole: judge each by what the last-guard cut at 36 px would leave
            best = min(drafts_, key=lambda d: (len(missing_facts(facts, outcome(d["text"])[0])), -order[id(d)]))
        else:
            best = min(drafts_, key=lambda d: (len(d["miss"]), -order[id(d)]))
        out, miss = best["text"], best["miss"]
        ok = not miss and best["fit"]
        how = "claude" + (f", {rounds} repair round(s)" if rounds else "")
        if last_resort:
            how += ", last-resort sentence drop" + ("" if best is drafts_[-1] else " (not used)")
        if not ok:
            how += f", unresolved after {rounds} repair(s)"
        if names_ok:  # without the names the check was incomplete: do not keep the result
            self.cache[k] = {"text": out, "budget": budget, "facts": facts, "rounds": rounds, "ok": ok,
                             "last_resort": last_resort,
                             "model": CONDENSE_MODEL, "prompt_version": PROMPT_VERSION,
                             "title": theme["title"], "original_chars": len(theme["text"]),
                             "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.dirty = True
        return {"text": out, "how": how, "facts": facts, "missing": miss, "rounds": rounds, "ok": ok,
                "last_resort": last_resort}

    def shorten_title(self, theme: dict, n: int, one_line) -> str | None:
        """A generated title that does not fit on one line at 40 px: ask the condensing model
        for one of at most `n` characters (cached). `one_line(title)` checks it on the card.
        Returns None when no reply fits (the caller then lets the title wrap)."""
        k = "title:" + hashlib.sha1((PROMPT_VERSION + "\x00" + theme["title"] + "\x00" + str(n))
                                    .encode("utf-8")).hexdigest()
        hit = self.cache.get(k)
        if hit and hit.get("title") and one_line(hit["title"]):
            return hit["title"]
        for limit in (n, int(n * 0.9)):
            try:
                reply = self._call(CONDENSE_MODEL, [{"role": "user", "content": _TITLE_PROMPT.format(
                    n=limit, title=theme["title"], text=theme["text"][:1500])}], system=_SYSTEM, max_tokens=200)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"theme {theme['pos']}: title shortening failed ({e})")
                return None
            t = " ".join(_clean_reply(reply).split()).strip().strip('"“”').rstrip(".")
            if t and one_line(t):
                self.cache[k] = {"title": t, "original": theme["title"], "limit": n,
                                 "model": CONDENSE_MODEL,
                                 "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                self.dirty = True
                return t
        return None

    def cost_usd(self) -> float:
        tot = 0.0
        for model, u in self.usage.items():
            pin, pout = PRICE_PER_MTOK.get(model, (2.0, 10.0))
            tot += u["input"] * pin / 1e6 + u["output"] * pout / 1e6
        return tot


# ── planning ────────────────────────────────────────────────────────────

def _norm_body(text: str) -> str:
    """Owner's text -> the card's body format: paragraphs separated by one blank line,
    single line breaks inside a paragraph read as spaces."""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", str(text or "").replace("\r", ""))]
    return "\n\n".join(p for p in paras if p)


def _fit_title(fitter: _Fitter, cond: "_Condenser", t: dict, date: str, owner_title: bool) -> list[str]:
    """Sets t["title_px"] (and t["title_wrap"] / a shortened t["title"]) so the title sits on
    one line (owner, 29 Sep 2026). Returns log notes."""
    notes: list[str] = []
    px = fitter.title_px(t, date)
    if px is None and not owner_title:
        n = fitter.title_capacity(t, date)
        new = cond.shorten_title(t, n, lambda s: fitter.title_px(dict(t, title=s), date) is not None)
        if new:
            note = (f"theme {t['pos']} (entry {t['num']}): title too long for one line at {TITLE_MIN_PX} px "
                    f"({len(t['title'])} chars, {n} fit); shortened by the model to {new!r}")
            logger.info(note)
            notes.append(note)
            t["title"] = new
            px = fitter.title_px(t, date)
    if px is None:
        t["title_wrap"] = True
        px = TITLE_MIN_PX
        note = (f"theme {t['pos']} (entry {t['num']}): the title {t['title']!r} does not fit on one line "
                f"at {TITLE_MIN_PX} px" + ("; it is the owner's title, so it wraps" if owner_title else
                                           "; shortening failed, so it wraps"))
        logger.warning(note)
        notes.append(note)
    t["title_px"] = px
    return notes


def plan_cards(fitter: _Fitter, themes: list[dict], date: str,
               overrides: dict | None = None) -> tuple[list[dict], list[str]]:
    """[{theme, text, px, budget, condensed, how, facts_total, facts_kept, missing, rounds,
    override, cut, gen_title}] for each theme (at most MAX_CARDS-1), plus log notes.
    `overrides`: {pos: {title, body}} from drafts.card_overrides (the owner's edits); an
    override's title and body, when not empty, replace the generated ones, with no Claude
    call."""
    overrides = overrides or {}
    notes: list[str] = []
    if len(themes) > MAX_CARDS - 1:
        dropped = themes[MAX_CARDS - 1:]
        themes = themes[:MAX_CARDS - 1]
        note = (f"{len(dropped)} theme(s) left off to stay within {MAX_CARDS} cards with the "
                f"sign-up card: " + ", ".join(str(t["num"]) for t in dropped))
        logger.warning(note)
        notes.append(note)
    cond = _Condenser()
    plan = []
    for t0 in themes:
        t = dict(t0)
        ov = overrides.get(t["pos"]) or {}
        ov_title = " ".join(str(ov.get("title") or "").split())
        ov_body = _norm_body(ov.get("body") or "")
        if ov_title:
            t["title"] = ov_title
        notes += _fit_title(fitter, cond, t, date, owner_title=bool(ov_title))
        budget = fitter.budget(t, date)
        text, how, condensed = t["text"], "unchanged", False
        facts: list[dict] = []
        rounds = 0
        last_resort = False
        if ov_body:
            text, how = ov_body, "owner's text"
        elif len(text) > budget or not fitter.fits(card_html(t, date, text, px=BODY_PX)):
            r = cond.condense(t, budget, lambda x, t=t: fitter.fits(card_html(t, date, x, px=BODY_PX)),
                              lambda x, t=t: _capacity(fitter, t, date, x, BODY_PX),
                              lambda x, t=t: _outcome(fitter, t, date, x))
            text, how, facts, rounds, condensed = r["text"], r["how"], r["facts"], r["rounds"], True
            last_resort = r.get("last_resort", False)
        px = BODY_PX
        cut = False
        full = text
        if not fitter.fits(card_html(t, date, text, px=BODY_PX)):  # last guard: never clip
            before = len(text)
            text = _cut_to_fit(fitter, t, date, text, BODY_PX)
            cut = True
            how += f", CUT at a sentence end at {BODY_PX} px ({before} -> {len(text)} chars)"
            logger.warning(f"theme {t['pos']} (entry {t['num']}): did not fit at {BODY_PX} px; cut at a "
                           f"sentence end ({before} -> {len(text)} chars)"
                           + (" -- the owner's text is too long for the card" if ov_body else ""))
        missing = missing_facts(facts, text)
        if missing:
            logger.warning(f"theme {t['pos']} (entry {t['num']}, {t['title']!r}): the card is missing "
                           + "; ".join(_fact_label(f) for f in missing))
        kept = len(facts) - len(missing)
        fid = (f"facts {kept}/{len(facts)}, {rounds} repair(s)" if condensed else
               "owner's text" if ov_body else "facts all (unchanged)")
        note = (f"theme {t['pos']} (entry {t['num']}): original {len(t0['text'])} chars, budget "
                f"{budget}, final {len(text)} chars, {px} px, title {t['title_px']} px, {fid}, {how}"
                + (f"; MISSING: {', '.join(_fact_label(f) for f in missing)}" if missing else ""))
        logger.info(note)
        notes.append(note)
        plan.append(dict(theme=t, text=text, full_text=full, px=px, budget=budget, condensed=condensed,
                         how=how, facts=facts, facts_total=len(facts), facts_kept=kept, missing=missing,
                         rounds=rounds, last_resort=last_resort, cut=cut, gen_title=t0["title"],
                         override={"title": bool(ov_title), "body": bool(ov_body)} if ov else None))
    cond.save()
    if cond.usage:
        parts = [f"{m}: {u['calls']} call(s), {u['input']} in + {u['output']} out tokens"
                 for m, u in cond.usage.items()]
        note = f"condensation: {'; '.join(parts)}; about ${cond.cost_usd():.3f} for this edition"
    else:
        note = "condensation: no API calls (all themes fit, came from the cache, or were edited)"
    logger.info(note)
    notes.append(note)
    return plan, notes


def render_cards(draft: dict, out_dir: Path, notes: list | None = None) -> list[Path]:
    """Writes card1..cardK.png (themes, then the CTA card) and the carousel PDF; returns
    the PNG paths then the PDF. `notes`, when given, receives the per-theme log lines."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    date = draft.get("date") or datetime.now().strftime("%Y-%m-%d")
    themes = free_themes(draft)
    if not themes:
        raise RuntimeError(f"{date}: no free themes, so no cards")
    outs: list[Path] = []
    try:
        overrides = drafts.card_overrides(date)
    except Exception as e:  # noqa: BLE001  (never block the cards on the overrides table)
        logger.warning(f"{date}: card overrides not read ({e}); generated text used")
        overrides = {}
    cta_desc = " ".join(str((overrides.get(0) or {}).get("body") or "").split()) or None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        fitter = _Fitter(page)
        plan, pnotes = plan_cards(fitter, themes, date, overrides)
        if notes is not None:
            notes.extend(pnotes)
        htmls = [card_html(c["theme"], date, c["text"], px=c["px"]) for c in plan] + [cta_html(date, cta_desc)]
        for k, h in enumerate(htmls, start=1):
            if not fitter.fits(h):  # measured above; never expected
                logger.warning(f"card {k} overflows")
            out = out_dir / f"Housing at Noon {date} card{k}.png"
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
            outs.append(out)
        browser.close()
    _remove_stale(out_dir, date, len(outs))
    # Carousel PDF: the PNGs as pages, each 1080x1350 px at 96 dpi (810x1012.5 pt), no
    # margins, embedded losslessly. PyMuPDF rather than Chromium's page.pdf, which rounds
    # the page height to 1013.04 pt.
    pdf_out = out_dir / f"Housing at Noon {date} carousel.pdf"
    make_carousel_pdf(outs, pdf_out)
    _write_manifest(out_dir, date, plan, cta_desc)
    outs.append(pdf_out)
    return outs


def manifest_path(date: str, folder: Path | None = None) -> Path:
    return (folder or CARDS_DIR) / f"Housing at Noon {date} cards.json"


def _write_manifest(folder: Path, date: str, plan: list[dict], cta_desc: str | None) -> None:
    """What each card shows, for the editor's card panel (app.py /api/cards)."""
    cards_ = []
    for n, c in enumerate(plan, start=1):
        t = c["theme"]
        cards_.append({"n": n, "pos": t["pos"], "num": t["num"], "title": t["title"],
                       "gen_title": c["gen_title"], "title_px": t.get("title_px"),
                       "title_wrap": bool(t.get("title_wrap")), "body": c["text"],
                       "full_body": c["full_text"], "budget": c["budget"], "chars": len(c["text"]),
                       "px": c["px"], "cut": c["cut"], "override": c["override"], "how": c["how"],
                       "facts_total": c["facts_total"], "facts_kept": c["facts_kept"],
                       "missing": [_fact_label(f) for f in c["missing"]],
                       "last_resort": c.get("last_resort", False)})
    data = {"date": date, "rendered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cards": cards_, "cta": {"n": len(plan) + 1, "desc": cta_desc or CTA_DESC,
                                     "default_desc": CTA_DESC, "override": bool(cta_desc)}}
    path = manifest_path(date, folder)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def _remove_stale(folder: Path, date: str, k: int) -> None:
    """Delete card(k+1).png and higher left by an earlier render of the same date."""
    pat = re.compile(rf"^Housing at Noon {re.escape(date)} card(\d+)\.png$")
    for f in folder.glob(f"Housing at Noon {date} card*.png"):
        m = pat.match(f.name)
        if m and int(m.group(1)) > k:
            f.unlink(missing_ok=True)
            logger.info(f"removed stale {f}")


def make_carousel_pdf(pngs: list[Path], out: Path) -> Path:
    import pymupdf as fitz  # PyMuPDF (pulse/requirements.txt)
    w_pt, h_pt = W * 72 / 96, H * 72 / 96
    doc = fitz.open()
    for png in pngs:
        page = doc.new_page(width=w_pt, height=h_pt)
        page.insert_image(fitz.Rect(0, 0, w_pt, h_pt), filename=str(png), keep_proportion=False)
    doc.set_metadata({"title": out.stem, "author": "Home Economics", "creator": "Housing at Noon cards.py"})
    doc.save(str(out), deflate=True, garbage=3)
    doc.close()
    return out


def publish_cards(draft: dict) -> list[Path]:
    outs = render_cards(draft, CARDS_DIR)
    logger.info(f"cards written: {len(outs)} in {CARDS_DIR}")
    if DROPBOX_DIR:
        try:
            dest = Path(DROPBOX_DIR) / "cards"
            dest.mkdir(parents=True, exist_ok=True)
            for o in outs:
                shutil.copyfile(o, dest / o.name)
            _remove_stale(dest, draft.get("date") or "", sum(1 for o in outs if o.suffix == ".png"))
            logger.info(f"cards mirrored to {dest}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dropbox mirror failed: {e}")
    return outs


def _main() -> int:
    ap = argparse.ArgumentParser(description="Render social cards for an edition")
    ap.add_argument("--date", default=None)
    ap.add_argument("--out", default=None, help="output dir (default: publish to NOON_CARDS_DIR + Dropbox)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    date = a.date or drafts.today_et()
    row = drafts.get(date)
    if row is None:
        print(f"no draft for {date}")
        return 1
    outs = render_cards(row["json"], Path(a.out)) if a.out else publish_cards(row["json"])
    for o in outs:
        print(o)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
