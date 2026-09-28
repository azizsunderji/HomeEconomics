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
     is used unchanged. Otherwise Claude condenses it to at most N = 95% of the budget
     characters; if the reply is longer than N it is asked once more with N cut by 10%,
     and if that is still too long the text is cut at a sentence end.
  3. The card is measured again. If it still overflows (rare), the body steps to 34 px,
     then the text is cut at a sentence end. Nothing is clipped.
Condensed text is cached in NOON_CARDS_CACHE (default ~/work/noon/cards_cache.json), keyed
by sha1(theme markdown + budget), so re-rendering the same draft gives the same cards at
no cost. Each condensation and the token usage are logged.

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
FALLBACK_PX = 34
MARGIN = 80
# The pipeline's live synthesis (v4b) has no Sonnet step (Opus writes, Haiku gates), so
# the owner's default applies.
CONDENSE_MODEL = os.environ.get("NOON_CARDS_MODEL", "claude-sonnet-5")
PRICE_PER_MTOK = {"claude-sonnet-5": (2.00, 10.00)}   # input, output USD

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
        out.append({"pos": i, "num": num, "title": title, "text": text, "md": md,
                    "pills": el._entry_pills(e)})
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


def _cut_sentences(text: str, ok) -> str:
    """Drop whole sentences from the end, one at a time, until ok(text) is true (measured
    by rendering the card). Keeps at least one sentence."""
    paras = _para_sentences(text)
    join = lambda ps: "\n\n".join(" ".join(p) for p in ps if p)  # noqa: E731
    while not ok(join(paras)) and sum(len(p) for p in paras) > 1:
        paras[-1].pop()
        paras = [p for p in paras if p]
    return join(paras)


def _cut_to_chars(text: str, n: int) -> str:
    """Whole sentences (paragraphs kept) up to n characters; at least one sentence."""
    paras = _para_sentences(text)
    out: list[list[str]] = []
    total = 0
    for p in paras:
        cur: list[str] = []
        for s in p:
            add = len(s) + (1 if cur else (2 if out else 0))
            if total + add > n and (out or cur):
                if cur:
                    out.append(cur)
                return "\n\n".join(" ".join(x) for x in out)
            cur.append(s)
            total += add
        out.append(cur)
    return "\n\n".join(" ".join(x) for x in out)


# ── condensing with Claude ──────────────────────────────────────────────

_SYSTEM = ("You condense sections of Housing at Noon, a daily brief on the U.S. housing market, "
           "so that each section fits on one social-media card. You shorten wording; you never "
           "add facts, opinions or emphasis.")


def _aim(theme: dict, n: int) -> int:
    """The length suggested to the model, below the hard limit n. The model overshoots
    more the harder it has to compress, so the longer the theme is relative to n, the
    lower the suggestion: 85% of n for light cuts, down to 60% for a theme 3-4 times n."""
    r = len(theme["text"]) / max(1, n)
    return int(n * max(0.60, min(0.85, 0.95 - 0.12 * (r - 1))))


