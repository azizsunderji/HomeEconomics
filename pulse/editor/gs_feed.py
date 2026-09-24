"""Goldman Sachs Research feed, built from the owner's logged-in session in the server's live Chrome.

2026-09-24 (owner: "build it, series list is right, and make sure the health email tells me if
this, or any others, need me to re-login"). Goldman research has no RSS and sits behind the
owner's client login. This script opens ONE new tab in the live Chrome on CDP 9223 (cdp_tab.py;
never Playwright on 9223), runs the site's own search with Goldman's URL filter (the same URL
NewsAtNoon/scripts/204_gs_tab.py `q` uses), keeps reports published in the last LOOKBACK_DAYS
from the series in SERIES and housing reports from HOUSING_QUERIES, reads each new report once,
caches it in FEEDS_DIR/gs_cache.json (newest CACHE_MAX kept) and writes FEEDS_DIR/gs_research.xml
(RSS 2.0, newest FEED_MAX items, body text as description). FEEDS_DIR is the private folder
/home/aziz/work/noon/feeds_private; Caddy serves it only at
https://noon.homeeconomics.us/private/<NOON_PRIVATE_TOKEN>/ (the text is licensed; 24 Sep 2026).
Loads are 3-5 seconds apart, at most MAX_LOADS per run.

Login check: if a page lands on a login/SSO page, shows a password form, or a report comes back
with no text, the run stops at once (no retry), still writes the feed from the cache, and records
{"site": "gs", "status": "logged_out"|"logged_in"|"error", "checked_at", "detail"} in
STATUS_PATH, which login_status.py merges into the public login_status.json. Exit code 2 means
logged out, 1 means another error, 0 means the run completed.

Runs from noon-gsfeed.timer (Mon-Fri 10:00 UTC). Log: /home/aziz/work/noon/logs/gsfeed.log.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from cdp_tab import CdpTab, LoadBudgetExceeded

# Licensed text: written to the private folder, served only at /private/<NOON_PRIVATE_TOKEN>/ (24 Sep 2026).
FEEDS_DIR = Path(os.environ.get("NOON_PRIVATE_FEEDS_DIR", "/home/aziz/work/noon/feeds_private"))
CACHE_PATH = FEEDS_DIR / "gs_cache.json"
FEED_PATH = FEEDS_DIR / "gs_research.xml"
STATUS_PATH = Path(os.environ.get("NOON_GS_STATUS", "/home/aziz/work/noon/gs_status.json"))
LOOKBACK_DAYS = 3
MAX_LOADS = 30
CACHE_MAX = 200
FEED_MAX = 40
BODY_MAX = 8000

# Series named by the owner; a result counts when its title starts with the series name.
SERIES = [
    "US Economics Analyst",
    "Global Views",
    "US Weekly Kickstart",
    "Europe Weekly Kickstart",
    "Housing and Mortgage Monitor",
    "Global Strategy Views",
]
# Housing and homebuilder research. "homebuilders" keeps equity research results; "housing" and
# "mortgage" match anywhere in a report's text, so those keep only results whose title is about housing.
HOUSING_QUERIES = ["homebuilders", "housing", "mortgage"]
HOUSING_TITLE = re.compile(r"\b(home ?build\w*|housing|mortgages?|homes?|residential|building products|"
                           r"home ?improvement|real estate|rents?|rental)\b", re.I)

SEARCH = ("https://publishing.gs.com/content/research/site/search.html?facets=()&language=%5B%22en%22%5D"
          "&page=1&sort=time&limitTo=%5B%22%22%5D&filter=")
ROWS_JS = r"""JSON.stringify([...document.querySelectorAll('tr')].filter(t => t.querySelector('a[href*="/reports/"]')).map(t => {
  const a = t.querySelector('a[href*="/reports/"]');
  const ts = t.querySelector('.SearchResults__hiddenEl');
  const meta = t.querySelector('.SearchResults__metaText');
  return {href: a.href.split('?')[0], title: a.innerText.trim().replace(/\s+/g, ' '),
          ts: ts ? Number(ts.innerText.trim()) : 0, meta: meta ? meta.innerText.trim().replace(/\s+/g, ' ') : ''};
}))"""
SEARCH_READY = "/Search Results:/.test(document.body ? document.body.innerText : '') || !!document.querySelector('input[type=password]')"
REPORT_READY = "/Investors should consider|All Tags/.test(document.body ? document.body.innerText : '') || !!document.querySelector('input[type=password]')"
LOGIN_URL = re.compile(r"login|signin|sign-in|/sso|/auth|oauth|saml|marquee\.gs\.com/welcome", re.I)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("noon.gsfeed")


class LoggedOut(RuntimeError):
    pass


def search_url(term: str) -> str:
    f = "(all EQ ${(" + term + ")}$ AND totalPages IN [1,400])"
    return SEARCH + urllib.parse.quote(f, safe="()")


async def login_problem(tab: CdpTab, info: dict) -> str:
    """Why this page looks logged out, or '' if it does not."""
    url = info.get("url", "")
    host = urllib.parse.urlsplit(url).netloc
    if host != "publishing.gs.com" or LOGIN_URL.search(url):
        return f"redirected to {host}{urllib.parse.urlsplit(url).path[:60]}"
    if await tab.eval("!!document.querySelector('input[type=password]')"):
        return "page shows a password form"
    text = (info.get("text") or "")[:3000].lower()
    if re.search(r"\b(sign in|log in)\b", text) and len(info.get("text") or "") < 1500:
        return "page shows a sign-in prompt"
    return ""


def report_body(text: str) -> str:
    """The report's prose from the page's innerText: after the PDF/Share/Bookmark/More toolbar,
    before the disclosure line, with analyst contact lines dropped."""
    start = text.find("\nBookmark\nMore\n")
    body = text[start + len("\nBookmark\nMore\n"):] if start >= 0 else text
    for stop in ("Investors should consider this report", "\nAll Tags\n"):
        i = body.find(stop)
        if i >= 0:
            body = body[:i]
    keep = []
    for line in body.splitlines():
        s = line.strip()
        if not s or "@gs.com" in s or re.fullmatch(r"\+?[\d() .-]{8,}", s) or re.match(r"Goldman Sachs[ &(].*(LLC|L\.L\.C\.|International|Bank|Ltd|plc|AG|Inc)", s):
            continue
        keep.append(s)
    return "\n".join(keep)[:BODY_MAX].strip()


def parse_meta(meta: str) -> tuple[str, str]:
    """'Research | Economics -  A,  B, and others…' -> ('Research | Economics', 'A, B')."""
    division, _, names = meta.partition(" - ")
    names = re.sub(r",?\s*and others.*$", "", names)
    # "Andrea Newkirk, Ph.D., Morgan Lamberti": glue credential suffixes back onto the preceding name
    parts = []
    for n in [x.strip() for x in names.split(",") if x.strip()]:
        if parts and n in ("CFA", "Ph.D.", "Ph.D", "CPA"):
            parts[-1] += f", {n}"
        else:
            parts.append(n)
    return division.strip(), ", ".join(parts)


def write_status(status: str, detail: str) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    rec = {"site": "gs", "status": status, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "detail": detail[:300]}
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    tmp.replace(STATUS_PATH)
    log.info(f"status: {status} ({detail[:160]})")


def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> dict:
    newest = sorted(cache.items(), key=lambda kv: kv[1].get("published", ""), reverse=True)[:CACHE_MAX]
    cache = dict(newest)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    os.chmod(tmp, 0o644)
    tmp.replace(CACHE_PATH)
    return cache


def write_feed(cache: dict) -> int:
    items = [(u, c) for u, c in cache.items() if len(c.get("body") or "") >= 200]
    items.sort(key=lambda kv: kv[1]["published"], reverse=True)
    out = []
    for url, c in items[:FEED_MAX]:
        when = datetime.fromisoformat(c["published"])
        by = f"By {c['authors']}. " if c.get("authors") else ""
        out.append(f"<item><title>{html.escape(c['title'])}</title><link>{html.escape(url)}</link>"
                   f"<guid isPermaLink=\"true\">{html.escape(url)}</guid><pubDate>{format_datetime(when)}</pubDate>"
                   + (f"<author>{html.escape(c['authors'])}</author>" if c.get("authors") else "")
                   + f"<category>{html.escape(c.get('series') or 'Goldman Sachs Research')}</category>"
                   f"<description>{html.escape(by + c['body'])}</description></item>")
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel>"
           "<title>Goldman Sachs Research</title><link>https://publishing.gs.com/content/research/</link>"
           "<description>Goldman Sachs Research reports read on the noon server from the owner's logged-in "
           "session (macro series and housing research)</description>"
           f"<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>"
           + "".join(out) + "</channel></rss>")
    tmp = FEED_PATH.with_suffix(".xml.tmp")
    tmp.write_text(xml, encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(FEED_PATH)
    return len(out)


async def collect(cache: dict) -> tuple[int, int]:
    """Search, then read new reports into the cache. Returns (candidates, reports read)."""
    cutoff_ms = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).timestamp() * 1000
    wanted: dict[str, dict] = {}
    async with CdpTab(max_loads=MAX_LOADS) as tab:
        queries = [(f'"{s}"', s) for s in SERIES] + [(q, None) for q in HOUSING_QUERIES]
        for term, series in queries:
            info = await tab.goto(search_url(term), settle=3, ready=SEARCH_READY, timeout=20)
            why = await login_problem(tab, info)
            if why:
                raise LoggedOut(f"search {term}: {why}")
            if "Search Results:" not in (info.get("text") or ""):
                raise RuntimeError(f"search {term}: results page did not render ({info.get('url', '')[:80]})")
            rows = json.loads(await tab.eval(ROWS_JS) or "[]")
            hits = 0
            for r in rows:
                if r["ts"] < cutoff_ms:
                    continue
                division, authors = parse_meta(r["meta"])
                if series:
                    if not r["title"].lower().startswith(series.lower()):
                        continue
                    label = series
                elif term == "homebuilders":
                    if "equity" not in division.lower():
                        continue
                    label = "Housing and homebuilder research"
                else:
                    if not HOUSING_TITLE.search(r["title"]):
                        continue
                    label = "Housing and homebuilder research"
                hits += 1
                wanted.setdefault(r["href"], {"title": r["title"], "authors": authors, "division": division,
                                              "series": label, "ts": r["ts"]})
            log.info(f"search {term}: {len(rows)} rows, {hits} in the last {LOOKBACK_DAYS} days")
        new = [u for u in sorted(wanted, key=lambda u: -wanted[u]["ts"]) if len(cache.get(u, {}).get("body") or "") < 200]
        read = 0
        for url in new:
            if tab.loads >= MAX_LOADS:
                log.warning(f"load budget reached; {len(new) - read} reports left for the next run")
                break
            w = wanted[url]
            info = await tab.goto(url, settle=4, ready=REPORT_READY, timeout=20)
            why = await login_problem(tab, info)
            if why:
                raise LoggedOut(f"report {url}: {why}")
            body = report_body(info.get("text") or "")
            if len(body) < 200:
                raise LoggedOut(f"report {url} returned no report text ({len(body)} chars)")
            cache[url] = {"title": w["title"], "authors": w["authors"], "series": w["series"],
                          "division": w["division"],
                          "published": datetime.fromtimestamp(w["ts"] / 1000, timezone.utc).isoformat(timespec="seconds"),
                          "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "body": body}
            read += 1
            log.info(f"read {w['series']}: {w['title'][:90]} ({len(body)} chars)")
    return len(wanted), read


def main() -> int:
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_cache()
    code = 0
    try:
        found, read = asyncio.run(collect(cache))
        write_status("logged_in", f"{found} reports in the last {LOOKBACK_DAYS} days, {read} read this run")
    except LoggedOut as e:
        write_status("logged_out", str(e))
        code = 2
    except LoadBudgetExceeded as e:
        write_status("logged_in", f"stopped early: {e}")
    except Exception as e:  # noqa: BLE001
        write_status("error", f"{type(e).__name__}: {e}")
        code = 1
    cache = save_cache(cache)
    n = write_feed(cache)
    log.info(f"feed: {n} items in {FEED_PATH}, {len(cache)} cached")
    return code


if __name__ == "__main__":
    sys.exit(main())
