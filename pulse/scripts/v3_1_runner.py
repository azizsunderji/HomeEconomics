"""V3.1 runner: two-stage clustering with Haiku sub-grouping by shared
story, plus per-cluster Sonnet synthesis.

Architecture:
  1. Load corpus from items table (last 24h).
  2. OpenAI embed every item.
  3. HDBSCAN cluster (topic-level).
  4. Housing-relevance check per topic cluster (Haiku YES/NO).
  5. NEW (v3.1): Haiku sub-grouping. For each housing-relevant topic
     cluster, Haiku splits items into sub-clusters by SHARED STORY/
     event/argument. Items that are topically similar but story-
     distinct end up in separate sub-clusters.
  6. For each sub-cluster: Sonnet writes ONE conversation_themes entry
     with the v1 SYSTEM_PROMPT. Anchor rule is relaxed (v3.1) — a theme
     can be anchored on either a news event OR a shared
     argument/debate, since organic conversations don't always have a
     single event trigger.
  7. Aggregate themes into a briefing. Use v1's most-recent briefing
     for the non-theme scaffold (paper_of_the_day, conversation_pulse,
     ai_brief, etc.).
  8. Render via render_briefing_html, send via Resend with [Pulse V3.1]
     subject prefix.

Run:
    python pulse/scripts/v3_runner.py [--to me@x.com]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pulse" / "scripts"))

import anthropic
import httpx

from analysis.roundup_clustering import (
    load_corpus, embed_corpus, cluster_items, is_housing_relevant,
    Cluster, CorpusItem, MIN_CLUSTER_SIZE,
    _is_quote_or_reply_tweet, _count_substantive_words,
    _aggregate_threads, MIN_SUBSTANTIVE_WORDS,
)
from analysis.synthesize import SYSTEM_PROMPT as V1_SYSTEM_PROMPT
from analysis.synthesize import _compute_cited_sources
from delivery.email_briefing import render_briefing_html
from datetime import timedelta
import numpy as np

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_DB = os.environ.get(
    "PULSE_DB", "/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"
)
DEFAULT_TO = "aziz@home-economics.us"
EMAIL_FROM = "Pulse V3.1 <onboarding@resend.dev>"
OPUS_MODEL = "claude-opus-4-8"  # writer (match v1)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SUBCLUSTER_MODEL = HAIKU_MODEL
COHERENCE_MODEL = HAIKU_MODEL
US_HOUSING_CHECK_MODEL = HAIKU_MODEL

# Haiku prompt for two-stage sub-grouping (v3.1).
SUBCLUSTER_SYSTEM = """You are organizing housing-related items (tweets, articles, posts) into SUB-GROUPS by SHARED STORY.

Two items belong to the same sub-group only if they share at least one of:
  - The same specific news event (same deal, ruling, bill, data release, named report)
  - The same specific argument or debate (multiple voices engaging with each other, or with the same shared data/claim)
  - The same specific entity as the subject (same metro, same company, same regulation, same paper)

Items that share only a BROAD TOPIC ("housing supply", "zoning") but are about DIFFERENT specific stories must be in DIFFERENT sub-groups. Don't lump together unrelated tweets just because they all mention rents or builders. Be discriminating.

Items that don't fit any sub-group (single isolated posts, generic commentary) should be left out.

Return ONLY a JSON object — no prose, no markdown fences:
{"subgroups": [
  {"item_ids": [<int>, <int>, ...], "label": "short description of the shared story (5-10 words)"},
  ...
]}

A sub-group must have at least 2 items. Skip singletons."""


# v3.1 wrapper that runs BEFORE v1's SYSTEM_PROMPT. Tells Sonnet its
# job is to write ONE roundup for the conversation_roundups section
# from a pre-clustered set of items. v1's "News Themes" section
# (conversation_themes) is OWNED BY v1 and not touched here.
V3_ROUNDUP_WRITER_PREFIX = """You are writing ONE conversation_roundups entry for a daily housing-economics briefing. v1's News Themes section is being produced separately — your job here is the discursive "Conversations" section: organic conversations, debates, and discussion threads.

The items below have been pre-clustered into a tight sub-group sharing one specific story, debate, or shared argument. Write a SINGLE polished roundup JSON object describing this sub-cluster.

