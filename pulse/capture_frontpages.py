#!/usr/bin/env python3
"""Capture stylized print-edition front pages of major US papers.

Pipeline (per paper):
  1. Download today's PDF front page from Freedom Forum's CDN
     (https://cdn.freedomforum.org/dfp/pdf{DAY}/{SLUG}.pdf).
  2. Parse with PyMuPDF: render page 1 to a high-res PNG, and extract
     the top 3-5 headlines (largest font on the page, grouped into
     multi-line headlines).
  3. Tilt the rendered page with a PIL perspective transform (no
     image-gen, no 3D engine — just a coefficient matrix).
  4. Project the headline bboxes through the same transform so we
     know where each headline now sits in the tilted image.
  5. Compose a single image per paper: tilted page on one side,
     headline callouts on the other, leader lines with brand-color
     dots connecting them.
  6. Save to /tmp/front_pages/{name}.png AND to
     pulse/data/screenshots/{name}.png so the existing email pipeline
     picks it up. Also uploads JPGs to Bluehost for the email's <img>.

Brand colors used: Black #3D3733 (lines), Blue #0BB4FF and Orange
#F4743B (dots, alternating), Background cream #F6F7F3.
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Configuration ────────────────────────────────────────────────────

OUT_DIR = Path("/tmp/front_pages")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCREENSHOTS_DIR = Path(__file__).parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Bluehost SFTP config (re-used from old script — same target dir)
SSH_KEY = os.environ.get("SFTP_KEY_PATH", os.path.expanduser("~/.ssh/bluehost_deploy"))
SSH_USER = os.environ.get("SFTP_USER", "yxwrmjmy")
SSH_HOST = os.environ.get("SFTP_HOST", "home-economics.us")
REMOTE_DIR = "public_html/pulse-screenshots"

# Freedom Forum paper slugs. Each paper participates in the Newseum
# project under a known {STATE}_{ABBR} (or sometimes just ABBR) code.
# WaPo does NOT participate in Freedom Forum (paywall holdout), so we
# substitute the Washington Times as the second DC-area paper. If/when
# WaPo returns, swap "DC_WT" -> the new slug here.
PAPERS = [
    {"name": "nyt", "slug": "NY_NYT", "masthead": "THE NEW YORK TIMES",
     "side": "left", "url": "https://www.nytimes.com"},
    {"name": "wsj", "slug": "WSJ",    "masthead": "THE WALL STREET JOURNAL",
     "side": "left",  "url": "https://www.wsj.com"},
    {"name": "lat", "slug": "CA_LAT", "masthead": "LOS ANGELES TIMES",
     "side": "left", "url": "https://www.latimes.com"},
    {"name": "hc",  "slug": "TX_HC",  "masthead": "HOUSTON CHRONICLE",
     "side": "left",  "url": "https://www.houstonchronicle.com"},
]

# Brand palette
COLOR_BG          = (246, 247, 243)  # F6F7F3
COLOR_BLACK       = ( 61,  55,  51)  # 3D3733
COLOR_BLUE        = ( 11, 180, 255)  # 0BB4FF
COLOR_ORANGE      = (244, 116,  59)  # F4743B
COLOR_GREY        = (180, 175, 168)  # subdued line colour

CANVAS_W, CANVAS_H = 2400, 1640    # 2x for retina-crisp email rendering
PAGE_RENDER_DPI    = 320           # high enough to keep PDF text sharp after
                                   # the resize-to-canvas step (the page ends
                                   # up ~1000px wide in the final composite).

# Font fallbacks. Oracle is brand standard but optional; if absent we
# fall back to system DejaVu/Helvetica.
ORACLE_DIR = Path("/Users/azizsunderji/Dropbox/Home Economics/Brand Assets/"
                  "OracleFont/Oracle Aziz Sunderji/Desktop")
ORACLE_REGULAR = ORACLE_DIR / "ABCOracle-Regular.otf"
ORACLE_BOLD    = ORACLE_DIR / "ABCOracle-Bold.otf"
ORACLE_MEDIUM  = ORACLE_DIR / "ABCOracle-Medium.otf"

DEJAVU_REGULAR = "/Library/Fonts/Arial.ttf"
DEJAVU_BOLD    = "/Library/Fonts/Arial Bold.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load Oracle if available, otherwise fall back to a system font."""
    candidates = []
    if bold:
        candidates += [ORACLE_BOLD, ORACLE_MEDIUM]
    else:
        candidates += [ORACLE_REGULAR]
    candidates += [
        Path(DEJAVU_BOLD if bold else DEJAVU_REGULAR),
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for c in candidates:
        try:
            if Path(c).exists():
                return ImageFont.truetype(str(c), size)
        except Exception:
            continue
    return ImageFont.load_default()


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

    Returns list of dicts {text, bbox=(x0,y0,x1,y1), page_w, page_h}.
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
                # Skip obvious chrome (page numbers, prices, dates etc.)
                if t.lower() in {"the", "a", "an", "of", "to", "in"}:
                    continue
                spans.append({
                    "text": t,
                    "size": s["size"],
                    "bbox": s["bbox"],
                })

    if not spans:
        return []

    # Threshold: keep anything at or above some fraction of the BIGGEST
    # JOURNALISM-SIZED font on the page. We compute a "robust max" rather
    # than the raw max because the front page often has gigantic ad copy
    # ("AN EVENING WITH THE STARS" / Houston Ballet at 76pt) that would
    # otherwise drag the headline-detection threshold above the actual
    # banner headline. The rule: if the largest distinct size is >40%
    # larger than the second-largest, treat the top as an outlier and
    # anchor on the second-largest.
    distinct_sizes = sorted({round(s["size"], 1) for s in spans}, reverse=True)
    if len(distinct_sizes) >= 2 and distinct_sizes[0] > distinct_sizes[1] * 1.4:
        robust_max = distinct_sizes[1]
    else:
        robust_max = distinct_sizes[0]
    # 0.50 ratio is more permissive than the old 0.55 — papers with a steep
    # font-size drop-off (Houston Chronicle, Washington Times) have column
    # headlines at ~half the banner size. The downstream filters (≥4 words,
    # ≥24 chars, not all-caps-short) cull the noise this admits.
    threshold = robust_max * 0.50
    big = [s for s in spans if s["size"] >= threshold]

    # Group consecutive spans that belong to the same headline. Two
    # spans are merged only when they're VERY clearly in the same column
    # — same x-anchor within ~12pt, same font size, and stacked vertically
    # with no big gap. Newspapers print multiple headlines on the same
    # baseline in different columns; a generous overlap rule welds those
    # together into a single garbled "headline" (e.g. the NYT extraction
    # was producing "Payout Fund Deadly Russian Attack on Kyiv Comes
    # With…" — three separate stories from three columns merged).
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
            # Horizontal: either left edges align (left-aligned column) OR
            # there's substantial bbox overlap (center-aligned headlines —
            # NYT specifically lays them out with slight x-shifts between
            # lines, but the bboxes overlap heavily). The overlap rule is
            # tight enough that ADJACENT columns (no overlap) won't merge.
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

    # Build headline dicts from groups (bbox = union of all spans).
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

    # Sort by font size (largest first), then take top N.
    heads.sort(key=lambda h: (-h["size"], h["bbox"][1]))

    # De-dup near-identical text and obvious non-headlines (mastheads,
    # page numbers, price/date strings, ALL-CAPS standing labels /
    # "kickers" like SECTION / MEETING AT THE TOP).
    bad_prefixes = (
        "vol.", "vol ", "no.", "no ", "$", "©", "edition", "designated",
        "high ", "low ", "weather", "index",
    )
    out, seen = [], set()
    for h in heads:
        t = h["text"].strip()
        if any(t.lower().startswith(p) for p in bad_prefixes):
            continue
        # Skip if mostly digits/punctuation (e.g. "47 60 12")
        alpha = sum(c.isalpha() for c in t)
        if alpha < len(t) * 0.5:
            continue
        # Real headlines on a front page are at least 4 words AND >= 24
        # characters. Below that they're almost always partial fragments
        # (column wrap artifacts) or standing kickers.
        if len(t) < 24 or len(t.split()) < 4:
            continue
        # ALL-CAPS short strings are section labels / kickers, not headlines.
        if t.isupper() and len(t) < 40:
            continue
        key = t.lower()[:40]
        if key in seen:
            continue
        # Also skip if a previously-kept headline contains this one (subset).
        if any(key in s for s in seen):
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= max_headlines:
            break

    # Pass 2: for each surviving headline, find its article's lede.
    # We open the doc once via fitz blocks (already in `page` above) — but
    # _extract_headlines doesn't keep the page handle, so we re-open below.
    for h in out:
        h["lede"] = _find_lede_blocks(pdf_path, h["bbox"], h["size"])
    return out


def _find_lede_blocks(pdf_path: Path, headline_bbox: tuple, headline_size: float,
                      max_chars: int = 240) -> str:
    """Return the article's first ~2 sentences cleanly via block-level layout.

    Strategy:
      • Use PyMuPDF blocks (which respect column layout) instead of stitching
        raw spans (which mash adjacent columns together in PDF source order).
      • A valid lede block sits directly below the headline AND inside the
        headline's horizontal column (with tight tolerance — no slop into
        neighbouring columns).
      • If the immediate block is a byline/dateline, skip and take the next.
      • Trim to ~2 sentences (or max_chars at last sentence boundary).
    """
    import re as _re
    doc = fitz.open(pdf_path)
    page = doc[0]
    hx0, hy0, hx1, hy1 = headline_bbox
    head_w = hx1 - hx0

    # Pull blocks. Each = (x0, y0, x1, y1, text, block_no, block_type).
    blocks = page.get_text("blocks")

    # Determine the "first column" under the headline. If the headline spans
    # >= ~3 typical column widths (e.g. a banner head), the lede is in a
    # narrow sub-column STARTING at the headline's left edge. Otherwise
    # the lede block roughly matches the headline width.
    typical_col_w = min(head_w, 220)

    candidates = []
    for b in blocks:
        bx0, by0, bx1, by1, btext, *_ = b
        if not btext or not btext.strip():
            continue
        # Must be BELOW the headline, within ~280pt vertical search.
        if by0 < hy1 - 2 or by0 > hy1 + 280:
            continue
        # Must START inside the headline's horizontal column (with small
        # left-edge tolerance) — this is what cuts out adjacent-column body.
        if bx0 < hx0 - 12:
            continue
        if bx0 > hx0 + head_w * 0.6:
            continue
        # Must be narrower than the headline (otherwise it's likely another
        # banner-spanning block, e.g. a sub-deck).
        if bx1 > hx0 + typical_col_w + 30:
            continue
        candidates.append((by0, bx0, btext))

    if not candidates:
        return ""

    candidates.sort()

    # Try each candidate block in vertical order; skip bylines/datelines
    # and accept the first one that reads as prose.
    BYLINE_RE = _re.compile(r"^\s*By\s+[A-Z][\w\.\-']+(\s+(and|,)\s+[A-Z][\w\.\-']+)*\s*$",
                            _re.MULTILINE)
    SECTION_KICKER_RE = _re.compile(r"^[A-Z][A-Z &]{2,}$")

    accepted = []
    for _, _, raw in candidates:
        text = " ".join(raw.split())            # collapse whitespace
        text = BYLINE_RE.sub("", text).strip()  # drop trailing standalone bylines
        # Strip leading byline / dateline patterns.
        text = _re.sub(r"^By\s+[A-Z][\w\.\-']+(\s+(and|,)\s+[A-Z][\w\.\-']+)*\s*", "", text)
        text = _re.sub(r"^[A-Z][A-Z\s\.\,]{4,40}\s*[\-—–]\s*", "", text)  # "WASHINGTON — "
        text = _re.sub(r"^[A-Z][A-Z\s]{4,30}\s+", "", text)               # bare ALLCAPS kicker

        # Reject if too short or all-caps section header.
        if len(text) < 30 or SECTION_KICKER_RE.match(text):
            continue
        # Reject if it has the "by X" structure dominating.
        if len(BYLINE_RE.findall(text)) > 0 and len(text) < 80:
            continue
        accepted.append(text)
        # Stop once we have ~2 sentences (or comfortably enough).
        if len(" ".join(accepted)) >= max_chars * 0.7:
            break

    if not accepted:
        return ""

    full = " ".join(accepted)
    full = " ".join(full.split())

    # Sentence split — but protect common abbreviations so we don't break
    # on "U.S.", "Mr.", "Inc.", etc.
    ABBR = ["U.S.", "U.K.", "U.N.", "E.U.", "D.C.",
            "Mr.", "Mrs.", "Ms.", "Dr.", "Jr.", "Sr.", "St.",
            "Inc.", "Co.", "Corp.", "Ltd.", "Gov.", "Sen.", "Rep.",
            "vs.", "etc.", "i.e.", "e.g.", "No."]
    SENTINEL = "⦙"  # arbitrary placeholder
    protected = full
    for a in ABBR:
        protected = protected.replace(a, a.replace(".", SENTINEL))
    sentences = _re.split(r"(?<=[\.\?!])\s+", protected)
    sentences = [s.replace(SENTINEL, ".") for s in sentences]

    out = ""
    for s in sentences[:3]:
        if len(out) + len(s) + 1 > max_chars:
            break
        out = (out + " " + s).strip()
    if not out:
        truncated = full[:max_chars]
        last_period = max(truncated.rfind(". "), truncated.rfind("? "), truncated.rfind("! "))
        if last_period > max_chars // 2:
            out = truncated[:last_period + 1]
        else:
            out = truncated.rsplit(" ", 1)[0] + "…"
    return out


# ── Perspective transform ────────────────────────────────────────────

def _perspective_coeffs(src_corners, dst_corners):
    """Solve for PIL.Image.PERSPECTIVE coefficients.

    PIL expects the transform that maps each *output* pixel back to a
    source pixel, so we pass (output_corners, input_corners) here.
    """
    matrix = []
    for s, t in zip(src_corners, dst_corners):
        matrix.append([t[0], t[1], 1, 0, 0, 0, -s[0] * t[0], -s[0] * t[1]])
        matrix.append([0, 0, 0, t[0], t[1], 1, -s[1] * t[0], -s[1] * t[1]])
    A = np.array(matrix, dtype=float)
    B = np.array(src_corners, dtype=float).reshape(8)
    return tuple(np.linalg.solve(A, B))


def _project_point(x: float, y: float, fwd_coeffs) -> tuple[float, float]:
    """Apply a forward perspective transform to a single point."""
    a, b, c, d, e, f, g, h = fwd_coeffs
    denom = g * x + h * y + 1.0
    if denom == 0:
        return (0.0, 0.0)
    return ((a * x + b * y + c) / denom, (d * x + e * y + f) / denom)


def _tilt_page(page_img: Image.Image, tilt_inset_frac: float = 0.08
               ) -> tuple[Image.Image, tuple]:
    """Apply a 'page tilted backwards' perspective.

    Top edge stays nearly full-width; the bottom edge narrows and
    shortens slightly (vertically compressed) to suggest the page is
    laying flat-ish with its top tilted toward the camera.

    Returns (tilted_image_with_alpha, forward_coeffs) where forward_coeffs
    maps (x_orig, y_orig) -> (x_tilted, y_tilted) on the OUTPUT image.
    """
    w, h = page_img.size
    inset = int(w * tilt_inset_frac)
    # Output (tilted) corners — bottom is narrower than top.
    dst = [(0, 0), (w, 0), (w - inset, h), (inset, h)]
    src = [(0, 0), (w, 0), (w, h),         (0, h)]

    # PIL needs the INVERSE mapping (output -> input).
    inv_coeffs = _perspective_coeffs(src, dst)
    # And we need the FORWARD mapping to project headline bboxes.
    fwd_coeffs = _perspective_coeffs(dst, src)

    # Use an RGBA page so the trapezoid sits cleanly on the composite.
    if page_img.mode != "RGBA":
        page_img = page_img.convert("RGBA")
    tilted = page_img.transform(
        (w, h), Image.PERSPECTIVE, inv_coeffs,
        resample=Image.BICUBIC,
    )
    # Anything outside the trapezoid is transparent because the input
    # had alpha=255 only inside the page rectangle. PIL gives black/0
    # for "no source pixel" cases here, but with RGBA source the alpha
    # naturally falls to 0 outside the mapped region.
    return tilted, fwd_coeffs


# ── Composition ──────────────────────────────────────────────────────

def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap into lines that fit within max_width pixels."""
    words = text.split()
    lines, line = [], ""
    for w in words:
        candidate = (line + " " + w).strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _compose(paper: dict, pdf_path: Path, out_path: Path) -> bool:
    """Build the final composite PNG for one paper."""
    name      = paper["name"]
    masthead  = paper["masthead"]
    side      = paper["side"]   # which side of canvas the page sits on
    try:
        # 1. Render PDF page to a PIL image.
        doc = fitz.open(pdf_path)
        page = doc[0]
        zoom = PAGE_RENDER_DPI / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        page_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        pdf_w, pdf_h = page.rect.width, page.rect.height
        render_w, render_h = pix.width, pix.height

        # 2. Extract headlines (PDF coords).
        headlines = _extract_headlines(pdf_path, max_headlines=4)

        # 3. Resize page so it fits the canvas (no top masthead anymore;
        #    the paper name now lives as a small kicker over the headline
        #    column, so the page can use most of the canvas height).
        target_h = CANVAS_H - 80
        scale = target_h / render_h
        new_w = int(render_w * scale)
        new_h = int(render_h * scale)
        page_img = page_img.resize((new_w, new_h), Image.LANCZOS)

        # Drop a subtle shadow under the page by darkening edges? Skip
        # for V1 — it muddies the look on the cream background.

        # 4. No tilt — keep the page flat and crisp so the headlines
        #    embedded in the snapshot stay legible. Just convert to RGBA
        #    so the bottom-fade alpha gradient (below) has something
        #    to write to.
        tilted = page_img.convert("RGBA")
        t_w, t_h = tilted.size

        # 5. Build canvas. No centered masthead / date — the paper name
        #    now lives as a small ALL-CAPS kicker over the headline column.
        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), COLOR_BG)
        draw = ImageDraw.Draw(canvas)

        # 6. Crop the page to the top third-ish and fade to cream sooner
        #    than before. We don't need the full front page visible — just
        #    enough that the masthead + lead-headline area reads clearly.
        FADE_KEEP_FRAC = 0.30   # top 30% stays fully opaque
        FADE_RAMP_FRAC = 0.12   # next 12% ramps from 255 -> 0
        keep_h = int(t_h * FADE_KEEP_FRAC)
        ramp_h = int(t_h * FADE_RAMP_FRAC)
        new_t_h = keep_h + ramp_h
        tilted = tilted.crop((0, 0, t_w, new_t_h))
        # Build a single-column vertical alpha gradient and apply it
        # (no per-pixel loop now that the page is rectangular).
        gradient_col = Image.new("L", (1, new_t_h), 0)
        gc_px = gradient_col.load()
        for yy in range(new_t_h):
            if yy < keep_h:
                gc_px[0, yy] = 255
            else:
                gc_px[0, yy] = max(0, int(255 * (1.0 - (yy - keep_h) / max(ramp_h, 1))))
        new_alpha = gradient_col.resize((t_w, new_t_h))
        r_ch, g_ch, b_ch, _a_old = tilted.split()
        tilted = Image.merge("RGBA", (r_ch, g_ch, b_ch, new_alpha))
        t_h = new_t_h

        # Place page on the chosen side, shifted down a little so the
        # headlines column starts at roughly the same Y as the masthead
        # of the paper inside the snapshot.
        page_y = 80
        margin = 60
        if side == "left":
            page_x = margin
            text_x0 = page_x + t_w + 80
            text_x1 = CANVAS_W - margin
        else:
            page_x = CANVAS_W - t_w - margin
            text_x0 = margin
            text_x1 = page_x - 80

        canvas.paste(tilted, (page_x, page_y), tilted)

        # 7. Headlines column: small ALL-CAPS kicker with the paper name,
        #    then the stack of headlines. No leader lines, no dots, no
        #    lede text. Medium weight (less bold than V2).
        if not headlines:
            print(f"  {name}: no headlines extracted — composing without callouts")
        f_kicker   = _font(28, bold=True)
        f_headline = _font(46, bold=False)
        head_line_h = 56
        block_gap = 42
        kicker_gap = 36   # gap below the kicker before headlines start

        kicker_y = page_y + 16
        draw.text((text_x0, kicker_y), masthead,
                  fill=COLOR_BLACK, font=f_kicker)
        # Thin rule under the kicker.
        rule_y = kicker_y + 44
        draw.line([(text_x0, rule_y), (text_x1, rule_y)],
                  fill=COLOR_GREY, width=2)

        cur_y = rule_y + kicker_gap
        for h in headlines:
            text = h["text"]
            if text.isupper():
                text = text.title()
            head_lines = _wrap_text(draw, text, f_headline, text_x1 - text_x0)
            for j, line in enumerate(head_lines):
                draw.text((text_x0, cur_y + j * head_line_h),
                          line, fill=COLOR_BLACK, font=f_headline)
            cur_y += len(head_lines) * head_line_h + block_gap

        # 8. Trim canvas to the actual content bottom so each paper's
        #    composite is as tight as possible — when the four are stacked
        #    in the email there's no wasted cream gap below the shorter
        #    papers. We use the max of (headlines column bottom, fading
        #    page bottom). Subtract block_gap because the last loop
        #    iteration added one trailing.
        content_bottom = max(cur_y - block_gap, page_y + t_h)
        trim_h = min(CANVAS_H, content_bottom + 40)
        if trim_h < CANVAS_H:
            canvas = canvas.crop((0, 0, CANVAS_W, trim_h))

        # Save (PNG for the cached/local copy, JPG for the email upload).
        canvas.save(out_path, "PNG", optimize=True)
        # Copy into screenshots dir so the existing email pipeline finds it.
        local_copy = SCREENSHOTS_DIR / f"{name}.png"
        canvas.save(local_copy, "PNG", optimize=True)
        # And a JPG for upload.
        jpg_path = out_path.with_suffix(".jpg")
        canvas.save(jpg_path, "JPEG", quality=85, optimize=True)
        print(f"  {name}: composite saved ({canvas.size[0]}x{canvas.size[1]}, "
              f"{len(headlines)} headlines)")
        return True
    except Exception as e:
        import traceback
        print(f"  {name}: compose failed: {e}")
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
    print("Capturing stylized front pages...")
    uploads: list[str] = []
    for paper in PAPERS:
        name = paper["name"]
        slug = paper["slug"]
        print(f"\n[{name}] slug={slug}")
        pdf_path = OUT_DIR / f"{slug}.pdf"
        if not _download_pdf(slug, pdf_path):
            continue
        png_path = OUT_DIR / f"{name}.png"
        jpg_path = OUT_DIR / f"{name}.jpg"
        if _compose(paper, pdf_path, png_path):
            uploads.append(str(jpg_path))

    if not uploads:
        print("No composites produced — exiting non-zero")
        sys.exit(1)

    if _upload_to_bluehost(uploads):
        print("\nDone. Composites live at:")
        for f in uploads:
            print(f"  https://home-economics.us/pulse-screenshots/{os.path.basename(f)}")
    else:
        # Don't fail the workflow just because upload failed locally —
        # the screenshots dir still has the PNGs.
        print("Upload step skipped/failed; local PNGs are in", SCREENSHOTS_DIR)


if __name__ == "__main__":
    main()
