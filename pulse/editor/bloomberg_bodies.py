"""Bloomberg article bodies read through the owner's own Mac Chrome, published as a private full-text feed.

2026-10-16 (owner): Bloomberg challenges every automated load from the server and from Browserbase
with its "Are you a robot?" page, so the corpus held teasers only. The owner is a paying Bloomberg
subscriber and reads it in his Mac Chrome, which this server reaches over the Tailscale CDP relay
(mac-ts-ip; port 9222). The Mac is kept awake permanently (caffeinate), so this runs around the clock.

Each run:
  1. If the Mac's CDP endpoint does not answer within 5 seconds, record mac_reachable=false in the
     status file and exit quietly (exit code 0).
  2. Select Bloomberg items from the synced corpus (opened READ-ONLY; never written from this server)
     collected in the last LOOKBACK_HOURS whose body is under MIN_CHARS or is a wall page, highest
     relevance and newest first, skipping URLs already fetched (STATE_PATH) or tried MAX_TRIES times.
     At most MAX_PER_RUN per run (MORNING_MAX in the 10:20-10:50 UTC slot, so the run finishes
     before the 11:00 UTC synthesis re-collects the feeds).
  3. For each, open a BACKGROUND tab in the Mac Chrome (Target.createTarget background=True), load
     the article, wait WAIT_SECONDS, read the HTML, close that tab. Never activates a tab and never
     touches the owner's other tabs. GAP_SECONDS between articles.
  4. Keep texts of at least MIN_CHARS that are not wall pages (enrich_articles._extract_article_text
     and _is_blocked_page), cache them, and write the newest FEED_MAX as the private feed
     FEEDS_DIR/bloomberg_bodies.xml (same shape as gs_research.xml; link = the original bloomberg.com
     URL, description = full text). The collector (rss_feeds.py, PRIVATE_FEEDS) reads it from
     FEED_PRIVATE_BASE and upgrades the stored teaser bodies (store.upsert_item).
  5. Write STATUS_PATH and a copy in the public feeds folder (statuses and titles only, no article
     text), read by the health report's stage 2.2.

Runs from noon-bloomberg.timer. Log: /home/aziz/work/noon/logs/bloomberg_bodies.log.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

# Configure logging before importing enrich_articles: its module-level basicConfig would otherwise
# send this job's lines to the enrichment log.
LOG_PATH = Path(os.environ.get("NOON_BLOOMBERG_LOG", "/home/aziz/work/noon/logs/bloomberg_bodies.log"))
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(str(LOG_PATH)), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("noon.bloomberg")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enrich_articles as ea  # noqa: E402  (text extractor and wall-page check)

import websockets  # noqa: E402

PULSE_DB = os.environ.get("PULSE_DB", "/home/aziz/Dropbox/Home Economics/Data/Pulse/pulse.db")
FEEDS_DIR = Path(os.environ.get("NOON_PRIVATE_FEEDS_DIR", "/home/aziz/work/noon/feeds_private"))
FEED_PATH = FEEDS_DIR / "bloomberg_bodies.xml"
PUBLIC_DIR = Path(os.environ.get("NOON_FEEDS_DIR", "/home/aziz/work/noon/feeds"))
STATUS_PATH = Path("/home/aziz/work/noon/bloomberg_bodies_status.json")
STATE_PATH = Path("/home/aziz/work/noon/bloomberg_bodies_state.json")   # attempts per URL (small)
CACHE_PATH = Path("/home/aziz/work/noon/bloomberg_bodies_cache.json")   # licensed text; not web-served

LOOKBACK_HOURS = 36
MIN_CHARS = 500
MAX_PER_RUN = 25
MORNING_MAX = 15            # 10:20-10:50 UTC: finish before the 11:00 synthesis re-collects the feeds
MAX_TRIES = 3
WAIT_SECONDS = 8
GAP_SECONDS = 6
FEED_MAX = 60
BODY_MAX = 20000
STATE_KEEP_DAYS = 7
CDP_PORT = 9222
FALLBACK_MAC_IP = "100.124.82.61"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save_json(path: Path, data, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.chmod(tmp, mode)
    tmp.replace(path)


def mac_ws_url() -> str | None:
    """The Mac Chrome's browser websocket URL, or None when it does not answer within 5 s."""
    ip = ""
    try:
        ip = subprocess.run(["/home/aziz/.local/bin/mac-ts-ip"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    for host in dict.fromkeys([ip, FALLBACK_MAC_IP]):
        if not host:
            continue
        try:
            with urllib.request.urlopen(f"http://{host}:{CDP_PORT}/json/version", timeout=5) as r:
                return json.loads(r.read())["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            continue
    return None


def candidates(state: dict, limit: int) -> list[dict]:
    """Bloomberg items needing a body, from the synced corpus opened read-only."""
    cutoff = iso(now_utc() - timedelta(hours=LOOKBACK_HOURS))
    conn = sqlite3.connect(f"file:{PULSE_DB}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, url, title, body, relevance_score, published_at, collected_at FROM items "
            "WHERE collected_at >= ? AND (url LIKE 'https://www.bloomberg.com/%' OR url LIKE 'https://bloomberg.com/%') "
            "AND url NOT LIKE '%/videos/%' AND url NOT LIKE '%/audio/%' AND url NOT LIKE '%/live/%' "
            "ORDER BY COALESCE(relevance_score, 0) DESC, COALESCE(NULLIF(published_at, ''), collected_at) DESC",
            (cutoff,)).fetchall()
    finally:
        conn.close()
    out, seen = [], set()
    for r in rows:
        url = r["url"]
        body = r["body"] or ""
        if url in seen or (len(body) >= MIN_CHARS and not ea._is_blocked_page(body)):
            continue
        seen.add(url)
        st = state.get(url, {})
        if st.get("ok") or st.get("tries", 0) >= MAX_TRIES:
            continue
        out.append(dict(r))
        if len(out) >= limit:
            break
    return out


_BYLINE_DATE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4} at \d{1,2}:\d{2} [AP]M [A-Z]{2,4}$")
_END_MARKERS = ("\nLinkCopy Link", "\nBefore it's here, it's on the Bloomberg Terminal")
_DROP_LINES = re.compile(r"^(Takeaways by Bloomberg AI.*|Read More:.*|BookmarkSave|Translate|Chevron Down|Listen|\d{1,2}:\d{2})$")


def clean_bloomberg(text: str) -> str:
    """Article prose without Bloomberg's page furniture: the share/gift/feedback controls before the
    byline and the follow/alerts/related-stories block after the last paragraph. The byline is kept.
    Falls back to the extractor's text when the trim leaves under MIN_CHARS."""
    lines = text.splitlines()
    start, byline = 0, ""
    for i, ln in enumerate(lines[:80]):
        if _BYLINE_DATE.match(ln.strip()):
            start = i + 1
            if i and lines[i - 1].startswith("By "):
                byline = lines[i - 1].strip()
            break
    body = "\n".join(ln for ln in lines[start:] if not _DROP_LINES.match(ln.strip()))
    for m in _END_MARKERS:
        j = body.find(m)
        if j >= 0:
            body = body[:j]
    body = ((byline + ". ") if byline else "") + body.strip()
    return body if len(body) >= MIN_CHARS else text


class Cdp:
    """Minimal CDP client on the browser websocket (flattened sessions)."""

    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method: str, sid: str | None = None, timeout: float = 60, **params):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params}
        if sid:
            msg["sessionId"] = sid
        await self.ws.send(json.dumps(msg))
        deadline = time.monotonic() + timeout
        while True:
            r = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=max(1.0, deadline - time.monotonic())))
            if r.get("id") == self.n:
                if "error" in r:
                    raise RuntimeError(f"{method}: {r['error'].get('message', r['error'])}")
                return r.get("result", {})