HARD CONSTRAINTS specific to per-cluster roundup mode:
  - You may ONLY cite items from the cluster provided. Do not invent items, do not reference items outside the cluster.
  - The roundup may be anchored on EITHER a news event OR a shared organic argument/debate (multiple voices engaging with each other, or with shared data/claim). Both are valid for the Conversations section. Do not require a hard news hook — organic discourse is the point.
  - Write in v1's measured, restrained, data-first tone. Inline-link every attribution to the cited item's URL using markdown `[anchor text](url)` syntax.
  - FIRST-SENTENCE RULE (HARD GATE). The first sentence of the summary MUST lead with a specific named source's specific claim, a specific data point, or a specific named event. DO NOT open with meta-statements about discourse ("The discourse is circling...", "A loose thread is emerging...", "Multiple voices are debating...", "Conversation is brewing about..."). Forbidden opener words/phrases: "discourse", "loose thread", "the conversation", "voices are", "people are talking about", "interesting thread", "running discussion". Start with the substance: "[Named source] argued that [specific claim]" or "[Named outlet] reported [specific event]" — that's the only acceptable opener pattern.
  - HISTORICAL CONTEXT INTEGRATION — ENCOURAGED (added 2026-06-10). The input includes a "PAST 6 DAYS" context section with items from earlier this week that are topically related. You are ENCOURAGED to weave these in whenever genuinely relevant — historical weaving with explicit time stamps is a value-add, not padding, and substantially deepens the roundup. The bar is "genuinely relevant," not "strictly necessary": if a past-6-day item meaningfully extends, contextualizes, or contrasts with today's claim, cite it. Use explicit time stamps ("Earlier this week, [source] argued...", "Tuesday, [source] flagged...", "Friday's data showed..."). HARD GATE: verify country/metro/topic match before citing — never weld an Australian item into a US theme, never weld rent-control discourse into a permitting-reform theme. If no historical item is a clean fit, ignore the past-6-day section entirely. Today's items don't need a date stamp; historical items always require one. Historical items are connective tissue — they cannot anchor the roundup; today's items must anchor.
  - ENRICH_LINKS — SECONDARY REFERENCES INSIDE ARTICLES. Some items have an `enrich_links` field with outbound hyperlinks the article's own author included (e.g., an outlet's piece on data centers might link to a Pew survey or a Maine moratorium story). You are ENCOURAGED to cite those URLs as secondary references inside the roundup using normal `[anchor text](url)` markdown whenever they're relevant — secondary linking is a value-add, not padding, and substantially deepens the roundup. Rules: (a) cite an enrich_link whenever it is genuinely relevant to a claim you're making, even if not strictly necessary; (b) use the original anchor text from `enrich_links[i].anchor_text` as your link text, not invented phrasing; (c) the primary cluster items' URLs still take precedence — enrich_links add a layer; (d) skip pure-nav refs that point to topic pages, section indexes, or research-overview pages; cite refs that point to specific stories, reports, or data releases.
  - Skip ONLY if: (1) cluster content is private email correspondence (reply chains, person-to-person addressing, quoted-reply patterns), (2) cluster is off-topic to US housing/real-estate/zoning/urbanism/affordability/demographics, (3) cluster is brokerage content marketing without any independent news or argument, OR (4) cluster items are genuinely unrelated to each other (Haiku miscalled the sub-grouping).
  - When skipping, return {"skip": true, "reason": "..."}.

OUTPUT: a single JSON object with these fields, or {"skip": true, "reason": "..."}. No prose preamble, no markdown fences. Plain JSON only.

REQUIRED OUTPUT SCHEMA when not skipping:
{
  "topic": "<short headline-style title for the roundup>",
  "summary": "<multi-paragraph prose with inline markdown links to the cluster items, applying every prose/citation/paragraph-break rule from the SYSTEM_PROMPT below>"
}

All applicable rules from the briefing SYSTEM_PROMPT below apply: citation discipline, paragraph breaks, attribution hyperlinks, technical precision, the privacy hard gate, no canonization. The roundup section in v1 omits the heat_level / platforms / topics / related_news_trigger fields that themes carry, so do not output those here.

================================================================
ORIGINAL BRIEFING SYSTEM PROMPT (for rule reference):
================================================================

