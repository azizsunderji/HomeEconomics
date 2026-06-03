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
  4. Resolve a per-headline article URL via a two-layer cascade:
        L1: RSS match against pulse.db (free)
        L2: Brave Search API ($5/1k requests + $5/mo free credit)
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
from datetime import datetime, date
from difflib import SequenceMatcher
from pathlib import Path

import fitz  # PyMuPDF
import requests
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
    """Download today's PDF for `slug`. Returns True on success."""
    day = datetime.now().day  # non-padded — Freedom Forum uses "pdf3" not "pdf03"
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


# ── Layer 2: Brave Search API ────────────────────────────────────────
#
# Brave Search ships a proper search API: $5 per 1000 requests with a free
# $5/month credit (covers ~450/month, comfortably more than we need).
# Replaces an earlier Google-via-Browserbase scraper that hit CAPTCHA
# pages after ~10 queries — Google fingerprints Browserbase's browser
# regardless of proxy IP, and Browserbase's `advanced_stealth` flag is
# Enterprise-only.

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_FRESHNESS_DAYS = 4


def _brave_search_url(paper: dict, paper_domain: str,
                      headline: str) -> str | None:
    """Resolve an article URL by querying the Brave Search API for
    `<headline> site:<paper_domain>` and picking the first result that
    (a) matches the paper's article URL pattern AND (b) is fresh.

    Freshness check: URL paths with /YYYY/MM/DD/ or /YYYY-MM-DD/ are
    parsed and required to be within _BRAVE_FRESHNESS_DAYS of today.
    URLs without a date (e.g., Houston Chronicle's slug-based scheme)
    fall back to Brave's `age` field — "X hours ago" / "X days ago" /
    "1 week ago" are accepted; older is rejected.

    Returns None when no result clears both gates — caller leaves the
    headline as plain text (user directive: better unlinked than
    linked to a stale or wrong article).
    """
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        logger.warning("BRAVE_API_KEY not set — skipping search resolution")
        return None
    pattern = paper.get("article_url_re")
    if not pattern or not paper_domain:
        return None

    # Strip punctuation that confuses Brave's tokenizer ("$1.8" → "1.8",
    # quotes/apostrophes → space). Keep word chars, hyphens, periods,
    # and spaces.
    cleaned = re.sub(r"[^\w\s.\-]", " ", headline)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    query = f"{cleaned} site:{paper_domain}"

    try:
        r = requests.get(
            _BRAVE_ENDPOINT,
            params={"q": query, "count": 10, "country": "us"},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=12,
        )
    except Exception as e:
        logger.warning(f"brave search request failed for {headline!r}: {e}")
        return None
    if r.status_code != 200:
        logger.warning(
            f"brave search status {r.status_code} for {headline!r}: "
            f"{r.text[:160]}"
        )
        return None
    try:
        results = r.json().get("web", {}).get("results", []) or []
    except Exception as e:
        logger.warning(f"brave search JSON parse failed: {e}")
        return None

    today = datetime.now().date()
    _date_re = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/|/(\d{4})-(\d{2})-(\d{2})/")

    def _is_article_url(u: str) -> bool:
        if not re.match(pattern, u):
            return False
        if "/search" in u or u.endswith("/"):
            return False
        if "?mod=nav" in u or "?mod=wsjheader" in u or "?mod=wsjfooter" in u:
            return False
        return True

    for res in results:
        url = (res.get("url") or "").strip()
        if not url or not _is_article_url(url):
            continue
        url_clean = url.split("#")[0]

        m_dt = _date_re.search(url_clean)
        if m_dt:
            try:
                yy = int(m_dt.group(1) or m_dt.group(4))
                mm = int(m_dt.group(2) or m_dt.group(5))
                dd = int(m_dt.group(3) or m_dt.group(6))
                url_date = date(yy, mm, dd)
                if 0 <= (today - url_date).days <= _BRAVE_FRESHNESS_DAYS:
                    return url_clean
                continue  # dated but stale; keep looking
            except Exception:
                pass

        # No date in URL — use Brave's `age` field as a freshness signal.
        age = (res.get("age") or "").lower()
        if any(t in age for t in ("hour", "day", "today", "yesterday")):
            return url_clean
        if "1 week" in age or "week ago" in age:
            return url_clean
        # Older — skip and look at the next result.

    return None


def _resolve_article_urls(papers_data: dict, db: sqlite3.Connection | None
                          ) -> dict:
    """Populate {article_url} on each headline via a two-layer cascade.

      L1 RSS    — free, low hit rate today but cheap to keep
      L2 Brave  — Brave Search API, $5/1000 with $5/mo free credit
                  (~$0/run at our volume of ~450 queries/month)

    No fake-URL fallback: when both layers miss, article_url is set to
    None and the renderer leaves the headline as plain text. Per user
    directive: better unlinked than linked to the wrong article.

    Returns a stats dict: {paper: {rss, brave, unlinked: int}}.
    """
    stats: dict = {}
    brave_calls = 0

    for slug, info in papers_data.items():
        s = {"rss": 0, "brave": 0, "unlinked": 0}
        rss_where = info.pop("_rss_match", "")
        domain = info.pop("_domain", "")
        paper_cfg = info.pop("_paper_cfg", {})

        for h in info["headlines"]:
            text = h["text"]
            url = None

            # L1 — RSS corpus match (free)
            if db is not None and rss_where:
                u, r = _rss_match_url(db, rss_where, text)
                if u and r >= RSS_MATCH_THRESHOLD:
                    url = u
                    s["rss"] += 1
                    print(f"  [{slug}] rss-match r={r:.2f} {text[:50]!r}")

            # L2 — Brave Search API
            if url is None and paper_cfg:
                brave_calls += 1
                u = _brave_search_url(paper_cfg, domain, text)
                if u:
                    url = u
                    s["brave"] += 1
                    print(f"  [{slug}] brave-hit {text[:50]!r}")
                    print(f"    -> {url[:120]}")

            if url is None:
                s["unlinked"] += 1

            h["article_url"] = url  # None means render as plain text

        stats[slug] = s
        print(f"  [{slug}] resolution: rss={s['rss']} brave={s['brave']} "
              f"unlinked={s['unlinked']}")

    # Brave: $5 per 1000 queries → $0.005 per call
    cost = brave_calls * 0.005
    print(f"  totals: brave={brave_calls} (est. cost ${cost:.3f})")
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
        FADE_KEEP_FRAC = 0.30
        FADE_RAMP_FRAC = 0.12
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
