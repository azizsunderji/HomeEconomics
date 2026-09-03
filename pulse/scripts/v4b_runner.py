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
  3b. Per-item relevance gate: one Haiku YES/NO call per attached item
     against the theme. NO items are dropped; a cluster that loses every
     item is detached and returned to the standalone pool.
  4. Rewrite each theme that has >= 1 attachment with ONE Opus call
     (V4B_REWRITE_PREFIX + v1 SYSTEM_PROMPT), returning a paragraphs
     list. Length / paragraph / bridge checks; at most ONE retry call;
     then keep_original. The model may also return keep_original.
     Themes with no attachments pass through untouched.
  5. Unattached (or detached) coherent clusters -> v4's
     V4_ENTRY_WRITER_PREFIX writer.
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
    DEFAULT_DB, OPUS_MODEL, HAIKU_MODEL,
    load_corpus_v3_1, coherence_check, merge_adjacent_clusters,
    load_historical_pool, historical_context_for_cluster,
    is_us_housing_relevant, subcluster_by_shared_story,
    _format_items_for_sonnet, _format_cluster_for_sonnet,
    load_v1_scaffold,
    _post_resend,
)
from analysis.roundup_clustering import (  # noqa: E402
    embed_corpus, cluster_items, Cluster, CorpusItem, MIN_CLUSTER_SIZE,
    OPENAI_EMBED_MODEL,
)
from analysis.synthesize import (  # noqa: E402
    SYSTEM_PROMPT as V1_SYSTEM_PROMPT,
    _split_sentences_for_validation,
)
from analysis.anthropic_spend import record_usage as _record_usage, get_spend_cents  # noqa: E402

# v4 pieces reused verbatim (v4_runner.py is not modified).
from delivery.email_lunch import render_lunch_html  # noqa: E402
from delivery.variants import make_free_variant, scrub_archive_links  # noqa: E402
from v3_1_runner import (  # noqa: E402
    get_subscribers, make_unsubscribe_url, RESEND_BATCH_LIMIT, PULSE_POSTAL_ADDRESS,
)
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
EMAIL_FROM = "News at Noon <pulse@home-economics.us>"
PRODUCT_NAME = "News at Noon"
OWN_DOMAINS = ("homeeconomics.substack.com", "home-economics.us", "homeeconomics.us")
SHADOW_SUBJECT_PREFIX = "[V4B SHADOW] "

DEFAULT_ATTACH_THRESHOLD = 0.55
DEFAULT_ATTACH_MIN_URL_OVERLAP = 1
THEME_ENTRY_ID_BASE = 700_000  # synthetic cluster_id for theme entries

# platforms[].name values that are social platforms rather than outlets
SOCIAL_PLATFORM_NAMES = {"twitter", "x", "bluesky", "hackernews", "hn",
                         "reddit", "threads", "mastodon", "linkedin",
                         "youtube", "tiktok"}

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")

# Rewrite guardrails (run 3). The prompt states the soft ceiling
# max(REWRITE_SOFT_RATIO * orig, orig + REWRITE_SOFT_SLACK); the code
# retries once above REWRITE_HARD_RATIO * orig and keeps the original if
# the retry is still over.
REWRITE_SOFT_RATIO = 1.35
REWRITE_SOFT_SLACK = 500
REWRITE_HARD_RATIO = 1.5
REWRITE_MAX_PARA_CHARS = 900
# Local forbidden-bridge check on rewrites, applied at paragraph starts
# only. Deliberately separate from synthesize.FORBIDDEN_BRIDGE_PATTERN
# (which is not edited): these four are the "bolt a sub-story on" forms
# seen in run 2 ("Separately on the GSEs, ...").
_REWRITE_BRIDGE_RE = re.compile(
    r"^\s*(?:Separately\b|In other news\b|On a related note\b|Meanwhile,)",
    re.IGNORECASE)

