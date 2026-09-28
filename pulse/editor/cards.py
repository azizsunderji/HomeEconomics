"""Social image cards for a Housing at Noon edition, for X and LinkedIn.

Four 1080x1350 PNGs (4:5 portrait), rendered from the draft JSON with Playwright in the
house style: cream ground, ink text, blue numbers, ABC Oracle Edu. Nothing is set below
28 px, and every card is checked for overflow and trimmed until it fits. The same four
cards are also written as one four-page PDF (`... carousel.pdf`, pages 1080x1350 px, no
margins) for a LinkedIn document post; X takes the four PNGs as one post.

  card 1  intro: "Housing at Noon · date", then the whole standfirst set as large as fits
          (56 px down to 34 px; first sentence in Medium, the rest Regular), then the
          first theme's image with its caption when it has one, otherwise the large logo.
          If the standfirst does not fit at 34 px even with the logo, it is cut at a
          sentence end with an ellipsis. With no standfirst, the first theme's title is
          set large instead (and that theme is left out of cards 2-4).
  card 2-3  the next themes: number, title, opening paragraph(s), source pills
  card 4  one more theme above a call to action in the bottom quarter
          ("Housing at Noon. Free edition every weekday at noon ET." homeeconomics.us/noon)

Themes on cards 2-4 are the first three free-tier themes in edition order (filled from
premium themes when fewer than three are free); when the hook is the first theme's own
title, that theme is skipped. Owner's rules: carousel format (27 Sep 2026); card 1 carries
the whole standfirst, and the cards are made for X and LinkedIn without a posting step
(28 Sep 2026: "just the cards formatted right, for both platforms, with the CTA").

Files: `Housing at Noon YYYY-MM-DD card1.png` … `card4.png` and
`Housing at Noon YYYY-MM-DD carousel.pdf` in NOON_CARDS_DIR (default NOON_PDF_DIR/cards),
mirrored to NOON_PDF_DROPBOX_DIR/cards when set.

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
LOGO_URL = "https://homeeconomics.us/logo-email.png"
SIGNUP = "homeeconomics.us/noon"
W, H = 1080, 1350

INK, MUTED, BLUE, CREAM, LIGHT = "#3D3733", "#7F7570", "#0BB4FF", "#F6F7F3", "#DADFCE"
SANS = '"ABC Oracle Edu", "Helvetica Neue", Helvetica, Arial, sans-serif'
SERIF = 'Gelasio, Georgia, "Times New Roman", serif'

_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\)")


_LINK_FULL = re.compile(r"""\[([^\]]+)\]\((https?://[^\s)]+)(?:\s+(?:"[^"]*"|'[^']*'))?\)""")
# An image dropped in from the editor (![caption](url) on its own line) is left
# out of the cards: they are images themselves.
_IMG_LINE = re.compile(r"^[ \t]*!\[[^\]\n]*\]\(https?://[^\s)]+\)[ \t]*$", re.M)


def _no_images(md: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _IMG_LINE.sub("", str(md or "")))


def linked(md: str) -> str:
    """Markdown summary -> HTML with the house link style (ink text, blue
    underline), everything else escaped. Used by the PDF cards, where links
    survive; the PNG cards use plain()."""
    md = _no_images(md)
    out, i = [], 0
    for m in _LINK_FULL.finditer(md):
        out.append(_esc(md[i:m.start()]))
        out.append(f'<a href="{_html.escape(m.group(2), quote=True)}">{_esc(m.group(1))}</a>')
        i = m.end()
    out.append(_esc(md[i:]))
    t = "".join(out)
    return re.sub(r"[*_`]+", "", t).replace("\r", "").strip()


def plain(md: str) -> str:
    """Markdown summary -> plain text (links to their text, no markup)."""
    t = _LINK_RE.sub(r"\1", _no_images(md))
    t = re.sub(r"[*_`]+", "", t)
    return t.replace("\r", "").strip()


def first_paragraph(md: str, max_chars: int) -> str:
    """Opening text: the first paragraph, plus the second when both fit."""
    paras = [x.strip() for x in plain(md).split("\n\n") if x.strip()]
    t = paras[0] if paras else ""
    if len(paras) > 1 and len(t) + 2 + len(paras[1]) <= max_chars:
        t = t + "\n\n" + paras[1]
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    # end at the last sentence boundary if one exists past the midpoint
    m = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if m > max_chars // 2:
        return cut[: m + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "…"


def date_label(date: str) -> str:
    return datetime.strptime(date[:10], "%Y-%m-%d").strftime("%A, %B %-d, %Y")


def _esc(s: str) -> str:
    return _html.escape(str(s or ""), quote=False)


def _base_css() -> str:
    return f"""
<style>
  html, body {{ margin:0; padding:0; background:{CREAM}; }}
  body {{ width:{W}px; height:{H}px; overflow:hidden; color:{INK}; font-family:{SANS};
          -webkit-font-smoothing:antialiased; }}
  .card {{ box-sizing:border-box; width:{W}px; height:{H}px; padding:72px 80px 64px 80px;
           display:flex; flex-direction:column; overflow:hidden; }}
  .head {{ display:flex; align-items:center; justify-content:space-between; flex:none; }}
  .head img {{ height:56px; width:auto; display:block; }}
  .head .date {{ font-size:28px; color:{MUTED}; }}
  .kicker {{ font-size:30px; color:{MUTED}; flex:none; }}
  .kicker b {{ color:{INK}; font-weight:700; }}
  .hookline {{ font-weight:700; line-height:1.08; letter-spacing:-0.035em; margin:44px 0 0 0; flex:none; }}
  .intro {{ font-weight:400; line-height:1.28; letter-spacing:-0.012em; margin:40px 0 0 0; flex:none; }}
  .intro p {{ margin:0 0 0.55em 0; }}
  .intro p:last-child {{ margin-bottom:0; }}
  .intro .lead {{ font-weight:500; }}
  .visual {{ flex:1 1 auto; min-height:0; margin:48px 0 0 0; display:flex; flex-direction:column;
             justify-content:flex-start; }}
  .visual .imgbox {{ flex:1 1 auto; min-height:0; display:flex; align-items:flex-end; }}
  .visual .imgbox img {{ max-width:100%; max-height:100%; object-fit:contain; display:block; }}
  .visual .cap {{ font-size:28px; line-height:1.3; color:{MUTED}; margin-top:14px; flex:none; }}
  .visual .logo {{ width:520px; height:auto; display:block; margin-top:auto; }}
  .visual .tag {{ font-size:32px; line-height:1.3; color:{MUTED}; margin:24px 0 0 0; }}
  .num {{ color:{BLUE}; font-size:120px; font-weight:700; line-height:1; margin:60px 0 0 0; flex:none; }}
  .num.small {{ font-size:88px; margin-top:44px; }}
  .h {{ font-size:58px; font-weight:700; line-height:1.08; letter-spacing:-1.2px; margin:20px 0 36px 0; flex:none; }}
  .body {{ font-size:33px; line-height:1.42; flex:none; }}
  .pills {{ margin:32px 0 0 0; display:flex; flex-wrap:wrap; gap:12px; flex:none; }}
  .pill {{ background:{LIGHT}; color:{INK}; font-size:28px; padding:10px 20px; border-radius:999px; }}
  .foot {{ margin-top:auto; padding-top:32px; display:flex; flex:none; white-space:nowrap;
           justify-content:space-between; align-items:baseline; font-size:28px; color:{MUTED}; }}
  .foot b {{ color:{INK}; font-weight:500; }}
  .foot .cta {{ color:{INK}; }}
  .ctabox {{ margin-top:auto; flex:none; box-sizing:border-box; height:{H // 4}px; margin-left:-80px;
             margin-right:-80px; margin-bottom:-64px; padding:52px 80px 56px 80px; background:{LIGHT};
             display:flex; flex-direction:column; justify-content:center; }}
  .ctabox .l1 {{ font-size:46px; font-weight:700; line-height:1.15; letter-spacing:-0.02em; }}
  .ctabox .l2 {{ font-size:40px; line-height:1.2; margin-top:6px; }}
  .ctabox .url {{ font-size:46px; font-weight:700; color:{INK}; margin-top:26px; }}
  .ctabox .url span {{ border-bottom:5px solid {BLUE}; padding-bottom:2px; }}
  a {{ color:{INK}; text-decoration:none; border-bottom:3px solid {BLUE}; padding-bottom:2px; }}
  /* PDF deck: one card per page, links live */
  body.deck {{ height:auto; overflow:visible; }}
  body.deck .card {{ page-break-after:always; break-after:page; }}
  body.deck .card:last-child {{ page-break-after:auto; break-after:auto; }}
  @page {{ size:{W}px {H}px; margin:0; }}
</style>
"""


def _foot(label: str, links: bool = False) -> str:
    cta = (f'<a href="https://{SIGNUP}?src=cards">{SIGNUP}</a>' if links else SIGNUP)
    return (f'<div class="foot"><span><b>Housing at Noon</b> · {_esc(label)}</span>'
            f'<span class="cta">{cta}</span></div>')


# ── hook card helpers ───────────────────────────────────────────────────

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


def intro_paragraphs(draft: dict) -> list[list[str]]:
    """The standfirst as plain text: paragraphs, each a list of sentences (images and
    link markup removed)."""
    intro = plain(draft.get("intro") or "")
    return [sentences(p) for p in intro.split("\n\n") if p.strip()]


def trim_intro(paras: list[list[str]], keep: int) -> list[list[str]]:
    """The first `keep` sentences of the standfirst, with an ellipsis after the last one
    when anything was cut."""
    total = sum(len(p) for p in paras)
    if keep >= total:
        return paras
    out: list[list[str]] = []
    left = keep
    for p in paras:
        if left <= 0:
            break
        out.append(p[:left])
        left -= len(p[:left])
    out[-1] = out[-1][:-1] + [out[-1][-1] + " …"]
    return out


def intro_html(paras: list[list[str]]) -> str:
    """Paragraphs of sentences -> HTML, the first sentence in Medium."""
    out = []
    for i, p in enumerate(paras):
        if i == 0 and p:
            body = f'<span class="lead">{_esc(p[0])}</span>' + (" " + _esc(" ".join(p[1:])) if p[1:] else "")
        else:
            body = _esc(" ".join(p))
        out.append(f"<p>{body}</p>")
    return "".join(out)


def hook_line(draft: dict) -> tuple[str, bool]:
    """(the card-1 hook, whether it came from the first theme's title)."""
    intro = plain(draft.get("intro") or "")
    s = sentences(intro)
    if s:
        return s[0], False
    entries = [e for e in (draft.get("entries") or []) if (e.get("title") or "").strip()]
    return ((entries[0]["title"].strip(), True) if entries else ("Housing at Noon", False))


_IMG_MD = re.compile(r"!\[([^\]\n]*)\]\((https?://[^\s)]+)\)")


def first_image(entry: dict | None) -> tuple[str, str] | None:
    """(url, caption) of the first image dropped into a theme's summary, if any."""
    if not entry:
        return None
    m = _IMG_MD.search(str(entry.get("summary") or ""))
    return (m.group(2), m.group(1).strip()) if m else None


def _data_uri(path: Path) -> str:
    import base64
    kind = "jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "png"
    return f"data:image/{kind};base64," + base64.b64encode(path.read_bytes()).decode()


def _image_src(url: str) -> str:
    """Images uploaded through the editor are read from disk (no network needed);
    anything else is loaded from its URL."""
    try:
        import paths as _p
        images_dir = Path(os.environ.get("NOON_IMAGES_DIR", str(Path.home() / "work" / "noon" / "images")))
        for base in {_p.BASE_URL, "https://noon.homeeconomics.us"}:
            prefix = base.rstrip("/") + "/images/"
            if url.startswith(prefix):
                f = images_dir / url[len(prefix):]
                if f.is_file():
                    return _data_uri(f)
    except Exception:  # noqa: BLE001
        pass
    return url


LOGO_LARGE = Path(__file__).resolve().parent / "static" / "he-large-black.png"


def card_hook(draft: dict, hook: str, image: tuple[str, str] | None, hook_px: int = 84,
              paras: list[list[str]] | None = None) -> str:
    """Card 1. With `paras` (the standfirst) the text is the standfirst in `.intro`;
    otherwise `hook` (a theme title) in the bold `.hookline`."""
    date = draft.get("date") or datetime.now().strftime("%Y-%m-%d")
    if image:
        cap = f'<div class="cap">{_esc(image[1])}</div>' if image[1] else ""
        visual = (f'<div class="visual"><div class="imgbox"><img src="{_html.escape(_image_src(image[0]), quote=True)}" '
                  f'alt=""></div>{cap}</div>')
    else:
        logo = _data_uri(LOGO_LARGE) if LOGO_LARGE.is_file() else LOGO_URL
        visual = (f'<div class="visual"><img class="logo" src="{logo}" alt="Home Economics">'
                  f'<div class="tag">A daily brief on the U.S. housing market</div></div>')
    text = (f'<div class="intro" style="font-size:{hook_px}px">{intro_html(paras)}</div>' if paras
            else f'<div class="hookline" style="font-size:{hook_px}px">{_esc(hook)}</div>')
    return f"""
<div class="card">
  <div class="kicker"><b>Housing at Noon</b> · {_esc(date_label(date))}</div>
  {text}
  {visual}
  <div class="foot"><span>Free edition daily at noon ET</span><span class="cta">{SIGNUP}</span></div>
</div>"""


def _first_paragraph_md(md: str, max_chars: int) -> str:
    """Like first_paragraph but keeps [text](url) markup (length measured on
    the plain text, so the cut matches the PNG cards)."""
    paras = [x.strip() for x in _no_images(md).replace("\r", "").split("\n\n") if x.strip()]
    if not paras:
        return ""
    t = paras[0]
    if len(paras) > 1 and len(plain(t)) + 2 + len(plain(paras[1])) <= max_chars:
        t = t + "\n\n" + paras[1]
    if len(plain(t)) <= max_chars:
        return t
    # Cut on the plain text, then keep the markdown prefix that maps to it:
    # walk the markdown counting visible characters.
    target = len(first_paragraph(t, max_chars).rstrip("…"))
    seen, i = 0, 0
    while i < len(t) and seen < target:
        m = _LINK_FULL.match(t, i)
        if m:
            seen += len(m.group(1)); i = m.end()
        else:
            seen += 1; i += 1
    cut = t[:i].rstrip(",;: ")
    return cut if cut.endswith((".", "?", "!")) else cut + "…"


def _paragraphs_md(md: str, max_paras: int, max_chars: int) -> list[str]:
    """Opening paragraphs of a summary (markdown kept) within a plain-text
    character budget. Paragraphs are never cut mid-way; the budget decides
    how many fit."""
    paras = [x.strip() for x in _no_images(md).replace("\r", "").split("\n\n") if x.strip()]
    out: list[str] = []
    total = 0
    for x in paras[:max_paras]:
        n = len(plain(x))
        if out and total + 2 + n > max_chars:
            break
        out.append(x)
        total += n + 2
    return out or paras[:1]


def _card_links(md_paragraph: str) -> str:
    """The email's link treatment (only the reporting verb, never a handle,
    'On X,' before handles, original links kept if narrowing would lose one)
    with the email's inline styles dropped so the card CSS underlines."""
    from delivery.email_lunch import _body_links
    html = _body_links(md_paragraph)
    return re.sub(r'<a\s+href="([^"]+)"[^>]*>', r'<a href="\1">', html)


def card_theme(draft: dict, entry: dict, number: int, links: bool = False,
               max_paras: int = 3, max_chars: int = 1100, body_px: int = 33,
               cta: bool = False, trim_to: int | None = None, title_px: int = 58) -> str:
    """One theme. `cta` = card 4: the theme above the call-to-action block, no pills.
    `trim_to` cuts the opening paragraph to that many characters (sentence boundary
    where possible, else a word boundary with an ellipsis)."""
    title = (entry.get("title") or "").strip()
    if trim_to:
        paras = [_first_paragraph_md(entry.get("summary") or "", trim_to)]
    else:
        paras = _paragraphs_md(entry.get("summary") or "", max_paras, max_chars)
    date = draft.get("date") or ""
    body_html = "".join(f'<p style="margin:0 0 22px 0">{_card_links(x)}</p>' for x in paras)
    head = (f'<div class="head"><img src="{LOGO_URL}" alt="Home Economics">'
            f'<span class="date">{_esc(date_label(date))}</span></div>')
    num_cls = "num small" if cta else "num"
    top = (f'{head}<div class="{num_cls}">{number}</div>'
           f'<div class="h" style="font-size:{title_px}px">{_esc(title)}</div>'
           f'<div class="body" style="font-size:{body_px}px">{body_html}</div>')
    if cta:
        return f"""
<div class="card">
  {top}
  <div class="ctabox"><div class="l1">Housing at Noon.</div>
    <div class="l2">Free edition every weekday at noon ET.</div>
    <div class="url"><span>{SIGNUP}</span></div></div>
</div>"""
    pills = "".join(f'<span class="pill">{_esc(p)}</span>' for p in (entry.get("news_outlets") or [])[:5])
    return f"""
<div class="card">
  {top}
  <div class="pills">{pills}</div>
  {_foot("theme %d" % number, links)}
</div>"""


def _doc(cards: list[str], deck: bool = False) -> str:
    body_cls = ' class="deck"' if deck else ""
    return (f'<!doctype html><html><head><meta charset="utf-8">{_base_css()}</head>'
            f'<body{body_cls}>{"".join(cards)}</body></html>')


def pick_entries(draft: dict, skip_first: bool = False) -> tuple[list[dict], list[tuple[int, dict]]]:
    """(all entries in order, [(number, entry)] for cards 2-4: the first three free-tier
    themes, filled from the rest if fewer than three). `skip_first` leaves out theme 1
    (used when the hook card already shows its title)."""
    entries = [e for e in (draft.get("entries") or []) if (e.get("title") or "").strip()]
    pool = [(i, e) for i, e in enumerate(entries, start=1) if not (skip_first and i == 1)]
    free = [(i, e) for i, e in pool if e.get("tier") != "premium"]
    chosen = free[:3]
    if len(chosen) < 3:
        chosen += [(i, e) for i, e in pool if (i, e) not in chosen][: 3 - len(chosen)]
        chosen.sort(key=lambda x: x[0])
    return entries, chosen


def render_cards(draft: dict, out_dir: Path) -> list[Path]:
    """Writes card1-4.png and the carousel PDF; returns the four PNG paths then the PDF."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    date = draft.get("date") or datetime.now().strftime("%Y-%m-%d")
    hook, hook_is_title = hook_line(draft)
    intro = intro_paragraphs(draft)
    entries, chosen = pick_entries(draft, skip_first=hook_is_title)
    image = first_image(entries[0] if entries else None)
    outs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)

        def overflows() -> bool:
            # anything pushed past the card's bottom or right edge, or text wider than its box
            return page.evaluate("""() => {
              const c = document.querySelector('.card');
              if (c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1) return true;
              const f = document.querySelector('.foot');
              if (f && f.scrollWidth > f.clientWidth + 1) return true;
              const v = document.querySelector('.visual .imgbox');
              if (v && v.clientHeight < 380) return true;
              const vis = document.querySelector('.visual');
              if (vis && vis.scrollHeight > vis.clientHeight + 1) return true;
              return false; }""")

        def show(html: str) -> None:
            page.set_content(_doc([html]), wait_until="networkidle")
            page.wait_for_timeout(150)

        def shot(k: int) -> None:
            out = out_dir / f"Housing at Noon {date} card{k}.png"
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
            outs.append(out)

        # card 1 (owner's rule 28 Sep 2026: the whole standfirst, as large as fits, never
        # below 34 px). Order: with the image, then with the logo, then cut at a sentence
        # end (with the logo) until it fits.
        fitted = False
        if intro:
            n_sent = sum(len(x) for x in intro)
            tries = [(intro, image, px) for px in range(56, 33, -2)]
            if image:
                tries += [(intro, None, px) for px in range(56, 33, -2)]
            tries += [(trim_intro(intro, k), None, 34) for k in range(n_sent - 1, 0, -1)]
            for paras, img, px in tries:
                show(card_hook(draft, hook, img, hook_px=px, paras=paras))
                if not overflows():
                    fitted = True
                    break
            if not fitted:
                logger.warning("card 1: the standfirst's first sentence overflows at 34 px")
        else:
            for text in [hook] + [first_paragraph(hook, n) for n in (220, 170, 130, 90)]:
                for px in (92, 84, 76, 68, 60, 54, 48):
                    show(card_hook(draft, text, image, hook_px=px))
                    if not overflows():
                        fitted = True
                        break
                if fitted:
                    break
            if not fitted and image:  # an image too tall to leave room: fall back to the logo
                show(card_hook(draft, first_paragraph(hook, 170), None, hook_px=60))
        shot(1)

        # cards 2-4: as much of the opening as fits — 3 paragraphs at 33px, then smaller
        # type (never below 28px), then fewer paragraphs, then a trimmed first paragraph
        for k, (n, e) in enumerate(chosen, start=2):
            cta = k == 4
            attempts = [dict(max_paras=mp, body_px=px) for mp in ((2, 1) if cta else (3, 2, 1))
                        for px in (33, 31, 29, 28)]
            attempts += [dict(trim_to=t, body_px=px) for t in (1000, 880, 760, 640, 520, 420, 330, 250, 180, 120)
                         for px in (31, 28)]
            attempts += [dict(trim_to=t, body_px=28, title_px=46) for t in (180, 120, 80)]
            for kw in attempts:
                show(card_theme(draft, e, n, cta=cta, **kw))
                if not overflows():
                    break
            else:
                logger.warning(f"card {k}: theme {n} still overflows at the smallest setting")
            shot(k)

        browser.close()
    # LinkedIn carousel: the same four PNGs as the pages of one PDF. Each page is
    # 1080x1350 px at 96 dpi (810x1012.5 pt), no margins, the PNG filling the page
    # (embedded losslessly), so every page is exactly one card. PyMuPDF rather than
    # Chromium's page.pdf, which rounds the page height to 1013.04 pt.
    pdf_out = out_dir / f"Housing at Noon {date} carousel.pdf"
    make_carousel_pdf(outs, pdf_out)
    outs.append(pdf_out)
    return outs


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
