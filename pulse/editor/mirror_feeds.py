"""Mirror the substack.com-hosted competitor feeds from the droplet.

Substack's own domain answers HTTP 403 to GitHub Actions and to Browserbase, so
17 of the 45 configured newsletters never produced an item (every run since at
least 2026-06-18). The droplet is not blocked. This script fetches each feed
whose host ends in .substack.com and writes it to FEEDS_DIR/<slug>.xml, which
Caddy serves at https://noon.homeeconomics.us/feeds/<slug>.xml; the collector
(collectors/rss_substacks.py) reads the mirror first when SUBSTACK_MIRROR_BASE
is set. Runs hourly from noon-feeds.timer. A failed fetch keeps the previous
file; index.json records the status and time of every feed.

Fetches with urllib on purpose: from this droplet Cloudflare answers httpx with a
bot challenge (403, cf-mitigated: challenge) but serves urllib and curl the feed.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from config import COMPETITOR_SUBSTACKS  # noqa: E402

FEEDS_DIR = Path(os.environ.get("NOON_FEEDS_DIR", "/home/aziz/work/noon/feeds"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 NewsAtNoon/1.0")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("noon.feeds")


def slug_for(feed_url: str) -> str | None:
    host = urlsplit(feed_url).netloc.lower()
    return host.split(".")[0] if host.endswith(".substack.com") else None


def main() -> int:
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(FEEDS_DIR, 0o755)
    index_path = FEEDS_DIR / "index.json"
    index = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text()).get("feeds", {})
        except Exception:
            index = {}
    targets = [(n, u, slug_for(u)) for n, u in COMPETITOR_SUBSTACKS if slug_for(u)]
    ok = 0
    headers = {"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"}
    if True:
        for name, url, slug in targets:
            now = datetime.now(timezone.utc).isoformat()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
                    status, body = r.status, r.read()
                looks_like_feed = status == 200 and len(body) > 500 and (b"<rss" in body[:4000] or b"<feed" in body[:4000])
                if not looks_like_feed:
                    raise RuntimeError(f"HTTP {status}, {len(body)} bytes")
                tmp = FEEDS_DIR / f"{slug}.xml.tmp"
                tmp.write_bytes(body)
                os.chmod(tmp, 0o644)
                tmp.replace(FEEDS_DIR / f"{slug}.xml")
                index[slug] = {"name": name, "url": url, "ok": True, "fetched_at": now, "bytes": len(body)}
                ok += 1
            except Exception as e:  # noqa: BLE001
                prev = index.get(slug, {})
                index[slug] = {"name": name, "url": url, "ok": False, "error": str(e)[:200],
                               "failed_at": now, "fetched_at": prev.get("fetched_at")}
                log.warning(f"{name}: {e}")
            time.sleep(0.5)
    index_path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                      "feeds": index}, indent=1))
    os.chmod(index_path, 0o644)
    log.info(f"mirrored {ok} of {len(targets)} substack.com feeds into {FEEDS_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
