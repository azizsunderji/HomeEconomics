"""The owner's own publications, for callbacks in Housing at Noon (owner, 14 Oct 2026).

Owner's rule: "You have all my publications and you should be actively monitoring them. When
there's a piece of news that touches on something we've written before, we should try to make a
callback to that piece, along with the chart that I included in that piece. This should be
integrated into the pipeline and happen automatically."

This script builds and refreshes that corpus on the server:

  sources   1. The Substack export (posts/*.html + posts.csv) copied from the owner's Mac into
               OwnPublications/raw/substack_export_dec2025/ (chart images in chart_images/).
            2. Substack API post JSON (body_html, logged in so paid posts are whole):
               OwnPublications/raw/api_posts/<slug>.json, plus the read-only archive the podcast
               project fetched (2026_09_24_ProfGPodcast/data/pushback/api/).
            3. Discovery of new posts: the corpus items from the owner's own feed in pulse.db
               (read-only) and the Substack archive listing. Missing posts are fetched through the
               server's live Chrome (CDP 9223, cdp_tab.py), one same-origin fetch() every few seconds.
  charts    Every chart-like image (not banners, not GIFs) is converted to PNG (max 1200 px wide)
            and hosted at ~/work/noon/images/own/<slug>/<n>.png, public at
            https://noon.homeeconomics.us/images/own/<slug>/<n>.png (the editor's /images mount).
            Haiku looks at each image once: is it a chart, and a one-line caption.
  summary   Haiku writes a 2-3 sentence summary of each post once (cached).
  outputs   OwnPublications/own_publications.parquet (data lake, full text) and
            pulse/data/own_publications.json (compact, committed; read by v4b_runner.py in
            GitHub Actions for callback retrieval).

    python own_publications.py                 # weekly refresh: discover, fetch, build, commit
    python own_publications.py --no-discover --no-commit   # rebuild from what is on disk
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import html as htmllib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("own_publications")

DATA_DIR = Path(os.environ.get("OWN_PUBS_DIR", "/home/aziz/OVH/Data/OwnPublications"))
RAW = DATA_DIR / "raw"
EXPORT = RAW / "substack_export_dec2025"
API_DIR = RAW / "api_posts"
EXTRA_API_DIRS = [Path("/home/aziz/OVH/2026_09_24_ProfGPodcast/data/pushback/api")]
CACHE_PATH = DATA_DIR / "cache.json"
PARQUET = DATA_DIR / "own_publications.parquet"
IMAGES_ROOT = Path(os.environ.get("NOON_IMAGES_DIR", str(Path.home() / "work" / "noon" / "images")))
OWN_IMAGES = IMAGES_ROOT / "own"
PUBLIC_BASE = os.environ.get("NOON_BASE_URL", "https://noon.homeeconomics.us").rstrip("/") + "/images/own"
REPO = Path(__file__).resolve().parents[2]
REPO_JSON = REPO / "pulse" / "data" / "own_publications.json"
PULSE_DB = os.environ.get("PULSE_DB", "/home/aziz/OVH/Data/Pulse/pulse.db")
SITE = "https://homeeconomics.substack.com"
HAIKU = "claude-haiku-4-5-20251001"
MAX_CHART_WIDTH = 1200
JSON_TEXT_CHARS = 1500
MAX_FETCH = 40          # live-Chrome fetches per run (shared proxy IP; never burst)
FETCH_GAP = 4.0         # seconds between fetches


def canonical_url(slug: str) -> str:
    return f"{SITE}/p/{slug}"


_SLUG_RE = re.compile(r"(?:homeeconomics\.substack\.com|home-economics\.us|homeeconomics\.us)/p/([a-z0-9-]+)")


def slug_from_url(url: str) -> str | None:
    m = _SLUG_RE.search(url or "")
    return m.group(1) if m else None


# ── HTML parsing ───────────────────────────────────────────────────────
_FIG_RE = re.compile(r"<figure\b.*?</figure>", re.S)
_ATTRS_RE = re.compile(r'data-attrs="([^"]*)"')
_IMG_SRC_RE = re.compile(r'<img[^>]*\bsrc="([^"]+)"')
_CAPTION_RE = re.compile(r"<figcaption[^>]*>(.*?)</figcaption>", re.S)
_SIZE_RE = re.compile(r"_(\d+)x(\d+)\.(png|jpe?g|gif|webp)", re.I)
_BOILER = re.compile(r"^(Hi! I.m Aziz\b.*|Subscribe now|Share|Leave a comment|Give a gift subscription|"
                     r"Thanks for reading.*|Upgrade to paid|.*hit reply and let me know what you think.*)$", re.I)


def _strip_tags(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_body(body_html: str) -> tuple[str, list[dict]]:
    """Plain text (paragraphs separated by blank lines) and the figures in order:
    [{src, alt, caption, width, height, context}] where context is the paragraph before."""
    figures: list[dict] = []
    paras: list[str] = []
    pos = 0
    body = body_html or ""
    # images: inside <figure> (older editor) or a bare <img> (newer posts carry no figure)
    blocks = re.compile(r"<figure\b.*?</figure>|<img\b[^>]*>|<(p|h[1-6]|li|blockquote)\b[^>]*>(.*?)</\1>", re.S)
    for m in blocks.finditer(body):
        chunk = m.group(0)
        if chunk.startswith("<figure") or chunk.startswith("<img"):
            attrs = {}
            am = _ATTRS_RE.search(chunk)
            if am:
                try:
                    attrs = json.loads(htmllib.unescape(am.group(1)))
                except ValueError:
                    attrs = {}
            src = attrs.get("src") or (_IMG_SRC_RE.search(chunk).group(1) if _IMG_SRC_RE.search(chunk) else "")
            if not src:
                continue
            cap = _CAPTION_RE.search(chunk)
            w, h = attrs.get("width"), attrs.get("height")
            sm = _SIZE_RE.search(src)
            if (not w or not h) and sm:
                w, h = int(sm.group(1)), int(sm.group(2))
            figures.append({"src": src, "alt": (attrs.get("alt") or "").strip(),
                            "caption": _strip_tags(cap.group(1)) if cap else "",
                            "width": int(w or 0), "height": int(h or 0),
                            "context": paras[-1][:400] if paras else ""})
            continue
        t = re.sub(r"\s+", " ", _strip_tags(m.group(2)))
        if t and not _BOILER.match(t):
            paras.append(t)
    return "\n\n".join(paras), figures


def _image_name(src: str) -> str:
    """substack-post-media file name (uuid_WxH.ext) from either URL form."""
    u = urllib.request.unquote(src)
    m = re.search(r"/public/images/([^/?#]+)$", u) or re.search(r"/images/([^/?#]+)$", u)
    return m.group(1) if m else hashlib.sha1(src.encode()).hexdigest()[:16]


def _is_candidate_chart(f: dict) -> bool:
    name = _image_name(f["src"]).lower()
    if name.endswith(".gif") or "video" in name:
        return False
    w, h = f.get("width") or 0, f.get("height") or 0
    if w and h:
        return w >= 500 and h / w >= 0.3
    return True


# ── sources ────────────────────────────────────────────────────────────
def _audience(a: str | None) -> str:
    return {"everyone": "free", "only_paid": "paid", "founding": "paid", "only_free": "free"}.get(a or "", a or "")


def load_export() -> dict[str, dict]:
    out: dict[str, dict] = {}
    rows = list(csv.DictReader(open(EXPORT / "posts.csv", newline="", encoding="utf-8")))
    for r in rows:
        if r.get("is_published") != "true" or r.get("type") not in ("newsletter", "podcast"):
            continue
        pid_slug = r["post_id"]
        slug = pid_slug.split(".", 1)[1] if "." in pid_slug else pid_slug
        f = EXPORT / "posts" / f"{pid_slug}.html"
        if not f.exists():
            continue
        out[slug] = {"slug": slug, "title": (r.get("title") or "").strip(),
                     "subtitle": (r.get("subtitle") or "").strip(),
                     "date": (r.get("post_date") or "")[:10], "audience": _audience(r.get("audience")),
                     "body_html": f.read_text(encoding="utf-8", errors="ignore"), "source": "export"}
    return out


def _from_api_json(d: dict, source: str) -> dict | None:
    if not d.get("slug") or not d.get("body_html") or d.get("is_published") is False:
        return None
    if d.get("type") not in (None, "newsletter", "podcast"):
        return None
    return {"slug": d["slug"], "title": (d.get("title") or "").strip(),
            "subtitle": (d.get("subtitle") or "").strip(),
            "date": (d.get("post_date") or "")[:10], "audience": _audience(d.get("audience")),
            "body_html": d["body_html"], "source": source}


def load_api_dirs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d, label in [(p, "pushback_api") for p in EXTRA_API_DIRS] + [(API_DIR, "api")]:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if f.name.endswith(".comments.json"):
                continue
            try:
                rec = _from_api_json(json.loads(f.read_text()), label)
            except (ValueError, OSError):
                continue
            if rec:
                out[rec["slug"]] = rec      # api_posts (fetched last) wins
    return out


def feed_slugs() -> dict[str, str]:
    """slug -> published date for the owner's posts in the corpus (read-only)."""
    out: dict[str, str] = {}
    if not Path(PULSE_DB).exists():
        return out
    c = sqlite3.connect(f"file:{PULSE_DB}?mode=ro", uri=True)
    for url, pub in c.execute("SELECT url, published_at FROM items WHERE url LIKE '%homeeconomics.substack.com/p/%' "
                              "OR url LIKE '%home-economics.us/p/%' OR feed_name = 'Home Economics — Aziz Sunderji'"):
        s = slug_from_url(url)
        if s:
            out.setdefault(s, (pub or "")[:10])
    return out


