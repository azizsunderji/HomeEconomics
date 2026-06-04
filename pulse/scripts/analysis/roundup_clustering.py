"""V2 roundup generation via bottom-up clustering.

The v1 synth picks roundup titles first and then pads them with adjacent
voices, producing welded slop ("Multifamily Construction Collapse in the
Northeast" with SF inclusionary zoning + Berkeley adaptive reuse mixed
in). The fix is to invert: cluster items by semantic similarity first,
then write a roundup using ONLY each cluster's members.

Workflow:
    1. Pull items from the last N hours (default 24h)
    2. Filter: drop quote/reply/short tweets; aggregate threads
    3. Embed via OpenAI text-embedding-3-small (~$0.06/run, ~40s)
    4. HDBSCAN cluster (cosine, min_cluster_size=3)
    5. For each cluster: Haiku check "is this housing/real-estate/urban?"
    6. For each surviving cluster: Opus writes a roundup using ONLY that
       cluster's items. Deterministic post-check: every cited URL must be
       in the cluster's member list, else reject and skip.
    7. Cap at top-K roundups by cluster size.

Not wired into production yet — used by tools/v2_runner.py and
optionally by a parallel pulse-synth-v2 workflow.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ── Tunables ─────────────────────────────────────────────────────────

DEFAULT_LOOKBACK_HOURS = 24
MIN_CLUSTER_SIZE = 3
MAX_ROUNDUPS = 5

OPENAI_EMBED_MODEL = "text-embedding-3-small"
HOUSING_CHECK_MODEL = "claude-haiku-4-5"
ROUNDUP_WRITE_MODEL = "claude-opus-4-7"  # Opus is expensive; tune to taste

# Filter thresholds
MIN_SUBSTANTIVE_WORDS = 20
THREAD_AGGREGATION_WINDOW_MIN = 10  # consecutive same-author tweets within this gap

_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "from",
    "with", "by", "as", "is", "was", "are", "be", "but", "at", "it", "this",
    "that", "i", "you", "we", "they", "he", "she", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "can", "if", "so", "not", "no", "yes", "all", "some", "any",
}


# ── Data types ───────────────────────────────────────────────────────

@dataclass
class CorpusItem:
    """One unit fed into the clustering pipeline. May be a single article,
    a single substantive tweet, or an aggregated thread."""
    id: int
    source: str  # twitter/bluesky/rss/gmail/substack/...
    url: str
    title: str
    body: str
    author: str
    published_at: str
    feed_name: str
    # Thread aggregation tracks the merged ids so we can record citation
    # provenance + look up member URLs.
    merged_ids: list[int] = field(default_factory=list)
    merged_urls: list[str] = field(default_factory=list)


@dataclass
class Cluster:
    """A HDBSCAN cluster of CorpusItems."""
    cluster_id: int
    items: list[CorpusItem]

    @property
    def size(self) -> int:
        return len(self.items)

    @property
    def all_urls(self) -> set[str]:
        """All URLs (item URL + merged URLs from threads) that may be cited."""
        urls: set[str] = set()
        for it in self.items:
            if it.url:
                urls.add(it.url)
            for u in it.merged_urls:
                if u:
                    urls.add(u)
        return urls


@dataclass
class Roundup:
    """A v2-generated roundup ready to drop into a briefing JSON."""
    topic: str
    summary: str
    cluster_id: int
    cluster_size: int
    cited_urls: list[str] = field(default_factory=list)


# ── Stage 1: corpus loading + filtering ──────────────────────────────

def _count_substantive_words(text: str) -> int:
    """Word count after stripping URLs, @mentions, hashtags, stop-words."""
    if not text:
        return 0
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"#\w+", " ", text)
    words = re.findall(r"[A-Za-z]+", text.lower())
    return sum(1 for w in words if len(w) >= 3 and w not in _STOP_WORDS)


def _is_quote_or_reply_tweet(item: dict) -> bool:
    """Heuristic: drop tweets that are mostly quoting/replying without a
    substantive original take. Conservative — only filters obvious cases."""
    if item.get("source") not in ("twitter", "bluesky"):
        return False
    body = (item.get("body") or "").strip()
    # Replies typically start with one or more @handles
    if re.match(r"^(@\w+\s+){1,5}", body):
        return True
    # Quote-RT pattern (rare on modern Twitter but still seen)
    if body.startswith("RT @"):
        return True
    # Very short tweets that are mostly a URL → drop (low signal)
    text_no_url = re.sub(r"https?://\S+", "", body).strip()
    if len(text_no_url) < 30 and "http" in body:
        return True
    return False


def load_corpus(conn: sqlite3.Connection, hours: int = DEFAULT_LOOKBACK_HOURS,
                end: Optional[datetime] = None) -> list[CorpusItem]:
    """Load items from the items table, aggregate threads, filter noise.

    `end` defaults to now (UTC). Window is [end - hours, end].

    Returns a list of CorpusItem ready for embedding.
    """
    end_dt = end or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(hours=hours)
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()

    rows = conn.execute(
        """SELECT id, source, url, title, body, author, published_at, feed_name
           FROM items
           WHERE published_at >= ? AND published_at <= ?
           ORDER BY published_at ASC""",
        (start_iso, end_iso),
    ).fetchall()
    items = [dict(r) if isinstance(r, sqlite3.Row) else
             {"id": r[0], "source": r[1], "url": r[2], "title": r[3],
              "body": r[4], "author": r[5], "published_at": r[6],
              "feed_name": r[7]}
             for r in rows]
    logger.info(f"loaded {len(items)} raw items in last {hours}h")

    # Aggregate threads: consecutive same-author tweets/bsky posts within
    # THREAD_AGGREGATION_WINDOW_MIN of each other become one synthetic item.
    items = _aggregate_threads(items)
    logger.info(f"after thread aggregation: {len(items)} units")

    # Filter
    kept: list[CorpusItem] = []
    drop_replies = 0
    drop_short = 0
    drop_empty = 0
    for it in items:
        body = (it.get("body") or "").strip()
        if not body and not (it.get("title") or "").strip():
            drop_empty += 1
            continue
        if _is_quote_or_reply_tweet(it):
            drop_replies += 1
            continue
        combined = f"{it.get('title') or ''} {body}"
        if _count_substantive_words(combined) < MIN_SUBSTANTIVE_WORDS:
            drop_short += 1
            continue
        kept.append(CorpusItem(
            id=it["id"],
            source=it.get("source", ""),
            url=it.get("url", "") or "",
            title=(it.get("title") or "").strip(),
            body=body,
            author=(it.get("author") or "").strip(),
            published_at=it.get("published_at", "") or "",
            feed_name=(it.get("feed_name") or "").strip(),
            merged_ids=it.get("_merged_ids", []),
            merged_urls=it.get("_merged_urls", []),
        ))

    logger.info(
        f"filter: dropped {drop_empty} empty, {drop_replies} reply/quote, "
        f"{drop_short} short. kept={len(kept)}"
    )
    return kept


def _aggregate_threads(items: list[dict]) -> list[dict]:
    """Merge consecutive same-author tweets within THREAD_AGGREGATION_WINDOW_MIN.

    Returns a list of dicts where merged items carry _merged_ids and
    _merged_urls. The anchor (earliest) tweet retains its id/url; the
    body is concatenated.
    """
    if not items:
        return []

    # Group by (source, author), sort within each group by published_at,
    # walk through and aggregate adjacent items within the window.
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    other: list[dict] = []
    for it in items:
        src = it.get("source", "")
        author = (it.get("author") or "").strip()
        if src in ("twitter", "bluesky") and author:
            groups[(src, author)].append(it)
        else:
            other.append(it)

    aggregated: list[dict] = []
    for key, group in groups.items():
        group.sort(key=lambda x: x.get("published_at") or "")
        i = 0
        while i < len(group):
            anchor = dict(group[i])
            anchor["_merged_ids"] = [anchor["id"]]
            anchor["_merged_urls"] = [anchor.get("url") or ""]
            anchor_body = anchor.get("body") or ""
            anchor_time = _parse_dt(anchor.get("published_at"))
            j = i + 1
            while j < len(group):
                cand = group[j]
                cand_time = _parse_dt(cand.get("published_at"))
                if anchor_time is None or cand_time is None:
                    break
                gap_min = (cand_time - anchor_time).total_seconds() / 60.0
                if gap_min > THREAD_AGGREGATION_WINDOW_MIN:
                    break
                # Merge
                anchor_body = (anchor_body + " ↪ " + (cand.get("body") or "")).strip()
                anchor["_merged_ids"].append(cand["id"])
                anchor["_merged_urls"].append(cand.get("url") or "")
                anchor_time = cand_time
                j += 1
            anchor["body"] = anchor_body
            aggregated.append(anchor)
            i = j

    return aggregated + other


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


# ── Stage 2: embedding ────────────────────────────────────────────────

def embed_corpus(items: list[CorpusItem],
                 openai_client=None) -> np.ndarray:
    """Embed each item via OpenAI text-embedding-3-small. Returns
    L2-normalized matrix shape (N, 1536)."""
    if not items:
        return np.zeros((0, 1536), dtype=np.float32)
    if openai_client is None:
        from openai import OpenAI
        openai_client = OpenAI()

    texts = []
    for it in items:
        body = (it.body or "")[:1500]
        texts.append(f"{it.title}\n\n{body}".strip() or " ")

    all_embs: list[list[float]] = []
    BATCH = 128
    t0 = time.time()
    for i in range(0, len(texts), BATCH):
        batch = [t[:8000] for t in texts[i:i + BATCH]]
        resp = openai_client.embeddings.create(model=OPENAI_EMBED_MODEL,
                                               input=batch)
        all_embs.extend([d.embedding for d in resp.data])
    embs = np.array(all_embs, dtype=np.float32)
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    embs = embs / np.clip(norms, 1e-8, None)
    logger.info(f"embedded {len(items)} items in {time.time() - t0:.1f}s, "
                f"shape {embs.shape}")
    return embs


# ── Stage 3: clustering ──────────────────────────────────────────────

def cluster_items(items: list[CorpusItem], embs: np.ndarray,
                  min_cluster_size: int = MIN_CLUSTER_SIZE) -> list[Cluster]:
    """HDBSCAN cluster using cosine distance. Returns list of Cluster
    objects, sorted by size descending. Items in noise (-1) are dropped."""
    if len(items) < min_cluster_size:
        return []
    import hdbscan
    from sklearn.metrics.pairwise import cosine_distances

    dist = cosine_distances(embs).astype(np.float64)
    np.fill_diagonal(dist, 0.0)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        metric="precomputed",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(dist)

    from collections import defaultdict
    by_cluster: dict[int, list[CorpusItem]] = defaultdict(list)
    for i, lab in enumerate(labels):
        if lab == -1:
            continue
        by_cluster[int(lab)].append(items[i])

    clusters = [Cluster(cluster_id=cid, items=members)
                for cid, members in by_cluster.items()]
    clusters.sort(key=lambda c: -c.size)
    logger.info(
        f"clustered into {len(clusters)} clusters "
        f"(largest={clusters[0].size if clusters else 0}, "
        f"noise={(labels == -1).sum()})"
    )
    return clusters


# ── Stage 4: housing-relevance filter ────────────────────────────────

HOUSING_CHECK_SYSTEM = (
    "You are filtering clusters of news/social items for a daily housing"
    "-economics briefing. A cluster is 'housing-relevant' if its core topic"
    " is materially about: U.S. or international housing markets, real"
    " estate (sales, prices, supply, demand), rental markets, mortgages,"
    " homebuilding, zoning/land-use, urbanism/cities, affordability,"
    " homelessness, property insurance, brokerages/MLS, housing policy,"
    " or housing-adjacent demographics/migration. NOT housing-relevant:"
    " general politics, foreign policy, sports, entertainment, AI"
    " companies/tech IPOs (unless about housing/real-estate impact),"
    " general macroeconomics without a housing through-line. Reply with a"
    " single word: YES or NO."
)


def is_housing_relevant(cluster: Cluster, anthropic_client=None) -> bool:
    """Single Haiku call asking if this cluster's content is housing-related."""
    if anthropic_client is None:
        from anthropic import Anthropic
        anthropic_client = Anthropic()
    samples = []
    for it in cluster.items[:8]:
        title = it.title[:140]
        snip = (it.body or "")[:280].replace("\n", " ")
        samples.append(f"- {title}\n  {snip}")
    user = "Cluster items:\n" + "\n".join(samples) + "\n\nHousing-relevant? Reply YES or NO."
    try:
        resp = anthropic_client.messages.create(
            model=HOUSING_CHECK_MODEL,
            max_tokens=4,
            system=HOUSING_CHECK_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = (resp.content[0].text or "").strip().upper()
        return text.startswith("Y")
    except Exception as e:
        logger.warning(f"housing-check failed for cluster {cluster.cluster_id}: {e}")
        return False


# ── Stage 5: write roundups (constrained to cluster members) ─────────

ROUNDUP_WRITE_SYSTEM = """You are writing one paragraph for a daily housing-economics briefing.

You will be given a tight cluster of items (articles, tweets, newsletters) that an automated clustering system grouped because they engage the same specific question or event. Your job: write ONE roundup paragraph that describes what these voices are collectively saying.

HARD RULES:
1. ONLY cite items from the provided cluster. Do NOT invent or cite anything else.
2. EVERY attribution must be wrapped in a markdown link [text](url). Use the URLs provided in the cluster items.
3. Use multiple short paragraphs (`\\n\\n` between them) when you have 3+ distinct voices or sub-points. Each paragraph 1-3 sentences max.
4. No bridge words at paragraph starts ("Separately,", "Meanwhile,", "Moreover,", etc.). Blank line is the transition.
5. No editorializing. State what each voice argued; do not synthesize a meta-take.
6. If after honest assessment the cluster doesn't cohere around a single specific question/event, output exactly: `INCOHERENT`
7. Produce a TOPIC label (5-10 words, specific) — describe the actual question/event the items engage.

OUTPUT FORMAT (strict JSON):
{
  "topic": "<5-10 word specific label>",
  "summary": "<one or more short paragraphs with inline [text](url) markdown links>"
}
"""


def _format_cluster_for_writer(cluster: Cluster) -> str:
    """Render the cluster's items as a structured list for the model."""
    lines = []
    for i, it in enumerate(cluster.items, 1):
        bits = [
            f"[{i}] source={it.source}",
            f"author={it.author or '?'}",
            f"feed={it.feed_name or '?'}",
        ]
        header = " · ".join(bits)
        body_snip = (it.body or "")[:600].replace("\n", " ")
        lines.append(f"{header}\nurl: {it.url}\ntitle: {it.title}\nbody: {body_snip}")
    return "\n\n".join(lines)


def write_roundup(cluster: Cluster, anthropic_client=None) -> Optional[Roundup]:
    """Run the constrained writer for one cluster. Returns None if model
    says INCOHERENT or output is invalid."""
    if anthropic_client is None:
        from anthropic import Anthropic
        anthropic_client = Anthropic()

    user = f"Cluster items:\n\n{_format_cluster_for_writer(cluster)}"
    try:
        resp = anthropic_client.messages.create(
            model=ROUNDUP_WRITE_MODEL,
            max_tokens=800,
            system=ROUNDUP_WRITE_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:
        logger.warning(f"writer failed for cluster {cluster.cluster_id}: {e}")
        return None

    text = (resp.content[0].text or "").strip()
    if text == "INCOHERENT" or text.startswith("INCOHERENT"):
        logger.info(f"cluster {cluster.cluster_id} flagged INCOHERENT, skipping")
        return None

    # Strip optional ```json fence
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(f"cluster {cluster.cluster_id} non-JSON output: {e}; "
                       f"raw[:200]={text[:200]!r}")
        return None

    topic = (data.get("topic") or "").strip()
    summary = (data.get("summary") or "").strip()
    if not topic or not summary:
        logger.warning(f"cluster {cluster.cluster_id} missing topic/summary")
        return None

    # Validate URLs: every cited [text](url) must be in cluster.all_urls
    member_urls = cluster.all_urls
    cited = re.findall(r"\[[^\]]+\]\(([^)]+)\)", summary)
    bad = [u for u in cited if u not in member_urls]
    if bad:
        logger.warning(
            f"cluster {cluster.cluster_id} cited {len(bad)} URLs not in member list, "
            f"rejecting. bad[:3]={bad[:3]}"
        )
        return None

    return Roundup(
        topic=topic,
        summary=summary,
        cluster_id=cluster.cluster_id,
        cluster_size=cluster.size,
        cited_urls=cited,
    )


# ── Top-level orchestrator ────────────────────────────────────────────

def cluster_and_write_roundups(
    conn: sqlite3.Connection,
    hours_lookback: int = DEFAULT_LOOKBACK_HOURS,
    end: Optional[datetime] = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    max_roundups: int = MAX_ROUNDUPS,
    openai_client=None,
    anthropic_client=None,
    debug_dir: Optional[str] = None,
) -> tuple[list[Roundup], dict]:
    """Run the full pipeline. Returns (roundups, stats_dict)."""
    stats: dict = {
        "lookback_hours": hours_lookback,
        "min_cluster_size": min_cluster_size,
        "max_roundups": max_roundups,
    }

    logger.info("=== v2 roundup pipeline ===")
    t0 = time.time()
    items = load_corpus(conn, hours=hours_lookback, end=end)
    stats["items_after_filter"] = len(items)
    if len(items) < min_cluster_size:
        logger.warning("not enough items to cluster")
        return [], stats

    embs = embed_corpus(items, openai_client=openai_client)
    stats["embed_seconds"] = round(time.time() - t0, 1)

    clusters = cluster_items(items, embs, min_cluster_size=min_cluster_size)
    stats["clusters_total"] = len(clusters)
    stats["largest_cluster_size"] = clusters[0].size if clusters else 0

    if debug_dir:
        import os as _os
        _os.makedirs(debug_dir, exist_ok=True)
        with open(_os.path.join(debug_dir, "clusters.json"), "w") as f:
            json.dump([{
                "cluster_id": c.cluster_id,
                "size": c.size,
                "items": [{
                    "id": it.id, "source": it.source, "url": it.url,
                    "title": it.title, "author": it.author,
                    "feed_name": it.feed_name,
                } for it in c.items],
            } for c in clusters], f, indent=2, default=str)

    # Housing-relevance filter on top-(max_roundups * 3) candidates
    # — most large clusters are non-housing (general news), so we check
    # plenty of candidates before giving up.
    candidates = clusters[:max(max_roundups * 3, 15)]
    housing_clusters: list[Cluster] = []
    for c in candidates:
        if is_housing_relevant(c, anthropic_client=anthropic_client):
            housing_clusters.append(c)
            logger.info(f"cluster {c.cluster_id} (n={c.size}) → housing-relevant")
        if len(housing_clusters) >= max_roundups:
            break
    stats["housing_relevant_clusters"] = len(housing_clusters)

    # Write each housing cluster's roundup
    roundups: list[Roundup] = []
    for c in housing_clusters:
        r = write_roundup(c, anthropic_client=anthropic_client)
        if r is not None:
            roundups.append(r)
            logger.info(
                f"cluster {c.cluster_id} → roundup '{r.topic}' "
                f"({len(r.cited_urls)} citations)"
            )
    stats["roundups_written"] = len(roundups)
    stats["pipeline_seconds"] = round(time.time() - t0, 1)

    return roundups[:max_roundups], stats
