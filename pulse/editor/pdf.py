"""Daily PDF of the edition (for posting on social media).

Renders the premium edition — every theme, working links, no upgrade
boxes — through headless Chromium (Playwright) onto US Letter pages, and
keeps `News at Noon YYYY-MM-DD.pdf` plus `latest.pdf` in NOON_PDF_DIR
(and, when set, a copy in NOON_PDF_DROPBOX_DIR so it lands on the Mac).
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import paths
import render

logger = logging.getLogger("noon.pdf")

PDF_DIR = Path(os.environ.get("NOON_PDF_DIR", str(Path.home() / "work" / "noon" / "pdf")))
DROPBOX_DIR = os.environ.get("NOON_PDF_DROPBOX_DIR", "")
PDF_TIER = os.environ.get("NOON_PDF_TIER", "premium")

# Print stylesheet: Letter page, the 600px email column centred, links kept
# in the house style, front-page images never split across pages.
PRINT_CSS = """
<style>
  @page { size: Letter; margin: 0.55in 0.6in 0.6in 0.6in; }
  html, body { background: #ffffff !important; }
  body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  /* The whole email is one wrapping table/row, so never forbid breaks
     inside generic tables or rows (that pushed the body to page 2). */
  table, tr, td { page-break-inside: auto; break-inside: auto; }
  img { page-break-inside: avoid; break-inside: avoid; }
  tr.fp-row { page-break-inside: avoid; break-inside: avoid; }
  h1, h2, h3 { page-break-after: avoid; break-after: avoid; }
  a[href] { color: inherit; }
</style>
"""


def edition_html(draft: dict, tier: str = PDF_TIER) -> str:
    premium_html, free_html, _ = render.render_variants(draft)
    html = premium_html if tier == "premium" else free_html
    idx = html.lower().find("</head>")
    return html[:idx] + PRINT_CSS + html[idx:] if idx != -1 else PRINT_CSS + html


def make_pdf(draft: dict, out: Path, tier: str = PDF_TIER) -> Path:
    from playwright.sync_api import sync_playwright

    html = edition_html(draft, tier)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 816, "height": 1056})
        page.set_content(html, wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(path=str(out), format="Letter", print_background=True, prefer_css_page_size=True,
                 display_header_footer=False)
        browser.close()
    logger.info(f"pdf written: {out} ({out.stat().st_size:,} bytes)")
    return out


def publish_pdf(draft: dict, tier: str = PDF_TIER) -> Path:
    """Write the dated PDF, refresh latest.pdf, mirror to Dropbox if configured."""
    date = draft["date"]
    dated = PDF_DIR / f"News at Noon {date}.pdf"
    make_pdf(draft, dated, tier)
    shutil.copyfile(dated, PDF_DIR / "latest.pdf")
    if DROPBOX_DIR:
        try:
            dest = Path(DROPBOX_DIR)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dated, dest / dated.name)
            shutil.copyfile(dated, dest / "latest.pdf")
            logger.info(f"pdf mirrored to {dest}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dropbox mirror failed: {e}")
    return dated