# Per-item relevance gate (stage 3b). Reuses the Haiku model string the
# v3.1 helpers use so analysis.anthropic_spend prices it.
GATE_MODEL = HAIKU_MODEL
GATE_THEME_SUMMARY_CHARS = 600
GATE_ITEM_BODY_CHARS = 800
GATE_SYSTEM = (
    "You check whether one collected item belongs with one entry of a daily "
    "US housing-economics briefing. Answer with a single word: YES if the "
    "item reports on, or reacts to, the same event or argument as the "
    "theme; NO if it is about a different event, data release, company, "
    "bill or argument, even if it comes from the same author or outlet or "
    "shares vocabulary with the theme. Output YES or NO and nothing else."
)


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
  - KEEP THE ORIGINAL'S PARAGRAPH BREAKS. The original summary is split into paragraphs by blank lines. You return the rewritten summary as a JSON LIST of paragraph strings (`paragraphs`), one string per paragraph, in reading order. Every original paragraph boundary must survive as a boundary between list elements; add further elements for each new voice or data source you introduce. Never collapse the entry into one element; no element may exceed 900 characters.
  - DO NOT ADD NEW LINKS INTO THE ORIGINAL'S SENTENCES. A sentence that carries an original fact keeps only its original link(s). Put every new citation in its own new sentence. (A later URL check strips any sentence whose link cannot be verified; a new link inside an original sentence would take the original fact down with it.)
  - INTEGRATE, DO NOT APPEND. Place each attached voice where it belongs in the argument: further reporting next to the reporting it extends, reaction next to the claim it reacts to. Do NOT add a separate "reaction paragraph" or "social media responded" block at the end. The reader should not be able to tell which sentences were added.
  - CITE ONLY ALLOWED SOURCES. You may link ONLY (a) URLs already linked in the original summary or listed in its platforms, (b) URLs of items in the attached clusters, (c) `enrich_links` URLs carried by those items (use their `anchor_text` verbatim as link text), and (d) PAST 6 DAYS items when genuinely relevant, always with an explicit time stamp ("Earlier this week, ..."). No other outlets, handles or URLs. Never invent an item.
  - USE WHAT IS ATTACHED, ON MERIT. Cite an attached item when it adds a fact, a number, a named argument or a substantive disagreement. Skip attached items that merely repeat the original (say nothing about them), and skip promotional, private-correspondence or off-topic items. An attached cluster can contain an item on a different sub-story (the clustering merges same-author posts); leave such an item out rather than bolting it on as a final paragraph.
  - FIRST-SENTENCE RULE. The summary must stand alone: its first sentence restates the anchor event itself (entity, action, key number). Never open with "But", "And", "Meanwhile", "Still", "However", or any meta-statement about discourse ("The conversation...", "Voices are...", "The discourse...").
  - LENGTH CEILING (hard). The rewritten summary must be no longer than max(1.35 x the original summary's character count, the original + 500 characters). The user message states the exact ceiling in characters. Every sentence you add must add a specific fact, number, quotation or named reaction that is not already in the original; no scene-setting, no context sentences, no "the repricing is global"-style framing, no restating what the original already says in other words. If the attachments contain nothing that meets that bar, return keep_original. Keep the entry to 2-4 short paragraphs. Every paragraph obeys the SYSTEM_PROMPT rules: no paragraph longer than ~3 sentences / ~75 words; a new voice, new data source or time shift starts a new paragraph; the paragraph boundary is the transition.
  - NO BRIDGED SUB-STORIES. Never start a paragraph with "Separately", "In other news", "On a related note" or "Meanwhile,". If a paragraph would need such an opener, its content is a different story: leave it out entirely rather than bolting it on.
  - CITATION FORMAT. Inline `[anchor text](url)` markdown on every attributed claim; a second story from the same outlet gets its own second URL; never put square brackets inside link anchor text; pronoun chains stay tied to the linked speaker. All SYSTEM_PROMPT rules on attribution fidelity, technical precision, the privacy hard gate, no canonization and never narrating insufficient content apply.
  - TITLE. Keep the original title unless the integrated material makes a more specific, still headline-style (5-10 words) title clearly better.

IF THE ATTACHED MATERIAL IS OFF-TOPIC. If, after reading it, none of the attached items belongs with this theme (different event, different country, only superficially similar wording), do not force it in. Return {"keep_original": true, "reason": "<one specific sentence>"} and the original entry will be published unchanged.

OUTPUT: a single JSON object, no prose preamble, no markdown fences. Either
{
  "title": "<headline-style title, 5-10 words>",
  "paragraphs": ["<paragraph 1 prose with inline markdown links>", "<paragraph 2>", ...],
  "trigger": "<the original related_news_trigger, unchanged>",
  "anchor_type": "news" | "mixed"
}
or
{"keep_original": true, "reason": "..."}

`paragraphs` is a JSON array of plain strings (no blank lines inside a string, no "summary" field). The strings are joined with blank lines programmatically.

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


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]


def _opus_stream_text(anthropic_client, system_prompt: str, messages: list[dict],
                      tracker: Optional[RunCostTracker]) -> str:
    """One streamed Opus call (same model, token limit and prompt caching
    as v4's writer). Usage is recorded in the spend table and tracker."""
    response_text = ""
    with anthropic_client.messages.stream(
        model=OPUS_MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": system_prompt,
                 "cache_control": {"type": "ephemeral"}}],
        messages=messages,
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
    return response_text.strip()


def _rewrite_paragraphs_from_obj(obj: dict) -> list[str]:
    """`paragraphs` list preferred; a legacy `summary` string is split on
    blank lines. Empty strings dropped; blank lines inside a list element
    are treated as further breaks."""
    paras = obj.get("paragraphs")
    out: list[str] = []
    if isinstance(paras, list):
        for p in paras:
            if isinstance(p, str):
                out.extend(_split_paragraphs(p))
    elif isinstance(obj.get("summary"), str):
        out = _split_paragraphs(obj["summary"])
    return out


_NUMBER_TOKEN_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def _original_numbers(text: str) -> list[str]:
    """Distinct numeric tokens ($450,000, 6.87%, 16, 2021) in the original
    summary; every one must survive the rewrite verbatim."""
    seen: list[str] = []
    for tok in _NUMBER_TOKEN_RE.findall(text or ""):
        tok = tok.rstrip(".,")
        if tok and tok not in seen:
            seen.append(tok)
    return seen


