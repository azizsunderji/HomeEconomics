"""Login status of every subscription site the pipeline reads, by the mechanism that reads it.

2026-09-24 (owner: "build it, series list is right, and make sure the health email tells me if
this, or any others, need me to re-login"; amended the same day: each site is tagged with the
mechanism that fetches it, that mechanism is what gets checked, and the row says where to log in).

Two mechanisms hold logins:
  - "server chrome": the live Chrome on this server (CDP 9223, profile claude-browser-profile,
    reached by the owner at https://browser.homeeconomics.us). Used by gs_feed.py for Goldman
    Sachs Research, and by the ad hoc readers live_tab_fetch.py and paywall_fetch.py (which copies
    this profile). Checked here: ONE new tab, one page per site, loads 3-5 seconds apart, tab
    closed at the end (cdp_tab.py). Never Playwright on 9223.
  - "browserbase": the persistent Browserbase context used by pulse/enrich_articles.py on GitHub
    Actions for article bodies. enrich_articles.py checks NYT, FT and WSJ there every run and
    stores the result in pulse.db's paywall_auth table (health stage 2.0). This script copies
    those rows from the synced pulse.db (read-only) rather than opening a paid Browserbase session.

Writes FEEDS_DIR/login_status.json, public at https://noon.homeeconomics.us/feeds/login_status.json
and read by the health report's stage 2.1. It holds statuses and marker notes only: no cookies,
no credentials. Runs from noon-loginstatus.timer (daily 10:30 UTC). Log: ~/work/noon/logs/loginstatus.log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cdp_tab import CdpTab

FEEDS_DIR = Path(os.environ.get("NOON_FEEDS_DIR", "/home/aziz/work/noon/feeds"))
OUT_PATH = FEEDS_DIR / "login_status.json"
GS_STATUS = Path(os.environ.get("NOON_GS_STATUS", "/home/aziz/work/noon/gs_status.json"))
PULSE_DB = os.environ.get("PULSE_DB", "/home/aziz/Dropbox/Home Economics/Data/Pulse/pulse.db")
RELOGIN_URL = "https://browser.homeeconomics.us"
RELOGIN = {
    "server chrome": f"re-login at {RELOGIN_URL}",
    "browserbase": "re-login needed in the Browserbase context (ask Claude to open the login page there)",
}
GS_SEARCH = ("https://publishing.gs.com/content/research/site/search.html?facets=()&language=%5B%22en%22%5D"
             "&page=1&sort=time&limitTo=%5B%22%22%5D&filter=" +
             urllib.parse.quote('(all EQ ${("US Economics Analyst")}$ AND totalPages IN [1,400])', safe="()"))

# Server Chrome checks: (site, used_by, url, in_links, in_text, out_text).
# in_links: exact link/button labels that only a signed-in page shows. in_text / out_text: lowercase
# wording in the page text. As in enrich_articles.py's Browserbase check, a site is logged_out only
# when every out_text marker is present and no signed-in marker is. Markers seen on 2026-09-24.
CHROME_SITES = [
    ("Goldman Sachs Research", "gs_feed.py (Goldman Sachs Research feed)", GS_SEARCH, [], ["search results:"], []),
    ("WSJ", "ad hoc reads (live_tab_fetch.py)", "https://www.wsj.com/", ["sign out", "my account"], ["sign out"], ["sign in"]),
    ("NYT", "ad hoc reads (live_tab_fetch.py, paywall_fetch.py)", "https://www.nytimes.com/", ["account", "log out"], [], ["log in", "subscribe for"]),
    ("FT", "ad hoc reads (live_tab_fetch.py)", "https://www.ft.com/", ["my account", "myft", "sign out"], [], ["sign in"]),
    ("Bloomberg", "ad hoc reads (live_tab_fetch.py)", "https://www.bloomberg.com/", ["account", "sign out"], [], ["sign in"]),
    ("Economist", "ad hoc reads (live_tab_fetch.py)", "https://www.economist.com/", ["log out", "manage account"], [], ["log in"]),
    ("Substack", "ad hoc reads of paid posts (paywall_fetch.py, live_tab_fetch.py)",
     "https://homeeconomics.substack.com/p/manhattan-is-densifying-again", ["dashboard"], [],
     ["this post is for paid subscribers"]),
]
# Browserbase sites: pulse.db paywall_auth site -> display name. enrich_articles.PAYWALL_AUTH_SITES.
BROWSERBASE_SITES = {"wsj.com": "WSJ", "nytimes.com": "NYT", "ft.com": "FT"}
BROWSERBASE_USED_BY = "enrich_articles.py article bodies (GitHub Actions)"
NOTES = [
    "Bloomberg and Economist article bodies in the pipeline also go through the Browserbase context, "
    "but enrich_articles.py checks only NYT, FT and WSJ there, so their Browserbase login is not known.",
]

BOT = ["unusual activity", "not a robot", "are you a robot", "press & hold", "verify you are human",
       "access is temporarily restricted", "access denied", "checking your browser"]
LOGIN_URL = re.compile(r"login|signin|sign-in|/sso|/auth|oauth|saml", re.I)
LINKS_JS = ("JSON.stringify([...document.querySelectorAll('a,button')].map(e => (e.innerText || "
            "e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').toLowerCase()).filter(t => t && t.length < 40))")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("noon.loginstatus")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def judge(url: str, info: dict, links: list[str], has_pw: bool, in_links, in_text, out_text) -> tuple[str, str]:
    text = (info.get("text") or "").lower()
    final = info.get("url", "")
    if len(text) < 500 and not has_pw:
        return "unknown", f"page did not render ({len(text)} chars)"
    bot = [b for b in BOT if b in text[:4000]]
    if bot:
        return "unknown", f"bot check shown ({bot[0]}); not retried"
    if urllib.parse.urlsplit(final).netloc != urllib.parse.urlsplit(url).netloc or LOGIN_URL.search(urllib.parse.urlsplit(final).path):
        if has_pw or LOGIN_URL.search(final):
            return "logged_out", f"redirected to a login page ({urllib.parse.urlsplit(final).netloc})"
    if has_pw:
        return "logged_out", "page shows a password form"
    hit_in = sorted({m for m in in_links if m in links} | {m for m in in_text if m in text})
    hit_out = [m for m in out_text if m in text]
    if hit_in:
        return "logged_in", f"signed-in marker: {', '.join(hit_in)}"
    if out_text and len(hit_out) == len(out_text):
        return "logged_out", f"sign-in wording: {', '.join(hit_out)}"
    if not out_text:
        return "unknown", "no signed-in marker found"
    return "logged_in", "no sign-in prompt on the page"


async def probe_chrome() -> list[dict]:
    rows = []
    async with CdpTab(max_loads=len(CHROME_SITES)) as tab:
        for site, used_by, url, in_links, in_text, out_text in CHROME_SITES:
            try:
                info = await tab.goto(url, settle=4, ready="document.readyState === 'complete'", timeout=15)
                await asyncio.sleep(3)  # late-rendering headers (NYT, FT)
                info = {"url": await tab.eval("location.href"), "text": await tab.eval("document.body ? document.body.innerText : ''")}
                links = json.loads(await tab.eval(LINKS_JS) or "[]")
                has_pw = bool(await tab.eval("!!document.querySelector('input[type=password]')"))
                status, detail = judge(url, info, links, has_pw, in_links, in_text, out_text)
            except Exception as e:  # noqa: BLE001
                status, detail = "unknown", f"{type(e).__name__}: {str(e)[:150]}"
            log.info(f"server chrome {site}: {status} ({detail})")
            rows.append({"site": site, "mechanism": "server chrome", "used_by": used_by, "status": status,
                         "detail": detail, "checked_at": now_iso()})
    return rows


def merge_gs(rows: list[dict]) -> None:
    """Fold in the status gs_feed.py recorded on its last run."""
    try:
        feed = json.loads(GS_STATUS.read_text())
    except (OSError, ValueError):
        return
    row = next((r for r in rows if r["site"] == "Goldman Sachs Research"), None)
    if row is None:
        return
    fs = {"logged_in": "logged_in", "logged_out": "logged_out"}.get(feed.get("status"), "unknown")
    row["feed_run"] = {"status": feed.get("status"), "checked_at": feed.get("checked_at"), "detail": feed.get("detail", "")[:200]}
    row["detail"] += f"; feed run {feed.get('checked_at', '?')[:16]}Z: {feed.get('status')}"
    fresh = feed.get("checked_at", "") >= (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
    if row["status"] == "unknown" and fresh and fs != "unknown":
        row["status"] = fs


def browserbase_rows() -> list[dict]:
    try:
        conn = sqlite3.connect(f"file:{PULSE_DB}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        got = conn.execute("SELECT site, status, detail, MAX(checked_at) AS checked_at FROM paywall_auth GROUP BY site").fetchall()
        conn.close()
    except sqlite3.Error as e:
        log.warning(f"paywall_auth not readable: {e}")
        got = []
    by_site = {r["site"]: r for r in got}
    rows = []
    for key, name in BROWSERBASE_SITES.items():
        r = by_site.get(key)
        rows.append({"site": name, "mechanism": "browserbase", "used_by": BROWSERBASE_USED_BY,
                     "status": (r["status"] if r and r["status"] in ("logged_in", "logged_out") else "unknown"),
                     "detail": (f"enrich_articles.py check: {r['detail'][:150]}" if r else "no check recorded in pulse.db"),
                     "checked_at": (r["checked_at"] if r else None)})
    return rows


def main() -> int:
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    rows = asyncio.run(probe_chrome())
    merge_gs(rows)
    rows += browserbase_rows()
    for r in rows:
        r["relogin"] = RELOGIN[r["mechanism"]] if r["status"] == "logged_out" else ""
    out = {"generated_at": now_iso(), "relogin_url": RELOGIN_URL, "relogin": RELOGIN,
           "sites": rows, "notes": NOTES}
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1))
    os.chmod(tmp, 0o644)
    tmp.replace(OUT_PATH)
    out_sites = [f"{r['site']} ({r['mechanism']})" for r in rows if r["status"] == "logged_out"]
    log.info(f"wrote {OUT_PATH}: " + (f"logged out: {', '.join(out_sites)}" if out_sites else "no site logged out"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
