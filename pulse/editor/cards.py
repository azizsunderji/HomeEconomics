"""Social image cards for a Housing at Noon edition: Instagram and X carousels, and a
LinkedIn document PDF.

Owner's rule (Aziz, 28 Sep 2026): no opening or intro card; one card per FREE theme with the
theme's entire text; a format that works for Instagram and X carousels (1080x1350).

  * The themes are exactly the free edition's, in its order and with its numbers
    (email_lunch._split_entries on the rank-sorted entries; the number is the rank).
  * Each theme gets one card, continuing onto a second (or third) card when its text does
    not fit. Layout: a thin header with the theme number and title (continuation cards show
    "Title (continued)" small), the body at 40 px stepping down to 34 px to fit, paragraphs
    kept, links as plain text (the email's anchor words, no underline), source pills on the
    theme's last card only, and a footer "Housing at Noon · <date>" with a "2/2" marker on
    continuation cards. Margins 64 px. Nothing is set below 28 px.
  * The last card of the set carries the sign-up band ("Housing at Noon. / Free edition
    every weekday at noon ET. / homeeconomics.us/noon").
  * At most 10 cards (Instagram's limit). When the themes need more, the last theme's text
    is cut at a sentence end with " …" (then the one before it, if still needed); no theme
    is dropped, and the cut is logged.
  * Every card is measured after rendering: the font steps down first, then the text
    splits onto another card; nothing is clipped.

Files: `Housing at Noon YYYY-MM-DD card1.png` … `cardK.png` (K varies; stale higher-numbered
cards from an earlier render of the same date are removed) and `Housing at Noon YYYY-MM-DD
carousel.pdf` (all cards as 1080x1350 px pages), in NOON_CARDS_DIR (default
NOON_PDF_DIR/cards), mirrored to NOON_PDF_DROPBOX_DIR/cards when set.

    python cards.py            # today's draft
    python cards.py --date 2026-09-04 --out /tmp/cards
"""
from __future__ import annotations

import argparse
import html as _html
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import paths  # noqa: F401  (sys.path setup)
import drafts

logger = logging.getLogger("noon.cards")

PDF_DIR = Path(os.environ.get("NOON_PDF_DIR", str(Path.home() / "work" / "noon" / "pdf")))
CARDS_DIR = Path(os.environ.get("NOON_CARDS_DIR", str(PDF_DIR / "cards")))
DROPBOX_DIR = os.environ.get("NOON_PDF_DROPBOX_DIR", "")
SIGNUP = "homeeconomics.us/noon"
W, H = 1080, 1350
MAX_CARDS = 10          # Instagram's carousel limit
BODY_PX = (40, 38, 36, 34)
MARGIN = 64

