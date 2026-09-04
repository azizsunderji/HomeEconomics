"""Social image cards for a News at Noon edition.

Four 1080x1350 PNGs (the 4:5 portrait X and LinkedIn show largest), rendered
from the draft JSON with Playwright in the house style: cream ground, ink text,
blue numbers, ABC Oracle Edu for sans and Gelasio for the standfirst.

  card 1  masthead, date, standfirst, numbered list of every theme title
  card 2-4  the first three free themes: number, title, opening paragraph,
            source pills

Files: `News at Noon YYYY-MM-DD card1.png` … `card4.png` in NOON_CARDS_DIR
(default NOON_PDF_DIR/cards), mirrored to NOON_PDF_DROPBOX_DIR/cards when set.

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


_LINK_FULL = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def linked(md: str) -> str:
    """Markdown summary -> HTML with the house link style (ink text, blue
    underline), everything else escaped. Used by the PDF cards, where links
    survive; the PNG cards use plain()."""
    out, i = [], 0
    for m in _LINK_FULL.finditer(str(md or "")):
        out.append(_esc(md[i:m.start()]))
        out.append(f'<a href="{_html.escape(m.group(2), quote=True)}">{_esc(m.group(1))}</a>')
        i = m.end()
    out.append(_esc(md[i:]))
    t = "".join(out)
    return re.sub(r"[*_`]+", "", t).replace("\r", "").strip()


def plain(md: str) -> str:
    """Markdown summary -> plain text (links to their text, no markup)."""
    t = _LINK_RE.sub(r"\1", str(md or ""))
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
           display:flex; flex-direction:column; }}
  .head {{ display:flex; align-items:center; justify-content:space-between; }}
  .head img {{ height:56px; width:auto; display:block; }}
  .head .date {{ font-size:24px; color:{MUTED}; }}
  .title {{ font-size:84px; font-weight:700; letter-spacing:-2px; line-height:1; margin:56px 0 12px 0; }}
  .stand {{ font-family:{SERIF}; font-size:38px; line-height:1.32; margin:44px 0 0 0; }}
  .toc {{ margin:44px 0 0 0; padding:0; list-style:none; }}
  .toc li {{ display:flex; gap:22px; font-size:29px; line-height:1.28; margin:0 0 16px 0; }}
  .toc .n {{ color:{BLUE}; font-weight:700; min-width:44px; text-align:right; }}
  .toc .p {{ color:{MUTED}; font-size:22px; letter-spacing:.14em; text-transform:uppercase;
             margin-left:auto; align-self:center; white-space:nowrap; }}
  .num {{ color:{BLUE}; font-size:120px; font-weight:700; line-height:1; margin:60px 0 0 0; }}
  .h {{ font-size:58px; font-weight:700; line-height:1.08; letter-spacing:-1.2px; margin:20px 0 36px 0; }}
  .body {{ font-size:33px; line-height:1.42; }}
  .pills {{ margin:40px 0 0 0; display:flex; flex-wrap:wrap; gap:12px; }}
  .pill {{ background:{LIGHT}; color:{INK}; font-size:22px; padding:10px 18px; border-radius:999px; }}
  .foot {{ margin-top:auto; padding-top:32px; border-top:2px solid {LIGHT}; display:flex;
           justify-content:space-between; align-items:baseline; font-size:24px; color:{MUTED}; }}
  .foot b {{ color:{INK}; font-weight:500; }}
  .foot .cta {{ color:{INK}; }}
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
    return (f'<div class="foot"><span><b>News at Noon</b> · {_esc(label)}</span>'
            f'<span class="cta">Free daily at noon ET → {cta}</span></div>')


LATEST_URL = "https://noon.homeeconomics.us/latest"


def card_cover(draft: dict, entries: list[dict], links: bool = False) -> str:
    date = draft.get("date") or datetime.now().strftime("%Y-%m-%d")
    # standfirst keeps its links (underlined), cut on the plain-text length
    stand_md = _paragraphs_md(draft.get("intro") or "", 1, 10_000)[0] if (draft.get("intro") or "").strip() else ""
    if len(plain(stand_md)) > 300:
        stand_md = _first_paragraph_md(stand_md, 300)
    stand = plain(stand_md)
    # fit the list to the card: fewer titles when the standfirst is long
    max_items = 12 if len(stand) < 160 else 9 if len(stand) < 240 else 7
    items = ""
    for i, e in enumerate(entries, start=1):
        if i > max_items:
            items += f'<li><span class="n"></span><span style="color:{MUTED}">and {len(entries) - max_items} more</span></li>'
            break
        prem = '<span class="p">Premium</span>' if e.get("tier") == "premium" else ""
        t = _esc(e.get("title") or "")
        if links:
            t = f'<a href="{LATEST_URL}">{t}</a>'
        items += f'<li><span class="n">{i}</span><span>{t}</span>{prem}</li>'
    return f"""