# ── live Chrome (CDP 9223) ─────────────────────────────────────────────
async def _cdp_fetch(paths: list[str]) -> dict[str, str]:
    """Same-origin fetch() of each path on the Substack site inside one tab of the live Chrome
    (the owner is logged in, so paid bodies come back whole). One page load, then one request
    every FETCH_GAP seconds."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cdp_tab import CdpTab
    out: dict[str, str] = {}
    async with CdpTab(max_loads=2) as tab:
        await tab.goto(f"{SITE}/archive", settle=6)
        for p in paths:
            js = f"fetch({json.dumps(p)}, {{credentials: 'include'}}).then(r => r.ok ? r.text() : 'HTTP ' + r.status)"
            try:
                out[p] = await tab.eval(js) or ""
            except Exception as e:  # noqa: BLE001
                out[p] = f"ERR {e}"
            await asyncio.sleep(FETCH_GAP)
    return out


def discover_and_fetch(have: set[str], since: str, max_fetch: int = MAX_FETCH) -> list[str]:
    """List the archive (newest first, down to `since`), add the corpus feed's slugs, and fetch
    every post body not on disk. Returns the slugs fetched."""
    listing: list[dict] = []
    offset = 0
    while True:
        res = asyncio.run(_cdp_fetch([f"/api/v1/archive?sort=new&search=&offset={offset}&limit=50"]))
        raw = next(iter(res.values()))
        try:
            page = json.loads(raw)
        except ValueError:
            logger.warning(f"archive listing failed at offset {offset}: {raw[:120]!r}")
            break
        if not page:
            break
        listing.extend(page)
        if min((p.get("post_date") or "")[:10] for p in page) < since or len(page) < 50:
            break
        offset += 50
    want = {p["slug"]: (p.get("post_date") or "")[:10] for p in listing
            if p.get("slug") and (p.get("post_date") or "")[:10] >= since and p.get("type") in ("newsletter", "podcast")}
    for s, d in feed_slugs().items():
        if d >= since:
            want.setdefault(s, d)
    missing = sorted((s for s in want if s not in have), key=lambda s: want[s], reverse=True)[:max_fetch]
    logger.info(f"archive listed {len(listing)} posts; {len(want)} since {since}; fetching {len(missing)} missing")
    if not missing:
        return []
    API_DIR.mkdir(parents=True, exist_ok=True)
    res = asyncio.run(_cdp_fetch([f"/api/v1/posts/{s}" for s in missing]))
    got = []
    for s in missing:
        raw = res.get(f"/api/v1/posts/{s}", "")
        try:
            d = json.loads(raw)
        except ValueError:
            logger.warning(f"fetch {s} failed: {raw[:120]!r}")
            continue
        (API_DIR / f"{s}.json").write_text(json.dumps(d))
        got.append(s)
    return got


# ── Haiku: chart captions and summaries ────────────────────────────────
def _anthropic():
    import anthropic
    return anthropic.Anthropic()


_USAGE = {"in": 0, "out": 0}


def _haiku(client, content, max_tokens=300) -> str:
    resp = client.messages.create(model=HAIKU, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": content}])
    # Not recorded in pulse.db's spend table: on this server PULSE_DB is the synced corpus,
    # which must never be written from here. Token totals are logged instead.
    _USAGE["in"] += resp.usage.input_tokens
    _USAGE["out"] += resp.usage.output_tokens
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


CHART_PROMPT = ("This image is from a post in Home Economics, a newsletter on US housing economics. "
                "Reply with JSON only: {\"is_chart\": true|false, \"caption\": \"...\"}. is_chart is true for a "
                "data chart, map or data table; false for a photo, logo, cartoon, screenshot of text, or banner. "
                "caption: one line of at most 16 words saying what the chart shows, using its own title when it "
                "has one (no 'This chart shows').")

SUMMARY_PROMPT = ("Summarise this Home Economics post by Aziz Sunderji in 2 to 3 plain sentences (at most 70 words): "
                  "the question it asks and what the data shows, with the key numbers. No preamble.\n\n"
                  "TITLE: {title}\n\nTEXT:\n{text}")


def _parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def caption_chart(client, png: bytes) -> dict:
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    if im.width > 1000:
        im = im.resize((1000, int(im.height * 1000 / im.width)))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(buf.getvalue()).decode()}},
               {"type": "text", "text": CHART_PROMPT}]
    d = _parse_json(_haiku(client, content, 120))
    return {"is_chart": bool(d.get("is_chart")), "caption": str(d.get("caption") or "").strip()}


# ── chart hosting ──────────────────────────────────────────────────────
def _image_bytes(src: str) -> bytes | None:
    name = _image_name(src)
    local = EXPORT / "chart_images" / name
    if local.exists():
        return local.read_bytes()
    for url in (f"https://substack-post-media.s3.amazonaws.com/public/images/{name}", src):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (HousingAtNoon own-publications)"})
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception:  # noqa: BLE001
            continue
    return None


def _to_png(data: bytes) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.width > MAX_CHART_WIDTH:
        im = im.resize((MAX_CHART_WIDTH, int(im.height * MAX_CHART_WIDTH / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ── build ──────────────────────────────────────────────────────────────
def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {"charts": {}, "summaries": {}}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=0))


def build(limit: int | None = None) -> list[dict]:
    posts = load_export()
    posts.update(load_api_dirs())      # API copies (later, whole bodies) win over the export
    client = _anthropic()
    cache = _load_cache()
    out: list[dict] = []
    n_new_caps = n_new_sums = 0
    for i, (slug, p) in enumerate(sorted(posts.items(), key=lambda kv: kv[1]["date"], reverse=True)):
        if limit and i >= limit:
            break
        text, figs = parse_body(p["body_html"])
        if len(text) < 200:
            continue
        charts: list[dict] = []
        n = 0
        for f in figs:
            if not _is_candidate_chart(f):
                continue
            key = _image_name(f["src"])
            cap = cache["charts"].get(key)
            data = None
            if cap is None:
                data = _image_bytes(f["src"])
                if data is None:
                    logger.warning(f"{slug}: image not reachable {key}")
                    continue
            if cap is None:
                try:
                    cap = caption_chart(client, data)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"{slug}: caption failed {key}: {e}")
                    continue
                cache["charts"][key] = cap
                n_new_caps += 1
                if n_new_caps % 25 == 0:
                    _save_cache(cache)
            if not cap.get("is_chart"):
                continue
            n += 1
            dest = OWN_IMAGES / slug / f"{n}.png"
            hosted = cache.setdefault("hosted", {})
            if not dest.exists() or hosted.get(f"{slug}/{n}") != key:
                data = data or _image_bytes(f["src"])
                if data is None:
                    n -= 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(_to_png(data))
                hosted[f"{slug}/{n}"] = key
            src_clean = f"https://substack-post-media.s3.amazonaws.com/public/images/{key}"
            charts.append({"n": n, "image_url": src_clean, "caption": cap.get("caption") or "",
                           "alt": f.get("alt") or "", "figcaption": f.get("caption") or "",
                           "context": f.get("context") or "",
                           "hosted_url": f"{PUBLIC_BASE}/{slug}/{n}.png"})
        skey = f"{slug}:{hashlib.sha1(text[:6000].encode()).hexdigest()[:10]}"
        summary = cache["summaries"].get(skey)
        if summary is None:
            try:
                summary = _haiku(client, SUMMARY_PROMPT.format(title=p["title"], text=text[:6000]), 220)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{slug}: summary failed: {e}")
                summary = ""
            if summary:
                cache["summaries"][skey] = summary
                n_new_sums += 1
        out.append({"slug": slug, "url": canonical_url(slug), "title": p["title"], "subtitle": p.get("subtitle") or "",
                    "date": p["date"], "audience": p["audience"], "source": p["source"],
                    "text": text, "summary": summary, "charts": charts})
        if (i + 1) % 20 == 0:
            logger.info(f"  {i + 1} posts processed ({n_new_caps} new chart captions, {n_new_sums} new summaries)")
            _save_cache(cache)
    _save_cache(cache)
    logger.info(f"built {len(out)} posts; {sum(len(p['charts']) for p in out)} charts hosted; "
                f"{n_new_caps} new chart captions, {n_new_sums} new summaries; Haiku tokens in {_USAGE['in']:,} "
                f"out {_USAGE['out']:,} (about ${(_USAGE['in'] + 5 * _USAGE['out']) / 1e6:.2f})")
    return out


# Chart fields from own_chart_meta.py / own_chart_refresh.py (owner, 16 Oct 2026: old charts get updated when a
# callback shows them; static charts are shown as they are).
CHART_FIELDS = ("chart_kind", "last_period", "last_period_date", "update_frequency", "sources", "description",
                "builder_path", "current_url", "refreshed_at", "refresh_status", "replica_status")
CHART_STRUCT = ("chart_kind VARCHAR, last_period VARCHAR, last_period_date VARCHAR, update_frequency VARCHAR, "
                "sources VARCHAR[], description VARCHAR, builder_path VARCHAR, current_url VARCHAR, "
                "refreshed_at VARCHAR, refresh_status VARCHAR, replica_status VARCHAR")


def write_outputs(pubs: list[dict]) -> None:
    import duckdb
    pubs = sorted(pubs, key=lambda p: p["date"], reverse=True)
    tmp = DATA_DIR / "_own_publications.jsonl"
    with open(tmp, "w") as f:
        for p in pubs:
            f.write(json.dumps({k: p[k] for k in ("slug", "url", "title", "subtitle", "date", "audience", "source",
                                                   "text", "summary")}
                               | {"charts": [{k: c.get(k) for k in ("n", "image_url", "caption", "alt", "figcaption",
                                                                    "context", "hosted_url") + CHART_FIELDS}
                                           for c in p["charts"]]}) + "\n")
    con = duckdb.connect()
    con.execute(f"""COPY (SELECT slug, url, title, subtitle, CAST(date AS DATE) AS date, audience, source, text, summary, charts
                          FROM read_json('{tmp}', format='newline_delimited', columns={{
                            slug:'VARCHAR', url:'VARCHAR', title:'VARCHAR', subtitle:'VARCHAR', date:'VARCHAR',
                            audience:'VARCHAR', source:'VARCHAR', text:'VARCHAR', summary:'VARCHAR',
                            charts:'STRUCT(n INTEGER, image_url VARCHAR, caption VARCHAR, alt VARCHAR, figcaption VARCHAR, context VARCHAR, hosted_url VARCHAR, {CHART_STRUCT})[]'}})
                          ORDER BY date DESC)
                    TO '{PARQUET}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 100000)""")
    tmp.unlink()
    compact = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "note": "Owner's own publications for callbacks (pulse/editor/own_publications.py). "
                       "Charts are hosted on noon.homeeconomics.us.",
               "posts": [{"slug": p["slug"], "url": p["url"], "title": p["title"], "date": p["date"],
                          "audience": p["audience"], "summary": p["summary"], "text": p["text"][:JSON_TEXT_CHARS],
                          "charts": [{"hosted_url": c["hosted_url"], "caption": c["caption"],
                                      "context": c["context"][:240]}
                                     | {k: c.get(k) for k in CHART_FIELDS if k != "builder_path"}
                                     for c in p["charts"]]}
                         for p in pubs]}
    REPO_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPO_JSON.write_text(json.dumps(compact, indent=1, ensure_ascii=False) + "\n")
    logger.info(f"wrote {PARQUET} and {REPO_JSON} ({REPO_JSON.stat().st_size // 1024} KB)")


