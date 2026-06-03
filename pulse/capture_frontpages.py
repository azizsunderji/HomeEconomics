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
     "side": "right", "url": "https://www.nytimes.com"},
    {"name": "wsj", "slug": "WSJ",    "masthead": "THE WALL STREET JOURNAL",
     "side": "left",  "url": "https://www.wsj.com"},
    {"name": "lat", "slug": "CA_LAT", "masthead": "LOS ANGELES TIMES",
     "side": "right", "url": "https://www.latimes.com"},
    {"name": "hc",  "slug": "TX_HC",  "masthead": "HOUSTON CHRONICLE",
     "side": "left",  "url": "https://www.houstonchronicle.com"},
]

# Brand palette
COLOR_BG          = (246, 247, 243)  # F6F7F3
COLOR_BLACK       = ( 61,  55,  51)  # 3D3733
COLOR_BLUE        = ( 11, 180, 255)  # 0BB4FF
COLOR_ORANGE      = (244, 116,  59)  # F4743B
COLOR_GREY        = (180, 175, 168)  # subdued line colour

CANVAS_W, CANVAS_H = 1200, 820
PAGE_RENDER_DPI    = 220   # PyMuPDF render DPI. 300 is enormous (10MB+) and
                           # gets resized away anyway; 220 is plenty for the
                           # ~500px tall tilted page that ends up in the
                           # composite. Keeps memory low in GHA.

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

    # Threshold: keep anything at or above 60% of the max font size — this
    # captures multi-line headlines whose lines all share the same big size,
    # plus deck headlines / secondary stories. We aggressively de-dup later.
    # Some papers (WSJ, Washington Times) have a single huge headline and
    # then a steep drop-off; if we set the threshold too high we miss every
    # other story on the page.
    max_size = max(s["size"] for s in spans)
    threshold = max_size * 0.55
    big = [s for s in spans if s["size"] >= threshold]

    # Group adjacent spans that belong to the same headline. Two spans
    # are in the same group if their font size matches (±10%), they
    # overlap horizontally (or one's column contains the other's x0),
    # and they're vertically near (gap < 1.5 * line height).
    big.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
    groups: list[list[dict]] = []
    for s in big:
        placed = False
        for g in groups:
            last = g[-1]
            lx0, ly0, lx1, ly1 = last["bbox"]
            sx0, sy0, sx1, sy1 = s["bbox"]
            same_size = abs(last["size"] - s["size"]) / max(last["size"], 1) < 0.12
            vgap = sy0 - ly1
            line_h = (ly1 - ly0) or s["size"]
            # Horizontal overlap test — these are headlines in a column,
            # so successive lines start at roughly the same x.
            hoverlap = (min(lx1, sx1) - max(lx0, sx0)) > -line_h
            x_aligned = abs(lx0 - sx0) < line_h * 1.5
            if same_size and vgap < line_h * 1.5 and (hoverlap or x_aligned):
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
    # page numbers, price/date strings).
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

        # 3. Resize page so it fits in the left/right ~55% of the canvas.
        target_h = CANVAS_H - 140   # leave room for masthead + date
        scale = target_h / render_h
        new_w = int(render_w * scale)
        new_h = int(render_h * scale)
        page_img = page_img.resize((new_w, new_h), Image.LANCZOS)

        # Drop a subtle shadow under the page by darkening edges? Skip
        # for V1 — it muddies the look on the cream background.

        # 4. Apply tilt. Alternate the direction slightly so a stack of
        # the 4 papers has visual variety: papers on the right side of
        # the canvas tilt backwards (bottom narrows), papers on the left
        # tilt the same way but the trapezoid sits on the other side of
        # the canvas. The inset is ~12% for a noticeable but realistic
        # "laying back" look — much more and the page looks origami'd.
        tilted, fwd_coeffs = _tilt_page(page_img, tilt_inset_frac=0.12)
        t_w, t_h = tilted.size

        # 5. Build canvas.
        canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), COLOR_BG)
        draw = ImageDraw.Draw(canvas)

        # Masthead + date at the top.
        f_mast = _font(34, bold=True)
        f_date = _font(16, bold=False)
        date_str = datetime.now().strftime("%A, %B %-d, %Y").upper()
        mast_w = draw.textlength(masthead, font=f_mast)
        draw.text(((CANVAS_W - mast_w) / 2, 22), masthead,
                  fill=COLOR_BLACK, font=f_mast)
        date_w = draw.textlength(date_str, font=f_date)
        draw.text(((CANVAS_W - date_w) / 2, 68), date_str,
                  fill=(120, 115, 110), font=f_date)
        # Thin rule under masthead.
        draw.line([(80, 102), (CANVAS_W - 80, 102)],
                  fill=COLOR_GREY, width=1)

        # Place tilted page.
        page_y = 130
        if side == "left":
            page_x = 40
            text_x0 = page_x + t_w + 60
            text_x1 = CANVAS_W - 40
        else:
            page_x = CANVAS_W - t_w - 40
            text_x0 = 40
            text_x1 = page_x - 60

        canvas.paste(tilted, (page_x, page_y), tilted)

        # 6. Lay out headline callouts on the opposite side.
        if not headlines:
            print(f"  {name}: no headlines extracted — composing without callouts")
        f_callout = _font(18, bold=True)
        f_callout_small = _font(15, bold=True)
        line_height = 26
        para_gap = 14
        # Vertically distribute callouts evenly between page_y and page_y + t_h.
        n = len(headlines)
        text_avail_h = t_h - 20
        slot_h = text_avail_h / max(n, 1)

        dot_colors = [COLOR_BLUE, COLOR_ORANGE]
        for i, h in enumerate(headlines):
            # Anchor point on tilted page (centroid of bbox projected
            # through the same perspective the page was tilted with).
            px0, py0, px1, py1 = h["bbox"]
            # Convert PDF -> render-image coords -> resized-page coords.
            cx = (px0 + px1) / 2 * (render_w / pdf_w) * scale
            cy = (py0 + py1) / 2 * (render_h / pdf_h) * scale
            # Project through tilt.
            tx, ty = _project_point(cx, cy, fwd_coeffs)
            anchor_x = page_x + int(tx)
            anchor_y = page_y + int(ty)

            # Callout text position.
            slot_y = page_y + 10 + int(i * slot_h + slot_h / 2)
            # Wrap headline text into the callout column.
            text = h["text"]
            # Title-case if all caps (some papers shout) — looks softer.
            if text.isupper():
                text = text.title()
            font_use = f_callout if n <= 3 else f_callout_small
            lines = _wrap_text(draw, text, font_use, text_x1 - text_x0 - 30)
            block_h = len(lines) * line_height
            text_y = slot_y - block_h // 2

            for j, line in enumerate(lines):
                tx_draw = text_x0 if side == "right" else text_x1 - draw.textlength(line, font=font_use)
                draw.text((tx_draw, text_y + j * line_height),
                          line, fill=COLOR_BLACK, font=font_use)

            # Leader line: from callout edge to anchor on page.
            if side == "right":
                callout_endpoint = (text_x0 + (text_x1 - text_x0) // 2 + int((text_x1 - text_x0) * 0.35), slot_y)
                # Actually simpler: end at left edge of the right-side page
                callout_endpoint = (text_x1 - 8, slot_y)
                # ...no, we want the line to start from NEAR the text and
                # head TOWARD the page. Use the rightmost point of the
                # text block as the start.
                rightmost = text_x0
                for line in lines:
                    rightmost = max(rightmost, text_x0 + draw.textlength(line, font=font_use))
                callout_endpoint = (int(rightmost) + 10, slot_y)
            else:
                # Text right-aligned; line starts at leftmost text edge.
                leftmost = text_x1
                for line in lines:
                    leftmost = min(leftmost, text_x1 - draw.textlength(line, font=font_use))
                callout_endpoint = (int(leftmost) - 10, slot_y)

            # Draw leader line (thin black) + small filled dot at page end.
            draw.line(
                [callout_endpoint, (anchor_x, anchor_y)],
                fill=COLOR_BLACK, width=2,
            )
            dot_color = dot_colors[i % 2]
            r = 5
            draw.ellipse(
                [anchor_x - r, anchor_y - r, anchor_x + r, anchor_y + r],
                fill=dot_color, outline=COLOR_BLACK, width=1,
            )

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
