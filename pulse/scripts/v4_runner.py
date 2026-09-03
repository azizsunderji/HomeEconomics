"""V4 runner: one unified, ranked list of entries.

The v1 "News Themes" / v3.1 "Conversations" split goes away. v4 produces a
single ranked list of entries, each written from one pre-clustered set of
items and integrating the news reporting and the social reaction in one
summary. A dated news anchor is optional; when present it is recorded as
`trigger`.

Pipeline (all clustering machinery is imported from v3_1_runner and
analysis/roundup_clustering — nothing there is modified):

  1. Scaffold: latest v1 `daily` briefing (paper_of_the_day, pulse, press
     mentions, stats all kept). Only conversation_themes and
     conversation_roundups are replaced.
  2. Corpus -> OpenAI embeddings -> HDBSCAN -> US-housing check (Haiku)
     -> sub-cluster by shared story (Haiku) -> same-author merge
     -> coherence gate. Identical calls to v3.1.
  3. Rescue pass (new): high-relevance news items (rss/google_news/
     substack/gmail) that fell into HDBSCAN noise or were dropped before
     the coherence gate become rescue clusters (near-duplicates grouped by
     embedding cosine >= 0.85). Capped at 8.
  4. Write: one Opus call per cluster (real + rescue) with
     V4_ENTRY_WRITER_PREFIX + v1 SYSTEM_PROMPT. No v1-theme dedup list
     (there is nothing to dedup against).
  5. Programmatic dedup (new): embed title+summary of every entry; pairs
     with cosine >= --dedup-threshold lose the smaller-cluster member.
  6. Ranking: score = cluster_size + 2*n_distinct_news_sources
     + (2 if anchor_type == "news" else 1 if "mixed" else 0). Keep
     --max-entries.
  7. Assemble: v4["entries"] (canonical) + v4["conversation_themes"]
     (same entries mapped to the shape render_briefing_html expects, so
     the existing template renders unchanged) + conversation_roundups = [].
  8. Post-process with the same v1 functions v1 runs on its themes
     (refusal strip, bridge strip, paragraph breaks, handle autolink, URL
     validation) and recompute cited_sources.
  9. Store as briefing_type = "daily_v4_unified" (unless --no-store).
 10. Send: --to = single-recipient shadow send with "[V4 SHADOW] " subject
     prefix; otherwise the v3.1 subscriber path.

Run:
    source ~/.pulse_dev_env
    python pulse/scripts/v4_runner.py --no-send --no-store
    python pulse/scripts/v4_runner.py --to aziz@home-economics.us --no-store
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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pulse" / "scripts"))

import anthropic
import numpy as np

# ── Everything reusable comes from v3.1 / roundup_clustering / synthesize ──
from v3_1_runner import (  # noqa: E402
    DEFAULT_DB, DEFAULT_TO, OPUS_MODEL, HISTORICAL_WINDOW_DAYS,
    _ITEM_META,
    load_corpus_v3_1, coherence_check, merge_adjacent_clusters,
    load_historical_pool, historical_context_for_cluster,
    is_us_housing_relevant, subcluster_by_shared_story,
    _format_items_for_sonnet, _format_cluster_for_sonnet,
    load_v1_scaffold,
    _render_variants, _with_unsub_footer, _subscriber_subject, _post_resend,
    send_v3_email_to_subscribers,
)
from analysis.roundup_clustering import (  # noqa: E402
    embed_corpus, cluster_items, Cluster, CorpusItem, MIN_CLUSTER_SIZE,
    OPENAI_EMBED_MODEL,
)
from analysis.synthesize import SYSTEM_PROMPT as V1_SYSTEM_PROMPT  # noqa: E402
from analysis.synthesize import (  # noqa: E402
    _compute_cited_sources, _strip_refusal_meta, _strip_forbidden_bridges,
    _enforce_paragraph_breaks, _autolink_bare_handles, _validate_briefing_urls,
    _classify_url_only,
)
from analysis.anthropic_spend import (  # noqa: E402
    record_usage as _record_usage, get_spend_cents, _compute_microcents,
)

logger = logging.getLogger("v4_runner")

BRIEFING_TYPE = "daily_v4_unified"
EMAIL_FROM = "Pulse <pulse@home-economics.us>"
SHADOW_SUBJECT_PREFIX = "[V4 SHADOW] "

# Sources that count as "news" for the rescue pass, the outlet count in the
# ranking formula, and the platforms[] mapping.
NEWS_SOURCES = {"rss", "google_news", "substack", "gmail"}
SOCIAL_SOURCES = {"twitter", "bluesky", "hackernews", "reddit"}

DEFAULT_MAX_ENTRIES = 18
DEFAULT_RESCUE_RELEVANCE = 60
DEFAULT_DEDUP_THRESHOLD = 0.80
RESCUE_NEAR_DUP_COS = 0.85
RESCUE_MAX_CLUSTERS = 8
RESCUE_CLUSTER_ID_BASE = 900_000  # keeps rescue ids clear of HDBSCAN/sub ids

# OpenAI text-embedding-3-small list price, cents per million tokens.
OPENAI_EMBED_CENTS_PER_MTOK = 2.0


# ────────────────────────────────────────────────────────────────────────
# Writer prompt
# ────────────────────────────────────────────────────────────────────────

V4_ENTRY_WRITER_PREFIX = """You are writing ONE entry for the unified daily list of a housing-economics briefing. There is no longer a separate "News Themes" section and a separate "Conversations" section: every entry in this briefing is written the same way, from one pre-clustered set of items, and the entries are ranked afterwards by a program. Your job is to write this ONE entry well.
  - SOCIAL ATTRIBUTION: when citing a post, name the platform first: 'On X, @nickgerli1 [pushed back](url)…', 'On Bluesky, @handle [argued](url)…'. Never open with a bare @handle.
  - LINK ANCHORS: hyperlink ONLY the reporting verb, never a name or a phrase. Write 'Ned Resnikoff [argued](url) that…', 'Alex Stapp [made](url) a parallel point', 'HousingWire [reported](url)…'. Never '[Ned Resnikoff argued](url)' and never a multi-word anchor.