def _prompt(theme: dict, n: int) -> str:
    aim = _aim(theme, n)
    return (
        f"Condense the theme below to AT MOST {n} characters in total, counting spaces and "
        f"punctuation; aim for about {aim} characters (roughly {max(20, aim // 6)} "
        f"words), since going over the limit is not allowed. The original is {len(theme['text'])} "
        f"characters, so cut about {max(0, 100 - round(100 * n / max(1, len(theme['text']))))}% of it.\n\n"
        "Rules:\n"
        "- Keep the theme's meaning and its main point.\n"
        "- Keep every number exactly as written (percentages, dollar amounts, counts, rates, "
        "dates, rankings).\n"
        "- Keep every attribution: who reported, said, estimated, found or wrote each fact "
        "(people, firms, agencies, publications, and platform lead-ins such as \"On X,\" or "
        "\"On LinkedIn,\").\n"
        "- Passages in the first person (\"I\", \"my\", \"to me\") are the author's own commentary, "
        "the most distinctive part of the brief. Keep that view, condensed, in the first person, "
        "as the last paragraph. Leave out only a sentence that points to something not on the "
        "card (\"the map below\", \"see my post\", \"I wrote about this here\").\n"
        "- Numbers and attributions take priority over descriptive wording: cut restatement, "
        "background, adjectives and connecting phrases first.\n"
        "- Keep the paragraph structure, with at most 2 paragraphs separated by one blank line "
        "(the reported facts first, the author's commentary, if any, second).\n"
        "- Plain text only: no markdown links (keep only the anchor words), no headings, no "
        "bullets, no bold or italics, no quotation of this prompt.\n"
        "- Write in the brief's register: measured, precise, restrained, no sensationalism.\n"
        f"- Return only the condensed text, at most {n} characters.\n\n"
        f"Theme title (context only; do not repeat it): {theme['title']}\n\n"
        f"Theme text:\n{theme['text']}"
    )


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
        self.usage = {"calls": 0, "input": 0, "output": 0}
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
        return hashlib.sha1((theme["md"] + "\x00" + str(budget)).encode("utf-8")).hexdigest()

    def _ask(self, theme: dict, n: int, previous: str | None = None, n0: int | None = None) -> str:
        """One request. With `previous`, the model is shown its earlier reply (which was
        over n0) and asked to shorten that to at most n."""
        import anthropic
        if self.client is None:
            self.client = anthropic.Anthropic()
        messages = [{"role": "user", "content": _prompt(theme, n0 or n)}]
        if previous is not None:
            # show the reply's sentences with their lengths: the model does the arithmetic
            # far better than it estimates length
            listing = "\n".join(
                f"[{len(x)}] {x}" for para in _para_sentences(previous) for x in para)
            cut = len(previous) - int(n * 0.9)
            messages += [
                {"role": "assistant", "content": previous},
                {"role": "user", "content": (
                    f"That is {len(previous)} characters; the limit is now {n}, so about {cut} "
                    "characters must go. Here are your sentences with their lengths in characters:\n\n"
                    f"{listing}\n\n"
                    "Shorten or merge sentences until the lengths add up to at most "
                    f"{int(n * 0.9)} (plus about 1 per space between sentences). Remove background, "
                    "restatement, adjectives and connecting words first. Keep the numbers, attributions "
                    "and the author's first-person commentary. The limit is strict: only if it cannot be "
                    "met otherwise, drop the least important reported fact (with its number and its "
                    "source) rather than go over, and keep the commentary. At most 2 paragraphs. Return "
                    "only the text, without the bracketed lengths.")}]
        resp = self.client.messages.create(
            model=CONDENSE_MODEL, max_tokens=4000, system=_SYSTEM,
            thinking={"type": "disabled"}, messages=messages)
        self.usage["calls"] += 1
        self.usage["input"] += resp.usage.input_tokens
        self.usage["output"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError("the model declined")
        return _clean_reply("".join(b.text for b in resp.content if b.type == "text"))

    def condense(self, theme: dict, budget: int, fits=None) -> tuple[str, str]:
        """(text within N = 95% of budget characters, how it was made). `fits(text)` renders
        the card: a second reply that is over N but still fits at 36 px is kept, since the
        5% margin only exists to make the text fit; otherwise it is cut at a sentence end."""
        k = self.key(theme, budget)
        hit = self.cache.get(k)
        if hit and hit.get("text"):
            return hit["text"], "cache"
        n = int(budget * 0.95)
        how = "claude"
        try:
            out = self._ask(theme, n)
            if len(out) > n:
                logger.info(f"theme {theme['num']}: first reply {len(out)} chars > {n}; asking again")
                first = len(out)
                out = self._ask(theme, int(n * 0.9), previous=out, n0=n)
                how = f"claude (second try; first reply {first} chars, second {len(out)})"
            if len(out) > n and fits is not None and fits(out):
                how += f", over N={n} but fits at {BODY_PX} px, kept"
            elif len(out) > n:
                before = len(out)
                out = (_cut_sentences(out, fits) if fits is not None else _cut_to_chars(out, n))
                how += f", cut at a sentence end ({before} -> {len(out)} chars)"
        except Exception as e:  # noqa: BLE001  (no key, network, refusal): never block the cards
            logger.warning(f"theme {theme['num']}: condensation failed ({e}); cutting at a sentence end")
            t2 = _cut_sentences(theme["text"], fits) if fits is not None else _cut_to_chars(theme["text"], n)
            return t2, "cut (no condensation)"
        self.cache[k] = {"text": out, "budget": budget, "n": n, "model": CONDENSE_MODEL,
                         "title": theme["title"], "original_chars": len(theme["text"]),
                         "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.dirty = True
        return out, how

    def cost_usd(self) -> float:
        pin, pout = PRICE_PER_MTOK.get(CONDENSE_MODEL, (2.0, 10.0))
        return self.usage["input"] * pin / 1e6 + self.usage["output"] * pout / 1e6


# ── planning ────────────────────────────────────────────────────────────

def plan_cards(fitter: _Fitter, themes: list[dict], date: str) -> tuple[list[dict], list[str]]:
    """[{theme, text, px, budget, condensed, how}] for each theme (at most MAX_CARDS-1),
    plus log notes."""
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
        if len(text) > budget or not fitter.fits(card_html(t, date, text, px=BODY_PX)):
            text, how = cond.condense(
                t, budget, fits=lambda x, t=t: fitter.fits(card_html(t, date, x, px=BODY_PX)))
            condensed = True
        px = BODY_PX
        if not fitter.fits(card_html(t, date, text, px=px)):
            px = FALLBACK_PX
            if not fitter.fits(card_html(t, date, text, px=px)):
                text = _cut_to_fit(fitter, t, date, text, px)
                how += f", cut at a sentence end at {px} px"
            logger.warning(f"theme {t['num']}: overflowed at {BODY_PX} px; set at {px} px")
        note = (f"theme {t['pos']} (entry {t['num']}): original {len(t['text'])} chars, budget "
                f"{budget}, final {len(text)} chars, {how}, {px} px")
        logger.info(note)
        notes.append(note)
        plan.append(dict(theme=t, text=text, px=px, budget=budget, condensed=condensed, how=how))
    cond.save()
    if cond.usage["calls"]:
        note = (f"condensation: {cond.usage['calls']} call(s) to {CONDENSE_MODEL}, "
                f"{cond.usage['input']} input + {cond.usage['output']} output tokens, "
                f"about ${cond.cost_usd():.3f}")
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
