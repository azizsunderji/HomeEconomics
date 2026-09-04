"""LinkedIn collector via Apify (harvestapi/linkedin-profile-posts).

Scrapes recent posts from a curated list of LinkedIn profiles and company
pages (collectors/linkedin_targets.json) and returns them as PulseItems with
source="linkedin". No LinkedIn login or cookies are used; the actor reads
public posts only.

Cost: about $0.0015 per post returned (pay-per-event, "from $1.50 / 1,000").
A run over 40 targets with maxPosts=5 and a 24h window returns well under
100 posts on a normal day, so a few cents per run.

Requires APIFY_API_KEY in the environment (same key the Twitter collector uses).

Standalone use (writes into the pipeline DB the same way run_pipeline does):
    python -m collectors.linkedin_apify --posted-limit week --dry-run
    python -m collectors.linkedin_apify --posted-limit 24h
Env overrides: LINKEDIN_TARGETS (path to json), LINKEDIN_POSTED_LIMIT
(24h|week|month), LINKEDIN_MAX_POSTS (per target), LINKEDIN_MAX_TARGETS.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from collectors import PulseItem, record_collector_error

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "harvestapi/linkedin-profile-posts"
TARGETS_PATH = Path(os.environ.get(
    "LINKEDIN_TARGETS", str(Path(__file__).parent / "linkedin_targets.json")))
POSTED_LIMIT = os.environ.get("LINKEDIN_POSTED_LIMIT", "24h")   # 24h | week | month
MAX_POSTS = int(os.environ.get("LINKEDIN_MAX_POSTS", "5"))       # per target per run
MAX_TARGETS = int(os.environ.get("LINKEDIN_MAX_TARGETS", "80"))  # hard cap per run
RUN_TIMEOUT_S = 300


def load_targets(path: Path = TARGETS_PATH) -> list[dict]:
    """[{"url": "https://www.linkedin.com/in/...", "name": "...", "note": "..."}, ...]"""
    if not path.exists():
        logger.warning(f"LinkedIn targets file missing: {path}")
        return []
    data = json.loads(path.read_text())
    targets = data.get("targets", data) if isinstance(data, dict) else data
    out = []
    for t in targets:
        url = (t.get("url") if isinstance(t, dict) else t) or ""
        url = url.strip()
        if url.startswith("https://www.linkedin.com/"):
            out.append({"url": url, "name": (t.get("name") if isinstance(t, dict) else "") or ""})
    return out[:MAX_TARGETS]


def _run_actor(api_key: str, target_urls: list[str], posted_limit: str, max_posts: int) -> list[dict]:
    """Run the actor synchronously and return dataset items."""
    url = f"{APIFY_BASE}/acts/{ACTOR_ID.replace('/', '~')}/run-sync-get-dataset-items"
    payload = {
        "targetUrls": target_urls,
        "maxPosts": max_posts,
        "postedLimit": posted_limit,
        "includeReposts": False,      # a bare repost carries no text of the author's own
        "includeQuotePosts": True,
        "scrapeReactions": False,
        "scrapeComments": False,
    }
    with httpx.Client(timeout=RUN_TIMEOUT_S + 30) as client:
        r = client.post(url, params={"timeout": RUN_TIMEOUT_S},
                        headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        r.raise_for_status()
        data = r.json()
    return data if isinstance(data, list) else []


def _to_item(post: dict) -> PulseItem | None:
    content = (post.get("content") or "").strip()
    post_id = str(post.get("id") or post.get("entityId") or "").strip()
    url = (post.get("linkedinUrl") or "").strip()
    if not content or not post_id or not url:
        return None
    author = post.get("author") or {}
    name = (author.get("name") or "").strip()
    eng = post.get("engagement") or {}
    likes = int(eng.get("likes") or 0)
    comments = int(eng.get("comments") or 0)
    shares = int(eng.get("shares") or 0)
    ts = (post.get("postedAt") or {}).get("timestamp")
    published = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None
    return PulseItem(
        source="linkedin",
        source_id=f"li_{post_id}",
        url=url,
        title=content[:200],
        body=content,
        author=name,                       # a display name, not a handle
        published_at=published,
        score=likes + shares,
        num_comments=comments,
        engagement_raw={
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "reactions": eng.get("reactions") or [],
            "author_url": author.get("linkedinUrl") or "",
            "author_type": author.get("type") or "",
            "author_info": author.get("info") or "",
            "post_type": post.get("type") or "",
            "target_url": (post.get("query") or {}).get("targetUrl") or "",
            "is_conversation": comments >= 20,
        },
    )


def collect(target_urls: list[str] | None = None, posted_limit: str = POSTED_LIMIT,
            max_posts: int = MAX_POSTS) -> list[PulseItem]:
    api_key = os.environ.get("APIFY_API_KEY", "")
    if not api_key:
        raise RuntimeError("APIFY_API_KEY not set — LinkedIn collection skipped entirely")
    urls = target_urls or [t["url"] for t in load_targets()]
    if not urls:
        logger.warning("No LinkedIn targets — nothing to collect")
        return []
    try:
        posts = _run_actor(api_key, urls, posted_limit, max_posts)
    except Exception as e:  # noqa: BLE001
        record_collector_error("linkedin", e, "actor run")
        raise
    items, skipped = [], 0
    for p in posts:
        it = _to_item(p)
        if it is None:
            skipped += 1
            continue
        items.append(it)
    logger.info(f"LinkedIn: {len(urls)} targets, {len(posts)} posts returned, "
                f"{len(items)} items, {skipped} skipped (no text/id)")
    return items


def _main() -> None:
    ap = argparse.ArgumentParser(description="Collect LinkedIn posts into the Pulse DB")
    ap.add_argument("--posted-limit", default=POSTED_LIMIT, choices=["24h", "week", "month"])
    ap.add_argument("--max-posts", type=int, default=MAX_POSTS)
    ap.add_argument("--targets", default=None, help="path to targets json (default: collectors/linkedin_targets.json)")
    ap.add_argument("--dry-run", action="store_true", help="print items, do not write to the DB")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    urls = [t["url"] for t in load_targets(Path(a.targets))] if a.targets else None
    items = collect(urls, a.posted_limit, a.max_posts)
    for it in items:
        print(f"{(it.published_at or datetime.min).strftime('%Y-%m-%d')}  {it.author:32.32s}  "
              f"{it.score:>4} {it.num_comments:>3}  {it.title[:90]!r}")
    if a.dry_run:
        print(f"[dry run] {len(items)} items, nothing written")
        return
    from store import get_db, bulk_upsert, log_collection_start, log_collection_end
    conn = get_db()
    run_id = log_collection_start(conn, "linkedin")
    new, dupe = bulk_upsert(conn, items)
    log_collection_end(conn, run_id, len(items), new, dupe)
    print(f"linkedin: {new} new, {dupe} dupes (of {len(items)}) written to the DB")


if __name__ == "__main__":
    _main()