The items below have been pre-clustered into a tight group sharing one specific story, event, debate, or shared argument. Some clusters are large (news articles plus social reaction); some are small; some contain a SINGLE high-relevance news item that the clustering step left on its own. All of these are valid inputs.

ANCHOR IS OPTIONAL. This entry may be anchored on a specific dated news event (a release, ruling, bill, deal, filing, earnings report, published article) OR on an organic argument/debate among the voices in the cluster with no single dated event behind it. Both are legitimate. Do NOT skip an entry, and do NOT invent an event, because there is no news hook. Do NOT demote or hedge an organic debate because it lacks one.

INTEGRATE, DO NOT SEPARATE. When the cluster contains both news reporting and social/newsletter reaction, weave them into ONE summary: what happened (with the reporting outlet linked), then what specific people argued about it (each linked), in the order that reads best. Do not write a "news paragraph" followed by a "reaction paragraph" as two disconnected blocks unless the paragraph-break rules below call for a break anyway. The social reaction is evidence about the story, not a separate section.

HARD CONSTRAINTS for this per-cluster entry:
  - You may ONLY cite items from the cluster provided (plus the PAST 6 DAYS context items and any `enrich_links`, under the rules below). Do not invent items, do not reference items outside the cluster, do not add outlets or handles that are not in the input.
  - A single-item cluster is a valid entry: summarize that item's specific claims and data with the item linked. Do not skip it for having only one voice, and do not pad it with claims the item does not make.
  - Write in the measured, restrained, data-first tone of the SYSTEM_PROMPT below. Inline-link every attribution to the cited item's URL using markdown `[anchor text](url)` syntax. NEVER put square brackets inside link anchor text — write `[HousingWire reported](url)`, not `[[HousingWire] reported](url)`. Bracketed patterns elsewhere in this prompt (e.g. "[Named source] argued...") are placeholder notation, not literal brackets to reproduce.
  - FIRST-SENTENCE RULE (HARD GATE). The first sentence of the summary MUST lead with a specific named source's specific claim, a specific data point, or a specific named event. DO NOT open with meta-statements about discourse ("The discourse is circling...", "A loose thread is emerging...", "Multiple voices are debating...", "Conversation is brewing about..."). Forbidden opener words/phrases: "discourse", "loose thread", "the conversation", "voices are", "people are talking about", "interesting thread", "running discussion". Start with the substance: "[Named source] argued that [specific claim]" or "[Named outlet] reported [specific event]" — that's the only acceptable opener pattern.
  - HISTORICAL CONTEXT INTEGRATION — ENCOURAGED. The input may include a "PAST 6 DAYS" context section with items from earlier this week that are topically related. Weave these in whenever genuinely relevant — historical weaving with explicit time stamps is a value-add, not padding. The bar is "genuinely relevant," not "strictly necessary": if a past-6-day item meaningfully extends, contextualizes, or contrasts with today's claim, cite it. Use explicit time stamps ("Earlier this week, [source] argued...", "Tuesday, [source] flagged...", "Friday's data showed..."). HARD GATE: verify country/metro/topic match before citing — never weld an Australian item into a US entry, never weld rent-control discourse into a permitting-reform entry. If no historical item is a clean fit, ignore the past-6-day section entirely. Today's items don't need a date stamp; historical items always require one. Historical items are connective tissue — they cannot anchor the entry; today's items must anchor.
  - ENRICH_LINKS — SECONDARY REFERENCES INSIDE ARTICLES. Some items have an `enrich_links` field with outbound hyperlinks the article's own author included. You are ENCOURAGED to cite those URLs as secondary references using normal `[anchor text](url)` markdown whenever they're relevant. Rules: (a) cite an enrich_link whenever it is genuinely relevant to a claim you're making; (b) use the original anchor text from `enrich_links[i].anchor_text` as your link text, not invented phrasing; (c) the primary cluster items' URLs still take precedence — enrich_links add a layer; (d) skip pure-nav refs that point to topic pages, section indexes, or research-overview pages; cite refs that point to specific stories, reports, or data releases.
  - LENGTH is proportional to the cluster. A one- or two-item entry is one short paragraph (3-5 sentences). A cluster with several distinct voices or sub-points is 2-4 short paragraphs. Every paragraph obeys the SYSTEM_PROMPT paragraph rules: no paragraph longer than ~3 sentences / ~75 words, a new voice or new data source or time-shift starts a new paragraph, the blank line is the transition (no "Separately,", "Meanwhile," etc.). Never pad a thin cluster to look substantial; never compress a rich one into a skim.
  - Skip ONLY if: (1) cluster content is private email correspondence (reply chains, person-to-person addressing, quoted-reply patterns), (2) cluster is off-topic to US housing/real-estate/zoning/urbanism/affordability/demographics/mortgage-credit, (3) cluster is brokerage or listing-portal content marketing without any independent news or argument, OR (4) cluster items are genuinely unrelated to each other (the sub-grouping miscalled it). Having only one item, or having no dated news event, is NOT a reason to skip.
  - When skipping, return {"skip": true, "reason": "<one specific sentence>"}.

OUTPUT: a single JSON object with the fields below, or {"skip": true, "reason": "..."}. No prose preamble, no markdown fences. Plain JSON only.

