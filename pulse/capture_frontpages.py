#!/usr/bin/env python3
"""Capture stylized print-edition front pages of major US papers.

Pipeline (per paper):
  1. Download today's PDF front page from Freedom Forum's CDN
     (https://cdn.freedomforum.org/dfp/pdf{DAY}/{SLUG}.pdf).
  2. Parse with PyMuPDF: render page 1 to a high-res PNG, and extract
     the top 3-5 headlines (largest font on the page, grouped into
     multi-line headlines).
  3. Compose a PAGE-ONLY composite per paper: cropped top portion of
     the print page, faded to cream at the bottom. NO masthead or
     headline text rendered into the image — that all moves to the
     email's HTML side-by-side layout (see email_briefing.py).
  4. Headlines render as plain text — no per-headline URL resolution
     (print-vs-digital headline paraphrasing made automated matching
     too unreliable; user opted to ship without links 2026-06-03).
  5. Emit a sidecar JSON `headlines.json` with masthead/url/headlines
     [{text, article_url}] so email_briefing.py can render clickable
     headline links next to the page snapshot.
  6. Save PNGs to /tmp/front_pages and pulse/data/screenshots, JPGs +
     headlines.json upload to Bluehost.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────

OUT_DIR = Path("/tmp/front_pages")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# pulse.db lookup paths — local dev keeps a copy under data/, GHA mounts
# the canonical copy via Dropbox sync.
PULSE_DB_CANDIDATES = [
    Path(__file__).parent / "data" / "pulse.db",
    Path("/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"),
    Path(os.environ.get("PULSE_DB", "")) if os.environ.get("PULSE_DB") else None,
]
PULSE_DB_CANDIDATES = [p for p in PULSE_DB_CANDIDATES if p]

# Bluehost SFTP config (re-used from old script — same target dir)
SSH_KEY = os.environ.get("SFTP_KEY_PATH", os.path.expanduser("~/.ssh/bluehost_deploy"))
SSH_USER = os.environ.get("SFTP_USER", "yxwrmjmy")
SSH_HOST = os.environ.get("SFTP_HOST", "home-economics.us")
REMOTE_DIR = "public_html/pulse-screenshots"

# Freedom Forum paper slugs. Each paper carries:
#   article_url_re : regex that an article URL on the paper's domain must
#                    match. Used to filter Brave Search results down to
#                    actual article pages (not section indexes, search
#                    pages, etc.).
PAPERS = [
    {"name": "nyt", "slug": "NY_NYT", "masthead": "THE NEW YORK TIMES",
     "url": "https://www.nytimes.com",
     "domain": "nytimes.com",
     "article_url_re": r'https?://www\.nytimes\.com/\d{4}/\d{2}/\d{2}/[^"\'\s<>,\\]+',
     "rss_match": "feed_name LIKE '%New York Times%' OR feed_name LIKE '%NYT%' OR url LIKE '%nytimes.com%'"},
    {"name": "wsj", "slug": "WSJ", "masthead": "THE WALL STREET JOURNAL",
     "url": "https://www.wsj.com",
     "domain": "wsj.com",
     # WSJ digital URLs sit under section paths (politics/business/finance/etc.)
     # and end in an 8-char hash slug. /articles/ exists too but is now rare.
     "article_url_re": r'https?://www\.wsj\.com/(?:articles|politics|us-news|world|business|finance|economy|tech|opinion|lifestyle|real-estate|arts-culture|sports|science|markets|personal-finance|style)/[^"\'\s<>,\\]+',
     "rss_match": "feed_name LIKE '%WSJ%' OR feed_name LIKE '%Wall Street Journal%' OR url LIKE '%wsj.com%'"},
    {"name": "lat", "slug": "CA_LAT", "masthead": "LOS ANGELES TIMES",
     "url": "https://www.latimes.com",
     "domain": "latimes.com",
     "article_url_re": r'https?://www\.latimes\.com/[a-z\-]+/story/\d{4}-\d{2}-\d{2}/[^"\'\s<>,\\]+',
     "rss_match": "feed_name LIKE '%LA Times%' OR feed_name LIKE '%Los Angeles Times%' OR url LIKE '%latimes.com%'"},
    {"name": "hc", "slug": "TX_HC", "masthead": "HOUSTON CHRONICLE",
     "url": "https://www.houstonchronicle.com",
     "domain": "houstonchronicle.com",
     "article_url_re": r'https?://www\.houstonchronicle\.com/[a-z0-9\-/]+/article/[a-z0-9\-]+\.php',
     "rss_match": "feed_name LIKE '%Houston%' OR url LIKE '%houstonchronicle.com%'"},
]

# Brand palette
COLOR_BG          = (246, 247, 243)  # F6F7F3

CANVAS_W           = 1200            # was 2400 (side-by-side composite)
PAGE_RENDER_DPI    = 320

RSS_MATCH_THRESHOLD = 0.65


# ── PDF fetching ─────────────────────────────────────────────────────

def _download_pdf(slug: str, dst: Path) -> bool:
    """Download today's PDF for `slug`. Returns True on success.

    Day is computed in US Eastern time, not UTC. The 9pm-ET manual-trigger
    run lands at ~01:00 UTC (next calendar day in UTC); using UTC there
    fetches `pdf{tomorrow}/...` which 404s because Freedom Forum hasn't
    published tomorrow's print yet (saw this hit briefing #138 — all 4
    papers 404, front-pages section came out empty).
    """
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)  # ET ≈ UTC-4 (summer); off by 1 hr DST edges is OK
    day = et_now.day  # non-padded — Freedom Forum uses "pdf3" not "pdf03"
    url = f"https://cdn.freedomforum.org/dfp/pdf{day}/{slug}.pdf"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.freedomforum.org/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 10_000:
            print(f"  {slug}: PDF too small ({len(data)} bytes) — likely a placeholder, skipping")
            return False
        dst.write_bytes(data)
        print(f"  {slug}: downloaded {len(data)//1024} KB to {dst.name}")
        return True
    except Exception as e:
        print(f"  {slug}: download failed: {e}")
        return False


# ── Headline extraction ──────────────────────────────────────────────

def _extract_headlines(pdf_path: Path, max_headlines: int = 5) -> list[dict]:
    """Pull the top N headlines (by font size, grouped across lines).

    Returns list of dicts {text, bbox=(x0,y0,x1,y1), size}.
    bbox is in PDF coordinates (PyMuPDF's default — origin at top-left).
    """
    doc = fitz.open(pdf_path)
    page = doc[0]
    pw, ph = page.rect.width, page.rect.height

    # Gather every text span on the page with its size.
    spans: list[dict] = []
    for block in page.get_text("dict")["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for s in line["spans"]:
                t = s["text"].strip()
                if not t or len(t) < 2:
                    continue
                if t.lower() in {"the", "a", "an", "of", "to", "in"}:
                    continue
                spans.append({
                    "text": t,
                    "size": s["size"],
                    "bbox": s["bbox"],
                })

    if not spans:
        return []

    # Threshold: anchor on the largest journalism-sized font, ignoring
    # outlier mega-fonts from front-page ads.
    distinct_sizes = sorted({round(s["size"], 1) for s in spans}, reverse=True)
    if len(distinct_sizes) >= 2 and distinct_sizes[0] > distinct_sizes[1] * 1.4:
        robust_max = distinct_sizes[1]
    else:
        robust_max = distinct_sizes[0]
    threshold = robust_max * 0.50
    big = [s for s in spans if s["size"] >= threshold]

    # Group consecutive spans into multi-line headlines.
    big.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
    groups: list[list[dict]] = []
    for s in big:
        placed = False
        for g in groups:
            last = g[-1]
            lx0, ly0, lx1, ly1 = last["bbox"]
            sx0, sy0, sx1, sy1 = s["bbox"]
            same_size = abs(last["size"] - s["size"]) / max(last["size"], 1) < 0.10
            vgap = sy0 - ly1
            line_h = (ly1 - ly0) or s["size"]
            x_aligned = abs(lx0 - sx0) < 12
            overlap = max(0, min(lx1, sx1) - max(lx0, sx0))
            narrower = min(lx1 - lx0, sx1 - sx0) or 1
            substantial_overlap = (overlap / narrower) >= 0.70
            if (same_size and 0 <= vgap < line_h * 1.2
                    and (x_aligned or substantial_overlap)):
                g.append(s)
                placed = True
                break
        if not placed:
            groups.append([s])

    heads = []
    for g in groups:
        text = " ".join(x["text"] for x in g).strip()
        if len(text) < 8:
            continue
        x0 = min(x["bbox"][0] for x in g)
        y0 = min(x["bbox"][1] for x in g)
        x1 = max(x["bbox"][2] for x in g)
        y1 = max(x["bbox"][3] for x in g)
        avg_size = sum(x["size"] for x in g) / len(g)
        heads.append({
            "text": text,
            "bbox": (x0, y0, x1, y1),
            "size": avg_size,
            "page_w": pw,
            "page_h": ph,
        })

    heads.sort(key=lambda h: (-h["size"], h["bbox"][1]))

    bad_prefixes = (
        "vol.", "vol ", "no.", "no ", "$", "©", "edition", "designated",
        "high ", "low ", "weather", "index",
    )
    out, seen = [], set()
    for h in heads:
        t = h["text"].strip()
        if any(t.lower().startswith(p) for p in bad_prefixes):
            continue
        alpha = sum(c.isalpha() for c in t)
        if alpha < len(t) * 0.5:
            continue
        if len(t) < 24 or len(t.split()) < 4:
            continue
        if t.isupper() and len(t) < 40:
            continue
        key = t.lower()[:40]
        if key in seen:
            continue
        if any(key in s for s in seen):
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= max_headlines:
            break

    return out


# ── URL resolution ───────────────────────────────────────────────────

def _open_pulse_db() -> sqlite3.Connection | None:
    for cand in PULSE_DB_CANDIDATES:
        try:
            if cand and Path(cand).exists():
                return sqlite3.connect(str(cand))
        except Exception:
            continue
    return None


def _rss_match_url(con: sqlite3.Connection, rss_where: str, headline: str
                   ) -> tuple[str | None, float]:
    """Best fuzzy-match URL from pulse.db items table for a given headline.

    Returns (url_or_none, ratio). Caller decides whether ratio clears the bar.
    """
    try:
        q = ("SELECT url, title FROM items "
             "WHERE collected_at >= datetime('now', '-3 days') "
             f"AND source = 'rss' AND ({rss_where})")
        rows = con.execute(q).fetchall()
    except Exception as e:
        logger.warning(f"rss-match query failed: {e}")
        return None, 0.0

    if not rows:
        return None, 0.0

    h_lower = headline.lower()
    best_r, best_u = 0.0, None
    for url, title in rows:
        if not title:
            continue
        # Skip wire-tracking links (no canonical article URL) when possible.
        if not url:
            continue
        r = SequenceMatcher(None, h_lower, title.lower()).ratio()
        if r > best_r:
            best_r, best_u = r, url
    return best_u, best_r


def _resolve_article_urls(papers_data: dict, db: sqlite3.Connection | None
                          ) -> dict:
    """Set article_url=None on every headline so the renderer falls back
    to plain text.

    Per user directive 2026-06-03: print-vs-digital headline paraphrasing
    makes automated URL resolution too unreliable to ship. Brave Search
    + Browserbase-scraped Google + DDG all hit either coverage gaps
    (Brave doesn't index paywalled WSJ articles) or CAPTCHA walls. The
    decision: just show headlines as plain text.

    Stats are kept for back-compat with the print/log surface.
    """
    stats: dict = {}
    for slug, info in papers_data.items():
        info.pop("_rss_match", None)
        info.pop("_domain", None)
        info.pop("_paper_cfg", None)
        for h in info["headlines"]:
            h["article_url"] = None
        stats[slug] = {"rss": 0, "brave": 0, "unlinked": len(info["headlines"])}
    return stats


# ── Composition (page-only, no headlines drawn into image) ──────────

def _compose_page_image(pdf_path: Path, out_path: Path) -> bool:
    """Build a page-only composite PNG (cropped top, faded to cream)."""
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        zoom = PAGE_RENDER_DPI / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        page_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        render_w, render_h = pix.width, pix.height

        # Scale page to canvas width.
        target_w = CANVAS_W
        scale = target_w / render_w
        new_w = target_w
        new_h = int(render_h * scale)
        page_img = page_img.resize((new_w, new_h), Image.LANCZOS).convert("RGBA")

        # Crop to top portion and fade to cream at the bottom.
        # With the responsive stacked-on-mobile layout the image and
        # headlines no longer compete for height on mobile (image goes
        # above headlines at full width), so we tune the crop for the
        # desktop side-by-side case: image at 270px wide → ~155px tall,
        # matches the desktop headline column. Mobile renders the same
        # image at ~390px wide (full bleed), ~224px tall — fits nicely
        # above the headlines stack.
        FADE_KEEP_FRAC = 0.30
        FADE_RAMP_FRAC = 0.10
        keep_h = int(new_h * FADE_KEEP_FRAC)
        ramp_h = int(new_h * FADE_RAMP_FRAC)
        crop_h = keep_h + ramp_h
        page_img = page_img.crop((0, 0, new_w, crop_h))

        # Build vertical alpha gradient.
        gradient_col = Image.new("L", (1, crop_h), 0)
        gc_px = gradient_col.load()
        for yy in range(crop_h):
            if yy < keep_h:
                gc_px[0, yy] = 255
            else:
                gc_px[0, yy] = max(0, int(255 * (1.0 - (yy - keep_h) / max(ramp_h, 1))))
        new_alpha = gradient_col.resize((new_w, crop_h))
        r_ch, g_ch, b_ch, _a_old = page_img.split()
        page_img = Image.merge("RGBA", (r_ch, g_ch, b_ch, new_alpha))

        # Paste over cream background so the JPG export reads as continuous
        # with the email's #F6F7F3 page background.
        canvas = Image.new("RGB", (new_w, crop_h), COLOR_BG)
        canvas.paste(page_img, (0, 0), page_img)

        canvas.save(out_path, "PNG", optimize=True)
        canvas.save(SCREENSHOTS_DIR / out_path.name, "PNG", optimize=True)
        jpg_path = out_path.with_suffix(".jpg")
        canvas.save(jpg_path, "JPEG", quality=85, optimize=True)
        print(f"  composite saved ({canvas.size[0]}x{canvas.size[1]})")
        return True
    except Exception as e:
        import traceback
        print(f"  compose failed: {e}")
        traceback.print_exc()
        return False


# ── Upload (unchanged from old script) ───────────────────────────────

def _upload_to_bluehost(local_files: list[str]) -> bool:
    sftp_key_content = os.environ.get("SFTP_KEY", "")
    key_path = SSH_KEY
    if sftp_key_content and not os.path.exists(key_path):
        key_path = "/tmp/sftp_key"
        with open(key_path, "w") as f:
            f.write(sftp_key_content)
            if not sftp_key_content.endswith("\n"):
                f.write("\n")
        os.chmod(key_path, 0o600)

    if not os.path.exists(key_path):
        print(f"SSH key not found at {key_path} — skipping upload")
        return False

    subprocess.run([
        "ssh", "-i", key_path, "-o", "StrictHostKeyChecking=no",
        f"{SSH_USER}@{SSH_HOST}", f"mkdir -p {REMOTE_DIR}",
    ], check=True)

    for f in local_files:
        result = subprocess.run([
            "scp", "-i", key_path, "-o", "StrictHostKeyChecking=no", "-q",
            f, f"{SSH_USER}@{SSH_HOST}:{REMOTE_DIR}/",
        ])
        if result.returncode != 0:
            print(f"  Failed to upload {f}")
            return False
        print(f"  Uploaded {os.path.basename(f)}")
    return True


# ── Entrypoint ───────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true",
                    help="Skip the Bluehost SFTP step (local preview only).")
    args = ap.parse_args()

    print("Capturing print-edition front pages (page-only composites)...")
    uploads: list[str] = []
    papers_data: dict = {}

    for paper in PAPERS:
        name = paper["name"]
        slug = paper["slug"]
        print(f"\n[{name}] slug={slug}")
        pdf_path = OUT_DIR / f"{slug}.pdf"
        if not pdf_path.exists():
            if not _download_pdf(slug, pdf_path):
                continue
        else:
            print(f"  {slug}: PDF already on disk, skipping download")

        # Extract headlines.
        headlines = _extract_headlines(pdf_path, max_headlines=4)
        if not headlines:
            print(f"  {name}: no headlines extracted")

        # Compose page-only image.
        png_path = OUT_DIR / f"{name}.png"
        jpg_path = OUT_DIR / f"{name}.jpg"
        if not _compose_page_image(pdf_path, png_path):
            continue
        uploads.append(str(jpg_path))

        # Stash headline data for the sidecar JSON.
        papers_data[name] = {
            "masthead": paper["masthead"],
            "url": paper["url"],
            "headlines": [{"text": h["text"]} for h in headlines],
            # carrier fields for the resolver (popped before serialization)
            "_rss_match": paper["rss_match"],
            "_domain": paper["domain"],
            "_paper_cfg": {
                "internal_search": paper.get("internal_search"),
                "article_url_re": paper.get("article_url_re"),
            },
        }

    # Resolve per-headline article URLs.
    print("\nResolving per-headline article URLs...")
    db = _open_pulse_db()
    if db is None:
        print("  pulse.db not found — RSS-match step will be skipped")
    _resolve_article_urls(papers_data, db)
    if db is not None:
        db.close()

    # Write sidecar JSON (local + screenshots dir + queued for upload).
    out_json = {}
    for name, info in papers_data.items():
        out_json[name] = {
            "masthead": info["masthead"],
            "url": info["url"],
            "headlines": info["headlines"],
        }
    json_path = OUT_DIR / "headlines.json"
    json_path.write_text(json.dumps(out_json, indent=2, ensure_ascii=False))
    (SCREENSHOTS_DIR / "headlines.json").write_text(
        json.dumps(out_json, indent=2, ensure_ascii=False))
    uploads.append(str(json_path))
    print(f"\nWrote {json_path}")

    if not uploads:
        print("No composites produced — exiting non-zero")
        sys.exit(1)

    if args.no_upload:
        print("\n--no-upload set; skipping Bluehost SFTP. Local assets in:")
        print(f"  {OUT_DIR}")
        print(f"  {SCREENSHOTS_DIR}")
        return

    if _upload_to_bluehost(uploads):
        print("\nDone. Assets live at:")
        for f in uploads:
            print(f"  https://home-economics.us/pulse-screenshots/{os.path.basename(f)}")
    else:
        print("Upload step skipped/failed; local PNGs are in", SCREENSHOTS_DIR)


if __name__ == "__main__":
    main()