def _check_rewrite(paras: list[str], orig_summary: str, orig_paras: int) -> list[str]:
    """Violations that trigger the single retry: length over the hard
    ratio, paragraph collapse / over-long paragraph, bridged sub-story
    at a paragraph start, an original number missing. Returns
    human-readable reasons ([] = clean)."""
    reasons: list[str] = []
    orig_len = len(orig_summary or "")
    summary = "\n\n".join(paras)
    hard_ceiling = max(REWRITE_HARD_RATIO * orig_len, orig_len + REWRITE_SOFT_SLACK)
    if len(summary) > hard_ceiling:
        reasons.append(f"length {len(summary)} > hard ceiling {int(hard_ceiling)} (original {orig_len})")
    missing = [n for n in _original_numbers(orig_summary) if n not in summary]
    if missing:
        reasons.append(f"original numbers missing: {missing[:6]}")
    if orig_paras >= 2 and len(paras) < 2:
        reasons.append(f"paragraphs collapsed: original {orig_paras}, rewrite {len(paras)}")
    too_long = [i + 1 for i, p in enumerate(paras) if len(p) > REWRITE_MAX_PARA_CHARS]
    if too_long:
        reasons.append(f"paragraph(s) {too_long} over {REWRITE_MAX_PARA_CHARS} chars")
    bridged = [i + 1 for i, p in enumerate(paras) if _REWRITE_BRIDGE_RE.match(p)]
    if bridged:
        reasons.append(f"bridge opener at paragraph(s) {bridged}")
    return reasons