REQUIRED OUTPUT SCHEMA when not skipping:
{
  "title": "<headline-style title, 5-10 words, specific: name the entity, place, number, or claim — not a topic label>",
  "summary": "<prose with inline markdown links to the cluster items, applying every prose/citation/paragraph-break rule from the SYSTEM_PROMPT below>",
  "trigger": "<ONE sentence naming the specific dated news event that anchors this entry — who did what, when — e.g. 'Freddie Mac released its July House Price Index on Aug. 31 showing 2.3% annual growth.' Use null (JSON null, not a string) if no specific dated news event anchors the entry.>",
  "anchor_type": "news" | "social" | "mixed"
}

anchor_type definitions:
  - "news": the entry is anchored on a dated news event and the cluster is essentially the reporting of it (little or no independent social argument).
  - "mixed": a dated news event anchors the entry AND the cluster contains substantive social/newsletter argument reacting to it that the summary integrates.
  - "social": no specific dated news event; the entry is an organic argument, debate, analysis, or shared data claim among the voices.
`trigger` must be null when anchor_type is "social", and must be a non-empty sentence when anchor_type is "news" or "mixed".

All applicable rules from the briefing SYSTEM_PROMPT below apply to this entry: citation discipline (every attributed claim gets its own link; a second story from the same outlet gets its own second URL), paragraph breaks, forbidden bridge words, attribution fidelity (pronoun chains stay tied to the linked speaker), technical precision, the privacy hard gate, no canonization, never narrate insufficient content. Where the SYSTEM_PROMPT below distinguishes "conversation_themes" from "conversation_roundups" or requires a news anchor for a theme, IGNORE that distinction — it has been retired; this entry's anchor is optional as stated above. Do not output heat_level, platforms, topics, or related_news_trigger — those fields are generated programmatically.

================================================================
ORIGINAL BRIEFING SYSTEM PROMPT (for rule reference):
================================================================

