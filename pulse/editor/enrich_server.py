"""Article-body enrichment in the server's live Chrome, published for the synthesis workflow.

2026-09-24 (Aziz: "consolidate enrichment on the server Chrome; Browserbase stays as fallback for a
week, then is cancelled if the health report shows no blocks"). Step 1 of moving Housing at Noon's
article enrichment off Browserbase.

What it does:
  - Selects candidates from the READ-ONLY Dropbox mirror of pulse.db with enrich_articles.py's own
    query (_get_items_to_enrich: sources rss/gmail/substack/hackernews, collected in the last
    --hours, body under MIN_BODY_LEN for rss/hackernews, housekeeping URLs excluded, journals
    excluded, highest relevance first) and its SKIP_DOMAINS. Two additions: rows the synthesis run
    already enriched (enrich_mode set) are skipped, and duplicate URLs are fetched once.
    This script never writes to pulse.db; the synthesis workflow applies the bodies
    (pulse/scripts/apply_server_enrichment.py).
  - Loads each URL in ONE new tab of the live Chrome (CDP 9223, cdp_tab.py; the tab is closed at the
    end). Loads are 2-4 seconds apart, and at most one load every HOST_GAP seconds to the same host,
    because the browser exits through one fixed proxy IP. The first bot-check or access-denied page
    from a host stops that host for the rest of the run (no retry) and is recorded with its wording.
  - Extracts the body with enrich_articles._extract_article_text on the page HTML, so the text is
    what the Browserbase step would store. A body of 200+ characters counts as ok (same cut-off as
    enrich_articles.py). No archive.ph fallback here; the Browserbase step still does that.

Writes (Caddy serves FEEDS_DIR at https://noon.homeeconomics.us/feeds/):
  FEEDS_DIR/enriched_bodies.json  {"generated_at", "run": {...}, "hosts": {host: {"ok", "blocked",
      "empty"}}, "blocked": {host: page wording}, "items": {url: {"body" (<= 8000 chars), "title",
      "fetched_at", "mode": "server_chrome"}}}; items from the last KEEP_DAYS days are kept.
  STATUS_PATH (~/work/noon/enrich_status.json): counts and blocked hosts for this run.

Runs from noon-enrich.timer (Mon-Fri 10:15 UTC). Log: /home/aziz/work/noon/logs/enrich.log.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

# Configure logging before importing enrich_articles, so its basicConfig (a file handler on
# /tmp/pulse_enrich.log) is a no-op here.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("noon.enrich")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pulse/
import enrich_articles as ea  # noqa: E402

import cdp_tab  # noqa: E402
from cdp_tab import CdpTab, LoadBudgetExceeded  # noqa: E402

# Owner's spacing for this job: 2-4 s between any two loads (cdp_tab's defaults are 3-5 s).
cdp_tab.MIN_GAP, cdp_tab.MAX_GAP = 2.0, 4.0
HOST_GAP = 6.0          # seconds between loads to the same host (bloomberg, wsj, ft are the sensitive ones)
LOAD_TIMEOUT = 75.0     # seconds for one page, including settle and HTML read
BODY_MAX = 8000
OK_MIN = 200            # enrich_articles.py counts 200+ characters as a fetched body
KEEP_DAYS = 3

PULSE_DB = os.environ.get("PULSE_DB_MIRROR", "/home/aziz/OVH/Data/Pulse/pulse.db")
FEEDS_DIR = Path(os.environ.get("NOON_FEEDS_DIR", "/home/aziz/work/noon/feeds"))
OUT_PATH = FEEDS_DIR / "enriched_bodies.json"
STATUS_PATH = Path(os.environ.get("NOON_ENRICH_STATUS", "/home/aziz/work/noon/enrich_status.json"))

# Wording of bot-check / access-denied pages (lowercase). Checked in the title, and in the start of
# the page text when the page is short, so an article that mentions "captcha" is not a block.
BOT_MARKERS = [
    "are you a robot", "not a robot", "unusual activity", "press & hold", "press and hold",
    "verify you are human", "verifying you are human", "checking your browser", "just a moment",
    "access is temporarily restricted", "access denied", "access to this page has been denied",
    "pardon our interruption", "request unsuccessful", "attention required", "you have been blocked",
    "403 forbidden", "error 1020", "bot detection", "captcha",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def skipped(url: str) -> bool:
    # Same test as enrich_articles._enrich_batch (it strips "www." with lstrip, which only differs
    # for hosts starting with w; none of SKIP_DOMAINS does).
    d = host_of(url)
    return any(d == s or d.endswith("." + s) for s in ea.SKIP_DOMAINS)


def candidates(hours: int, limit: int) -> list[dict]:
    """enrich_articles.py's selection, run on the read-only mirror."""
    for attempt in range(3):
        try:
            conn = sqlite3.connect(f"file:{PULSE_DB}?mode=ro", uri=True, timeout=30)
            conn.row_factory = sqlite3.Row
            rows = ea._get_items_to_enrich(conn, hours, limit * 4)
            ids = [r["id"] for r in rows]
            done = set()
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                q = f"SELECT id FROM items WHERE id IN ({','.join('?' * len(chunk))}) AND enrich_mode IS NOT NULL"
                done |= {r[0] for r in conn.execute(q, chunk)}
            conn.close()
            break
        except sqlite3.Error as e:  # the Dropbox mirror can be mid-sync
            log.warning(f"pulse.db mirror not readable ({e}); retry {attempt + 1}/3")
            time.sleep(20)
    else:
        raise RuntimeError("pulse.db mirror not readable")
    out, seen = [], set()
    n_done = n_skip = 0
    for r in rows:
        if r["id"] in done:
            n_done += 1
            continue
        if skipped(r["url"]):
            n_skip += 1
            continue
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        out.append(r)
        if len(out) >= limit:
            break
    log.info(f"{len(rows)} rows match enrich_articles.py's query ({hours}h); {n_done} already enriched, "
             f"{n_skip} in SKIP_DOMAINS; {len(out)} URLs to fetch")
    return out


