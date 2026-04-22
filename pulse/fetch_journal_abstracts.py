#!/usr/bin/env python3
"""Fetch abstracts for today's rotated journal papers.

Journals that don't ship abstracts in their RSS (ScienceDirect, Wiley, Tandfonline,
etc.) require fetching the paper page. Uses playwright + Chrome cookies (same
plumbing as enrich_articles.py) so paywalled journals work.

Called from run_local_synthesis.sh right before the synthesis step. Reads the
rotation logic from run_pipeline to figure out which 5 papers need fetching
today, then stores abstracts back to items.body so they're cached across days.

Usage: python fetch_journal_abstracts.py [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import browser_cookie3
from playwright.async_api import async_playwright

DB_PATH = Path(__file__).parent / "data" / "pulse.db"
LOG_PATH = Path("/tmp/pulse_journal_abstracts.log")

_CHROME_BASE = "/Users/azizsunderji/Library/Application Support/Google/Chrome"
_CHROME_PROFILES = ["Default"] + [f"Profile {i}" for i in range(1, 6)]

MIN_ABSTRACT_LEN = 200

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(str(LOG_PATH), mode="a")],
)
logger = logging.getLogger("journal_abs")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _pick_todays_5(conn: sqlite3.Connection) -> list[dict]:
    """Mirror the rotation logic in run_pipeline.cmd_synthesize."""
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    # Find all journal-priority feeds from the OPML
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    from collectors.rss_feeds import parse_opml, DEFAULT_OPML_PATH
    feeds = parse_opml(DEFAULT_OPML_PATH)
    journal_feed_names = [f["title"] for f in feeds if f.get("priority") == "journal"]
    if not journal_feed_names:
        return []
    rows = conn.execute(
        "SELECT * FROM items WHERE source = 'rss' AND feed_name IN ({}) AND collected_at >= ? ORDER BY feed_name, collected_at DESC".format(
            ",".join(["?"] * len(journal_feed_names))
        ),
        list(journal_feed_names) + [cutoff_30d],
    ).fetchall()
    pool = []
    seen = set()
    for r in rows:
        item = dict(r)
        key = (item.get("title") or "")[:80].lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        pool.append(item)
    pool.sort(key=lambda x: (x.get("title") or "").lower())
    if not pool:
        return []
    day_idx = datetime.now(timezone.utc).toordinal()
    start = (day_idx * 5) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(5)]


def _get_cookies(url: str) -> list[dict]:
    parsed = urlparse(url)
    domain = parsed.netloc
    domain = ("." + domain[4:]) if domain.startswith("www.") else ("." + domain)
    seen, raw = set(), []
    for profile in _CHROME_PROFILES:
        try:
            jar = browser_cookie3.chrome(
                domain_name=domain,
                cookie_file=f"{_CHROME_BASE}/{profile}/Cookies",
            )
            for c in jar:
                if (c.name, c.domain) not in seen:
                    seen.add((c.name, c.domain))
                    raw.append(c)
        except Exception:
            pass
    return [
        {"name": c.name, "value": c.value, "domain": c.domain,
         "path": c.path, "secure": bool(c.secure), "httpOnly": False}
        for c in raw
    ]


def _extract_abstract(html: str) -> str:
    """Try many selectors/patterns to find an abstract in rendered HTML."""
    # 1. <meta name="description"> often has the abstract on academic pages
    m = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m and len(m.group(1)) > MIN_ABSTRACT_LEN:
        return _clean(m.group(1))
    # 2. Dublin Core
    m = re.search(r'<meta\s+name=["\']dc\.Description["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m and len(m.group(1)) > MIN_ABSTRACT_LEN:
        return _clean(m.group(1))
    # 3. citation_abstract (Highwire / Scholar standard)
    m = re.search(r'<meta\s+name=["\']citation_abstract["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m and len(m.group(1)) > MIN_ABSTRACT_LEN:
        return _clean(m.group(1))
    # 4. Common abstract containers
    for pattern in [
        r'<div[^>]*class="[^"]*\babstract-content\b[^"]*"[^>]*>(.*?)</div>',
        r'<section[^>]*class="[^"]*\babstract\b[^"]*"[^>]*>(.*?)</section>',
        r'<div[^>]*class="[^"]*\bArticleAbstract\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*\barticle-section__content\b[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*id=["\']abstract["\'][^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*\bAbstracts\b[^"]*"[^>]*>(.*?)</div>',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            txt = _clean(m.group(1))
            if len(txt) > MIN_ABSTRACT_LEN:
                return txt
    # 5. Look for "Abstract" heading followed by a paragraph
    m = re.search(r'<h[1-3][^>]*>\s*abstract\s*</h[1-3]>\s*<(?:p|div)[^>]*>(.*?)</(?:p|div)>', html, re.DOTALL | re.IGNORECASE)
    if m:
        txt = _clean(m.group(1))
        if len(txt) > MIN_ABSTRACT_LEN:
            return txt
    return ""


def _clean(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s)
    for entity, ch in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                       ("&#39;", "'"), ("&nbsp;", " "), ("&ndash;", "–"), ("&mdash;", "—")]:
        s = s.replace(entity, ch)
    s = re.sub(r'\s+', ' ', s).strip()
    if s.lower().startswith("abstract"):
        s = s[8:].strip(" :")
    return s[:2000]


def _extract_doi(url: str) -> str | None:
    """Pull a DOI out of common journal URL patterns."""
    m = re.search(r'/doi/(?:full/|abs/)?(10\.\d+/[^\?#\s]+)', url)
    if m:
        return m.group(1).rstrip("/")
    m = re.search(r'(10\.\d+/[^\?#\s]+)', url)
    if m:
        return m.group(1).rstrip("/")
    return None


def _rebuild_inverted_index(ai: dict) -> str:
    """OpenAlex serves abstracts as a position→word inverted index. Reconstruct."""
    if not ai:
        return ""
    words = {}
    for word, positions in ai.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def _fetch_abstract_by_doi(doi: str, http_client: "httpx.Client") -> str:
    """Try Semantic Scholar → OpenAlex → CrossRef. Return empty string if none have it."""
    try:
        r = http_client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "abstract"},
            timeout=15,
        )
        if r.status_code == 200:
            abstract = (r.json().get("abstract") or "").strip()
            if len(abstract) > MIN_ABSTRACT_LEN:
                return abstract[:2000]
    except Exception:
        pass
    try:
        r = http_client.get(
            f"https://api.openalex.org/works/https://doi.org/{doi}",
            timeout=15,
        )
        if r.status_code == 200:
            abstract = _rebuild_inverted_index(r.json().get("abstract_inverted_index") or {}).strip()
            if len(abstract) > MIN_ABSTRACT_LEN:
                return abstract[:2000]
    except Exception:
        pass
    try:
        r = http_client.get(
            f"https://api.crossref.org/works/{doi}",
            timeout=15,
        )
        if r.status_code == 200:
            abstract_xml = r.json().get("message", {}).get("abstract", "") or ""
            abstract = re.sub(r"<[^>]+>", " ", abstract_xml)
            abstract = re.sub(r"\s+", " ", abstract).strip()
            if len(abstract) > MIN_ABSTRACT_LEN:
                return abstract[:2000]
    except Exception:
        pass
    return ""


def _fetch_abstract_by_title(title: str, http_client: "httpx.Client") -> str:
    """Last-resort: search OpenAlex by title. Works for ScienceDirect papers
    that don't have a DOI in their URL. Returns empty string if nothing found
    or no abstract available."""
    try:
        r = http_client.get(
            "https://api.openalex.org/works",
            params={"search": title[:150], "per-page": 1},
            timeout=15,
        )
        if r.status_code == 200:
            results = r.json().get("results") or []
            if not results:
                return ""
            # Verify title match — search can return tangentially related papers
            found_title = (results[0].get("title") or "").lower()
            query_title = title.lower()
            # Require substantial overlap to avoid false-positive matches
            if found_title[:40] not in query_title and query_title[:40] not in found_title:
                return ""
            abstract = _rebuild_inverted_index(
                results[0].get("abstract_inverted_index") or {}
            ).strip()
            if len(abstract) > MIN_ABSTRACT_LEN:
                return abstract[:2000]
    except Exception:
        pass
    return ""


async def _fetch_abstracts(papers: list[dict], dry_run: bool = False) -> dict[str, str]:
    import httpx
    results: dict[str, str] = {}
    http = httpx.Client(headers={"User-Agent": "Pulse Briefing <aziz@home-economics.us>"})

    # Pass 1: DOI-based API lookup, then OpenAlex title search as fallback
    # (both skip the browser — fast and polite)
    remaining = []
    for paper in papers:
        title_short = (paper.get("title") or "")[:60]
        if len(paper.get("body") or "") > MIN_ABSTRACT_LEN:
            logger.info(f"  CACHED {title_short}")
            continue
        # Strip HTML from title (Cities feed puts <em> tags in titles)
        clean_title = re.sub(r"<[^>]+>", "", paper.get("title") or "").strip()

        doi = _extract_doi(paper["url"])
        if doi:
            abstract = _fetch_abstract_by_doi(doi, http)
            if abstract:
                results[paper["id"]] = abstract
                logger.info(f"  DOI-API ({len(abstract)}c via {doi}) {title_short}")
                continue

        # No DOI or DOI lookup failed — try title search on OpenAlex
        if clean_title:
            abstract = _fetch_abstract_by_title(clean_title, http)
            if abstract:
                results[paper["id"]] = abstract
                logger.info(f"  TITLE-API ({len(abstract)}c) {title_short}")
                continue

        remaining.append(paper)

    # Pass 2: for papers with no DOI or APIs returned nothing, try the full
    # browser page fetch as a last resort.
    if remaining:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, channel="chrome",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            for paper in remaining:
                url = paper["url"]
                title = (paper.get("title") or "")[:60]
                try:
                    cookies = _get_cookies(url)
                    ctx = await browser.new_context(
                        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/147.0.0.0 Safari/537.36"),
                    )
                    if cookies:
                        await ctx.add_cookies(cookies)
                    page = await ctx.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    await page.wait_for_timeout(1500)
                    html = await page.content()
                    await ctx.close()
                    abstract = _extract_abstract(html)
                    if abstract:
                        results[paper["id"]] = abstract
                        logger.info(f"  BROWSER ({len(abstract)}c) {title}")
                    else:
                        logger.info(f"  NO-ABS {title}")
                except Exception as e:
                    logger.warning(f"  FAIL {title}: {e}")
            await browser.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = _get_db()
    papers = _pick_todays_5(conn)
    logger.info(f"=== Fetching abstracts for {len(papers)} journal papers ===")
    results = asyncio.run(_fetch_abstracts(papers, args.dry_run))

    if args.dry_run:
        logger.info(f"Dry run — would update {len(results)} items")
    else:
        for item_id, abstract in results.items():
            conn.execute("UPDATE items SET body = ? WHERE id = ?", (abstract, item_id))
        conn.commit()
        logger.info(f"Stored abstracts for {len(results)} papers")


if __name__ == "__main__":
    main()