"""


# ────────────────────────────────────────────────────────────────────────
# Per-run cost tracking
# ────────────────────────────────────────────────────────────────────────

class RunCostTracker:
    """In-process tally of Anthropic + OpenAI usage for THIS run. The
    shared anthropic_spend table (PK date+model) keeps being written by
    record_usage() inside the reused v3.1 functions, so the daily total
    in the email header stays correct; this tracker gives the v4-only
    figure that goes into _v4_meta["cost"]."""

    def __init__(self) -> None:
        self.anthropic: dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "input": 0, "output": 0,
                     "cache_write": 0, "cache_read": 0, "microcents": 0})
        self.openai_tokens = 0
        self.openai_calls = 0
        self.openai_tokens_estimated = 0  # for embed calls we can't observe

    def add_anthropic(self, model: str, usage) -> None:
        if usage is None:
            return
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        m = self.anthropic[model]
        m["calls"] += 1
        m["input"] += in_tok
        m["output"] += out_tok
        m["cache_write"] += cw
        m["cache_read"] += cr
        m["microcents"] += _compute_microcents(model, in_tok, out_tok, cw, cr)

    def add_openai(self, total_tokens: int) -> None:
        self.openai_calls += 1
        self.openai_tokens += int(total_tokens or 0)

    def summary(self) -> dict:
        anth_cents = sum(m["microcents"] for m in self.anthropic.values()) / 100
        openai_cents = (self.openai_tokens / 1_000_000) * OPENAI_EMBED_CENTS_PER_MTOK
        openai_est_cents = ((self.openai_tokens_estimated / 1_000_000)
                            * OPENAI_EMBED_CENTS_PER_MTOK)
        return {
            "anthropic_cents": round(anth_cents, 2),
            "anthropic_by_model": {
                k: {**{kk: vv for kk, vv in v.items() if kk != "microcents"},
                    "cents": round(v["microcents"] / 100, 2)}
                for k, v in self.anthropic.items()
            },
            "openai_embed_tokens_observed": self.openai_tokens,
            "openai_embed_calls_observed": self.openai_calls,
            "openai_embed_cents_observed": round(openai_cents, 3),
            "openai_embed_tokens_estimated_unobserved": self.openai_tokens_estimated,
            "openai_embed_cents_estimated_unobserved": round(openai_est_cents, 3),
            "total_cents": round(anth_cents + openai_cents + openai_est_cents, 2),
            "pricing_note": (
                "Anthropic priced via analysis.anthropic_spend rates; OpenAI "
                f"text-embedding-3-small at {OPENAI_EMBED_CENTS_PER_MTOK} cents/MTok. "
                "'estimated_unobserved' covers the historical-pool embedding call "
                "inside v3_1_runner.load_historical_pool (chars/4)."
            ),
        }


class _TrackedMessages:
    def __init__(self, real, model_hint_tracker: RunCostTracker) -> None:
        self._real = real
        self._t = model_hint_tracker

    def create(self, **kwargs):
        resp = self._real.create(**kwargs)
        try:
            self._t.add_anthropic(kwargs.get("model", "?"), resp.usage)
        except Exception:
            pass
        return resp

    def stream(self, **kwargs):
        # Used only if a reused function streams; our own writer records
        # usage explicitly. Pass through unchanged.
        return self._real.stream(**kwargs)


class TrackedAnthropic:
    """Duck-typed stand-in for anthropic.Anthropic that tallies usage from
    .messages.create() into a RunCostTracker. The reused v3.1 helpers only
    call .messages.create / .messages.stream, so this is sufficient."""

    def __init__(self, real: anthropic.Anthropic, tracker: RunCostTracker) -> None:
        self._real = real
        self.messages = _TrackedMessages(real.messages, tracker)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _TrackedEmbeddings:
    def __init__(self, real, tracker: RunCostTracker) -> None:
        self._real = real
        self._t = tracker

    def create(self, **kwargs):
        resp = self._real.create(**kwargs)
        try:
            self._t.add_openai(resp.usage.total_tokens)
        except Exception:
            pass
        return resp


class TrackedOpenAI:
    def __init__(self, real, tracker: RunCostTracker) -> None:
        self._real = real
        self.embeddings = _TrackedEmbeddings(real.embeddings, tracker)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ────────────────────────────────────────────────────────────────────────
# Helpers: outlet naming, source counting
# ────────────────────────────────────────────────────────────────────────

_SENDER_NAME_RE = re.compile(r'^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$')


def news_outlet_name(it: CorpusItem) -> str:
    """Display name for a news item: feed_name, else the email sender's
    display name (gmail rows have empty feed_name), else the URL domain's
    publication name via synthesize._classify_url_only."""
    fn = (it.feed_name or "").strip()
    if fn:
        return fn
    if it.source == "gmail":
        m = _SENDER_NAME_RE.match(it.author or "")
        if m:
            return m.group(1).strip()
    if it.url:
        _, display = _classify_url_only(it.url)
        if display and display != "?":
            return display
    return (it.author or it.source or "?").strip()


def cluster_sources(cluster: Cluster) -> dict[str, int]:
    return dict(Counter((it.source or "?") for it in cluster.items))


def cluster_news_outlets(cluster: Cluster) -> list[str]:
    seen: dict[str, None] = {}
    for it in cluster.items:
        if (it.source or "") in NEWS_SOURCES:
            seen.setdefault(news_outlet_name(it), None)
    return list(seen.keys())


def cluster_item_ids(cluster: Cluster) -> list[int]:
    ids: list[int] = []
    for it in cluster.items:
        ids.append(it.id)
        for mid in (it.merged_ids or []):
            if mid != it.id and mid not in ids:
                ids.append(mid)
    return ids


def relevance_of(it: CorpusItem) -> int:
    return int((_ITEM_META.get(it.id) or {}).get("relevance_score") or 0)


# ────────────────────────────────────────────────────────────────────────
# Stage 3 — rescue pass
# ────────────────────────────────────────────────────────────────────────

def build_rescue_clusters(items: list[CorpusItem], embs: np.ndarray,
                          id_to_row: dict[int, int],
                          covered_ids: set[int],
                          min_relevance: int,
                          near_dup_cos: float = RESCUE_NEAR_DUP_COS,
                          cap: int = RESCUE_MAX_CLUSTERS
                          ) -> tuple[list[Cluster], dict]:
    """Items not in `covered_ids` (= not in any cluster that reached the
    coherence gate), with a news source and relevance >= min_relevance,
    are grouped by transitive embedding cosine >= near_dup_cos. Each group
    is a rescue Cluster (size may be 1). Highest max-relevance first, cap."""
    cands = [it for it in items
             if it.id not in covered_ids
             and (it.source or "") in NEWS_SOURCES
             and relevance_of(it) >= min_relevance]
    stats = {"rescue_candidates": len(cands)}
    if not cands:
        stats["rescue_groups_total"] = 0
        return [], stats

    rows = [id_to_row[it.id] for it in cands]
    sub = embs[rows]
    sims = sub @ sub.T
    n = len(cands)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= near_dup_cos:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)
    ordered = sorted(groups.values(),
                     key=lambda g: (-max(relevance_of(cands[i]) for i in g),
                                    -len(g)))
    stats["rescue_groups_total"] = len(ordered)
    out: list[Cluster] = []
    for k, g in enumerate(ordered[:cap]):
        members = [cands[i] for i in g]
        out.append(Cluster(cluster_id=RESCUE_CLUSTER_ID_BASE + k, items=members))
    stats["rescue_clusters"] = len(out)
    stats["rescue_clusters_dropped_by_cap"] = max(0, len(ordered) - cap)
    return out, stats


# ────────────────────────────────────────────────────────────────────────
# Stage 4 — write one entry per cluster
# ────────────────────────────────────────────────────────────────────────

def _parse_json_object(text: str) -> Optional[dict]:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    raw = m.group(1).strip() if m else text
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m2 = re.search(r"(\{.*\})", raw, re.DOTALL)
        if not m2:
            return None
        try:
            return json.loads(m2.group(1))
        except json.JSONDecodeError:
            return None


def write_entry_for_cluster(cluster: Cluster,
                            historical: Optional[list] = None,
                            anthropic_client: Optional[anthropic.Anthropic] = None,
                            tracker: Optional[RunCostTracker] = None,
                            ) -> tuple[Optional[dict], Optional[str]]:
    """Opus writes ONE v4 entry for this cluster. Same item packaging,
    past-6-day block, model, token limit, streaming and prompt caching as
    v3_1_runner.write_roundup_for_cluster; different system prefix; no
    v1-theme dedup block. Returns (entry_dict, None) on success or
    (None, skip_reason) on skip/failure."""
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()
    system_prompt = V4_ENTRY_WRITER_PREFIX + V1_SYSTEM_PROMPT
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
        f"Write the entry for this cluster as the JSON object described, "
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
                final = stream.get_final_message()
                _record_usage(OPUS_MODEL, final.usage)
                if tracker is not None:
                    tracker.add_anthropic(OPUS_MODEL, final.usage)
            except Exception:
                pass
        response_text = response_text.strip()
    except Exception as e:
        logger.warning(f"cluster {cluster.cluster_id} Opus call failed: {e}")
        return None, f"opus_call_failed: {e}"

    obj = _parse_json_object(response_text)
    if obj is None:
        logger.warning(f"cluster {cluster.cluster_id} returned unparseable: "
                       f"{response_text[:200]}")
        return None, "unparseable_json"
    if obj.get("skip"):
        return None, str(obj.get("reason") or "no reason given")

    title = (obj.get("title") or obj.get("topic") or obj.get("theme") or "").strip()
    summary = (obj.get("summary") or "").strip()
    if not title or not summary:
        return None, "missing_title_or_summary"

    trigger = obj.get("trigger")
    if isinstance(trigger, str):
        trigger = trigger.strip()
        if trigger.lower() in ("", "null", "none", "n/a"):
            trigger = None
    elif trigger is not None:
        trigger = str(trigger).strip() or None

    anchor_type = str(obj.get("anchor_type") or "").strip().lower()
    if anchor_type not in ("news", "social", "mixed"):
        inferred = "social" if not trigger else (
            "mixed" if any((it.source or "") in SOCIAL_SOURCES
                           for it in cluster.items) else "news")
        logger.warning(f"cluster {cluster.cluster_id}: anchor_type "
                       f"{anchor_type!r} invalid -> inferred {inferred!r}")
        anchor_type = inferred
    if anchor_type in ("news", "mixed") and not trigger:
        # No dated event stated -> no news ranking bonus. Keep it honest.
        logger.warning(f"cluster {cluster.cluster_id}: anchor_type "
                       f"{anchor_type!r} but trigger is null -> 'social'")
        anchor_type = "social"
    if anchor_type == "social" and trigger:
        logger.warning(f"cluster {cluster.cluster_id}: anchor_type 'social' "
                       f"but trigger given -> keeping trigger, 'mixed'")
        anchor_type = "mixed"

    return {
        "title": title,
        "summary": summary,
        "trigger": trigger,
        "anchor_type": anchor_type,
    }, None


# ────────────────────────────────────────────────────────────────────────
# Stage 5 — programmatic dedup
# ────────────────────────────────────────────────────────────────────────

def dedup_entries(entries: list[dict], threshold: float,
                  openai_client=None) -> tuple[list[dict], list[dict]]:
    """Embed title+summary of each entry; for every pair with cosine >=
    threshold drop the entry with the smaller cluster (tie: fewer news
    sources; second tie: the later one). Returns (kept, drop_log)."""
    if len(entries) < 2:
        return entries, []
    pseudo = [CorpusItem(id=i, source="entry", url="", title=e["title"],
                         body=e["summary"], author="", published_at="",
                         feed_name="")
              for i, e in enumerate(entries)]
    embs = embed_corpus(pseudo, openai_client=openai_client)
    sims = embs @ embs.T
    n = len(entries)
    pairs = [(float(sims[i, j]), i, j)
             for i in range(n) for j in range(i + 1, n)
             if sims[i, j] >= threshold]
    pairs.sort(reverse=True)
    alive = [True] * n
    log: list[dict] = []
    for score, i, j in pairs:
        if not (alive[i] and alive[j]):
            continue
        a, b = entries[i], entries[j]
        ka = (a["cluster_size"], a["n_news_sources"], -i)
        kb = (b["cluster_size"], b["n_news_sources"], -j)
        loser, winner = (j, i) if kb < ka else (i, j)
        alive[loser] = False
        log.append({
            "score": round(score, 3),
            "kept": entries[winner]["title"],
            "kept_cluster_id": entries[winner]["cluster_id"],
            "dropped": entries[loser]["title"],
            "dropped_cluster_id": entries[loser]["cluster_id"],
        })
        logger.info(f"DEDUP cos={score:.3f}: kept "
                    f"'{entries[winner]['title'][:60]}' "
                    f"(n={entries[winner]['cluster_size']}) / dropped "
                    f"'{entries[loser]['title'][:60]}' "
                    f"(n={entries[loser]['cluster_size']})")
    return [e for k, e in enumerate(entries) if alive[k]], log


# ────────────────────────────────────────────────────────────────────────
# Stage 6 — ranking
# ────────────────────────────────────────────────────────────────────────

ANCHOR_BONUS = {"news": 2, "mixed": 1, "social": 0}


def score_entry(e: dict) -> int:
    return (int(e["cluster_size"]) + 2 * int(e["n_news_sources"])
            + ANCHOR_BONUS.get(e.get("anchor_type"), 0))


def rank_entries(entries: list[dict], max_entries: int) -> list[dict]:
    for e in entries:
        e["score"] = score_entry(e)
    entries.sort(key=lambda e: (-e["score"], -e["cluster_size"],
                                -e["n_news_sources"], e["cluster_id"]))
    kept = entries[:max_entries]
    for i, e in enumerate(kept, 1):
        e["rank"] = i
    return kept


# ────────────────────────────────────────────────────────────────────────
# Stage 7/8 — assemble + render-shape mapping
# ────────────────────────────────────────────────────────────────────────

def entry_to_theme(e: dict, cluster: Cluster) -> dict:
    """Map a v4 entry onto the conversation_themes item shape that
    delivery.email_briefing.render_briefing_html renders. One platform
    badge per distinct news outlet, plus one per social platform present."""
    platforms: list[dict] = []
    seen: set[str] = set()
    for it in cluster.items:
        src = (it.source or "").lower()
        if src in NEWS_SOURCES:
            name = news_outlet_name(it)
        elif src:
            name = src
        else:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        platforms.append({"name": name, "reply_count": 0,
                          "sentiment": "neutral", "url": it.url or ""})
    return {
        "theme": e["title"],
        "summary": e["summary"],
        "related_news_trigger": e.get("trigger") or "",
        "platforms": platforms,
        "heat_level": "medium",
        "topics": [],
        "_v4_rank": e["rank"],
        "_v4_cluster_id": e["cluster_id"],
    }


def postprocess_entries(entries: list[dict], themes: list[dict],
                        conn: sqlite3.Connection) -> tuple[list[dict], list[dict], dict]:
    """Run the v1 post-processors on the v4 content only (a temp dict
    holding just conversation_themes), then copy cleaned summaries back
    onto the canonical entries. Entries whose summary was emptied by URL
    validation are dropped from both lists. Returns (entries, themes,
    stats)."""
    tmp = {"conversation_themes": themes, "conversation_roundups": [],
           "_briefing_id": None}
    tmp = _strip_refusal_meta(tmp)
    tmp = _strip_forbidden_bridges(tmp)
    tmp = _enforce_paragraph_breaks(tmp)
    tmp = _autolink_bare_handles(tmp, conn)
    # Snapshot before URL validation: it strips whole sentences whose
    # link fails a HEAD check, which on paywalled / bot-blocking sites is
    # a false negative. House rule: never drop a link. If validation
    # loses any URL (or empties a summary) we restore the pre-validation
    # text for that entry and keep its corrections only when nothing was lost.
    _url_re = re.compile(r"\]\((https?://[^)\s]+)\)")
    pre = {t["_v4_cluster_id"]: (t.get("summary") or "") for t in tmp["conversation_themes"]}
    tmp = _validate_briefing_urls(tmp, conn)

    surviving = tmp["conversation_themes"]
    by_cid = {t["_v4_cluster_id"]: t for t in surviving}
    kept_entries: list[dict] = []
    dropped: list[str] = []
    reverted: list[str] = []
    for e in entries:
        t = by_cid.get(e["cluster_id"])
        before = pre.get(e["cluster_id"], e.get("summary") or "")
        after = (t.get("summary") or "") if t is not None else ""
        lost = [u for u in _url_re.findall(before) if u not in after]
        if not after.strip() or lost:
            logger.warning(f"URL validation would drop {len(lost)} link(s) from {e['title']!r}; "
                           f"keeping the pre-validation text: {lost[:3]}")
            e["summary"] = before
            reverted.append(e["title"])
            if t is None:
                t = {**e, "summary": before, "_v4_cluster_id": e["cluster_id"]}
                surviving.append(t)
            else:
                t["summary"] = before
        else:
            e["summary"] = after
        kept_entries.append(e)
    for e in dropped:
        logger.warning(f"entry dropped in post-processing (summary emptied): {e!r}")
    stats = {
        "forbidden_bridge_strips": tmp.get("_forbidden_bridge_strips", 0),
        "paragraph_breaks_inserted": tmp.get("_paragraph_breaks_inserted", 0),
        "autolinked_handles": tmp.get("_autolinked_handles", 0),
        "url_audit": tmp.get("_url_audit", {}),
        "entries_dropped_in_postprocess": dropped,
        "url_validation_reverted": reverted,
    }
    return kept_entries, surviving, stats


def build_v4_briefing(v1: dict, entries: list[dict], themes: list[dict],
                      conn: sqlite3.Connection, meta: dict,
                      post_stats: dict) -> dict:
    v4 = json.loads(json.dumps(v1))  # deep copy of the scaffold
    v4["entries"] = entries
    v4["conversation_themes"] = themes
    v4["conversation_roundups"] = []
    v4["_v4_meta"] = meta
    # These top-level counters describe the themes content; in v4 that
    # content is v4's, so they carry v4's numbers (v1's are still in the
    # v1 row).
    v4["_forbidden_bridge_strips"] = post_stats["forbidden_bridge_strips"]
    v4["_paragraph_breaks_inserted"] = post_stats["paragraph_breaks_inserted"]
    v4["_autolinked_handles"] = post_stats["autolinked_handles"]
    v4["_url_audit"] = post_stats["url_audit"]
    v4["_social_anchor_rejections"] = 0  # gate retired in v4
    if "stats_summary" not in v4 or not isinstance(v4["stats_summary"], dict):
        v4["stats_summary"] = {}
    try:
        v4["stats_summary"]["cited_sources"] = _compute_cited_sources(v4, conn)
    except Exception as e:
        logger.warning(f"cited_sources recompute failed: {e}")
    return v4


# ────────────────────────────────────────────────────────────────────────
# Send
# ────────────────────────────────────────────────────────────────────────

def send_v4_shadow_email(v4: dict, to: str, source_v1_id: int) -> bool:
    """Single-recipient shadow send. Renders via v3.1's _render_variants
    (premium variant), appends the compliance footer without an
    unsubscribe link, and prefixes the reader-facing subject with
    '[V4 SHADOW] '."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set")
        return False
    premium_html, _free_html, top_theme = _render_variants(v4)
    html = _with_unsub_footer(premium_html, None)
    n = len(v4.get("entries") or [])
    subject = (f"{SHADOW_SUBJECT_PREFIX}{_subscriber_subject(v4, top_theme)} "
               f"| {n} entries | vs v1 #{source_v1_id}")
    ok = _post_resend(api_key, "https://api.resend.com/emails",
                      {"from": EMAIL_FROM, "to": [to],
                       "subject": subject, "html": html})
    if ok:
        logger.info(f"v4 shadow email sent to {to}: {subject}")
    return ok