async def read_article(cdp: Cdp, url: str) -> tuple[str, str]:
    """(text, why_rejected) for one article, read in a background tab that is closed afterwards."""
    tid = (await cdp.call("Target.createTarget", url="about:blank", background=True))["targetId"]
    try:
        sid = (await cdp.call("Target.attachToTarget", targetId=tid, flatten=True))["sessionId"]
        await cdp.call("Page.enable", sid)
        await cdp.call("Page.navigate", sid, url=url)
        text = ""
        for wait in (WAIT_SECONDS, 4):          # one short second look if the page is still rendering
            await asyncio.sleep(wait)
            res = await cdp.call("Runtime.evaluate", sid, expression="document.documentElement.outerHTML",
                                 returnByValue=True)
            text = ea._extract_article_text(res.get("result", {}).get("value") or "")
            if ea._is_blocked_page(text):
                return "", "wall page (robot check)"
            if len(text) >= 1500:
                break
        if len(text) < MIN_CHARS:
            return "", f"short ({len(text)} chars)"
        return clean_bloomberg(text)[:BODY_MAX], ""
    finally:
        try:
            await cdp.call("Target.closeTarget", targetId=tid, timeout=15)
        except Exception as e:  # noqa: BLE001
            log.warning(f"could not close tab {tid}: {e}")


async def fetch_all(ws_url: str, items: list[dict]) -> list[tuple[dict, str, str]]:
    results = []
    async with websockets.connect(ws_url, max_size=60_000_000, open_timeout=20) as ws:
        cdp = Cdp(ws)
        for i, it in enumerate(items):
            if i:
                await asyncio.sleep(GAP_SECONDS)
            try:
                text, why = await read_article(cdp, it["url"])
            except Exception as e:  # noqa: BLE001
                text, why = "", f"error: {type(e).__name__}: {e}"[:160]
            log.info(f"{'OK  ' if text else 'FAIL'} {len(text):>6} chars  {it['url'][:110]}" + (f"  ({why})" if why else ""))
            results.append((it, text, why))
    return results