def rewrite_theme_with_clusters(theme: dict, clusters: list[Cluster],
                                historical: Optional[list] = None,
                                anthropic_client: Optional[anthropic.Anthropic] = None,
                                tracker: Optional[RunCostTracker] = None,
                                ) -> tuple[Optional[dict], Optional[str], dict]:
    """ONE Opus call, plus at most ONE retry call when the first answer
    breaks a length / paragraph / bridge rule. Returns
    (rewrite_dict, None, prov) on success or (None, reason, prov) when
    the model kept the original, the call failed, or the retry still
    violates a rule. `prov` always carries orig_len, rewrite_len, ratio,
    retried, retry_reasons, original_paragraphs, rewritten_paragraphs,
    opus_calls."""
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()
    system_prompt = V4B_REWRITE_PREFIX + V1_SYSTEM_PROMPT
    historical = historical or []
    orig_summary = theme.get("summary") or ""
    orig_len = len(orig_summary)
    orig_paras = len(_split_paragraphs(orig_summary))
    ceiling = int(max(REWRITE_SOFT_RATIO * orig_len, orig_len + REWRITE_SOFT_SLACK))
    prov: dict = {"orig_len": orig_len, "rewrite_len": None, "ratio": None,
                  "retried": False, "retry_reasons": [],
                  "original_paragraphs": orig_paras, "rewritten_paragraphs": None,
                  "length_ceiling": ceiling, "opus_calls": 0}
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
        f"The original summary is {orig_len} characters in {orig_paras} "
        f"paragraph(s). HARD LENGTH CEILING for the rewritten summary: "
        f"{ceiling} characters (joined paragraphs).\n\n"
        f"ATTACHED CLUSTERS (integrate these on merit):\n{cluster_blocks}"
        f"{hist_block}\n\n"
        f"Return the rewritten entry as the JSON object described (with a "
        f"`paragraphs` list), or {{\"keep_original\": true, \"reason\": \"...\"}}."
    )
    messages: list[dict] = [{"role": "user", "content": user_content}]

    def _one_call(label: str):
        try:
            prov["opus_calls"] += 1
            return _opus_stream_text(anthropic_client, system_prompt, messages, tracker), None
        except Exception as e:
            logger.warning(f"rewrite {label} of {theme.get('theme')!r} failed: {e}")
            return None, f"opus_call_failed: {e}"

    def _parse(text: str):
        """-> (obj, paras, reason). reason set only on hard failure."""
        obj = _parse_json_object(text)
        if obj is None:
            return None, [], "unparseable_json"
        if obj.get("keep_original"):
            return obj, [], f"keep_original: {obj.get('reason') or 'no reason given'}"
        paras = _rewrite_paragraphs_from_obj(obj)
        if not paras:
            return obj, [], "missing_paragraphs"
        return obj, paras, None

    response_text, err = _one_call("call 1")
    if err:
        return None, err, prov
    obj, paras, err = _parse(response_text)
    if err:
        return None, err, prov
    prov["rewrite_len"] = len("\n\n".join(paras))
    prov["ratio"] = round(prov["rewrite_len"] / max(orig_len, 1), 3)
    prov["rewritten_paragraphs"] = len(paras)

    violations = _check_rewrite(paras, orig_summary, orig_paras)
    if violations:
        prov["retried"] = True
        prov["retry_reasons"] = violations
        prov["first_attempt"] = {"rewrite_len": prov["rewrite_len"],
                                 "ratio": prov["ratio"],
                                 "paragraphs": len(paras)}
        logger.warning(f"rewrite of {theme.get('theme')!r} violates "
                       f"{violations}; one retry")
        fix_lines = []
        if any(v.startswith("length") for v in violations):
            fix_lines.append(
                f"- Tighten the summary to UNDER {ceiling} characters (joined "
                f"paragraphs; it was {prov['rewrite_len']}). Remove every "
                f"sentence that does not add a specific fact, number, quotation "
                f"or named reaction absent from the original. Cut scene-setting "
                f"and framing sentences first. Cut ONLY sentences you added: do "
                f"not remove, shorten or merge any sentence of the original.")
        if any(v.startswith("original numbers missing") for v in violations):
            fix_lines.append(
                "- Numbers from the original summary are missing. Restore every "
                "original sentence with its figures verbatim; the original's "
                "facts are not the place to save space.")
        if any(v.startswith("paragraphs collapsed") or v.startswith("paragraph(s)")
               for v in violations):
            fix_lines.append(
                f"- Return at least {max(orig_paras, 2)} paragraphs as separate "
                f"list elements, preserving every original paragraph boundary, "
                f"and keep every element under {REWRITE_MAX_PARA_CHARS} characters "
                f"(~3 sentences).")
        if any(v.startswith("bridge opener") for v in violations):
            fix_lines.append(
                "- A paragraph starts with 'Separately' / 'In other news' / 'On a "
                "related note' / 'Meanwhile,'. That paragraph is a bolted-on "
                "sub-story: REMOVE the sub-story entirely (do not just delete the "
                "opener). Keep only material about the anchor event.")
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content":
                         "Your rewrite breaks these hard rules:\n"
                         + "\n".join(f"  * {v}" for v in violations)
                         + "\n\nReturn the corrected JSON object only, same schema "
                           "(`paragraphs` list), or keep_original:\n"
                         + "\n".join(fix_lines)
                         + f"\n- The hard length ceiling of {ceiling} characters "
                           f"(joined paragraphs) still applies; every original "
                           f"sentence, number and link must remain."})
        response_text, err = _one_call("retry")
        if err:
            return None, err, prov
        obj, paras, err = _parse(response_text)
        if err:
            return None, err, prov
        prov["rewrite_len"] = len("\n\n".join(paras))
        prov["ratio"] = round(prov["rewrite_len"] / max(orig_len, 1), 3)
        prov["rewritten_paragraphs"] = len(paras)
        still = _check_rewrite(paras, orig_summary, orig_paras)
        if still:
            prov["retry_still_violates"] = still
            return None, f"retry_still_violates: {'; '.join(still)}", prov

    summary = "\n\n".join(paras)
    title = (obj.get("title") or "").strip() or (theme.get("theme") or "").strip()
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
        return None, f"foreign_links: {foreign[:3]}", prov

    return {"title": title, "summary": summary, "trigger": trigger,
            "anchor_type": anchor_type,
            "original_len": orig_len,
            "rewritten_len": len(summary),
            "original_paragraphs": orig_paras,
            "rewritten_paragraphs": len(paras),
            **{k: prov[k] for k in ("orig_len", "rewrite_len", "ratio", "retried",
                                    "retry_reasons")}}, None, prov


# ────────────────────────────────────────────────────────────────────────
# Stage 3b — per-item relevance gate (Haiku)
# ────────────────────────────────────────────────────────────────────────