def bot_wording(title: str, text: str) -> str:
    t = (title or "").lower()
    head = (text or "")[:1500].lower()
    for m in BOT_MARKERS:
        if m in t or (len(text or "") < 4000 and m in head):
            snippet = " ".join(((title or "") + " | " + (text or "")[:300]).split())
            return snippet[:300]
    return ""


async def fetch_all(items: list[dict], max_minutes: float) -> tuple[dict, dict, dict, dict]:
    results: dict[str, dict] = {}
    hosts: dict[str, dict] = {}
    blocked: dict[str, str] = {}
    counts = {"attempted": 0, "ok": 0, "empty": 0, "blocked": 0, "errors": 0, "skipped_blocked_host": 0,
              "left_for_time": 0}
    last_host_load: dict[str, float] = {}
    pending = list(items)
    deadline = time.monotonic() + max_minutes * 60
    async with CdpTab(max_loads=len(items) + 5) as tab:
        await tab.send("Page.setDownloadBehavior", {"behavior": "deny"})  # a PDF link must not save files
        while pending:
            if time.monotonic() > deadline:
                counts["left_for_time"] = len(pending)
                log.warning(f"time budget of {max_minutes:.0f} min reached; {len(pending)} URLs not fetched")
                break
            before = len(pending)
            pending = [it for it in pending if host_of(it["url"]) not in blocked]
            counts["skipped_blocked_host"] += before - len(pending)
            if not pending:
                break
            now = time.monotonic()
            pick = next((it for it in pending if now - last_host_load.get(host_of(it["url"]), -1e9) >= HOST_GAP), None)
            if pick is None:
                wait = min(HOST_GAP - (now - last_host_load.get(host_of(it["url"]), -1e9)) for it in pending)
                await asyncio.sleep(max(wait, 0.2))
                continue
            pending.remove(pick)
            url, host = pick["url"], host_of(pick["url"])
            h = hosts.setdefault(host, {"ok": 0, "blocked": 0, "empty": 0})
            counts["attempted"] += 1
            try:
                info = await asyncio.wait_for(
                    tab.goto(url, settle=3, ready="document.readyState !== 'loading'", timeout=20), LOAD_TIMEOUT)
                await asyncio.sleep(2)  # late-rendering bodies, as enrich_articles.py waits 4 s after DOMContentLoaded
                title = await tab.eval("document.title") or info.get("title") or ""
                text = await tab.eval("document.body ? document.body.innerText : ''") or ""
                final = host_of(await tab.eval("location.href") or url)
                if final and final != host:  # a tracking link landed on another host: space and block that one too
                    last_host_load[final] = time.monotonic()
                why = bot_wording(title, text)
                if why:
                    h["blocked"] += 1
                    counts["blocked"] += 1
                    blocked[host] = why
                    if final and final != host and final != "about:blank":
                        blocked[final] = why
                    log.warning(f"BLOCKED {host}{' -> ' + final if final != host else ''}: {why[:200]} "
                                f"(no more loads to that host this run)")
                    continue
                html = await asyncio.wait_for(tab.eval("document.documentElement.outerHTML"), 30) or ""
                body = ea._extract_article_text(html)
                if len(body) < OK_MIN:
                    h["empty"] += 1
                    counts["empty"] += 1
                    log.info(f"  EMPTY ({len(body)}c) {host}: {(pick.get('title') or '')[:60]}")
                    continue
                body = body[:BODY_MAX]
                results[url] = {"body": body, "title": title.strip() or (pick.get("title") or ""),
                                "fetched_at": now_iso(), "mode": "server_chrome"}
                h["ok"] += 1
                counts["ok"] += 1
                log.info(f"  OK ({len(body)}c) {host}: {(pick.get('title') or '')[:60]}")
            except LoadBudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001
                h["empty"] += 1
                counts["errors"] += 1
                log.warning(f"  FAIL {host}: {type(e).__name__}: {str(e)[:120]}")
                try:  # a stuck dialog or navigation: dismiss and move on
                    await asyncio.wait_for(tab.send("Page.handleJavaScriptDialog", {"accept": True}), 5)
                except Exception:  # noqa: BLE001
                    pass
            finally:
                last_host_load[host] = time.monotonic()
        try:
            await asyncio.wait_for(tab.send("Page.navigate", {"url": "about:blank"}), 10)
        except Exception:  # noqa: BLE001
            pass
    return results, hosts, blocked, counts


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--max-minutes", type=float, default=40,
                   help="stop loading after this long so the file is ready before the 11:00 UTC synthesis")
    p.add_argument("--dry-run", action="store_true", help="list candidates only; no page loads, no files")
    args = p.parse_args()

    started = now_iso()
    log.info(f"=== server-Chrome enrichment (last {args.hours}h, limit {args.limit}) ===")
    items = candidates(args.hours, args.limit)
    if args.dry_run:
        by_host: dict[str, int] = {}
        for it in items:
            by_host[host_of(it["url"])] = by_host.get(host_of(it["url"]), 0) + 1
        for hst, n in sorted(by_host.items(), key=lambda kv: -kv[1])[:40]:
            log.info(f"  {n:4d} {hst}")
        return 0

    code = 0
    results, hosts, blocked = {}, {}, {}
    counts = {"attempted": 0, "ok": 0, "empty": 0, "blocked": 0, "errors": 0}
    error = ""
    try:
        results, hosts, blocked, counts = asyncio.run(fetch_all(items, args.max_minutes))
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {str(e)[:200]}"
        log.error(f"run failed: {error}")
        code = 1

    try:
        old = json.loads(OUT_PATH.read_text()).get("items", {})
    except (OSError, ValueError):
        old = {}
    keep_from = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    merged = {u: v for u, v in old.items() if (v.get("fetched_at") or "") >= keep_from}
    merged.update(results)
    finished = now_iso()
    run = {"started_at": started, "finished_at": finished, "hours": args.hours, "limit": args.limit,
           "candidates": len(items), **counts, "error": error}
    write_json(OUT_PATH, {"generated_at": finished, "run": run, "hosts": hosts, "blocked": blocked,
                          "items": merged})
    write_json(STATUS_PATH, {"generated_at": finished, **run, "blocked_hosts": blocked,
                             "items_in_file": len(merged), "file": str(OUT_PATH)})
    log.info(f"wrote {OUT_PATH}: {len(results)} new bodies, {len(merged)} in file (last {KEEP_DAYS} days)")
    for hst, c in sorted(hosts.items(), key=lambda kv: -sum(kv[1].values())):
        log.info(f"  host {hst}: ok={c['ok']} blocked={c['blocked']} empty={c['empty']}")
    log.info(f"done: {counts.get('ok', 0)} ok, {counts.get('empty', 0)} empty, {counts.get('blocked', 0)} blocked "
             f"of {counts.get('attempted', 0)} loads; blocked hosts: {', '.join(blocked) or 'none'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