def write_feed(cache: dict) -> int:
    items = sorted(cache.items(), key=lambda kv: kv[1].get("published") or kv[1]["fetched_at"], reverse=True)[:FEED_MAX]
    out = []
    for url, c in items:
        try:
            when = datetime.fromisoformat((c.get("published") or c["fetched_at"]).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
        except ValueError:
            when = datetime.fromisoformat(c["fetched_at"])
        out.append(f"<item><title>{html.escape(c['title'])}</title><link>{html.escape(url)}</link>"
                   f"<guid isPermaLink=\"true\">{html.escape(url)}</guid><pubDate>{format_datetime(when)}</pubDate>"
                   f"<category>Bloomberg</category>"
                   f"<description>{html.escape(c['body'])}</description></item>")
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel>"
           "<title>Bloomberg (full text)</title><link>https://www.bloomberg.com/</link>"
           "<description>Bloomberg article bodies read in the owner's own Mac Chrome (his subscription), "
           "for upgrading teaser bodies in the corpus</description>"
           f"<lastBuildDate>{format_datetime(now_utc())}</lastBuildDate>"
           + "".join(out) + "</channel></rss>")
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FEED_PATH.with_suffix(".xml.tmp")
    tmp.write_text(xml, encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(FEED_PATH)
    return len(out)


def write_status(state: dict, reachable: bool, fetched: int, failed: int, examples: list[dict], note: str = "") -> None:
    now = now_utc()
    prev = load_json(STATUS_PATH, {})
    day_ago = iso(now - timedelta(hours=24))
    ok_24 = sum(1 for s in state.values() if s.get("ok") and s.get("last_try", "") >= day_ago)
    fail_24 = sum(1 for s in state.values() if not s.get("ok") and s.get("last_try", "") >= day_ago)
    rec = {
        "last_run": iso(now),
        "mac_reachable": reachable,
        "last_reachable_at": iso(now) if reachable else prev.get("last_reachable_at"),
        "fetched": fetched, "failed": failed,
        "fetched_24h": ok_24, "failed_24h": fail_24,
        "feed_items": len(load_json(CACHE_PATH, {})),
        "examples": examples[:5],
        "note": note,
    }
    save_json(STATUS_PATH, rec, 0o644)
    save_json(PUBLIC_DIR / "bloomberg_bodies_status.json", rec, 0o644)   # read by the health report


def main() -> int:
    start = now_utc()
    state = load_json(STATE_PATH, {})
    cutoff = iso(start - timedelta(days=STATE_KEEP_DAYS))
    state = {u: s for u, s in state.items() if s.get("last_try", "") >= cutoff}

    ws_url = mac_ws_url()
    if not ws_url:
        log.info("Mac Chrome not reachable (5 s); nothing to do")
        write_status(state, False, 0, 0, [], "Mac Chrome did not answer on the Tailscale CDP relay")
        return 0

    morning = start.hour == 10 and 20 <= start.minute < 50
    limit = MORNING_MAX if morning else MAX_PER_RUN
    todo = candidates(state, limit)
    log.info(f"Mac Chrome reachable; {len(todo)} Bloomberg item(s) to read (cap {limit})")
    cache = load_json(CACHE_PATH, {})
    fetched = failed = 0
    examples = []
    if todo:
        results = asyncio.run(fetch_all(ws_url, todo))
        for it, text, why in results:
            st = state.setdefault(it["url"], {"tries": 0})
            st["tries"] = st.get("tries", 0) + 1
            st["last_try"] = iso(now_utc())
            st["ok"] = bool(text)
            st["chars"] = len(text)
            if why:
                st["why"] = why
            if text:
                fetched += 1
                cache[it["url"]] = {"title": it["title"] or "", "published": it["published_at"] or it["collected_at"],
                                    "fetched_at": st["last_try"], "body": text, "chars": len(text)}
                examples.append({"title": (it["title"] or "")[:100], "chars": len(text), "stored_chars": len(it["body"] or "")})
            else:
                failed += 1
                examples.append({"title": (it["title"] or "")[:100], "chars": 0, "why": why})
    # Keep the newest FEED_MAX bodies in the cache.
    keep = sorted(cache.items(), key=lambda kv: kv[1].get("fetched_at", ""), reverse=True)[:FEED_MAX]
    cache = dict(keep)
    save_json(CACHE_PATH, cache)
    save_json(STATE_PATH, state)
    n = write_feed(cache)
    write_status(state, True, fetched, failed, examples)
    log.info(f"done in {(now_utc() - start).total_seconds():.0f}s: fetched {fetched}, failed {failed}; feed has {n} items")
    return 0


if __name__ == "__main__":
    sys.exit(main())