def item_belongs_with_theme(theme: dict, it: CorpusItem, anthropic_client,
                            ) -> tuple[Optional[bool], str]:
    """One Haiku YES/NO call. Returns (verdict, raw_answer); verdict is
    None when the call failed (caller fails open and keeps the item)."""
    user = (
        f"THEME\n"
        f"Title: {(theme.get('theme') or '').strip()}\n"
        f"Trigger: {(theme.get('related_news_trigger') or '').strip()}\n"
        f"Summary (first {GATE_THEME_SUMMARY_CHARS} chars): "
        f"{(theme.get('summary') or '')[:GATE_THEME_SUMMARY_CHARS].strip()}\n\n"
        f"ITEM\n"
        f"Source: {it.source}{(' / ' + it.feed_name) if it.feed_name else ''}"
        f"{(' / ' + it.author) if it.author else ''}\n"
        f"Title: {(it.title or '').strip()[:300]}\n"
        f"Text (first {GATE_ITEM_BODY_CHARS} chars): "
        f"{(it.body or '')[:GATE_ITEM_BODY_CHARS].strip()}\n\n"
        f"Does this item report on, or react to, the same event or argument "
        f"as this theme? Answer YES or NO."
    )
    try:
        resp = anthropic_client.messages.create(
            model=GATE_MODEL, max_tokens=4, system=GATE_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = (resp.content[0].text if resp.content else "").strip().upper()
        try:
            _record_usage(GATE_MODEL, resp.usage)
        except Exception:
            pass
        return text.startswith("YES"), text[:8]
    except Exception as e:
        logger.warning(f"relevance gate failed for item {it.id}: {e}")
        return None, f"error: {e}"


def gate_attached_items(theme_idx: int, theme: dict, clusters: list[Cluster],
                        anthropic_client) -> tuple[list[Cluster], list[Cluster], list[dict], int]:
    """Check every item of every attached cluster against the theme.
    Returns (kept_clusters, detached_clusters, drop_log, n_calls).
    kept_clusters are NEW Cluster objects (same cluster_id) holding only
    YES items; a cluster with no YES item is returned in detached_clusters
    unchanged so it can go to the standalone writer."""
    kept: list[Cluster] = []
    detached: list[Cluster] = []
    drops: list[dict] = []
    n_calls = 0
    for c in clusters:
        yes_items: list[CorpusItem] = []
        for it in c.items:
            n_calls += 1
            verdict, raw = item_belongs_with_theme(theme, it, anthropic_client)
            if verdict is None or verdict:
                yes_items.append(it)
                if verdict is None:
                    logger.warning(f"GATE item {it.id} kept (fail-open): {raw}")
                continue
            rec = {"theme_index": theme_idx, "theme": theme.get("theme"),
                   "cluster_id": c.cluster_id, "item_id": it.id,
                   "source": it.source, "author": it.author,
                   "title": (it.title or it.body or "")[:120], "answer": raw}
            drops.append(rec)
            logger.info(f"GATE DROP item {it.id} [{it.source}] "
                        f"{rec['title'][:70]!r} from T{theme_idx} "
                        f"{str(theme.get('theme'))[:50]!r} (cluster {c.cluster_id})")
        if yes_items:
            kept.append(c if len(yes_items) == c.size
                        else Cluster(cluster_id=c.cluster_id, items=yes_items))
        else:
            detached.append(c)
            logger.info(f"GATE DETACH cluster {c.cluster_id} (n={c.size}) from "
                        f"T{theme_idx}: every item answered NO; back to standalone pool")
    return kept, detached, drops, n_calls


# ────────────────────────────────────────────────────────────────────────
# Paragraph restoration after post-processing
# ────────────────────────────────────────────────────────────────────────

def _para_key(text: str) -> str:
    """Comparison form: markdown links reduced to anchor text, whitespace
    collapsed. Stable across _autolink_bare_handles (which wraps a bare
    @handle in a link) and URL corrections."""
    t = re.sub(r"\[([^\]]*)\]\((?:https?://[^)\s]+)\)", r"\1", text or "")
    return re.sub(r"\s+", " ", t).strip().lower()


def restore_paragraph_breaks(pre_summary: str, post_summary: str) -> tuple[str, bool]:
    """synthesize._validate_briefing_urls rejoins a summary with single
    spaces whenever it strips a sentence, losing every paragraph break.
    Rebuild the breaks by mapping each surviving sentence back to the
    paragraph of the pre-post-processing summary it came from. Returns
    (summary, restored)."""
    pre_paras = _split_paragraphs(pre_summary)
    post_paras = _split_paragraphs(post_summary)
    if len(pre_paras) < 2 or len(post_paras) >= len(pre_paras):
        return post_summary, False
    pre_keys = [_para_key(p) for p in pre_paras]
    groups: list[list[str]] = []
    cur_idx: Optional[int] = None
    for sent in _split_sentences_for_validation(post_summary):
        k = _para_key(sent)
        idx = next((i for i, pk in enumerate(pre_keys) if k and k in pk), None)
        if not groups:
            groups.append([sent])
            cur_idx = idx
        elif idx is None or idx == cur_idx:
            groups[-1].append(sent)  # unmatched sentences stay with the current paragraph
        elif cur_idx is None:
            groups[-1].append(sent)  # first matched sentence anchors the open group
            cur_idx = idx
        else:
            groups.append([sent])
            cur_idx = idx
    rebuilt = "\n\n".join(" ".join(g) for g in groups if g)
    if len(_split_paragraphs(rebuilt)) <= len(post_paras):
        return post_summary, False
    return rebuilt, True


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

def _protect_own_links(html: str) -> tuple[str, dict]:
    """make_free_variant walls every external href. Links to our own
    domains (the From Home Economics section, Pro Map) must survive in
    the free edition, so swap them for placeholders first and restore
    after walling. variants.py is not modified."""
    keep: dict = {}

    def _sub(m):
        href = m.group(1)
        if any(d in href for d in OWN_DOMAINS):
            key = f"__OWNLINK_{len(keep)}__"
            keep[key] = href
            return f'href="{key}"'
        return m.group(0)

    return re.sub(r'href="([^"]+)"', _sub, html), keep


def _restore_own_links(html: str, keep: dict) -> str:
    for key, href in keep.items():
        html = html.replace(f'href="{key}"', f'href="{href}"')
    return html


def _render_lunch_variants(v4b: dict) -> tuple[str, str, str]:
    """News at Noon premium + free variants. Premium: every entry,
    working links (archive.ph scrubbed). Free: tiered render (banner,
    top-N entries, withheld list) with every external link walled, except
    links to our own domains."""
    premium_html, top, _n = render_lunch_html(v4b, tier="premium")
    premium_html = scrub_archive_links(premium_html)
    free_raw, _t, _n2 = render_lunch_html(v4b, tier="free")
    free_raw = scrub_archive_links(free_raw)
    protected, keep = _protect_own_links(free_raw)
    free_html = _restore_own_links(make_free_variant(protected), keep)
    return premium_html, free_html, top or ""


def _noon_date_str() -> str:
    """'Thursday, September 3, 2026' in US Eastern time, matching the masthead."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).strftime("%A, %B %-d, %Y")


def _lunch_subject(top_title: str | None = None) -> str:
    """Subject is date-based by the owner's decision: 'News at Noon: <date>'.
    top_title is accepted for call-site compatibility and ignored."""
    return f"{PRODUCT_NAME}: {_noon_date_str()}"


def _lunch_footer(html: str, unsub_url: str | None) -> str:
    """Compliance footer (visible unsubscribe + postal address) for News
    at Noon. Same placement and rules as v3_1_runner._with_unsub_footer,
    with the product name changed and no border line."""
    parts = []
    if unsub_url:
        parts.append(f'<a href="{unsub_url}" style="color:#888888;">Unsubscribe</a>')
    if PULSE_POSTAL_ADDRESS:
        parts.append(PULSE_POSTAL_ADDRESS)
    if not parts:
        return html
    footer = (
        '<div style="max-width:600px;margin:24px auto 0;padding:16px 0 24px;'
        'font-size:12px;color:#888888;text-align:center;">'
        f"You&rsquo;re receiving {PRODUCT_NAME} at this address. "
        + " &middot; ".join(parts) + "</div>"
    )
    idx = html.lower().rfind("</body>")
    return html[:idx] + footer + html[idx:] if idx != -1 else html + footer


def send_v4b_shadow_email(v4b: dict, to: str, source_v1_id: int,
                          tier: str = "premium") -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set")
        return False
    premium_html, free_html, top = _render_lunch_variants(v4b)
    html = _lunch_footer(premium_html if tier == "premium" else free_html, None)
    entries = v4b.get("entries") or []
    n_theme = sum(1 for e in entries if e["origin"] != "cluster")
    subject = (f"{SHADOW_SUBJECT_PREFIX}{_lunch_subject(top)} "
               f"| {len(entries)} entries ({n_theme} theme, "
               f"{len(entries) - n_theme} standalone) | {tier} | vs v1 #{source_v1_id}")
    ok = _post_resend(api_key, "https://api.resend.com/emails",
                      {"from": EMAIL_FROM, "to": [to],
                       "subject": subject, "html": html})
    if ok:
        logger.info(f"v4b shadow email sent to {to}: {subject}")
    return ok


def send_lunch_to_subscribers(v4b: dict, source_v1_id: int) -> bool:
    """Subscriber-mode send, mirroring v3_1_runner.send_v3_email_to_subscribers
    but with the News at Noon template and footer. Free tier gets the
    tiered/walled variant, premium gets working links. Not exercised in
    shadow mode."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set")
        return False
    subscribers = get_subscribers()
    premium_html, free_html, top = _render_lunch_variants(v4b)
    subject = _lunch_subject(top)
    emails: list[dict] = []
    n_premium = n_free = 0
    for sub in subscribers:
        premium = bool(sub.get("premium"))
        unsub_url = make_unsubscribe_url(sub["user_id"]) if sub.get("user_id") else None
        if sub.get("user_id") and not unsub_url:
            logger.warning(f"no unsubscribe URL for user {sub['user_id']} "
                           f"(PULSE_UNSUB_SECRET missing?)")
        msg = {"from": EMAIL_FROM, "to": [sub["email"]], "subject": subject,
               "html": _lunch_footer(premium_html if premium else free_html, unsub_url)}
        if unsub_url:
            msg["headers"] = {"List-Unsubscribe": f"<{unsub_url}>",
                              "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if premium:
            n_premium += 1
        else:
            n_free += 1
        emails.append(msg)
    chunks = [emails[i:i + RESEND_BATCH_LIMIT]
              for i in range(0, len(emails), RESEND_BATCH_LIMIT)]
    sent = 0
    for chunk in chunks:
        if _post_resend(api_key, "https://api.resend.com/emails/batch", chunk):
            sent += len(chunk)
        else:
            logger.error(f"batch of {len(chunk)} failed")
    logger.info(f"{PRODUCT_NAME} sent: {sent}/{len(emails)} "
                f"({n_premium} premium, {n_free} free)")
    return sent > 0


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
    p.add_argument("--tier", choices=["premium", "free"], default="premium",
                   help="which variant the --to shadow send uses")
    p.add_argument("--to", default=None,
                   help="single-recipient shadow send ('[V4B SHADOW]' subject)")
    p.add_argument("--no-send", action="store_true")
    p.add_argument("--no-store", action="store_true")
    p.add_argument("--dump-json", default=None)
    args = p.parse_args()

    # Stream logs when stdout is redirected (tee / nohup).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

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

    # 3b. Per-item relevance gate (Haiku) on every attached item
    t0 = time.time()
    haiku_before = (tracker.anthropic[GATE_MODEL]["microcents"]
                    if GATE_MODEL in tracker.anthropic else 0)
    gate_log: list[dict] = []
    gate_calls = 0
    gate_detached: list[Cluster] = []
    attach_by_cid_rec = {r["cluster_id"]: r for r in attach_log}
    print(f"\nrelevance gate ({GATE_MODEL}) on "
          f"{sum(c.size for cs in attached_by_theme.values() for c in cs)} attached items:")
    for idx in sorted(attached_by_theme):
        kept, detached, drops, n = gate_attached_items(
            idx, themes[idx], attached_by_theme[idx], anth)
        gate_calls += n
        gate_log.extend(drops)
        for d in drops:
            print(f"  DROP item {d['item_id']} [{d['source']}] {d['title'][:60]!r} "
                  f"from T{idx} (cluster {d['cluster_id']})")
        for c in detached:
            print(f"  DETACH cluster {c.cluster_id} n={c.size} from T{idx}: "
                  f"no item passed; back to standalone pool")
            rec = attach_by_cid_rec.get(c.cluster_id)
            if rec is not None:
                rec["attached"] = False
                rec["attach_reason"] = (rec.get("attach_reason") or "") + "; detached_by_gate"
                rec["gate_detached"] = True
        for c in kept:
            rec = attach_by_cid_rec.get(c.cluster_id)
            if rec is not None:
                orig_n = next((oc.size for oc in attached_by_theme[idx]
                               if oc.cluster_id == c.cluster_id), c.size)
                rec["gate_kept_items"] = c.size
                rec["gate_dropped_items"] = orig_n - c.size
        gate_detached.extend(detached)
        if kept:
            attached_by_theme[idx] = kept
        else:
            del attached_by_theme[idx]
    attached_cids = {c.cluster_id for cs in attached_by_theme.values() for c in cs}
    standalone = [c for c in coherent if c.cluster_id not in attached_cids]
    standalone.sort(key=lambda c: -c.size)
    haiku_gate_cents = round(((tracker.anthropic[GATE_MODEL]["microcents"]
                               if GATE_MODEL in tracker.anthropic else 0)
                              - haiku_before) / 100, 2)
    counts["gate_items_checked"] = gate_calls
    counts["gate_items_dropped"] = len(gate_log)
    counts["gate_clusters_detached"] = len(gate_detached)
    counts["gate_haiku_calls"] = gate_calls
    counts["clusters_attached_after_gate"] = len(attached_cids)
    counts["themes_with_attachments_after_gate"] = len(attached_by_theme)
    counts["clusters_standalone_after_gate"] = len(standalone)
    print(f"  gate: {gate_calls} items checked, {len(gate_log)} dropped, "
          f"{len(gate_detached)} cluster(s) detached; "
          f"{len(attached_cids)} clusters still attached to "
          f"{len(attached_by_theme)} theme(s); {len(standalone)} standalone; "
          f"haiku {haiku_gate_cents}c")
    mark("relevance_gate", t0)

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
    n_rewrite_calls = 0        # themes sent to the rewriter
    n_rewrite_opus_calls = 0   # actual Opus calls (first attempts + retries)
    n_rewrite_retries = 0
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
            rewrite, reason, prov = rewrite_theme_with_clusters(
                theme, att, historical=ctx_items, anthropic_client=anth_real,
                tracker=tracker)
            n_rewrite_opus_calls += prov.get("opus_calls", 1)
            if prov.get("retried"):
                n_rewrite_retries += 1
            if rewrite is None:
                status = f"kept_original ({reason})"
                print(f"    KEPT ORIGINAL: {reason}"
                      + (f"  [retried: {prov['retry_reasons']}]" if prov.get("retried") else ""))
            else:
                status = "rewritten"
                print(f"    OK [{rewrite['anchor_type']}] {rewrite['title'][:70]!r} "
                      f"len {rewrite['original_len']} -> {rewrite['rewritten_len']} "
                      f"(x{prov['ratio']}, ceiling {prov['length_ceiling']}) "
                      f"paras {prov['original_paragraphs']} -> {prov['rewritten_paragraphs']}"
                      + (f"  RETRIED: {prov['retry_reasons']}" if prov.get("retried") else ""))
            rewrite_log.append({"theme_index": idx, "theme": theme.get("theme"),
                                "attached_cluster_ids": [c.cluster_id for c in att],
                                "attached_items": len(all_items),
                                "status": status,
                                "new_title": rewrite["title"] if rewrite else None,
                                "anchor_type": rewrite["anchor_type"] if rewrite else "news",
                                "orig_len": prov["orig_len"],
                                "rewrite_len": prov["rewrite_len"],
                                "ratio": prov["ratio"],
                                "length_ceiling": prov["length_ceiling"],
                                "retried": prov["retried"],
                                "retry_reasons": prov["retry_reasons"],
                                "retry_still_violates": prov.get("retry_still_violates"),
                                "first_attempt": prov.get("first_attempt"),
                                "opus_calls": prov["opus_calls"],
                                "original_paragraphs": prov["original_paragraphs"],
                                "rewritten_paragraphs": prov["rewritten_paragraphs"],
                                # kept for readers of run1/run2 JSON
                                "original_len": prov["orig_len"],
                                "rewritten_len": prov["rewrite_len"]})
        else:
            status = "no_attachments"
        e = make_theme_entry(idx, theme, att, rewrite, status, url_to_item, att_recs)
        if rewrite is not None:
            e["rewrite_provenance"] = {k: prov.get(k) for k in
                                       ("orig_len", "rewrite_len", "ratio", "retried",
                                        "retry_reasons", "length_ceiling",
                                        "original_paragraphs", "rewritten_paragraphs",
                                        "opus_calls")}
        theme_by_entry_id[e["cluster_id"]] = (theme, att)
        entries.append(e)
        if e["unmatched_urls"]:
            print(f"    T{idx}: {len(e['theme_item_ids'])} cited URLs matched to items, "
                  f"{len(e['unmatched_urls'])} unmatched")
    rewrite_cents = round(_opus_cents(tracker) - opus_before, 2)
    counts["theme_entries"] = len(entries)
    counts["rewrite_calls"] = n_rewrite_calls
    counts["rewrite_opus_calls"] = n_rewrite_opus_calls
    counts["rewrite_retries"] = n_rewrite_retries
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
    pre_post_summaries = {e["cluster_id"]: e["summary"] for e in entries}
    entries, themes_out, post_stats = postprocess_entries(entries, themes_out, conn)
    # synthesize._validate_briefing_urls rejoins with single spaces when it
    # strips a sentence, which is what flattened the data-center entry in
    # runs 1 and 2. Put the paragraph breaks back from the pre-post text.
    themes_by_cid = {t["_v4_cluster_id"]: t for t in themes_out}
    restored: list[dict] = []
    for e in entries:
        pre = pre_post_summaries.get(e["cluster_id"], "")
        new_summary, ok = restore_paragraph_breaks(pre, e["summary"])
        if ok:
            restored.append({"cluster_id": e["cluster_id"], "title": e["title"],
                             "pre_paragraphs": len(_split_paragraphs(pre)),
                             "post_paragraphs": len(_split_paragraphs(e["summary"])),
                             "restored_paragraphs": len(_split_paragraphs(new_summary))})
            print(f"  restored paragraph breaks in {e['title'][:60]!r}: "
                  f"{restored[-1]['post_paragraphs']} -> {restored[-1]['restored_paragraphs']} "
                  f"(pre-postprocess {restored[-1]['pre_paragraphs']})")
            e["summary"] = new_summary
            if e["cluster_id"] in themes_by_cid:
                themes_by_cid[e["cluster_id"]]["summary"] = new_summary
    post_stats["paragraph_breaks_restored"] = restored
    counts["paragraph_breaks_restored"] = len(restored)
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
    cost["opus_calls"] = {"rewrites": n_rewrite_opus_calls,
                          "rewrite_themes": n_rewrite_calls,
                          "rewrite_retries": n_rewrite_retries,
                          "standalone_writes": len(standalone),
                          "total": n_rewrite_opus_calls + len(standalone)}
    cost["opus_cents_by_phase"] = {"rewrites": rewrite_cents, "standalone_writes": standalone_cents}
    cost["haiku_gate"] = {"model": GATE_MODEL, "calls": gate_calls, "cents": haiku_gate_cents}
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "v4b_attach",
        "source_v1_briefing_id": v1_id,
        "v1_theme_titles": [t.get("theme") for t in themes],
        "counts": counts,
        "timings_seconds": timings,
        "attach_log": attach_log,
        "gate_log": gate_log,
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
    print(f"\nrelevance gate: {gate_calls} items checked, {len(gate_log)} dropped, "
          f"{len(gate_detached)} cluster(s) detached:")
    for d in gate_log:
        print(f"  - item {d['item_id']} [{d['source']}] {d['title'][:60]!r} "
              f"dropped from T{d['theme_index']} {str(d['theme'])[:45]!r} "
              f"(cluster {d['cluster_id']})")
    print(f"\nrewrites ({len(rewrite_log)}):")
    for r in rewrite_log:
        print(f"  - T{r['theme_index']} {r['theme'][:55]!r} + clusters {r['attached_cluster_ids']}: "
              f"{r['status']}  len {r['orig_len']} -> {r['rewrite_len']} (x{r['ratio']}) "
              f"paras {r['original_paragraphs']} -> {r['rewritten_paragraphs']} "
              f"retried={r['retried']}"
              + (f" {r['retry_reasons']}" if r['retried'] else ""))
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
            email_ok = send_v4b_shadow_email(v4b, args.to, v1_id, tier=args.tier)
        else:
            email_ok = send_lunch_to_subscribers(v4b, v1_id)
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
