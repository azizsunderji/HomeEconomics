"""V4B runner: v1 themes as the news backbone, coherent clusters attached.

Why this revision exists. The v4 cluster-first run showed that embedding
clusters group by wording rather than by event: a multi-outlet news story
fragmented across entries, three single-outlet v1 themes fell out, and
single-item rescue entries padded the list. v1's wide-context call
consolidates events well but attaches social reaction weakly; the v3.1
cluster chain is the better finder of organic conversations. v4b keeps
both strengths:

  v1 conversation_themes are the news backbone. Coherent social/mixed
  clusters attach to the nearest theme and the theme is rewritten with
  them integrated. Clusters that attach to nothing become standalone
  entries. One ranked list. No rescue pass.

Pipeline (everything reusable is imported from v4_runner, v3_1_runner,
analysis/roundup_clustering and analysis/synthesize; none is modified):

  1. Scaffold: latest v1 `daily` briefing. conversation_themes kept as
     the backbone (T themes); v1's conversation_roundups dropped.
  2. Corpus -> embeddings -> HDBSCAN -> US-housing check -> sub-cluster
     by shared story -> same-author merge -> coherence gate. Identical
     call sequence to v4. No rescue pass.
  3. Attachment: embed each theme; cluster vector = centroid of its item
     embeddings; cosine to every theme + normalized-URL overlap with the
     theme's cited URLs. Attach to the best theme if
     cosine >= --attach-threshold OR url_overlap >= --attach-min-url-overlap.
  4. Rewrite each theme that has >= 1 attachment with ONE Opus call
     (V4B_REWRITE_PREFIX + v1 SYSTEM_PROMPT). The model may return
     keep_original. Themes with no attachments pass through untouched.
  5. Unattached coherent clusters -> v4's V4_ENTRY_WRITER_PREFIX writer.
  6. Programmatic dedup across themes + standalone entries; a theme
     always beats a standalone entry.
  7. Rank with v4's formula; cap at --max-entries.
  8. Assemble / post-process / store (briefing_type daily_v4b_attach) /
     send ('[V4B SHADOW] ' subject prefix with --to).

Run:
    source ~/.pulse_dev_env
    python pulse/scripts/v4b_runner.py --no-send --no-store
    python pulse/scripts/v4b_runner.py --to aziz@home-economics.us --no-store
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
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "pulse" / "scripts"))

import anthropic
import numpy as np

from v3_1_runner import (  # noqa: E402
    DEFAULT_DB, OPUS_MODEL,
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
from analysis.anthropic_spend import record_usage as _record_usage, get_spend_cents  # noqa: E402

# v4 pieces reused verbatim (v4_runner.py is not modified).
from v4_runner import (  # noqa: E402
    RunCostTracker, TrackedAnthropic, TrackedOpenAI,
    NEWS_SOURCES, SOCIAL_SOURCES,
    DEFAULT_MAX_ENTRIES, DEFAULT_DEDUP_THRESHOLD,
    V4_ENTRY_WRITER_PREFIX,  # noqa: F401  (the standalone writer's prompt)
    write_entry_for_cluster, _parse_json_object,
    news_outlet_name, cluster_sources, cluster_news_outlets, cluster_item_ids,
    rank_entries, entry_to_theme, postprocess_entries, build_v4_briefing,
)

logger = logging.getLogger("v4b_runner")

BRIEFING_TYPE = "daily_v4b_attach"
EMAIL_FROM = "Pulse <pulse@home-economics.us>"
SHADOW_SUBJECT_PREFIX = "[V4B SHADOW] "

DEFAULT_ATTACH_THRESHOLD = 0.55
DEFAULT_ATTACH_MIN_URL_OVERLAP = 1
THEME_ENTRY_ID_BASE = 700_000  # synthetic cluster_id for theme entries

# platforms[].name values that are social platforms rather than outlets
SOCIAL_PLATFORM_NAMES = {"twitter", "x", "bluesky", "hackernews", "hn",
                         "reddit", "threads", "mastodon", "linkedin",
                         "youtube", "tiktok"}

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")


# ────────────────────────────────────────────────────────────────────────
# Rewrite prompt
# ────────────────────────────────────────────────────────────────────────

V4B_REWRITE_PREFIX = """You are REWRITING ONE existing entry of a daily housing-economics briefing so that newly attached material is integrated into it. You are not writing from scratch.

WHAT YOU RECEIVE:
  1. ORIGINAL THEME: an entry that was already written today from the day's news. It has a title, a summary with inline markdown links, and a `related_news_trigger` naming the dated news event it is anchored on. Treat it as correct and already edited.
  2. ATTACHED CLUSTERS: one or more small groups of items (news articles, newsletters, tweets, posts) that a clustering step judged to belong to the same story. They may add further reporting on the same event, social or newsletter reaction to it, or a closely related development.
  3. Optionally a PAST 6 DAYS context block of earlier items.

YOUR TASK: return a rewritten version of the theme in which the attached reporting and reaction are woven INTO the summary.