"""


# ────────────────────────────────────────────────────────────────────────
# v3.1 — looser load_corpus
# ────────────────────────────────────────────────────────────────────────

V3_1_MIN_SUBSTANTIVE_WORDS = 10  # was 20 — v3 was silently dropping high-
                                  # value short tweets (SF rent surge from
                                  # @conorsen/@mikesimonsen, etc.)
V3_1_HIGH_REL_BYPASS = 70         # items with relevance_score >= this
                                  # bypass the short-and-reply filter
                                  # entirely


# Per-item lookup table populated by load_corpus_v3_1, keyed by item.id.
# Used by _format_items_for_sonnet to attach enrich_links + relevance_score.
_ITEM_META: dict[int, dict] = {}


def load_corpus_v3_1(conn: sqlite3.Connection, hours: int = 24,
                     end: Optional[datetime] = None) -> list[CorpusItem]:
    """v3.1 corpus loader: same shape as analysis.roundup_clustering's
    load_corpus, but with looser filtering. Threshold drops from 20 → 10
    substantive words, and items with relevance_score >= 70 bypass the
    short-or-reply filter entirely (because high-rel items are by
    definition worth keeping even if short)."""
    end_dt = end or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(hours=hours)
    # Detect enrich_links column (added 2026-06-08; older DBs lack it)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    has_links = "enrich_links" in cols
    select_extra = ", enrich_links" if has_links else ""
    rows = conn.execute(
        f"""SELECT id, source, url, title, body, author, published_at,
                  feed_name, relevance_score{select_extra}
             FROM items
            WHERE published_at >= ? AND published_at <= ?
              AND coalesce(content_type, '') NOT IN ('private_email',
                                                      'denylisted')
            ORDER BY published_at ASC""",
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchall()
    items = [dict(r) for r in rows]
    logger.info(f"v3.1 loaded {len(items)} raw items in last {hours}h")

    # Stash per-id metadata (enrich_links + relevance_score) for later
    # use by _format_items_for_sonnet. These don't fit on the CorpusItem
    # dataclass without modifying roundup_clustering.py.
    _ITEM_META.clear()
    for it in items:
        meta = {"relevance_score": it.get("relevance_score") or 0}
        links_raw = it.get("enrich_links") if has_links else None
        if links_raw:
            try:
                meta["enrich_links"] = json.loads(links_raw)[:5]
            except (json.JSONDecodeError, TypeError):
                pass
        _ITEM_META[it["id"]] = meta

    try:
        from analysis.synthesize import unwrap_item_urls as _unwrap
        _unwrap(items)
    except Exception:
        pass

    items = _aggregate_threads(items)

    kept: list[CorpusItem] = []
    dropped_short = 0
    dropped_replies = 0
    dropped_empty = 0
    kept_by_rel_bypass = 0
    for it in items:
        body = (it.get("body") or "").strip()
        title = (it.get("title") or "").strip()
        if not body and not title:
            dropped_empty += 1
            continue
        rel = it.get("relevance_score") or 0
        bypass = rel >= V3_1_HIGH_REL_BYPASS
        if not bypass:
            if _is_quote_or_reply_tweet(it):
                dropped_replies += 1
                continue
            sw = _count_substantive_words(f"{title} {body}")
            if sw < V3_1_MIN_SUBSTANTIVE_WORDS:
                dropped_short += 1
                continue
        else:
            kept_by_rel_bypass += 1
        kept.append(CorpusItem(
            id=it["id"], source=it.get("source", ""),
            url=it.get("url", "") or "", title=title, body=body,
            author=(it.get("author") or "").strip(),
            published_at=it.get("published_at", "") or "",
            feed_name=(it.get("feed_name") or "").strip(),
            merged_ids=it.get("_merged_ids", []),
            merged_urls=it.get("_merged_urls", []),
        ))
    logger.info(
        f"v3.1 filter: dropped {dropped_empty} empty, {dropped_replies} "
        f"reply, {dropped_short} short; kept_by_rel_bypass="
        f"{kept_by_rel_bypass}; total kept={len(kept)}"
    )
    return kept


# ────────────────────────────────────────────────────────────────────────
# v3.1 — pre-write coherence gate
# ────────────────────────────────────────────────────────────────────────

def coherence_check(cluster: Cluster,
                    anthropic_client: Optional[anthropic.Anthropic] = None
                    ) -> tuple[bool, str]:
    """Programmatic-only coherence gate (v3.1 second iteration). The
    earlier LLM-driven version was rejecting clusters that v1 would have
    written 5-star roundups about ("topical overlap only" verdicts were
    too strict). Keep just the structural minimums; trust the Sonnet
    skip path for content-quality calls.

    Returns (pass, reason).

    Rules:
      1. ≥2 distinct authors/handles. Single-author clusters get
         dropped (kills the Australian Migration [0] case).
      2. ≥2 distinct sources, where "source" = URL domain OR feed_name
         OR a non-twitter/bluesky platform. Twitter-only clusters with
         multiple distinct handles still count as multi-source for this
         purpose — different handles ARE different voices, even on the
         same platform. (Without this carve-out the gate killed every
         legitimate Twitter conversation.)
    """
    authors = {(it.author or "").lower().strip() for it in cluster.items
               if (it.author or "").strip()}
    if len(authors) < 2:
        return False, f"single-author cluster ({list(authors) or '(blank)'})"

    # Distinct sources. Twitter handles count separately so a
    # multi-handle Twitter conversation passes; a single news outlet
    # echoed once does NOT pass.
    sources = set()
    for it in cluster.items:
        plat = (it.source or "").lower()
        if plat in ("twitter", "bluesky"):
            sources.add(f"{plat}:{(it.author or '').lower()}")
        else:
            if it.url:
                m = re.search(r"https?://([^/]+)/", (it.url or "") + "/")
                if m:
                    sources.add(m.group(1).lower().replace("www.", ""))
            if it.feed_name:
                sources.add("feed:" + it.feed_name.lower())
            if plat:
                sources.add("src:" + plat)
    if len(sources) < 2:
        return False, "single-source cluster"

    return True, f"{len(authors)} authors / {len(sources)} sources"


# ────────────────────────────────────────────────────────────────────────
# v3.1 — per-cluster historical context (Past 6 Days)
# ────────────────────────────────────────────────────────────────────────

HISTORICAL_WINDOW_DAYS = 6
HISTORICAL_MIN_RELEVANCE = 60
HISTORICAL_CONTEXT_PER_CLUSTER = 8


def load_historical_pool(conn: sqlite3.Connection,
                          today_end: datetime,
                          today_window_hours: int = 24
                          ) -> tuple[list[CorpusItem], np.ndarray]:
    """Past-6-day high-relevance items (excluding the 24h window already
    in the today corpus). Embedded once; returned as (items, embs)."""
    today_start = today_end - timedelta(hours=today_window_hours)
    hist_start = today_end - timedelta(days=HISTORICAL_WINDOW_DAYS)
    rows = conn.execute(
        """SELECT id, source, url, title, body, author, published_at,
                  feed_name
             FROM items
            WHERE published_at >= ? AND published_at < ?
              AND coalesce(relevance_score, 0) >= ?
              AND coalesce(content_type, '') NOT IN ('private_email',
                                                      'denylisted')
            ORDER BY published_at ASC
            LIMIT 600""",
        (hist_start.isoformat(), today_start.isoformat(),
         HISTORICAL_MIN_RELEVANCE),
    ).fetchall()
    items = [CorpusItem(
        id=r["id"], source=r["source"] or "", url=r["url"] or "",
        title=(r["title"] or "").strip(),
        body=(r["body"] or "").strip(),
        author=(r["author"] or "").strip(),
        published_at=r["published_at"] or "",
        feed_name=(r["feed_name"] or "").strip(),
    ) for r in rows]
    logger.info(f"historical pool: {len(items)} items "
                f"(past {HISTORICAL_WINDOW_DAYS}d, rel>={HISTORICAL_MIN_RELEVANCE})")
    if not items:
        return [], np.zeros((0, 1536), dtype=np.float32)
    embs = embed_corpus(items)
    return items, embs


def historical_context_for_cluster(cluster: Cluster, cluster_embs: np.ndarray,
                                    hist_items: list[CorpusItem],
                                    hist_embs: np.ndarray,
                                    top_n: int = HISTORICAL_CONTEXT_PER_CLUSTER
                                    ) -> list[CorpusItem]:
    """For each cluster, pick the top-N most-similar historical items by
    cosine similarity to the cluster centroid (mean embedding)."""
    if not hist_items or len(hist_embs) == 0:
        return []
    # Cluster centroid embedding — take indices of cluster items in the
    # original today corpus and average their embeddings.
    if cluster_embs.size == 0:
        return []
    centroid = cluster_embs.mean(axis=0)
    centroid = centroid / max(np.linalg.norm(centroid), 1e-8)
    sims = hist_embs @ centroid  # cosine since both L2-normalized
    # Pick top N with sim >= 0.3 (lowish bar — Sonnet decides relevance)
    idx = np.argsort(-sims)
    out = []
    for i in idx[:top_n * 2]:  # over-fetch then filter
        if sims[i] < 0.3:
            break
        out.append(hist_items[i])
        if len(out) >= top_n:
            break
    return out


# ────────────────────────────────────────────────────────────────────────
# v3.1 — US-housing-tighter check (replaces the generic housing-check)
# ────────────────────────────────────────────────────────────────────────

US_HOUSING_CHECK_SYSTEM = (
    "You are filtering clusters of items for a US-housing-focused daily "
    "briefing. PASS only if the cluster's content has a direct US-housing "
    "impact: US metros, US policy/regulation, US market data, US housing "
    "demographics, US homebuilders, US mortgage market. FAIL clusters "
    "that are international-only (Australian/Canadian/UK housing without "
    "explicit US comparison), general politics not tied to housing, AI "
    "without housing impact, foreign policy, sports. Reply with one word: "
    "YES or NO."
)


def is_us_housing_relevant(cluster: Cluster,
                            anthropic_client: Optional[anthropic.Anthropic]
                            = None) -> bool:
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()
    samples = []
    for it in cluster.items[:8]:
        samples.append(
            f"- {(it.title or '')[:140]}\n  {(it.body or '')[:200].strip()}"
        )
    user = ("Cluster items:\n" + "\n".join(samples)
            + "\n\nUS-housing-relevant? YES or NO.")
    try:
        resp = anthropic_client.messages.create(
            model=US_HOUSING_CHECK_MODEL, max_tokens=4,
            system=US_HOUSING_CHECK_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = (resp.content[0].text if resp.content else "").strip().upper()
        try:
            from analysis.anthropic_spend import record_usage as _rec_usage
            _rec_usage(US_HOUSING_CHECK_MODEL, resp.usage)
        except Exception:
            pass
        return text.startswith("YES")
    except Exception as e:
        logger.warning(f"US-housing check failed: {e}")
        return True  # fail-open


def subcluster_by_shared_story(
    cluster: Cluster,
    anthropic_client: Optional[anthropic.Anthropic] = None,
) -> list[Cluster]:
    """Stage 2: Haiku splits a topic cluster into sub-clusters by SHARED
    STORY/event/argument. Returns sub-clusters; items not in any
    sub-group are dropped."""
    if cluster.size < 4:
        # Too small to subdivide — pass through as a single sub-cluster.
        return [cluster]
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()
    items_json = []
    for it in cluster.items:
        items_json.append({
            "id": it.id,
            "source": it.source,
            "author": it.author,
            "title": (it.title or "")[:160],
            "snippet": (it.body or "")[:300],
        })
    user = (
        f"Items in this housing-relevant topic cluster ({cluster.size} total):\n"
        f"{json.dumps(items_json, indent=2)}\n\n"
        f"Organize them into sub-groups by SHARED STORY (event, argument, "
        f"or shared data/claim). Return the JSON object only."
    )
    try:
        resp = anthropic_client.messages.create(
            model=SUBCLUSTER_MODEL,
            max_tokens=2048,
            system=SUBCLUSTER_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text if resp.content else ""
        try:
            from analysis.anthropic_spend import record_usage as _rec_usage
            _rec_usage(SUBCLUSTER_MODEL, resp.usage)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"cluster {cluster.cluster_id} subcluster Haiku failed: {e}")
        return [cluster]
    # Strip code fences
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    raw = m.group(1).strip() if m else text
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m2 = re.search(r"(\{.*\})", raw, re.DOTALL)
        if not m2:
            return [cluster]
        try:
            obj = json.loads(m2.group(1))
        except json.JSONDecodeError:
            return [cluster]
    subgroups = obj.get("subgroups") or []
    if not subgroups:
        return []
    # Build sub-clusters
    by_id = {it.id: it for it in cluster.items}
    out: list[Cluster] = []
    next_sub_id = cluster.cluster_id * 100  # stable namespacing
    for i, sg in enumerate(subgroups):
        ids = sg.get("item_ids") or []
        members = [by_id[i] for i in ids if i in by_id]
        if len(members) < 2:
            continue
        out.append(Cluster(cluster_id=next_sub_id + i, items=members))
    if not out:
        return []
    logger.info(
        f"cluster {cluster.cluster_id} (n={cluster.size}) -> "
        f"{len(out)} sub-clusters: " + ", ".join(
            f"{c.size}" for c in out
        )
    )
    return out


def _format_items_for_sonnet(items: list, max_body: int = 1500) -> str:
    """Render items (cluster or historical) as a JSON array. Attaches
    enrich_links (outbound hyperlinks the article author included) when
    available — Sonnet can cite those as secondary references."""
    out = []
    for it in items:
        meta = _ITEM_META.get(it.id, {})
        entry = {
            "id": it.id,
            "source": it.source,
            "url": it.url or "",
            "title": (it.title or "")[:300],
            "body": (it.body or "")[:max_body],
            "author": it.author or "",
            "published_at": str(it.published_at) if it.published_at else "",
            "feed_name": it.feed_name or "",
        }
        # Outbound hyperlinks captured during article enrichment, if any.
        # Only include for items where they're available and substantive.
        links = meta.get("enrich_links") or []
        if links:
            entry["enrich_links"] = [
                {"anchor_text": l.get("anchor_text", "")[:140],
                 "url": l.get("url", ""),
                 "internal": l.get("internal", False)}
                for l in links if l.get("url")
            ][:5]
        out.append(entry)
    return json.dumps(out, indent=2, default=str)


def _format_cluster_for_sonnet(cluster: Cluster) -> str:
    return _format_items_for_sonnet(cluster.items)


def write_roundup_for_cluster(
    cluster: Cluster,
    historical: Optional[list] = None,
    anthropic_client: Optional[anthropic.Anthropic] = None,
) -> Optional[dict]:
    """Sonnet writes ONE conversation_roundups entry for this cluster
    (or returns None to skip). v3.1: also accepts historical items (past
    6 days, related by embedding similarity)."""
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()

    system_prompt = V3_ROUNDUP_WRITER_PREFIX + V1_SYSTEM_PROMPT
    historical = historical or []
    hist_block = (
        f"\n\nPAST 6 DAYS CONTEXT (topically related items from earlier in "
        f"the week — cite ONLY when directly relevant, with explicit time "
        f"stamps like 'Earlier this week,...' or 'Tuesday,...'; ignore if "
        f"none apply):\n{_format_items_for_sonnet(historical, max_body=600)}"
        if historical else ""
    )
    user_content = (
        f"Cluster ID: {cluster.cluster_id} (size={cluster.size})\n\n"
        f"TODAY'S CLUSTER ITEMS (cite these as primary content):\n"
        f"{_format_cluster_for_sonnet(cluster)}"
        f"{hist_block}\n\n"
        f"Write the conversation_roundups entry for this cluster, "
        f"or return {{\"skip\": true, \"reason\": \"...\"}}."
    )

    try:
        response_text = ""
        with anthropic_client.messages.stream(
            model=OPUS_MODEL,
            max_tokens=4096,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                response_text += text
            try:
                _final = stream.get_final_message()
                from analysis.anthropic_spend import record_usage as _rec_usage
                _rec_usage(OPUS_MODEL, _final.usage)
            except Exception:
                pass
        response_text = response_text.strip()
    except Exception as e:
        logger.warning(f"cluster {cluster.cluster_id} Sonnet call failed: {e}")
        return None

    # Strip optional markdown fences
    m = re.search(r"```(?:json)?\s*(.*?)```", response_text, re.DOTALL)
    raw_json = m.group(1).strip() if m else response_text

    try:
        obj = json.loads(raw_json)
    except json.JSONDecodeError:
        # Sonnet sometimes wraps the JSON in trailing prose; try to extract
        m2 = re.search(r"(\{.*\})", raw_json, re.DOTALL)
        if not m2:
            logger.warning(f"cluster {cluster.cluster_id} returned unparseable: {raw_json[:200]}")
            return None
        try:
            obj = json.loads(m2.group(1))
        except json.JSONDecodeError:
            logger.warning(f"cluster {cluster.cluster_id} JSON parse fail: {raw_json[:200]}")
            return None

    if obj.get("skip"):
        logger.info(
            f"cluster {cluster.cluster_id} skipped: {obj.get('reason', '?')}"
        )
        return None

    # Stamp the cluster metadata so we can debug downstream
    obj["_cluster_id"] = cluster.cluster_id
    obj["_cluster_size"] = cluster.size
    return obj


def load_v1_scaffold(conn: sqlite3.Connection) -> tuple[int, dict, str]:
    """Load the most recent v1 daily briefing — used as the structural
    scaffold (paper_of_the_day, ai_brief, conversation_pulse, etc.).
    Conversation themes get REPLACED by v3's output."""
    row = conn.execute(
        "SELECT id, content_json, created_at FROM briefings "
        "WHERE briefing_type = 'daily' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("no v1 daily briefing found in DB")
    return row[0], json.loads(row[1]), row[2]


def build_v3_briefing(v1: dict, v3_roundups: list[dict],
                      conn: sqlite3.Connection,
                      stats: dict) -> dict:
    """Construct the v3.1 briefing dict — v1 scaffold + v3.1 roundups."""
    v3 = json.loads(json.dumps(v1))  # deep copy
    v3["conversation_roundups"] = v3_roundups
    v3["_v3_1_meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stats": stats,
        "source_v1_briefing_id": v1.get("_briefing_id"),
    }
    if "stats_summary" not in v3:
        v3["stats_summary"] = {}
    try:
        v3["stats_summary"]["cited_sources"] = _compute_cited_sources(v3, conn)
    except Exception as e:
        logger.warning(f"cited_sources recompute failed: {e}")
    return v3


def send_v3_email(v3: dict, to: str, source_v1_id: int) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set")
        return False
    html, top_theme, theme_count = render_briefing_html(v3, with_sources_box=False)
    date = v3.get("date") or datetime.now(timezone.utc).strftime("%b %d")
    n_roundups = len(v3.get("conversation_roundups") or [])
    subject = (
        f"[Pulse V3.1] {n_roundups} roundups · subcluster+coherence+history "
        f"| vs v1 #{source_v1_id} | {date}"
    )

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"from": EMAIL_FROM, "to": [to], "subject": subject, "html": html},
        timeout=30,
    )
    if resp.status_code == 200:
        logger.info(f"v3 email sent: {subject}")
        return True
    logger.error(f"resend {resp.status_code}: {resp.text[:300]}")
    return False


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--to", default=DEFAULT_TO)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--lookback-hours", type=int, default=24)
    p.add_argument("--max-roundups", type=int, default=15,
                   help="cap on how many conversation_roundups to write")
    p.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE)
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--no-store", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # Scaffold
    v1_id, v1, v1_created = load_v1_scaffold(conn)
    v1["_briefing_id"] = v1_id
    end_dt = datetime.fromisoformat(v1_created.replace("Z", "+00:00"))
    print(f"loaded v1 scaffold briefing #{v1_id} created at {v1_created}")
    print(f"corpus window: {args.lookback_hours}h ending {end_dt.isoformat()}")

    # Stage 1: load corpus with LOOSER filter
    t0 = time.time()
    items = load_corpus_v3_1(conn, hours=args.lookback_hours, end=end_dt)
    print(f"v3.1 corpus: {len(items)} items after looser filter")

    # Stage 2-3: embed + HDBSCAN cluster
    embs = embed_corpus(items)
    clusters = cluster_items(items, embs, min_cluster_size=args.min_cluster_size)
    largest = max((c.size for c in clusters), default=0)
    print(f"clusters: {len(clusters)} (largest size={largest})")

    # Build a lookup from item_id -> embedding row index so we can pull
    # cluster centroids cheaply later.
    id_to_row = {it.id: i for i, it in enumerate(items)}

    stats = {
        "lookback_hours": args.lookback_hours,
        "min_cluster_size": args.min_cluster_size,
        "max_roundups": args.max_roundups,
        "items_after_filter": len(items),
        "clusters_total": len(clusters),
        "embed_cluster_seconds": round(time.time() - t0, 1),
    }

    # Stage 4: US-housing relevance check (tightened from generic
    # housing-check). Drops international housing content cleanly.
    print(f"US-housing-checking up to {len(clusters)} clusters...")
    anth = anthropic.Anthropic()
    us_housing_clusters: list[Cluster] = []
    for c in clusters:
        if is_us_housing_relevant(c, anthropic_client=anth):
            us_housing_clusters.append(c)
            print(f"  cluster {c.cluster_id} (n={c.size}) -> US-housing")
    stats["us_housing_clusters"] = len(us_housing_clusters)

    # Stage 5 (NEW v3.1): Haiku sub-clustering by shared story.
    print(f"sub-clustering {len(us_housing_clusters)} clusters by shared story...")
    sub_clusters: list[Cluster] = []
    for c in us_housing_clusters:
        for sub in subcluster_by_shared_story(c, anthropic_client=anth):
            sub_clusters.append(sub)
    print(f"sub-clusters: {len(sub_clusters)}")
    stats["sub_clusters_total"] = len(sub_clusters)

    # Stage 6 (NEW v3.1): pre-write coherence gate.
    print(f"coherence-gating {len(sub_clusters)} sub-clusters...")
    coherent: list[Cluster] = []
    for sc in sub_clusters:
        ok, reason = coherence_check(sc, anthropic_client=anth)
        if ok:
            coherent.append(sc)
            print(f"  sub-cluster {sc.cluster_id} (n={sc.size}) -> coherent")
        else:
            print(f"  sub-cluster {sc.cluster_id} (n={sc.size}) -> SKIP: {reason}")
    stats["coherent_clusters"] = len(coherent)
    # Cap to max_roundups; sort by cluster size descending for stability
    coherent.sort(key=lambda c: -c.size)
    coherent = coherent[:args.max_roundups]

    # Stage 7 (NEW v3.1): per-cluster historical context (Past 6 Days).
    print(f"loading historical pool (past {HISTORICAL_WINDOW_DAYS}d) for context...")
    hist_items, hist_embs = load_historical_pool(conn, end_dt)
    stats["historical_pool_size"] = len(hist_items)

    # Stage 8: Opus writes ONE conversation_roundups entry per coherent
    # sub-cluster, with historical context attached.
    print(f"writing roundups for {len(coherent)} coherent sub-clusters (Opus per cluster + historical)...")
    roundups: list[dict] = []
    for i, c in enumerate(coherent):
        # Compute the cluster's centroid embedding from today's corpus
        # rows; build cluster_embs from the original embs matrix.
        rows = [id_to_row[it.id] for it in c.items if it.id in id_to_row]
        c_embs = embs[rows] if rows else np.zeros((0, 1536), dtype=np.float32)
        ctx_items = historical_context_for_cluster(
            c, c_embs, hist_items, hist_embs
        )
        ctx_label = f"+{len(ctx_items)} hist" if ctx_items else "no hist"
        print(f"  [{i+1}/{len(coherent)}] sub-cluster {c.cluster_id} n={c.size} {ctx_label} -> Opus...")
        r = write_roundup_for_cluster(c, historical=ctx_items, anthropic_client=anth)
        if r is not None:
            roundups.append(r)
            print(f"    OK: '{r.get('topic', '?')[:60]}'")
        else:
            print(f"    skipped")
    stats["roundups_written"] = len(roundups)
    stats["pipeline_seconds"] = round(time.time() - t0, 1)

    print(f"\n=== v3.1 stats: {json.dumps(stats, indent=2)} ===\n")
    print(f"v3.1 roundups ({len(roundups)}):")
    for r in roundups:
        print(f"  - [{r.get('_cluster_size')}] {r.get('topic', '?')[:80]}")

    # Stage 9: build briefing, render, send
    v3 = build_v3_briefing(v1, roundups, conn, stats)

    if not args.no_send:
        ok = send_v3_email(v3, args.to, v1_id)
        if not ok:
            sys.exit(1)
    else:
        print("--no-send set; skipping email")

    if not args.no_store:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(briefings)").fetchall()}
        if "briefing_type" in cols:
            cur = conn.execute(
                "INSERT INTO briefings (briefing_type, content_json, created_at) "
                "VALUES ('daily_v3_1_hybrid', ?, ?)",
                (json.dumps(v3, default=str),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            print(f"stored v3 briefing as id={cur.lastrowid}")


if __name__ == "__main__":
    main()