<div class="card">
  <div class="head"><img src="{LOGO_URL}" alt="Home Economics"><span class="date">{_esc(date_label(date))}</span></div>
  <div class="title">News at Noon</div>
  <div class="stand">{_card_links(stand_md)}</div>
  <ul class="toc">{items}</ul>
  {_foot("a daily brief on the U.S. housing market", links)}
</div>"""


def _first_paragraph_md(md: str, max_chars: int) -> str:
    """Like first_paragraph but keeps [text](url) markup (length measured on
    the plain text, so the cut matches the PNG cards)."""
    paras = [x.strip() for x in str(md or "").replace("\r", "").split("\n\n") if x.strip()]
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
    paras = [x.strip() for x in str(md or "").replace("\r", "").split("\n\n") if x.strip()]
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
               max_paras: int = 3, max_chars: int = 1100, body_px: int = 33) -> str:
    title = (entry.get("title") or "").strip()
    paras = _paragraphs_md(entry.get("summary") or "", max_paras, max_chars)
    pills = "".join(f'<span class="pill">{_esc(p)}</span>' for p in (entry.get("news_outlets") or [])[:5])
    date = draft.get("date") or ""
    body_html = "".join(f'<p style="margin:0 0 22px 0">{_card_links(x)}</p>' for x in paras)
    return f"""
<div class="card">
  <div class="head"><img src="{LOGO_URL}" alt="Home Economics"><span class="date">{_esc(date_label(date))}</span></div>
  <div class="num">{number}</div>
  <div class="h">{_esc(title)}</div>
  <div class="body" style="font-size:{body_px}px">{body_html}</div>
  <div class="pills">{pills}</div>
  {_foot("theme %d of today's edition" % number, links)}
</div>"""


def _doc(cards: list[str], deck: bool = False) -> str:
    body_cls = ' class="deck"' if deck else ""
    return (f'<!doctype html><html><head><meta charset="utf-8">{_base_css()}</head>'
            f'<body{body_cls}>{"".join(cards)}</body></html>')


def pick_entries(draft: dict) -> tuple[list[dict], list[tuple[int, dict]]]:
    """(all entries in order, [(number, entry)] for the theme cards: first three
    free-tier themes, filled from the rest if fewer than three)."""
    entries = [e for e in (draft.get("entries") or []) if (e.get("title") or "").strip()]
    free = [(i, e) for i, e in enumerate(entries, start=1) if e.get("tier") != "premium"]
    chosen = free[:3]
    if len(chosen) < 3:
        chosen += [(i, e) for i, e in enumerate(entries, start=1) if (i, e) not in chosen][: 3 - len(chosen)]
    return entries, chosen


def render_cards(draft: dict, out_dir: Path) -> list[Path]:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    date = draft.get("date") or datetime.now().strftime("%Y-%m-%d")
    entries, chosen = pick_entries(draft)
    outs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)

        def overflows() -> bool:
            # the footer is pushed below the card when the body is too long
            return page.evaluate("() => { const c = document.querySelector('.card'); return c.scrollHeight > c.clientHeight + 1; }")

        def show(html: str) -> None:
            page.set_content(_doc([html]), wait_until="networkidle")
            page.wait_for_timeout(150)

        # cover
        show(card_cover(draft, entries))
        out = out_dir / f"News at Noon {date} card1.png"
        page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
        outs.append(out)
        # themes: as much of the opening as fits — try 3 paragraphs at 33px,
        # then smaller type, then fewer paragraphs
        for k, (n, e) in enumerate(chosen, start=2):
            fitted = None
            for max_paras in (3, 2, 1):
                for px in (33, 31, 29, 27):
                    show(card_theme(draft, e, n, max_paras=max_paras, body_px=px))
                    if not overflows():
                        fitted = (max_paras, px)
                        break
                if fitted:
                    break
            if not fitted:
                show(card_theme(draft, e, n, max_paras=1, max_chars=640, body_px=27))
            out = out_dir / f"News at Noon {date} card{k}.png"
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
            outs.append(out)
        browser.close()
    return outs


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
