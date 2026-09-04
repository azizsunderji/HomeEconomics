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
</style>
"""


def _foot(label: str) -> str:
    return (f'<div class="foot"><span><b>News at Noon</b> · {_esc(label)}</span>'
            f'<span class="cta">Free daily at noon ET → {SIGNUP}</span></div>')


def card_cover(draft: dict, entries: list[dict]) -> str:
    date = draft.get("date") or datetime.now().strftime("%Y-%m-%d")
    stand = plain(draft.get("intro") or "")
    stand = first_paragraph(stand, 300)
    # fit the list to the card: fewer titles when the standfirst is long
    max_items = 12 if len(stand) < 160 else 9 if len(stand) < 240 else 7
    items = ""
    for i, e in enumerate(entries, start=1):
        if i > max_items:
            items += f'<li><span class="n"></span><span style="color:{MUTED}">and {len(entries) - max_items} more</span></li>'
            break
        prem = '<span class="p">Premium</span>' if e.get("tier") == "premium" else ""
        items += f'<li><span class="n">{i}</span><span>{_esc(e.get("title") or "")}</span>{prem}</li>'
    return f"""<!doctype html><html><head><meta charset="utf-8">{_base_css()}</head><body>
<div class="card">
  <div class="head"><img src="{LOGO_URL}" alt="Home Economics"><span class="date">{_esc(date_label(date))}</span></div>
  <div class="title">News at Noon</div>
  <div class="stand">{_esc(stand)}</div>
  <ul class="toc">{items}</ul>
  {_foot("a daily brief on the U.S. housing market")}
</div></body></html>"""


def card_theme(draft: dict, entry: dict, number: int) -> str:
    title = (entry.get("title") or "").strip()
    body = first_paragraph(entry.get("summary") or "", 760 if len(title) < 50 else 640)
    pills = "".join(f'<span class="pill">{_esc(p)}</span>' for p in (entry.get("news_outlets") or [])[:5])
    date = draft.get("date") or ""
    body_html = "".join(f'<p style="margin:0 0 22px 0">{_esc(x)}</p>' for x in body.split("\n\n"))
    return f"""<!doctype html><html><head><meta charset="utf-8">{_base_css()}</head><body>
<div class="card">
  <div class="head"><img src="{LOGO_URL}" alt="Home Economics"><span class="date">{_esc(date_label(date))}</span></div>
  <div class="num">{number}</div>
  <div class="h">{_esc(title)}</div>
  <div class="body">{body_html}</div>
  <div class="pills">{pills}</div>
  {_foot("theme %d of today's edition" % number)}
</div></body></html>"""


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
    pages = [card_cover(draft, entries)] + [card_theme(draft, e, n) for n, e in chosen]
    outs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for k, html in enumerate(pages, start=1):
            out = out_dir / f"News at Noon {date} card{k}.png"
            page.set_content(html, wait_until="networkidle")
            page.wait_for_timeout(150)
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
