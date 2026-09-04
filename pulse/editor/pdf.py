"""Daily PDFs of the edition.

Two files per edition, rendered through headless Chromium (Playwright)
onto US Letter pages and kept in NOON_PDF_DIR (and, when set, mirrored
to NOON_PDF_DROPBOX_DIR so they land on the Mac):

  premium  `News at Noon YYYY-MM-DD.pdf`      + `latest.pdf`
           every theme, working links, no upgrade boxes (owner only:
           served at /latest-premium.pdf).
  social   `News at Noon YYYY-MM-DD free.pdf` + `latest-free.pdf`
           the free edition with sign-up copy and links walled to the
           sign-up page; this is what /latest.pdf serves publicly and
           what gets posted on social media.
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
    """Edition HTML with the print stylesheet. tier: premium | free | social."""
    if tier == "social":
        html = render.render_social(draft)
    else:
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
    """Write both edition PDFs and refresh the `latest` copies, then mirror
    all four files to Dropbox if configured.

    `tier` selects what goes into the main file (`News at Noon DATE.pdf` /
    `latest.pdf`; premium by default — `cli.py pdf --tier` can override).
    The social file (`News at Noon DATE free.pdf` / `latest-free.pdf`) is
    always written. Returns the main (premium) path, as callers expect.
    """
    date = draft["date"]
    dated = PDF_DIR / f"News at Noon {date}.pdf"
    make_pdf(draft, dated, tier)
    shutil.copyfile(dated, PDF_DIR / "latest.pdf")
    social = PDF_DIR / f"News at Noon {date} free.pdf"
    make_pdf(draft, social, "social")
    shutil.copyfile(social, PDF_DIR / "latest-free.pdf")
    logger.info(f"pdf published: {dated.name} ({tier}) and {social.name} (social)")
    if DROPBOX_DIR:
        try:
            dest = Path(DROPBOX_DIR)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dated, dest / dated.name)
            shutil.copyfile(dated, dest / "latest.pdf")
            shutil.copyfile(social, dest / social.name)
            shutil.copyfile(social, dest / "latest-free.pdf")
            logger.info(f"pdfs mirrored to {dest}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Dropbox mirror failed: {e}")
    return dated