def commit_json() -> None:
    rel = str(REPO_JSON.relative_to(REPO))
    def git(*a, check=True):
        return subprocess.run(["git", "-C", str(REPO), *a], check=check, capture_output=True, text=True)
    git("add", "-f", rel)        # pulse/data/ is gitignored; this one file is tracked
    if not git("diff", "--cached", "--quiet", "--", rel, check=False).returncode:
        logger.info("own_publications.json unchanged; nothing to commit")
        return
    git("commit", "-m", "own publications: weekly refresh of the callback corpus (owner, 14 Oct 2026: callbacks to "
        "prior work with its chart)", "--", rel)
    git("pull", "--rebase", "--autostash")
    git("push")
    logger.info("committed and pushed own_publications.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-discover", action="store_true", help="do not list the archive or fetch posts")
    ap.add_argument("--since", default="2025-12-01", help="fetch posts published on or after this date")
    ap.add_argument("--max-fetch", type=int, default=MAX_FETCH)
    ap.add_argument("--limit", type=int, default=None, help="process only the newest N posts (testing)")
    ap.add_argument("--no-commit", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not a.no_discover:
        # file stems too: a corpus URL can carry the post's id-style slug (/p/<uuid>), saved under that name
        have = set(load_export()) | set(load_api_dirs()) | ({f.stem for f in API_DIR.glob("*.json")} if API_DIR.exists() else set())
        got = discover_and_fetch(have, a.since, a.max_fetch)
        logger.info(f"fetched {len(got)} posts: {got}")
    pubs = build(a.limit)
    # chart fields: classify new charts, match builders for new time-series charts (both cached), then the
    # weekly line on refreshes (owner, 16 Oct 2026; refreshes happen at use time, not here)
    try:
        import own_chart_meta
        import own_chart_refresh
        own_chart_meta.classify_all(pubs)
        own_chart_meta.match_builders(pubs)
        own_chart_meta.annotate(pubs)
        logger.info(own_chart_refresh.week_report())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"chart fields not updated: {e}")
    write_outputs(pubs)
    if not a.no_commit:
        commit_json()


if __name__ == "__main__":
    main()
