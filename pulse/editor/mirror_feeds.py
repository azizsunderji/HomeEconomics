"""Mirror the substack.com-hosted competitor feeds, and every OPML feed, from the droplet.

Substack's own domain answers HTTP 403 to GitHub Actions and to Browserbase, so
17 of the 45 configured newsletters never produced an item (every run since at
least 2026-06-18). The droplet is not blocked. This script fetches each feed
whose host ends in .substack.com and writes it to FEEDS_DIR/<slug>.xml, which
Caddy serves at https://noon.homeeconomics.us/feeds/<slug>.xml; the collector
(collectors/rss_substacks.py) reads the mirror first when SUBSTACK_MIRROR_BASE
is set. Runs hourly from noon-feeds.timer. A failed fetch keeps the previous
file; index.json records the status and time of every feed.

2026-09-05: also mirrors every feed in the RSS OPML (pulse/data/Feeds.opml) as
f_<sha1(xmlUrl)[:12]>.xml. The RSS collector (collectors/rss_feeds.py) fetches
directly first and reads the mirror only when a feed answers non-200 from
GitHub Actions (14 did, among them the housing journals and Inman). Three
feeds block the droplet too (Century 21, both Wiley journals) and stay dark.

2026-09-08: Google News section searches (GNEWS_SEARCHES) are published as plain
RSS files gn_<slug>.xml with every link decoded to the publisher's URL (the
googlenewsdecoder needs Google, which GitHub Actions cannot reach). The OPML
lists the mirror URL directly, so the RSS collector treats it as an ordinary
feed. First use: the Journal's real estate section, which has no RSS feed and
which Brave Search does not index. Decoded links are cached in gnews_cache.json.

Fetches with urllib on purpose: from this droplet Cloudflare answers httpx with a
bot challenge (403, cf-mitigated: challenge) but serves urllib and curl the feed.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from config import COMPETITOR_SUBSTACKS  # noqa: E402
from collectors.rss_feeds import parse_opml, DEFAULT_OPML_PATH  # noqa: E402

# (file slug, feed title, Google News query). Newest GNEWS_MAX items are decoded per run.
GNEWS_SEARCHES = [
    ("wsj-real-estate", "WSJ > Real Estate (via Google News)", "site:wsj.com/real-estate"),
]
GNEWS_MAX = 25

FEEDS_DIR = Path(os.environ.get("NOON_FEEDS_DIR", "/home/aziz/work/noon/feeds"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 NewsAtNoon/1.0")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("noon.feeds")


def opml_slug(feed_url: str) -> str:
    """Mirror file name for an OPML feed; rss_feeds.py computes the same."""
    return "f_" + hashlib.sha1(feed_url.strip().encode()).hexdigest()[:12]


def _gnews_feed(slug: str, title: str, query: str, cache: dict) -> tuple[bool, str]:
    """Fetch a Google News RSS search, decode its links, write gn_<slug>.xml. Returns (ok, note)."""
    import feedparser
    from urllib.parse import quote
    from googlenewsdecoder import new_decoderv1
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=25) as r:
        raw = r.read()
    parsed = feedparser.parse(raw)
    entries = list(parsed.entries)[:GNEWS_MAX]
    out_items, decoded_now = [], 0
    for e in entries:
        link = e.get("link", "")
        real = cache.get(link)
        if not real and "news.google.com" in link:
            try:
                res = new_decoderv1(link, interval=1)
                real = res.get("decoded_url") if res.get("status") else None
                decoded_now += 1
            except Exception as ex:  # noqa: BLE001
                log.warning(f"gnews decode failed: {ex}")
                real = None
            if real:
                cache[link] = real
        if not real:
            continue
        t = html.escape((e.get("title") or "").rsplit(" - ", 1)[0].strip())
        d = html.escape(e.get("published") or "")
        desc = html.escape(re.sub(r"<[^>]+>", "", e.get("summary") or "")[:500])
        out_items.append(f"<item><title>{t}</title><link>{html.escape(real)}</link><guid>{html.escape(real)}</guid>"
                         f"<pubDate>{d}</pubDate><description>{desc}</description></item>")
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><rss version=\"2.0\"><channel>"
           f"<title>{html.escape(title)}</title><link>https://noon.homeeconomics.us/feeds/gn_{slug}.xml</link>"
           f"<description>Google News search: {html.escape(query)}; links decoded on the droplet</description>"
           + "".join(out_items) + "</channel></rss>").encode()
    tmp = FEEDS_DIR / f"gn_{slug}.xml.tmp"
    tmp.write_bytes(xml)
    os.chmod(tmp, 0o644)
    tmp.replace(FEEDS_DIR / f"gn_{slug}.xml")
    return bool(out_items), f"{len(entries)} entries, {len(out_items)} with decoded links ({decoded_now} decoded this run)"


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
    try:
        seen: set[str] = set()
        for f in parse_opml(DEFAULT_OPML_PATH):
            # Not deduped against the substack.com targets: an OPML feed that
            # shares a URL with one (Calculated Risk) must exist under its f_ slug
            # too, because rss_feeds.py looks it up by that name.
            if f["url"] not in seen:
                targets.append((f["title"], f["url"], opml_slug(f["url"])))
                seen.add(f["url"])
    except Exception as e:  # noqa: BLE001
        log.warning(f"OPML not mirrored: {e}")
    ok = 0
    headers = {"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"}
    if True:
        for name, url, slug in targets:
            now = datetime.now(timezone.utc).isoformat()
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
                    status, body = r.status, r.read()
                head = body[:4000]
                looks_like_feed = status == 200 and len(body) > 100 and (b"<rss" in head or b"<feed" in head or b"<rdf:RDF" in head)
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
            time.sleep(0.3)
    # Google News section searches, decoded on the droplet.
    cache_path = FEEDS_DIR / "gnews_cache.json"
    try:
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    except Exception:
        cache = {}
    for slug, title, query in GNEWS_SEARCHES:
        now = datetime.now(timezone.utc).isoformat()
        try:
            good, note = _gnews_feed(slug, title, query, cache)
            index[f"gn_{slug}"] = {"name": title, "url": query, "ok": good, "fetched_at": now, "note": note}
            log.info(f"{title}: {note}")
            ok += int(good)
        except Exception as e:  # noqa: BLE001
            index[f"gn_{slug}"] = {"name": title, "url": query, "ok": False, "error": str(e)[:200], "failed_at": now}
            log.warning(f"{title}: {e}")
    if len(cache) > 2000:
        cache = dict(list(cache.items())[-1500:])
    cache_path.write_text(json.dumps(cache))
    os.chmod(cache_path, 0o600)
    index_path.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                      "feeds": index}, indent=1))
    os.chmod(index_path, 0o644)
    log.info(f"mirrored {ok} of {len(targets)} feeds into {FEEDS_DIR}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