RULES (all are hard):
  - KEEP THE NEWS ANCHOR. The rewritten entry is still about the event named in the original `related_news_trigger`. Return that trigger unchanged (or with at most a trivial wording fix). Do not re-anchor the entry on the attached material.
  - PRESERVE THE ORIGINAL'S SUBSTANCE. Every specific number, named source, quotation and linked attribution in the original summary should survive unless the attached material directly contradicts it (then say so, with both sources linked). Do not drop the original's payment math, data points or arguments to make room. Do not delete a sentence of the original; you may tighten wording or move sentences.
  - KEEP THE ORIGINAL'S PARAGRAPH BREAKS. The original summary is split into paragraphs by blank lines; keep those blank lines (written as \\n\\n inside the JSON string) and add further blank-line breaks for each new voice or data source you introduce. Never collapse the entry into one paragraph.
  - DO NOT ADD NEW LINKS INTO THE ORIGINAL'S SENTENCES. A sentence that carries an original fact keeps only its original link(s). Put every new citation in its own new sentence. (A later URL check strips any sentence whose link cannot be verified; a new link inside an original sentence would take the original fact down with it.)
  - INTEGRATE, DO NOT APPEND. Place each attached voice where it belongs in the argument: further reporting next to the reporting it extends, reaction next to the claim it reacts to. Do NOT add a separate "reaction paragraph" or "social media responded" block at the end. The reader should not be able to tell which sentences were added.
  - CITE ONLY ALLOWED SOURCES. You may link ONLY (a) URLs already linked in the original summary or listed in its platforms, (b) URLs of items in the attached clusters, (c) `enrich_links` URLs carried by those items (use their `anchor_text` verbatim as link text), and (d) PAST 6 DAYS items when genuinely relevant, always with an explicit time stamp ("Earlier this week, ..."). No other outlets, handles or URLs. Never invent an item.
  - USE WHAT IS ATTACHED, ON MERIT. Cite an attached item when it adds a fact, a number, a named argument or a substantive disagreement. Skip attached items that merely repeat the original (say nothing about them), and skip promotional, private-correspondence or off-topic items. An attached cluster can contain an item on a different sub-story (the clustering merges same-author posts); leave such an item out rather than bolting it on as a final paragraph.
  - FIRST-SENTENCE RULE. The summary must stand alone: its first sentence restates the anchor event itself (entity, action, key number). Never open with "But", "And", "Meanwhile", "Still", "However", or any meta-statement about discourse ("The conversation...", "Voices are...", "The discourse...").
  - LENGTH. Keep the entry proportionate: 2-4 short paragraphs. Growth should track the amount of genuinely new material; as a guide, do not exceed roughly one and a half times the original length unless the attachments contain several distinct new data points. Every paragraph obeys the SYSTEM_PROMPT rules: no paragraph longer than ~3 sentences / ~75 words; a new voice, new data source or time shift starts a new paragraph; the blank line is the transition (no "Separately,", "Meanwhile," bridges).
  - CITATION FORMAT. Inline `[anchor text](url)` markdown on every attributed claim; a second story from the same outlet gets its own second URL; never put square brackets inside link anchor text; pronoun chains stay tied to the linked speaker. All SYSTEM_PROMPT rules on attribution fidelity, technical precision, the privacy hard gate, no canonization and never narrating insufficient content apply.
  - TITLE. Keep the original title unless the integrated material makes a more specific, still headline-style (5-10 words) title clearly better.

IF THE ATTACHED MATERIAL IS OFF-TOPIC. If, after reading it, none of the attached items belongs with this theme (different event, different country, only superficially similar wording), do not force it in. Return {"keep_original": true, "reason": "<one specific sentence>"} and the original entry will be published unchanged.

OUTPUT: a single JSON object, no prose preamble, no markdown fences. Either
{
  "title": "<headline-style title, 5-10 words>",
  "summary": "<rewritten prose with inline markdown links>",
  "trigger": "<the original related_news_trigger, unchanged>",
  "anchor_type": "news" | "mixed"
}
or
{"keep_original": true, "reason": "..."}

anchor_type: "mixed" when the rewritten summary integrates substantive social/newsletter argument reacting to the event; "news" when the attachments are essentially further reporting of the same event.

Do not output heat_level, platforms, topics or related_news_trigger as separate fields; those are handled programmatically. Where the SYSTEM_PROMPT below distinguishes conversation_themes from conversation_roundups, ignore that distinction: this is one entry in a single ranked list.

================================================================
ORIGINAL BRIEFING SYSTEM PROMPT (for rule reference):
================================================================