INK, MUTED, BLUE, CREAM, LIGHT = "#3D3733", "#7F7570", "#0BB4FF", "#F6F7F3", "#DADFCE"
SANS = '"ABC Oracle Edu", "Helvetica Neue", Helvetica, Arial, sans-serif'

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
    """[{num, title, paras: [[sentence, ...], ...], pills}] for the free edition, in its
    order: the same entry set, numbers, title casing, sentence-start fixes, link narrowing
    ("On X," before handles) and pills as email_lunch.render_lunch_html(tier="free")."""
    from delivery import email_lunch as el
    entries = [e for e in (draft.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: (e.get("rank") is None, e.get("rank", 10**6)))
    shown, _withheld = el._split_entries(entries, "free")
    out = []
    for i, e in enumerate(shown, start=1):
        num = e.get("rank") if isinstance(e.get("rank"), int) else i
        title = (e.get("title") or "").strip()
        title = title[:1].upper() + title[1:]
        summary = el._fix_sentence_starts(_no_images(e.get("summary") or "").strip())
        body = el._body_links(summary)
        paras = []
        for p in re.split(r"(?:<br\s*/?>\s*){2,}", body):
            t = _html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", " ", p)))
            t = " ".join(t.split())
            if t:
                paras.append(sentences(t))
        if re.search(r"https?://", " ".join(" ".join(p) for p in paras)):
            logger.warning(f"theme {num}: a bare URL is in the card text")
        out.append({"num": num, "title": title, "paras": paras, "pills": el._entry_pills(e)})
    return out


# ── HTML ────────────────────────────────────────────────────────────────

def _base_css() -> str:
    m = MARGIN
    return f"""
<style>
  html, body {{ margin:0; padding:0; background:{CREAM}; }}
  body {{ width:{W}px; height:{H}px; overflow:hidden; color:{INK}; font-family:{SANS};
          -webkit-font-smoothing:antialiased; }}
  .card {{ box-sizing:border-box; width:{W}px; height:{H}px; padding:{m}px {m}px {m - 8}px {m}px;
           display:flex; flex-direction:column; overflow:hidden; }}
  .th {{ display:flex; align-items:baseline; gap:20px; flex:none; margin:0 0 30px 0; }}
  .th .n {{ color:{BLUE}; font-weight:700; font-size:46px; line-height:1.1; flex:none; }}
  .th .t {{ font-weight:700; font-size:46px; line-height:1.1; letter-spacing:-0.02em; }}
  .th.cont {{ margin-bottom:24px; }}
  .th.cont .n, .th.cont .t {{ font-size:30px; font-weight:500; line-height:1.25; letter-spacing:0; }}
  .th.cont .t {{ color:{MUTED}; }}
  .body {{ line-height:1.36; letter-spacing:-0.005em; flex:none; }}
  .body p {{ margin:0 0 0.6em 0; }}
  .body p:last-child {{ margin-bottom:0; }}
  .pills {{ margin:28px 0 0 0; display:flex; flex-wrap:wrap; gap:12px; flex:none; }}
  .pill {{ background:{LIGHT}; color:{INK}; font-size:28px; line-height:1.2; padding:8px 20px;
           border-radius:999px; }}
  .foot {{ margin-top:auto; padding-top:28px; display:flex; flex:none; white-space:nowrap;
           justify-content:space-between; align-items:baseline; font-size:28px; color:{MUTED}; }}
  .foot b {{ color:{INK}; font-weight:500; }}
  .ctabox {{ flex:none; box-sizing:border-box; margin:24px -{m}px -{m - 8}px -{m}px;
             padding:40px {m}px 44px {m}px; background:{LIGHT}; }}
  .ctabox .l1 {{ font-size:42px; font-weight:700; line-height:1.15; letter-spacing:-0.02em; }}
  .ctabox .l2 {{ font-size:36px; line-height:1.2; margin-top:4px; }}
  .ctabox .url {{ font-size:42px; font-weight:700; color:{INK}; margin-top:18px; }}
  .ctabox .url span {{ border-bottom:5px solid {BLUE}; padding-bottom:2px; }}
</style>
"""


def card_html(theme: dict, date: str, units: list[tuple[int, str]], *, px: int, first: bool,
              last: bool, cta: bool, marker: str = "", ellipsis: bool = False) -> str:
    """One card. `units` = (paragraph index, sentence) pairs for this card's text;
    `first` = the theme's first card (full title), `last` = its last (pills), `cta` = the
    set's final card (sign-up band)."""
    if first:
        head = (f'<div class="th"><span class="n">{theme["num"]}</span>'
                f'<span class="t">{_esc(theme["title"])}</span></div>')
    else:
        head = (f'<div class="th cont"><span class="n">{theme["num"]}</span>'
                f'<span class="t">{_esc(theme["title"])} (continued)</span></div>')
    paras: list[list[str]] = []
    prev = None
    for pi, s in units:
        if pi != prev:
            paras.append([])
            prev = pi
        paras[-1].append(s)
    if ellipsis and paras:
        paras[-1][-1] = paras[-1][-1] + " …"
    body = "".join(f"<p>{_esc(' '.join(p))}</p>" for p in paras)
    pills = ""
    if last and theme["pills"]:
        pills = '<div class="pills">' + "".join(
            f'<span class="pill">{_esc(p)}</span>' for p in theme["pills"][:6]) + "</div>"
    foot = (f'<div class="foot"><span><b>Housing at Noon</b> · {_esc(date_label(date))}</span>'
            f'<span>{_esc(marker)}</span></div>')
    band = (f'<div class="ctabox"><div class="l1">Housing at Noon.</div>'
            f'<div class="l2">Free edition every weekday at noon ET.</div>'
            f'<div class="url"><span>{SIGNUP}</span></div></div>') if cta else ""
    return (f'<div class="card">{head}<div class="body" style="font-size:{px}px">{body}</div>'
            f'{pills}{foot}{band}</div>')


def _doc(inner: str = "") -> str:
    return (f'<!doctype html><html><head><meta charset="utf-8">{_base_css()}</head>'
            f'<body>{inner}</body></html>')


# ── fitting ─────────────────────────────────────────────────────────────

class _Fitter:
    """Measures card HTML in one Playwright page (body swapped in place, no reloads)."""

    def __init__(self, page, date: str):
        self.page, self.date = page, date
        page.set_content(_doc(), wait_until="load")
        page.evaluate("() => document.fonts.ready")

    def fits(self, html: str) -> bool:
        return not self.page.evaluate("""(h) => {
          document.body.innerHTML = h;
          const c = document.querySelector('.card');
          if (c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1) return true;
          const f = document.querySelector('.foot');
          if (f && f.scrollWidth > f.clientWidth + 1) return true;
          const b = document.querySelector('.ctabox');
          if (b && b.getBoundingClientRect().bottom > c.clientHeight + 1) return true;
          return false; }""", html)

    def pack(self, theme: dict, px: int, cta_last: bool, max_cards: int | None = None
             ) -> list[dict] | None:
        """Greedy split of a theme's sentences into cards at `px`. Returns [{units, first,
        last, ellipsis}] or None when a single sentence cannot fit. With `max_cards`, the
        text is cut at a sentence end (with " …") so the theme ends on that card."""
        units = [(pi, s) for pi, p in enumerate(theme["paras"]) for s in p]
        cards: list[dict] = []
        a = 0

        def fit(u, first, last, ell=False):
            return self.fits(card_html(theme, self.date, u, px=px, first=first, last=last,
                                       cta=cta_last and last, marker="0/0", ellipsis=ell))

        while True:
            first = not cards
            rest = units[a:]
            if fit(rest, first, True):
                cards.append(dict(units=rest, first=first, last=True, ellipsis=False))
                return cards
            if max_cards is not None and len(cards) + 1 >= max_cards:
                # the theme must end here: as many sentences as fit, then " …"
                lo, hi = a, len(units) - 1   # keep units[a:b], b in (a, hi]
                best = None
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if fit(units[a:mid], first, True, ell=True):
                        best, lo = mid, mid
                    else:
                        hi = mid - 1
                if best is None:
                    if not fit(units[a:a + 1], first, True, ell=True):
                        return None
                    best = a + 1
                cards.append(dict(units=units[a:best], first=first, last=True, ellipsis=True,
                                  dropped=len(units) - best))
                return cards
            # a card that continues: the most sentences that fit, leaving at least one
            lo, hi, best = a + 1, len(units) - 1, None
            while lo <= hi:
                mid = (lo + hi) // 2
                if fit(units[a:mid], first, False):
                    best, lo = mid, mid + 1
                else:
                    hi = mid - 1
            if best is None:
                # one sentence is taller than a card: split it at a word boundary
                pi, s = units[a]
                words = s.split(" ")
                if len(words) < 2:
                    return None
                h = len(words) // 2
                units[a:a + 1] = [(pi, " ".join(words[:h])), (pi, " ".join(words[h:]))]
                continue
            cards.append(dict(units=units[a:best], first=first, last=False, ellipsis=False))
            a = best

    def layout(self, theme: dict, cta_last: bool) -> tuple[int, list[dict]]:
        """(px, cards) for a whole theme: one card at the largest size from 40 to 34 px;
        otherwise the fewest cards (as packed at 34 px), at the largest size that keeps
        that count."""
        units = [(pi, s) for pi, p in enumerate(theme["paras"]) for s in p]
        for px in BODY_PX:
            if self.fits(card_html(theme, self.date, units, px=px, first=True, last=True,
                                   cta=cta_last, marker="")):
                return px, [dict(units=units, first=True, last=True, ellipsis=False)]
        best = self.pack(theme, BODY_PX[-1], cta_last)
        if best is None:
            raise RuntimeError(f"theme {theme['num']}: a sentence does not fit on a card")
        px_best = BODY_PX[-1]
        for px in BODY_PX[:-1]:
            c = self.pack(theme, px, cta_last)
            if c is not None and len(c) <= len(best):
                return px, c
        return px_best, best


def plan_cards(fitter: _Fitter, themes: list[dict]) -> tuple[list[tuple[dict, int, list[dict]]], list[str]]:
    """[(theme, px, cards)] for the whole set, capped at MAX_CARDS; plus log notes."""
    notes: list[str] = []
    plan = []
    for k, t in enumerate(themes):
        px, cards = fitter.layout(t, cta_last=(k == len(themes) - 1))
        plan.append((t, px, cards))
    total = sum(len(c) for _t, _p, c in plan)
    # over the cap: cut the last theme's text (then the one before it) at a sentence end
    for k in range(len(plan) - 1, -1, -1):
        if total <= MAX_CARDS:
            break
        t, px, cards = plan[k]
        others = total - len(cards)
        allowed = max(1, MAX_CARDS - others)
        if allowed >= len(cards):
            continue
        cut = fitter.pack(t, BODY_PX[-1], cta_last=(k == len(plan) - 1), max_cards=allowed)
        if cut is None:
            continue
        dropped = cut[-1].get("dropped", 0)
        kept = sum(len(c["units"]) for c in cut)
        note = (f"theme {t['num']} cut to {len(cut)} card(s) at {BODY_PX[-1]} px to stay within "
                f"{MAX_CARDS} cards: {kept} of {kept + dropped} sentences kept, ending with ' …'")
        logger.warning(note)
        notes.append(note)
        plan[k] = (t, BODY_PX[-1], cut)
        total = others + len(cut)
    if total > MAX_CARDS:
        note = f"{total} cards even after cutting: more than {MAX_CARDS} free themes"
        logger.warning(note)
        notes.append(note)
    return plan, notes


def render_cards(draft: dict, out_dir: Path) -> list[Path]:
    """Writes card1..cardK.png and the carousel PDF; returns the PNG paths then the PDF."""
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
        fitter = _Fitter(page, date)
        plan, _notes = plan_cards(fitter, themes)
        n_total = sum(len(c) for _t, _p, c in plan)
        k = 0
        for ti, (t, px, cards) in enumerate(plan):
            for ci, c in enumerate(cards):
                k += 1
                marker = f"{ci + 1}/{len(cards)}" if ci > 0 else ""
                html = card_html(t, date, c["units"], px=px, first=c["first"], last=c["last"],
                                 cta=(k == n_total), marker=marker, ellipsis=c["ellipsis"])
                if not fitter.fits(html):  # the packing measured this exact card; never expected
                    logger.warning(f"card {k}: theme {t['num']} overflows")
                out = out_dir / f"Housing at Noon {date} card{k}.png"
                page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
                outs.append(out)
            logger.info(f"theme {t['num']}: {len(cards)} card(s) at {px} px")
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
