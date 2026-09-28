"""Social image cards for a Housing at Noon edition: Instagram and X carousels, and a
LinkedIn document PDF.

Owner's rules (Aziz, 28 Sep 2026):
  * One card per FREE theme, in free-edition order, and every theme fits on ONE card at a
    fixed body size (36 px). No continuation cards.
  * When a theme's text is longer than the card holds, the builder condenses it with
    Claude to fit, more aggressively the longer the theme is. The condensed text keeps
    the theme's meaning, every number and every attribution, and at most two paragraphs.
  * "It should look really good, that's the key for social": an eyebrow line ("Theme
    One", "Theme Two" ... by position in the free edition, not the entry's rank), a strong
    title (Medium, 54 px), body 36 px at 1.35 leading, source pills (as in the email) on
    every card, and the small Home Economics logo in the footer of every card.
  * The set ends with a standalone call-to-action card (large logo, "Housing at Noon",
    one line of description, homeeconomics.us/noon). Instagram's cap of 10 includes it,
    so at most 9 themes; beyond that the last themes are dropped (logged), never the CTA.

How a theme is fitted:
  1. Budget. The card is rendered with this theme's own title and pills and a filler body
     (the theme's words, two paragraphs), and the longest filler that fits at 36 px is
     the character budget. A two-line title or a second row of pills lowers it.
  2. If the visible text (links reduced to their anchor words) is within the budget, it
     is used unchanged. Otherwise it is condensed, under the owner's rule (Aziz, 28 Sep
     2026): a card must keep every number and every attribution.
     a. Facts to keep are extracted from the visible text, less its pointer sentences
        ("My X post on this is here"): every number, percentage, dollar figure and date
        (regexes, with a few words of context), every @handle and "On X,"-style platform
        lead-in (regexes), every source pill the text names, and every other named source
        or person (Claude Haiku; names not found in the text are
        discarded).
     b. Claude Sonnet condenses the text, given the facts list, a target of 85% of the
        budget and a hard ceiling of the budget, and asked to keep the author's
        commentary (first-person sentences, and an unattributed closing paragraph) and to
        add nothing; at most two paragraphs, plain text.
     c. The reply is checked: every number and date verbatim, every name case-insensitive
        on word boundaries, every handle verbatim, the commentary present (by its
        distinctive words, and in the first person if it was), and the text within the
        budget and fitting at 36 px. If any check fails, a repair request lists the
        missing facts and the exact excess characters, measured on the card when the
        text is within the budget but does not fit (up to 3 repair rounds; only the
        latest draft is sent back, to keep the cost down).
  3. A condensed text is never cut at a sentence end. If no draft passes after 3 repairs,
     each draft is judged by what the card would show (the body steps to 34 px, then
     32 px, to fit it whole) and the one that loses the fewest facts, at the largest
     size, is used; a WARNING names the theme and what is missing. The last
     guard (overflow at 32 px) cuts at a sentence end and logs a WARNING; nothing is
     clipped.
Results are cached in NOON_CARDS_CACHE (default ~/work/noon/cards_cache.json), keyed by
sha1(PROMPT_VERSION + theme markdown + budget), so re-rendering the same draft gives the
same cards at no cost; bumping PROMPT_VERSION retires every cached text. Each
condensation, its fidelity (facts kept / total, repair rounds) and the token usage and
cost per model are logged.

Files: `Housing at Noon YYYY-MM-DD card1.png` … `cardK.png` (the themes, then the CTA
card; stale higher-numbered cards from an earlier render of the same date are removed)
and `Housing at Noon YYYY-MM-DD carousel.pdf` (all cards, 1080x1350 px pages), in
NOON_CARDS_DIR (default NOON_PDF_DIR/cards), mirrored to NOON_PDF_DROPBOX_DIR/cards.

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
BODY_PX = 36
FALLBACK_PXS = (34, 32)   # tried in order when a condensed text still does not fit at 36
MARGIN = 80
# Sonnet condenses and repairs; Haiku extracts names (owner's choice, 28 Sep 2026).
CONDENSE_MODEL = os.environ.get("NOON_CARDS_MODEL", "claude-sonnet-5")
EXTRACT_MODEL = os.environ.get("NOON_CARDS_EXTRACT_MODEL", "claude-haiku-4-5")
PRICE_PER_MTOK = {"claude-sonnet-5": (2.00, 10.00), "claude-haiku-4-5": (1.00, 5.00)}  # in, out USD
# Part of the cache key: bump it whenever the prompts or the checks change, so texts made
# under older rules are not reused.
PROMPT_VERSION = "cards-v5-2026-09-28-facts"
TARGET_SHARE = 0.85       # target length as a share of the budget; the budget is the ceiling
MAX_REPAIRS = 3

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
  .card {{ box-sizing:border-box; width:{W}px; height:{H}px; padding:{m}px {m}px 60px {m}px;
           display:flex; flex-direction:column; overflow:hidden; background:{CREAM}; }}
  .eb {{ flex:none; color:{BLUE}; font-weight:500; font-size:30px; line-height:1; letter-spacing:0.01em;
         margin:0 0 26px 0; }}
  .tt {{ flex:none; font-weight:500; font-size:54px; line-height:1.08; letter-spacing:-0.03em;
         margin:0 0 40px 0; text-wrap:balance; }}
  .body {{ flex:none; line-height:1.35; letter-spacing:-0.005em; }}
  .body p {{ margin:0 0 0.72em 0; hyphens:manual; }}
  .body p:last-child {{ margin-bottom:0; }}
  .pills {{ margin:auto 0 0 0; padding-top:40px; display:flex; flex-wrap:wrap; gap:14px; flex:none; }}
  .pills + .foot {{ margin-top:0; }}
  .pill {{ background:{LIGHT}; color:{INK}; font-size:28px; line-height:1.2; padding:9px 22px 10px;
           border-radius:999px; white-space:nowrap; }}
  .foot {{ margin-top:auto; padding-top:40px; display:flex; flex:none; white-space:nowrap;
           justify-content:space-between; align-items:center; font-size:28px; color:{MUTED}; }}
  .foot img {{ height:52px; width:auto; display:block; }}
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
    """One theme card: eyebrow, title, body (paragraphs separated by a blank line), pills,
    footer with the small logo."""
    paras = [" ".join(p.split()) for p in str(text or "").split("\n\n") if p.strip()]
    # a paragraph never ends on a one-word line: its last two words are bound together
    body = "".join(f"<p>{_esc(_bind_last(p))}</p>" for p in paras)
    pills = ""
    if theme.get("pills"):
        pills = '<div class="pills">' + "".join(
            f'<span class="pill">{_esc(p)}</span>' for p in theme["pills"][:6]) + "</div>"
    foot = (f'<div class="foot"><img src="{_logo()}" alt="Home Economics">'
            f'<span>Housing at Noon · {_esc(date_label(date))}</span></div>')
    return (f'<div class="card"><div class="eb">{theme_word(theme["pos"])}</div>'
            f'<div class="tt">{_esc(theme["title"])}</div>'
            f'<div class="body" style="font-size:{px}px">{body}</div>{pills}{foot}</div>')


def _bind_last(p: str) -> str:
    i = p.rfind(" ")
    return p if i < 0 or len(p) - i > 30 else p[:i] + "\u00a0" + p[i + 1:]


def cta_html(date: str) -> str:
    return (f'<div class="card cta"><img class="logo" src="{_logo()}" alt="Home Economics">'
            f'<h1>Housing at Noon</h1>'
            f'<div class="desc">A daily brief on the U.S. housing market, free every weekday at noon ET</div>'
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
    """(text as the card would show it, px): the largest of 36/34/32 px that fits the whole
    text, or (the last-guard cut at 32 px, None)."""
    for p in (BODY_PX, *FALLBACK_PXS):
        if fitter.fits(card_html(theme, date, text, px=p)):
            return text, p
    return _cut_to_fit(fitter, theme, date, text, FALLBACK_PXS[-1]), None


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
                   capacity: int | None = None) -> str:
    """`capacity`: characters of this draft that fit on the card at 36 px, measured by
    rendering (paragraph breaks and line ends make it lower than the budget)."""
    limit = budget if fits36 or capacity is None else min(budget, capacity)
    target = int(limit * (TARGET_SHARE if limit == budget else 0.95))
    parts = ["That draft needs changes before it can be used:"]
    if len(draft) > limit:
        why = ("" if limit == budget else
               f" (measured on the card: only the first {limit} characters of this draft fit, because "
               "paragraph breaks and line ends take room)")
        parts.append(f"- It is {len(draft)} characters; the limit is {limit}{why}, so at least "
                     f"{len(draft) - limit} characters must go (aim for about {target}).")
    elif not fits36:
        parts.append(f"- It is {len(draft)} characters but does not fit on the card; shorten it by "
                     f"about {max(30, len(draft) - int(budget * TARGET_SHARE))} characters.")
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
        if model.startswith("claude-sonnet-5"):
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
        fk = "facts:" + hashlib.sha1((PROMPT_VERSION + "\x00" + theme["md"]).encode("utf-8")).hexdigest()
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
        """{text, how, facts, missing, rounds, ok}. `fits36(text)` renders the card at 36 px;
        `capacity(text)` gives how many of the text's characters fit on it (for the repair);
        `outcome(text)` gives (text as the card would show it, px or None if it had to be cut).
        A draft passes when every fact is present, it is within the budget and it fits at
        36 px; otherwise up to MAX_REPAIRS repair requests follow. The result is never cut
        here: after the last repair the draft with the fewest missing facts is returned
        whole, and plan_cards sets it at a smaller size if it must."""
        k = self.key(theme, budget)
        hit = self.cache.get(k)
        if hit and hit.get("text"):
            facts = hit.get("facts") or []
            return {"text": hit["text"], "how": "cache", "facts": facts,
                    "missing": missing_facts(facts, hit["text"]), "rounds": hit.get("rounds", 0),
                    "ok": hit.get("ok", False)}
        try:
            facts, names_ok = self.facts(theme)
            messages = [{"role": "user", "content": _prompt(theme, budget, facts)}]
            drafts_: list[tuple[str, list[dict], bool]] = []
            rounds = 0
            while True:
                out = _clean_reply(self._call(CONDENSE_MODEL, messages, system=_SYSTEM))
                miss = missing_facts(facts, out)
                fit = fits36(out)
                drafts_.append((out, miss, fit))
                if not miss and len(out) <= budget and fit:
                    break
                if rounds >= MAX_REPAIRS:
                    break
                rounds += 1
                cap = capacity(out) if (capacity is not None and not fit) else None
                logger.info(f"theme {theme['pos']}: repair {rounds}: {len(out)} chars (budget {budget}"
                            + (f", {cap} fit on the card" if cap is not None else "") + "), "
                            f"missing {[_fact_label(f) for f in miss]}, fits at {BODY_PX} px: {fit}")
                # only the latest draft is sent back (not every earlier one): the repair
                # request carries all that is needed, and the input stays small
                messages = [messages[0], {"role": "assistant", "content": out},
                            {"role": "user", "content": _repair_prompt(out, budget, miss, fit, cap)}]
        except Exception as e:  # noqa: BLE001  (no key, network, refusal): never block the cards
            logger.warning(f"theme {theme['num']}: condensation failed ({e}); the full text is used "
                           "at a smaller size")
            return {"text": theme["text"], "how": "not condensed (error)", "facts": [], "missing": [],
                    "rounds": 0, "ok": False}
        passing = [d for d in drafts_ if not d[1] and d[2] and len(d[0]) <= budget]
        if passing:
            out, miss, fit = passing[-1]
        elif outcome is not None:
            # none passed: judge each draft by what the card would show after the size
            # fallback and, if it must, the last-guard cut; fewest facts lost, then the
            # largest type, then the shortest
            def score(d):
                shown, px = outcome(d[0])
                return (len(missing_facts(facts, shown)), px is None, -(px or 0), len(d[0]))
            out, miss, fit = min(drafts_, key=score)
        else:
            out, miss, fit = min(drafts_, key=lambda d: (len(d[1]), not (d[2] and len(d[0]) <= budget), len(d[0])))
        ok = not miss and fit and len(out) <= budget
        how = "claude" + (f", {rounds} repair round(s)" if rounds else "")
        if not ok:
            how += f", unresolved after {rounds} repair(s)"
        if names_ok:  # without the names the check was incomplete: do not keep the result
            self.cache[k] = {"text": out, "budget": budget, "facts": facts, "rounds": rounds, "ok": ok,
                             "model": CONDENSE_MODEL, "prompt_version": PROMPT_VERSION,
                             "title": theme["title"], "original_chars": len(theme["text"]),
                             "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.dirty = True
        return {"text": out, "how": how, "facts": facts, "missing": miss, "rounds": rounds, "ok": ok}

    def cost_usd(self) -> float:
        tot = 0.0
        for model, u in self.usage.items():
            pin, pout = PRICE_PER_MTOK.get(model, (2.0, 10.0))
            tot += u["input"] * pin / 1e6 + u["output"] * pout / 1e6
        return tot


# ── planning ────────────────────────────────────────────────────────────

def plan_cards(fitter: _Fitter, themes: list[dict], date: str) -> tuple[list[dict], list[str]]:
    """[{theme, text, px, budget, condensed, how, facts_total, facts_kept, missing, rounds}]
    for each theme (at most MAX_CARDS-1), plus log notes."""
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
    for t in themes:
        budget = fitter.budget(t, date)
        text, how, condensed = t["text"], "unchanged", False
        facts: list[dict] = []
        rounds = 0
        if len(text) > budget or not fitter.fits(card_html(t, date, text, px=BODY_PX)):
            r = cond.condense(t, budget, lambda x, t=t: fitter.fits(card_html(t, date, x, px=BODY_PX)),
                              lambda x, t=t: _capacity(fitter, t, date, x, BODY_PX),
                              lambda x, t=t: _outcome(fitter, t, date, x))
            text, how, facts, rounds, condensed = r["text"], r["how"], r["facts"], r["rounds"], True
        px = next((p for p in (BODY_PX, *FALLBACK_PXS) if fitter.fits(card_html(t, date, text, px=p))), None)
        if px is None:  # last guard: never clip
            px = FALLBACK_PXS[-1]
            before = len(text)
            text = _cut_to_fit(fitter, t, date, text, px)
            how += f", CUT at a sentence end at {px} px ({before} -> {len(text)} chars)"
            logger.warning(f"theme {t['pos']} (entry {t['num']}): did not fit at {px} px; cut at a "
                           f"sentence end ({before} -> {len(text)} chars)")
        if px != BODY_PX:
            logger.warning(f"theme {t['pos']} (entry {t['num']}): set at {px} px to fit the whole text")
        missing = missing_facts(facts, text)
        if missing:
            logger.warning(f"theme {t['pos']} (entry {t['num']}, {t['title']!r}): the card is missing "
                           + "; ".join(_fact_label(f) for f in missing))
        kept = len(facts) - len(missing)
        fid = f"facts {kept}/{len(facts)}, {rounds} repair(s)" if condensed else "facts all (unchanged)"
        note = (f"theme {t['pos']} (entry {t['num']}): original {len(t['text'])} chars, budget "
                f"{budget}, final {len(text)} chars, {px} px, {fid}, {how}"
                + (f"; MISSING: {', '.join(_fact_label(f) for f in missing)}" if missing else ""))
        logger.info(note)
        notes.append(note)
        plan.append(dict(theme=t, text=text, px=px, budget=budget, condensed=condensed, how=how,
                         facts=facts, facts_total=len(facts), facts_kept=kept, missing=missing,
                         rounds=rounds))
    cond.save()
    if cond.usage:
        parts = [f"{m}: {u['calls']} call(s), {u['input']} in + {u['output']} out tokens"
                 for m, u in cond.usage.items()]
        note = f"condensation: {'; '.join(parts)}; about ${cond.cost_usd():.3f} for this edition"
    else:
        note = "condensation: no API calls (all themes fit or came from the cache)"
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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        fitter = _Fitter(page)
        plan, pnotes = plan_cards(fitter, themes, date)
        if notes is not None:
            notes.extend(pnotes)
        htmls = [card_html(c["theme"], date, c["text"], px=c["px"]) for c in plan] + [cta_html(date)]
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
    outs.append(pdf_out)
    return outs


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