"""


# ────────────────────────────────────────────────────────────────────────
# URL helpers
# ────────────────────────────────────────────────────────────────────────

def normalize_url(u: str) -> str:
    """Strip scheme, leading www., query, fragment and trailing slash;
    lower-case the host. Empty string for empty/invalid input."""
    u = (u or "").strip()
    if not u:
        return ""
    try:
        p = urlsplit(u)
    except ValueError:
        return u.lower()
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    if not host:  # bare string without scheme
        return u.lower().rstrip("/")
    return f"{host}{path}"


def theme_cited_urls(theme: dict) -> list[str]:
    """Distinct raw URLs a v1 theme cites: markdown links inside the
    summary plus platforms[].url."""
    seen: dict[str, str] = {}
    for u in _MD_LINK_RE.findall(theme.get("summary") or ""):
        seen.setdefault(normalize_url(u), u)
    for p in theme.get("platforms") or []:
        u = (p or {}).get("url") or ""
        if u:
            seen.setdefault(normalize_url(u), u)
    seen.pop("", None)
    return list(seen.values())


def cluster_norm_urls(c: Cluster) -> set[str]:
    return {normalize_url(u) for u in c.all_urls if normalize_url(u)}


def theme_news_outlets(theme: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in theme.get("platforms") or []:
        name = ((p or {}).get("name") or "").strip()
        if not name or name.lower() in SOCIAL_PLATFORM_NAMES:
            continue
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


def build_url_to_item_map(items: list[CorpusItem], conn: sqlite3.Connection,
                          end_dt: datetime, days: int = 7) -> dict[str, int]:
    """normalized url -> item id. Corpus items first (their URLs are
    already unwrapped), then a raw DB lookup over the past `days` as a
    fallback for URLs v1 cited from outside the 24h corpus."""
    m: dict[str, int] = {}
    start = (end_dt - timedelta(days=days)).isoformat()
    for r in conn.execute(
            "SELECT id, url FROM items WHERE published_at >= ? AND url IS NOT NULL",
            (start,)):
        n = normalize_url(r["url"])
        if n:
            m.setdefault(n, int(r["id"]))
    for it in items:  # corpus wins (unwrapped URLs, thread-merged URLs)
        if it.url:
            m[normalize_url(it.url)] = it.id
        for mid, mu in zip(it.merged_ids or [], it.merged_urls or []):
            if mu:
                m.setdefault(normalize_url(mu), mid)
    m.pop("", None)
    return m


# ────────────────────────────────────────────────────────────────────────
# Stage 3 — attachment
# ────────────────────────────────────────────────────────────────────────

def embed_themes(themes: list[dict], openai_client) -> np.ndarray:
    """Embed `theme + "\\n" + related_news_trigger + "\\n" + summary` with
    the same model as the corpus. L2-normalized."""
    if not themes:
        return np.zeros((0, 1536), dtype=np.float32)
    texts = []
    for t in themes:
        txt = "\n".join([(t.get("theme") or "").strip(),
                         (t.get("related_news_trigger") or "").strip(),
                         (t.get("summary") or "").strip()]).strip()
        texts.append((txt or " ")[:8000])
    resp = openai_client.embeddings.create(model=OPENAI_EMBED_MODEL, input=texts)
    embs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    return embs / np.clip(np.linalg.norm(embs, axis=1, keepdims=True), 1e-8, None)


def cluster_centroid(c: Cluster, embs: np.ndarray, id_to_row: dict[int, int]) -> np.ndarray:
    rows = [id_to_row[it.id] for it in c.items if it.id in id_to_row]
    if not rows:
        return np.zeros(embs.shape[1], dtype=np.float32)
    v = embs[rows].mean(axis=0)
    return v / max(float(np.linalg.norm(v)), 1e-8)


def attach_clusters(clusters: list[Cluster], themes: list[dict],
                    theme_embs: np.ndarray, embs: np.ndarray,
                    id_to_row: dict[int, int],
                    threshold: float, min_url_overlap: int
                    ) -> tuple[dict[int, list[Cluster]], list[dict]]:
    """For every cluster: cosine to every theme + URL overlap with every
    theme. Candidate themes are ordered by (url_overlap, cosine) so a
    shared URL outranks wording similarity; the first candidate is the
    'best theme'. Attach there if cosine >= threshold OR
    url_overlap >= min_url_overlap. Returns (theme_idx -> clusters,
    attach_log)."""
    theme_urls = [{normalize_url(u) for u in theme_cited_urls(t)} for t in themes]
    attached: dict[int, list[Cluster]] = {}
    log: list[dict] = []
    for c in clusters:
        rec = {"cluster_id": c.cluster_id, "size": c.size,
               "first_title": (c.items[0].title or c.items[0].body or "")[:80],
               "sources": cluster_sources(c)}
        if not themes:
            rec.update({"best_theme": None, "cosine": None, "url_overlap": 0,
                        "attached": False})
            log.append(rec)
            continue
        cen = cluster_centroid(c, embs, id_to_row)
        cos = theme_embs @ cen
        curls = cluster_norm_urls(c)
        overlaps = [len(curls & tu) for tu in theme_urls]
        order = sorted(range(len(themes)),
                       key=lambda i: (-overlaps[i], -float(cos[i])))
        best = order[0]
        top_cos = int(np.argmax(cos))
        rec.update({
            "best_theme": best,
            "best_theme_title": themes[best].get("theme"),
            "cosine": round(float(cos[best]), 3),
            "url_overlap": overlaps[best],
            "top_cosine_theme": top_cos,
            "top_cosine": round(float(cos[top_cos]), 3),
            "all_cosines": [round(float(x), 3) for x in cos],
        })
        ok_cos = float(cos[best]) >= threshold
        ok_url = overlaps[best] >= min_url_overlap
        rec["attached"] = bool(ok_cos or ok_url)
        rec["attach_reason"] = ("cosine" if ok_cos else "") + \
            ("+" if ok_cos and ok_url else "") + ("url" if ok_url else "")
        if rec["attached"]:
            attached.setdefault(best, []).append(c)
        log.append(rec)
        logger.info(
            f"ATTACH cluster {c.cluster_id} n={c.size} -> T{best} "
            f"cos={rec['cosine']} url={overlaps[best]} "
            f"{'ATTACHED' if rec['attached'] else 'standalone'}")
    return attached, log


# ────────────────────────────────────────────────────────────────────────
# Stage 4 — rewrite attached themes
# ────────────────────────────────────────────────────────────────────────

def _allowed_links(theme: dict, clusters: list[Cluster],
                   historical: list[CorpusItem]) -> set[str]:
    from v3_1_runner import _ITEM_META
    allowed = {normalize_url(u) for u in theme_cited_urls(theme)}
    for c in clusters:
        allowed |= cluster_norm_urls(c)
        for it in c.items:
            for l in (_ITEM_META.get(it.id, {}).get("enrich_links") or []):
                if l.get("url"):
                    allowed.add(normalize_url(l["url"]))
    for it in historical:
        if it.url:
            allowed.add(normalize_url(it.url))
        for l in (_ITEM_META.get(it.id, {}).get("enrich_links") or []):
            if l.get("url"):
                allowed.add(normalize_url(l["url"]))
    allowed.discard("")
    return allowed


def rewrite_theme_with_clusters(theme: dict, clusters: list[Cluster],
                                historical: Optional[list] = None,
                                anthropic_client: Optional[anthropic.Anthropic] = None,
                                tracker: Optional[RunCostTracker] = None,
                                ) -> tuple[Optional[dict], Optional[str]]:
    """ONE Opus call. Same model, token limit, streaming and prompt
    caching as v4's writer. Returns (rewrite_dict, None) on success or
    (None, reason) when the model kept the original or the call failed."""
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()
    system_prompt = V4B_REWRITE_PREFIX + V1_SYSTEM_PROMPT
    historical = historical or []
    original = {k: theme.get(k) for k in
                ("theme", "summary", "related_news_trigger", "platforms",
                 "heat_level", "topics")}
    cluster_blocks = "\n\n".join(
        f"Cluster ID: {c.cluster_id} (size={c.size})\n{_format_cluster_for_sonnet(c)}"
        for c in clusters)
    hist_block = (
        f"\n\nPAST 6 DAYS CONTEXT (topically related items from earlier in "
        f"the week — cite ONLY when directly relevant, with explicit time "
        f"stamps like 'Earlier this week,...' or 'Tuesday,...'; ignore if "
        f"none apply):\n{_format_items_for_sonnet(historical, max_body=600)}"
        if historical else ""
    )
    user_content = (
        f"ORIGINAL THEME (already published today; keep its anchor and substance):\n"
        f"{json.dumps(original, indent=2, ensure_ascii=False)}\n\n"
        f"ATTACHED CLUSTERS (integrate these on merit):\n{cluster_blocks}"
        f"{hist_block}\n\n"
        f"Return the rewritten entry as the JSON object described, or "
        f"{{\"keep_original\": true, \"reason\": \"...\"}}."
    )
    try:
        response_text = ""
        with anthropic_client.messages.stream(
            model=OPUS_MODEL,
            max_tokens=4096,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
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
        logger.warning(f"rewrite of {theme.get('theme')!r} failed: {e}")
        return None, f"opus_call_failed: {e}"

    obj = _parse_json_object(response_text)
    if obj is None:
        return None, "unparseable_json"
    if obj.get("keep_original"):
        return None, f"keep_original: {obj.get('reason') or 'no reason given'}"

    title = (obj.get("title") or "").strip() or (theme.get("theme") or "").strip()
    summary = (obj.get("summary") or "").strip()
    if not summary:
        return None, "missing_summary"
    trigger = obj.get("trigger")
    trigger = (trigger or "").strip() if isinstance(trigger, str) else ""
    if not trigger:
        trigger = (theme.get("related_news_trigger") or "").strip()
    anchor_type = str(obj.get("anchor_type") or "").strip().lower()
    if anchor_type not in ("news", "mixed"):
        has_social = any((it.source or "") in SOCIAL_SOURCES
                         for c in clusters for it in c.items)
        anchor_type = "mixed" if has_social else "news"

    # Guardrail: every link must come from the allowed set. A foreign
    # link means the model reached outside its inputs; keep the original.
    allowed = _allowed_links(theme, clusters, historical)
    foreign = [u for u in _MD_LINK_RE.findall(summary)
               if normalize_url(u) not in allowed]
    if foreign:
        return None, f"foreign_links: {foreign[:3]}"

    orig_paras = (theme.get("summary") or "").count("\n\n") + 1
    new_paras = summary.count("\n\n") + 1
    if orig_paras >= 2 and new_paras == 1:
        logger.warning(f"rewrite of {theme.get('theme')!r} collapsed "
                       f"{orig_paras} paragraphs into one")
    return {"title": title, "summary": summary, "trigger": trigger,
            "anchor_type": anchor_type,
            "original_len": len(theme.get("summary") or ""),
            "rewritten_len": len(summary),
            "original_paragraphs": orig_paras,
            "rewritten_paragraphs": new_paras}, None


# ────────────────────────────────────────────────────────────────────────
# Entries
# ────────────────────────────────────────────────────────────────────────

def make_theme_entry(idx: int, theme: dict, attached: list[Cluster],
                     rewrite: Optional[dict], rewrite_status: str,
                     url_to_item: dict[str, int], attach_log_entries: list[dict]
                     ) -> dict:
    cited = theme_cited_urls(theme)
    theme_item_ids: list[int] = []
    unmatched: list[str] = []
    for u in cited:
        iid = url_to_item.get(normalize_url(u))
        if iid is None:
            unmatched.append(u)
        elif iid not in theme_item_ids:
            theme_item_ids.append(iid)

    attached_ids: list[int] = []
    for c in attached:
        for iid in cluster_item_ids(c):
            if iid not in attached_ids:
                attached_ids.append(iid)
    total_attached_items = sum(c.size for c in attached)

    sources = Counter()
    for c in attached:
        sources.update(cluster_sources(c))
    outlets = list(theme_news_outlets(theme))
    seen = {o.lower() for o in outlets}
    for c in attached:
        for o in cluster_news_outlets(c):
            if o.lower() not in seen:
                seen.add(o.lower())
                outlets.append(o)

    if rewrite is not None:
        title, summary = rewrite["title"], rewrite["summary"]
        trigger, anchor_type = rewrite["trigger"], rewrite["anchor_type"]
    else:
        title = (theme.get("theme") or "").strip()
        summary = (theme.get("summary") or "").strip()
        trigger = (theme.get("related_news_trigger") or "").strip() or None
        anchor_type = "news"

    return {
        "title": title,
        "summary": summary,
        "trigger": trigger,
        "anchor_type": anchor_type,
        "origin": "theme+attached" if attached else "theme",
        "v1_theme_index": idx,
        "v1_theme_title": theme.get("theme"),
        "attached_cluster_ids": [c.cluster_id for c in attached],
        "attach_log": attach_log_entries,
        "rewrite_status": rewrite_status,
        "item_ids": theme_item_ids + [i for i in attached_ids if i not in theme_item_ids],
        "theme_item_ids": theme_item_ids,
        "attached_item_ids": attached_ids,
        "v1_cited_urls": cited,
        "unmatched_urls": unmatched,
        "sources": dict(sources),
        "news_outlets": outlets,
        "n_news_sources": len(outlets),
        # size = distinct cited URLs in the v1 theme + attached items
        "cluster_size": len(cited) + total_attached_items,
        "cluster_id": THEME_ENTRY_ID_BASE + idx,
    }


def make_cluster_entry(entry: dict, c: Cluster, attach_rec: dict) -> dict:
    outlets = cluster_news_outlets(c)
    entry.update({
        "item_ids": cluster_item_ids(c),
        "sources": cluster_sources(c),
        "news_outlets": outlets,
        "n_news_sources": len(outlets),
        "cluster_id": c.cluster_id,
        "cluster_size": c.size,
        "origin": "cluster",
        "v1_theme_index": None,
        "attached_cluster_ids": [],
        "attach_log": [attach_rec],
        "rewrite_status": None,
    })
    return entry


def theme_entry_to_theme(e: dict, theme: dict, attached: list[Cluster]) -> dict:
    """Render-shape mirror for a theme-based entry: v1's platform badges
    kept, plus one badge per new outlet / social platform found in the
    attached clusters."""
    platforms = [dict(p) for p in (theme.get("platforms") or []) if p]
    seen = {(p.get("name") or "").lower() for p in platforms}
    for c in attached:
        for it in c.items:
            src = (it.source or "").lower()
            name = news_outlet_name(it) if src in NEWS_SOURCES else src
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            platforms.append({"name": name, "reply_count": 0,
                              "sentiment": "neutral", "url": it.url or ""})
    return {
        "theme": e["title"],
        "summary": e["summary"],
        "related_news_trigger": e.get("trigger") or "",
        "platforms": platforms,
        "heat_level": theme.get("heat_level") or "medium",
        "topics": list(theme.get("topics") or []),
        "_v4_rank": e["rank"],
        "_v4_cluster_id": e["cluster_id"],
        "_v4b_origin": e["origin"],
    }


# ────────────────────────────────────────────────────────────────────────
# Stage 6 — dedup (theme always beats standalone)
# ────────────────────────────────────────────────────────────────────────

def dedup_entries_v4b(entries: list[dict], threshold: float,
                      openai_client=None) -> tuple[list[dict], list[dict]]:
    """Same embedding and pairing as v4_runner.dedup_entries; the loser
    is chosen by (is_theme, cluster_size, n_news_sources, earlier index)
    so a theme-based entry never loses to a standalone cluster."""
    if len(entries) < 2:
        return entries, []
    pseudo = [CorpusItem(id=i, source="entry", url="", title=e["title"],
                         body=e["summary"], author="", published_at="",
                         feed_name="")
              for i, e in enumerate(entries)]
    embs = embed_corpus(pseudo, openai_client=openai_client)
    sims = embs @ embs.T
    n = len(entries)
    pairs = sorted(((float(sims[i, j]), i, j)
                    for i in range(n) for j in range(i + 1, n)
                    if sims[i, j] >= threshold), reverse=True)
    alive = [True] * n
    log: list[dict] = []

    def key(k: int):
        e = entries[k]
        return (1 if e["origin"] != "cluster" else 0,
                e["cluster_size"], e["n_news_sources"], -k)

    for score, i, j in pairs:
        if not (alive[i] and alive[j]):
            continue
        loser, winner = (j, i) if key(j) < key(i) else (i, j)
        alive[loser] = False
        log.append({"score": round(score, 3),
                    "kept": entries[winner]["title"],
                    "kept_id": entries[winner]["cluster_id"],
                    "kept_origin": entries[winner]["origin"],
                    "dropped": entries[loser]["title"],
                    "dropped_id": entries[loser]["cluster_id"],
                    "dropped_origin": entries[loser]["origin"]})
        logger.info(f"DEDUP cos={score:.3f}: kept {entries[winner]['title'][:60]!r} "
                    f"({entries[winner]['origin']}) / dropped "
                    f"{entries[loser]['title'][:60]!r} ({entries[loser]['origin']})")
    return [e for k, e in enumerate(entries) if alive[k]], log


# ────────────────────────────────────────────────────────────────────────
# Send
# ────────────────────────────────────────────────────────────────────────

def send_v4b_shadow_email(v4b: dict, to: str, source_v1_id: int) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set")
        return False
    premium_html, _free_html, top_theme = _render_variants(v4b)
    html = _with_unsub_footer(premium_html, None)
    entries = v4b.get("entries") or []
    n_theme = sum(1 for e in entries if e["origin"] != "cluster")
    subject = (f"{SHADOW_SUBJECT_PREFIX}{_subscriber_subject(v4b, top_theme)} "
               f"| {len(entries)} entries ({n_theme} theme, "
               f"{len(entries) - n_theme} standalone) | vs v1 #{source_v1_id}")
    ok = _post_resend(api_key, "https://api.resend.com/emails",
                      {"from": EMAIL_FROM, "to": [to],
                       "subject": subject, "html": html})
    if ok:
        logger.info(f"v4b shadow email sent to {to}: {subject}")
    return ok


# ────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────

def _opus_cents(tracker: RunCostTracker) -> float:
    return tracker.anthropic[OPUS_MODEL]["microcents"] / 100 if OPUS_MODEL in tracker.anthropic else 0.0


def main() -> None:
    p = argparse.ArgumentParser(description="Pulse v4b runner: v1 themes + attached clusters")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--lookback-hours", type=int, default=24)
    p.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE)
    p.add_argument("--max-entries", type=int, default=DEFAULT_MAX_ENTRIES)
    p.add_argument("--dedup-threshold", type=float, default=DEFAULT_DEDUP_THRESHOLD)
    p.add_argument("--attach-threshold", type=float, default=DEFAULT_ATTACH_THRESHOLD,
                   help="min cosine(cluster centroid, theme) to attach")
    p.add_argument("--attach-min-url-overlap", type=int, default=DEFAULT_ATTACH_MIN_URL_OVERLAP,
                   help="min count of cluster URLs also cited by the theme to attach")
    p.add_argument("--to", default=None,
                   help="single-recipient shadow send ('[V4B SHADOW]' subject)")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--no-store", action="store_true")
    p.add_argument("--dump-json", default=None)
    args = p.parse_args()

    t_start = time.time()
    timings: dict[str, float] = {}

    def mark(stage: str, t0: float) -> None:
        timings[stage] = round(time.time() - t0, 1)

    tracker = RunCostTracker()
    spend_before = get_spend_cents().get("total_cents", 0)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # 1. Scaffold — themes are the backbone
    v1_id, v1, v1_created = load_v1_scaffold(conn)
    v1["_briefing_id"] = v1_id
    end_dt = datetime.fromisoformat(v1_created.replace("Z", "+00:00"))
    themes: list[dict] = list(v1.get("conversation_themes") or [])
    print(f"loaded v1 scaffold briefing #{v1_id} created at {v1_created}; "
          f"{len(themes)} v1 themes as backbone; dropping "
          f"{len(v1.get('conversation_roundups') or [])} v1 roundups")
    print(f"corpus window: {args.lookback_hours}h ending {end_dt.isoformat()}")

    # 2. Corpus + embeddings + HDBSCAN (same chain as v4)
    t0 = time.time()
    items = load_corpus_v3_1(conn, hours=args.lookback_hours, end=end_dt)
    print(f"corpus: {len(items)} items after filter")
    from openai import OpenAI
    oai = TrackedOpenAI(OpenAI(), tracker)
    embs = embed_corpus(items, openai_client=oai)
    clusters = cluster_items(items, embs, min_cluster_size=args.min_cluster_size)
    id_to_row = {it.id: i for i, it in enumerate(items)}
    clustered_ids = {it.id for c in clusters for it in c.items}
    print(f"clusters: {len(clusters)}; noise items: {len(items) - len(clustered_ids)}")
    mark("embed_cluster", t0)

    counts: dict = {
        "lookback_hours": args.lookback_hours,
        "min_cluster_size": args.min_cluster_size,
        "max_entries": args.max_entries,
        "dedup_threshold": args.dedup_threshold,
        "attach_threshold": args.attach_threshold,
        "attach_min_url_overlap": args.attach_min_url_overlap,
        "v1_themes": len(themes),
        "items_after_filter": len(items),
        "items_in_hdbscan_noise": len(items) - len(clustered_ids),
        "clusters_total": len(clusters),
    }

    anth_real = anthropic.Anthropic()
    anth = TrackedAnthropic(anth_real, tracker)

    t0 = time.time()
    us_clusters = [c for c in clusters if is_us_housing_relevant(c, anthropic_client=anth)]
    counts["us_housing_clusters"] = len(us_clusters)
    print(f"US-housing clusters: {len(us_clusters)} of {len(clusters)}")
    mark("us_housing_check", t0)

    t0 = time.time()
    sub_clusters: list[Cluster] = []
    for c in us_clusters:
        sub_clusters.extend(subcluster_by_shared_story(c, anthropic_client=anth))
    counts["sub_clusters_total"] = len(sub_clusters)
    print(f"sub-clusters: {len(sub_clusters)}")
    mark("subcluster", t0)

    pre_merge = len(sub_clusters)
    sub_clusters, merge_log = merge_adjacent_clusters(sub_clusters, embs, id_to_row)
    for e in merge_log:
        tag = "MERGED" if e["merged"] else "below-thresh"
        print(f"  [{tag}] {e['a']} + {e['b']} shared={e['shared_authors']} cos={e['cos']}")
    counts["sub_clusters_after_merge"] = len(sub_clusters)
    print(f"sub-clusters after author/topic merge: {len(sub_clusters)} (was {pre_merge})")

    t0 = time.time()
    coherent: list[Cluster] = []
    gate_fail: list[dict] = []
    for sc in sub_clusters:
        ok, reason = coherence_check(sc, anthropic_client=anth)
        if ok:
            coherent.append(sc)
        else:
            gate_fail.append({"cluster_id": sc.cluster_id, "size": sc.size, "reason": reason})
            print(f"  sub-cluster {sc.cluster_id} (n={sc.size}) -> GATE FAIL: {reason}")
    coherent.sort(key=lambda c: -c.size)
    counts["coherent_clusters"] = len(coherent)
    print(f"coherent clusters: {len(coherent)}")
    mark("coherence_gate", t0)

    # 3. Attachment
    t0 = time.time()
    theme_embs = embed_themes(themes, oai)
    attached_by_theme, attach_log = attach_clusters(
        coherent, themes, theme_embs, embs, id_to_row,
        args.attach_threshold, args.attach_min_url_overlap)
    attached_cids = {c.cluster_id for cs in attached_by_theme.values() for c in cs}
    standalone = [c for c in coherent if c.cluster_id not in attached_cids]
    counts["clusters_attached"] = len(attached_cids)
    counts["themes_with_attachments"] = len(attached_by_theme)
    counts["clusters_standalone"] = len(standalone)
    print(f"\nattachment (threshold={args.attach_threshold}, min_url_overlap="
          f"{args.attach_min_url_overlap}):")
    print(f"  {'cluster':>8} {'n':>2} {'best':>4} {'cos':>6} {'url':>3} {'attached':>9}  first item / theme")
    for r in attach_log:
        print(f"  {r['cluster_id']:>8} {r['size']:>2} T{r['best_theme']:<3} "
              f"{r['cosine']:>6} {r['url_overlap']:>3} "
              f"{('YES ' + r['attach_reason']) if r['attached'] else 'no':>9}  "
              f"{r['first_title'][:50]!r} -> {str(r.get('best_theme_title'))[:45]!r}"
              + (f"  (top-cos T{r['top_cosine_theme']}={r['top_cosine']})"
                 if r.get("top_cosine_theme") != r["best_theme"] else ""))
    mark("attach", t0)

    # Historical pool (embedded inside load_historical_pool; tokens estimated)
    t0 = time.time()
    hist_items, hist_embs = load_historical_pool(conn, end_dt)
    tracker.openai_tokens_estimated += sum(
        len(f"{it.title}\n\n{(it.body or '')[:1500]}") // 4 for it in hist_items)
    counts["historical_pool_size"] = len(hist_items)
    mark("historical_pool", t0)

    url_to_item = build_url_to_item_map(items, conn, end_dt)

    # 4. Rewrite attached themes; pass the rest through
    t0 = time.time()
    opus_before = _opus_cents(tracker)
    entries: list[dict] = []
    theme_by_entry_id: dict[int, tuple[dict, list[Cluster]]] = {}
    rewrite_log: list[dict] = []
    n_rewrite_calls = 0
    for idx, theme in enumerate(themes):
        att = attached_by_theme.get(idx, [])
        att_recs = [r for r in attach_log if r["cluster_id"] in {c.cluster_id for c in att}]
        rewrite = None
        if att:
            all_items = [it for c in att for it in c.items]
            pseudo = Cluster(cluster_id=THEME_ENTRY_ID_BASE + idx, items=all_items)
            rows = [id_to_row[it.id] for it in all_items if it.id in id_to_row]
            c_embs = embs[rows] if rows else np.zeros((0, 1536), dtype=np.float32)
            ctx_items = historical_context_for_cluster(pseudo, c_embs, hist_items, hist_embs)
            print(f"  rewrite T{idx} {theme.get('theme')[:60]!r} with "
                  f"{len(att)} cluster(s) / {len(all_items)} items +{len(ctx_items)} hist -> Opus...")
            n_rewrite_calls += 1
            rewrite, reason = rewrite_theme_with_clusters(
                theme, att, historical=ctx_items, anthropic_client=anth_real,
                tracker=tracker)
            if rewrite is None:
                status = f"kept_original ({reason})"
                print(f"    KEPT ORIGINAL: {reason}")
            else:
                status = "rewritten"
                print(f"    OK [{rewrite['anchor_type']}] {rewrite['title'][:70]!r} "
                      f"len {rewrite['original_len']} -> {rewrite['rewritten_len']}")
            rewrite_log.append({"theme_index": idx, "theme": theme.get("theme"),
                                "attached_cluster_ids": [c.cluster_id for c in att],
                                "attached_items": len(all_items),
                                "status": status,
                                "new_title": rewrite["title"] if rewrite else None,
                                "anchor_type": rewrite["anchor_type"] if rewrite else "news",
                                "original_len": len(theme.get("summary") or ""),
                                "rewritten_len": rewrite["rewritten_len"] if rewrite else None,
                                "original_paragraphs": rewrite["original_paragraphs"] if rewrite else None,
                                "rewritten_paragraphs": rewrite["rewritten_paragraphs"] if rewrite else None})
        else:
            status = "no_attachments"
        e = make_theme_entry(idx, theme, att, rewrite, status, url_to_item, att_recs)
        theme_by_entry_id[e["cluster_id"]] = (theme, att)
        entries.append(e)
        if e["unmatched_urls"]:
            print(f"    T{idx}: {len(e['theme_item_ids'])} cited URLs matched to items, "
                  f"{len(e['unmatched_urls'])} unmatched")
    rewrite_cents = round(_opus_cents(tracker) - opus_before, 2)
    counts["theme_entries"] = len(entries)
    counts["rewrite_calls"] = n_rewrite_calls
    counts["themes_rewritten"] = sum(1 for r in rewrite_log if r["status"] == "rewritten")
    counts["themes_kept_original"] = n_rewrite_calls - counts["themes_rewritten"]
    mark("rewrite", t0)

    # 5. Standalone clusters
    t0 = time.time()
    opus_before = _opus_cents(tracker)
    skips: list[dict] = []
    cluster_by_id: dict[int, Cluster] = {}
    attach_by_cid = {r["cluster_id"]: r for r in attach_log}
    print(f"\nwriting {len(standalone)} standalone entries with Opus...")
    for i, c in enumerate(standalone, 1):
        rows = [id_to_row[it.id] for it in c.items if it.id in id_to_row]
        c_embs = embs[rows] if rows else np.zeros((0, 1536), dtype=np.float32)
        ctx_items = historical_context_for_cluster(c, c_embs, hist_items, hist_embs)
        print(f"  [{i}/{len(standalone)}] cluster {c.cluster_id} n={c.size} "
              f"+{len(ctx_items)} hist -> Opus...")
        entry, skip_reason = write_entry_for_cluster(
            c, historical=ctx_items, anthropic_client=anth_real, tracker=tracker)
        if entry is None:
            skips.append({"cluster_id": c.cluster_id, "size": c.size,
                          "reason": skip_reason,
                          "first_title": (c.items[0].title or c.items[0].body or "")[:80]})
            print(f"    SKIP ({c.cluster_id}): {skip_reason}")
            continue
        entries.append(make_cluster_entry(entry, c, attach_by_cid.get(c.cluster_id, {})))
        cluster_by_id[c.cluster_id] = c
        print(f"    OK [{entry['anchor_type']}]: {entry['title'][:70]!r}")
    standalone_cents = round(_opus_cents(tracker) - opus_before, 2)
    counts["standalone_calls"] = len(standalone)
    counts["standalone_written"] = len(cluster_by_id)
    counts["standalone_skips"] = len(skips)
    mark("standalone_write", t0)

    # 6. Dedup (theme wins)
    t0 = time.time()
    entries, dedup_log = dedup_entries_v4b(entries, args.dedup_threshold, openai_client=oai)
    counts["dedup_drops"] = len(dedup_log)
    mark("dedup", t0)

    # 7. Rank (v4 formula)
    entries = rank_entries(entries, args.max_entries)
    counts["entries_after_rank_cap"] = len(entries)

    # 8/9. Map to render shape, post-process, assemble
    t0 = time.time()
    themes_out: list[dict] = []
    for e in entries:
        if e["origin"] == "cluster":
            t = entry_to_theme(e, cluster_by_id[e["cluster_id"]])
            t["_v4b_origin"] = "cluster"
        else:
            th, att = theme_by_entry_id[e["cluster_id"]]
            t = theme_entry_to_theme(e, th, att)
        themes_out.append(t)
    entries, themes_out, post_stats = postprocess_entries(entries, themes_out, conn)
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    for t in themes_out:
        t["_v4_rank"] = next(e["rank"] for e in entries if e["cluster_id"] == t["_v4_cluster_id"])
    counts["final_entries"] = len(entries)
    counts["final_theme_entries"] = sum(1 for e in entries if e["origin"] != "cluster")
    counts["final_standalone_entries"] = sum(1 for e in entries if e["origin"] == "cluster")
    mark("postprocess", t0)

    timings["total"] = round(time.time() - t_start, 1)
    cost = tracker.summary()
    cost["anthropic_spend_table_delta_cents"] = round(
        get_spend_cents().get("total_cents", 0) - spend_before, 2)
    cost["opus_calls"] = {"rewrites": n_rewrite_calls, "standalone_writes": len(standalone),
                          "total": n_rewrite_calls + len(standalone)}
    cost["opus_cents_by_phase"] = {"rewrites": rewrite_cents, "standalone_writes": standalone_cents}
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "v4b_attach",
        "source_v1_briefing_id": v1_id,
        "v1_theme_titles": [t.get("theme") for t in themes],
        "counts": counts,
        "timings_seconds": timings,
        "attach_log": attach_log,
        "rewrite_log": rewrite_log,
        "skips": skips,
        "dedup_drops": dedup_log,
        "coherence_gate_failures": gate_fail,
        "merge_log": merge_log,
        "postprocess": post_stats,
        "cost": cost,
    }
    v4b = build_v4_briefing(v1, entries, themes_out, conn, meta, post_stats)
    v4b["_v4b_meta"] = v4b.pop("_v4_meta")

    print(f"\n=== v4b counts: {json.dumps(counts, indent=2)} ===")
    print(f"=== v4b timings (s): {json.dumps(timings)} ===")
    print(f"=== v4b cost: {json.dumps(cost, indent=2)} ===")
    print(f"\nrewrites ({len(rewrite_log)}):")
    for r in rewrite_log:
        print(f"  - T{r['theme_index']} {r['theme'][:55]!r} + clusters {r['attached_cluster_ids']}: "
              f"{r['status']}  len {r['original_len']} -> {r['rewritten_len']}")
    print(f"\nstandalone skips ({len(skips)}):")
    for s in skips:
        print(f"  - {s['cluster_id']} n={s['size']}: {s['reason']}  [{s['first_title']!r}]")
    print(f"\ndedup drops ({len(dedup_log)}):")
    for d in dedup_log:
        print(f"  - cos={d['score']}: KEPT {d['kept']!r} ({d['kept_origin']}) / "
              f"DROPPED {d['dropped']!r} ({d['dropped_origin']})")
    print(f"\nv4b entries ({len(entries)}):")
    for e in entries:
        print(f"  {e['rank']:>2}. score={e['score']:>2} [{e['anchor_type']:<6}] "
              f"{e['origin']:<14} size={e['cluster_size']:<2} news={e['n_news_sources']} "
              f"att={e['attached_cluster_ids']} {e['title'][:70]}")

    if args.dump_json:
        with open(args.dump_json, "w") as f:
            json.dump(v4b, f, indent=2, default=str)
        print(f"wrote {args.dump_json}")

    email_ok = False
    if not args.no_send:
        if args.to:
            email_ok = send_v4b_shadow_email(v4b, args.to, v1_id)
        else:
            email_ok = send_v3_email_to_subscribers(v4b, v1_id)
        if not email_ok:
            sys.exit(1)
    else:
        print("--no-send set; skipping email")

    if not args.no_store:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(briefings)").fetchall()}
        if "briefing_type" in cols:
            now_iso = datetime.now(timezone.utc).isoformat()
            cur = conn.execute(
                "INSERT INTO briefings (briefing_type, content_json, created_at, "
                "email_sent, email_sent_at) VALUES (?, ?, ?, ?, ?)",
                (BRIEFING_TYPE, json.dumps(v4b, default=str), now_iso,
                 1 if email_ok else 0, now_iso if email_ok else None),
            )
            conn.commit()
            print(f"stored v4b briefing as id={cur.lastrowid} (type={BRIEFING_TYPE})")
    else:
        print("--no-store set; not writing to briefings")


if __name__ == "__main__":
    main()