# ────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Pulse v4 unified-entries runner")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--lookback-hours", type=int, default=24)
    p.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE)
    p.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    p.add_argument("--rescue-relevance", type=int, default=DEFAULT_RESCUE_RELEVANCE,
                   help="min relevance_score for a news item to be rescued "
                        "from clustering noise")
    p.add_argument("--dedup-threshold", type=float, default=DEFAULT_DEDUP_THRESHOLD,
                   help="cosine threshold on title+summary embeddings above "
                        "which two entries are duplicates")
    p.add_argument("--to", default=None,
                   help="single-recipient shadow send ('[V4 SHADOW]' subject). "
                        "Omit to send to the subscriber list (v3.1 path).")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--no-store", action="store_true")
    p.add_argument("--dump-json", default=None,
                   help="write the assembled v4 briefing dict to this path")
    args = p.parse_args()

    t_start = time.time()
    timings: dict[str, float] = {}

    def mark(stage: str, t0: float) -> None:
        timings[stage] = round(time.time() - t0, 1)

    tracker = RunCostTracker()
    spend_before = get_spend_cents().get("total_cents", 0)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # 1. Scaffold
    v1_id, v1, v1_created = load_v1_scaffold(conn)
    v1["_briefing_id"] = v1_id
    end_dt = datetime.fromisoformat(v1_created.replace("Z", "+00:00"))
    print(f"loaded v1 scaffold briefing #{v1_id} created at {v1_created}")
    print(f"corpus window: {args.lookback_hours}h ending {end_dt.isoformat()}")

    # 2. Corpus + embeddings + HDBSCAN
    t0 = time.time()
    items = load_corpus_v3_1(conn, hours=args.lookback_hours, end=end_dt)
    print(f"v4 corpus: {len(items)} items after filter")
    from openai import OpenAI
    oai = TrackedOpenAI(OpenAI(), tracker)
    embs = embed_corpus(items, openai_client=oai)
    clusters = cluster_items(items, embs, min_cluster_size=args.min_cluster_size)
    id_to_row = {it.id: i for i, it in enumerate(items)}
    clustered_ids = {it.id for c in clusters for it in c.items}
    print(f"clusters: {len(clusters)} (largest={max((c.size for c in clusters), default=0)}); "
          f"noise items: {len(items) - len(clustered_ids)}")
    mark("embed_cluster", t0)

    counts: dict = {
        "lookback_hours": args.lookback_hours,
        "min_cluster_size": args.min_cluster_size,
        "max_entries": args.max_entries,
        "rescue_relevance": args.rescue_relevance,
        "dedup_threshold": args.dedup_threshold,
        "items_after_filter": len(items),
        "items_in_hdbscan_noise": len(items) - len(clustered_ids),
        "clusters_total": len(clusters),
    }

    anth_real = anthropic.Anthropic()
    anth = TrackedAnthropic(anth_real, tracker)

    # US-housing gate
    t0 = time.time()
    us_clusters = [c for c in clusters if is_us_housing_relevant(c, anthropic_client=anth)]
    counts["us_housing_clusters"] = len(us_clusters)
    print(f"US-housing clusters: {len(us_clusters)} of {len(clusters)}")
    mark("us_housing_check", t0)

    # Sub-cluster by shared story
    t0 = time.time()
    sub_clusters: list[Cluster] = []
    for c in us_clusters:
        sub_clusters.extend(subcluster_by_shared_story(c, anthropic_client=anth))
    counts["sub_clusters_total"] = len(sub_clusters)
    print(f"sub-clusters: {len(sub_clusters)}")
    mark("subcluster", t0)

    # Same-author merge
    pre_merge = len(sub_clusters)
    sub_clusters, merge_log = merge_adjacent_clusters(sub_clusters, embs, id_to_row)
    for e in merge_log:
        tag = "MERGED" if e["merged"] else "below-thresh"
        print(f"  [{tag}] {e['a']} + {e['b']} shared={e['shared_authors']} cos={e['cos']}")
    counts["sub_clusters_after_merge"] = len(sub_clusters)
    print(f"sub-clusters after author/topic merge: {len(sub_clusters)} (was {pre_merge})")

    # Coherence gate
    coherent: list[Cluster] = []
    gate_fail: list[dict] = []
    for sc in sub_clusters:
        ok, reason = coherence_check(sc, anthropic_client=anth)
        if ok:
            coherent.append(sc)
        else:
            gate_fail.append({"cluster_id": sc.cluster_id, "size": sc.size, "reason": reason})
            print(f"  sub-cluster {sc.cluster_id} (n={sc.size}) -> GATE FAIL: {reason}")
    counts["coherent_clusters"] = len(coherent)
    coherent.sort(key=lambda c: -c.size)
    print(f"coherent clusters: {len(coherent)}")

    # 3. Rescue pass. "Covered" = every item in a cluster that reached the
    # coherence gate (pass or fail). Items in gate-failed clusters are NOT
    # rescued (spec: noise or dropped before the gate).
    t0 = time.time()
    covered_ids = {it.id for c in sub_clusters for it in c.items}
    rescue_clusters, rescue_stats = build_rescue_clusters(
        items, embs, id_to_row, covered_ids, args.rescue_relevance)
    counts.update(rescue_stats)
    print(f"rescue: {rescue_stats.get('rescue_candidates', 0)} candidates -> "
          f"{rescue_stats.get('rescue_groups_total', 0)} groups -> "
          f"{len(rescue_clusters)} rescue clusters (cap {RESCUE_MAX_CLUSTERS})")
    for rc in rescue_clusters:
        names = ", ".join(f"{news_outlet_name(it)} r={relevance_of(it)}" for it in rc.items)
        print(f"  rescue {rc.cluster_id} n={rc.size}: {rc.items[0].title[:70]!r} [{names}]")
    mark("rescue", t0)

    # Historical pool (past 6 days) — embedded inside load_historical_pool
    # with an unobserved OpenAI client; estimate its tokens for the cost line.
    t0 = time.time()
    hist_items, hist_embs = load_historical_pool(conn, end_dt)
    tracker.openai_tokens_estimated += sum(
        len(f"{it.title}\n\n{(it.body or '')[:1500]}") // 4 for it in hist_items)
    counts["historical_pool_size"] = len(hist_items)
    mark("historical_pool", t0)

    # 4. Write one entry per cluster
    t0 = time.time()
    write_plan = [(c, "cluster") for c in coherent] + [(c, "rescue") for c in rescue_clusters]
    print(f"writing {len(write_plan)} entries ({len(coherent)} cluster + "
          f"{len(rescue_clusters)} rescue) with Opus...")
    entries: list[dict] = []
    skips: list[dict] = []
    cluster_by_id: dict[int, Cluster] = {}
    for i, (c, origin) in enumerate(write_plan, 1):
        rows = [id_to_row[it.id] for it in c.items if it.id in id_to_row]
        c_embs = embs[rows] if rows else np.zeros((0, 1536), dtype=np.float32)
        ctx_items = historical_context_for_cluster(c, c_embs, hist_items, hist_embs)
        print(f"  [{i}/{len(write_plan)}] {origin} {c.cluster_id} n={c.size} "
              f"+{len(ctx_items)} hist -> Opus...")
        entry, skip_reason = write_entry_for_cluster(
            c, historical=ctx_items, anthropic_client=anth_real, tracker=tracker)
        if entry is None:
            skips.append({"cluster_id": c.cluster_id, "size": c.size,
                          "origin": origin, "reason": skip_reason,
                          "first_title": (c.items[0].title or c.items[0].body or "")[:80]})
            print(f"    SKIP ({origin} {c.cluster_id}): {skip_reason}")
            continue
        outlets = cluster_news_outlets(c)
        entry.update({
            "item_ids": cluster_item_ids(c),
            "sources": cluster_sources(c),
            "news_outlets": outlets,
            "n_news_sources": len(outlets),
            "cluster_id": c.cluster_id,
            "cluster_size": c.size,
            "origin": origin,
        })
        cluster_by_id[c.cluster_id] = c
        entries.append(entry)
        print(f"    OK [{entry['anchor_type']}]: {entry['title'][:70]!r}")
    counts["entries_written"] = len(entries)
    counts["skips"] = len(skips)
    mark("write", t0)

    # 5. Programmatic dedup
    t0 = time.time()
    entries, dedup_log = dedup_entries(entries, args.dedup_threshold, openai_client=oai)
    counts["dedup_drops"] = len(dedup_log)
    mark("dedup", t0)

    # 6. Rank
    entries = rank_entries(entries, args.max_entries)
    counts["entries_after_rank_cap"] = len(entries)

    # 7/8. Map to render shape, post-process, assemble
    t0 = time.time()
    themes = [entry_to_theme(e, cluster_by_id[e["cluster_id"]]) for e in entries]
    entries, themes, post_stats = postprocess_entries(entries, themes, conn)
    # re-number ranks if post-processing dropped anything
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    for t in themes:
        t["_v4_rank"] = next(e["rank"] for e in entries if e["cluster_id"] == t["_v4_cluster_id"])
    counts["final_entries"] = len(entries)
    mark("postprocess", t0)

    timings["total"] = round(time.time() - t_start, 1)
    cost = tracker.summary()
    cost["anthropic_spend_table_delta_cents"] = round(
        get_spend_cents().get("total_cents", 0) - spend_before, 2)
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_v1_briefing_id": v1_id,
        "counts": counts,
        "timings_seconds": timings,
        "skips": skips,
        "dedup_drops": dedup_log,
        "coherence_gate_failures": gate_fail,
        "merge_log": merge_log,
        "postprocess": post_stats,
        "cost": cost,
    }
    v4 = build_v4_briefing(v1, entries, themes, conn, meta, post_stats)

    # Report
    print(f"\n=== v4 counts: {json.dumps(counts, indent=2)} ===")
    print(f"=== v4 timings (s): {json.dumps(timings)} ===")
    print(f"=== v4 cost: {json.dumps(cost, indent=2)} ===")
    print(f"\nskips ({len(skips)}):")
    for s in skips:
        print(f"  - {s['origin']} {s['cluster_id']} n={s['size']}: {s['reason']}  "
              f"[{s['first_title']!r}]")
    print(f"\ndedup drops ({len(dedup_log)}):")
    for d in dedup_log:
        print(f"  - cos={d['score']}: KEPT {d['kept']!r} / DROPPED {d['dropped']!r}")
    print(f"\nv4 entries ({len(entries)}):")
    for e in entries:
        print(f"  {e['rank']:>2}. score={e['score']:>2} [{e['anchor_type']:<6}] "
              f"{e['origin']:<7} n={e['cluster_size']:<2} news={e['n_news_sources']} "
              f"{e['title'][:75]}")

    if args.dump_json:
        with open(args.dump_json, "w") as f:
            json.dump(v4, f, indent=2, default=str)
        print(f"wrote {args.dump_json}")

    # 10. Send
    email_ok = False
    if not args.no_send:
        if args.to:
            email_ok = send_v4_shadow_email(v4, args.to, v1_id)
        else:
            email_ok = send_v3_email_to_subscribers(v4, v1_id)
        if not email_ok:
            sys.exit(1)
    else:
        print("--no-send set; skipping email")

    # 9. Store
    if not args.no_store:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(briefings)").fetchall()}
        if "briefing_type" in cols:
            now_iso = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "INSERT INTO briefings (briefing_type, content_json, created_at, "
                "email_sent, email_sent_at) VALUES (?, ?, ?, ?, ?)",
                (BRIEFING_TYPE, json.dumps(v4, default=str), now_iso,
                 1 if email_ok else 0, now_iso if email_ok else None),
            )
            conn.commit()
            print(f"stored v4 briefing as id={cur.lastrowid} "
                  f"(type={BRIEFING_TYPE}, email_sent={int(email_ok)})")
    else:
        print("--no-store set; not writing to briefings")


if __name__ == "__main__":
    main()
