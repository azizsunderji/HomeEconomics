"""Per-article trigger-type classifier (ported from news_first_test_v5.py).

A pre-synthesis filter that uses Opus to classify each input item by what KIND
of item it is. Items classified as opinion/retrospective/recap/profile/
analysis/explainer are dropped before synthesis sees them. Items classified
as action_event/investigation/official_data/court/breaking_news/commentary
pass through.

Validated against a prior corpus: catches the landlord op-ed pattern
that would otherwise sneak into themes. Scope expanded after a viral
single-tweet anchor slipped through unfiltered tweets into a theme.

Scope: ALL sources are classified (rss, gmail, twitter, bluesky, substack,
hackernews). Social/essay items typically classify as `commentary` — an
ACCEPT category that lets the item through to synthesis BUT with a metadata
marker so the synthesizer knows it cannot anchor a theme on its own.

On any failure (network, parse, API), items DEFAULT TO ACCEPT — we never
silently drop items because the classifier had a hiccup.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import anthropic


logger = logging.getLogger(__name__)


# Use Opus per the "quality > cost" directive. The classifier is the gatekeeper;
# false-rejects are expensive (lost news) and false-accepts are expensive
# (op-eds in the briefing). Worth the marginal cost over Sonnet.
# Bumped 4.7 -> 4.8 on 2026-06-03 — Opus 4.8 dropped on May 28 with 3x lower
# pricing ($5/M input, $25/M output vs 4.7's $15/$75) AND meaningfully better
# constraint adherence per Anthropic's release notes.
OPUS_MODEL = "claude-opus-4-8"

# Cache schema/category version. Bump this whenever the trigger-type taxonomy,
# the system prompt's classification rules, or the cached payload meaning
# changes — older cached rows are then ignored automatically.
# v1 -> v2 on 2026-06-03: scope expanded to ALL sources (was rss+gmail only);
# new ACCEPT category `commentary` added for social/essay items that pass
# through with a "cannot anchor a theme" flag.
CLASSIFIER_VERSION = "v2"

ACCEPT_TRIGGER_TYPES = frozenset({
    "action_event", "investigation", "official_data", "court", "breaking_news",
    "commentary",
})
REJECT_TRIGGER_TYPES = frozenset({
    "opinion", "retrospective", "recap", "profile", "analysis", "explainer"
})


# Title/feed-level pre-filter: items from feeds whose entire raison d'être is
# opinion content are classified as 'opinion' WITHOUT a network round-trip.
# Saves both latency and Opus tokens. Match is exact on feed_name (after
# whitespace strip). Add new variants here as the feed catalog grows.
OPINION_FEED_NAMES = {
    "FT Opinion",
    "FT Alphaville Opinion",
    "WSJ Opinion",
    "NYT Opinion",
    "New York Times Opinion",
    "NYT Opinions",
    "Washington Post Opinions",
    "WaPo Opinions",
    "Bloomberg Opinion",
    "Bloomberg Opinion - Markets",
    "Bloomberg Opinion - Politics",
    "City Journal",
    "Reason",
    "National Review",
    "The Atlantic Ideas",
    "Atlantic Ideas",
}


def _is_obvious_opinion_feed(item: dict) -> bool:
    feed = (item.get("feed_name") or "").strip()
    return feed in OPINION_FEED_NAMES


# ── Cross-run cache helpers ──────────────────────────────────────────────────
# The cache lives in the main Pulse SQLite DB (see store.py). We open it
# lazily here to avoid a hard import cycle (store imports from collectors,
# trigger_classifier doesn't otherwise need store).

def _open_cache_conn() -> Optional[sqlite3.Connection]:
    """Open the Pulse SQLite DB used as the cross-run classifier cache.

    Returns None on any failure (path missing, permission issue) — the
    classifier then degrades cleanly to "no cache" instead of crashing.
    """
    try:
        from store import get_db  # local import: avoid circular at module load
        return get_db()
    except Exception as e:
        logger.warning(f"trigger_classifier: could not open cache DB: {e!r}")
        return None


def _cache_lookup(
    conn: sqlite3.Connection,
    item_ids: list[int],
    version: str,
) -> dict[int, str]:
    """Return {item_id: trigger_type} for items already classified at this
    version. Items not in the cache (or at a stale version) are absent.
    """
    if not item_ids:
        return {}
    out: dict[int, str] = {}
    # Chunk to keep the SQL parameter list bounded
    CHUNK = 500
    for i in range(0, len(item_ids), CHUNK):
        chunk = item_ids[i:i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        try:
            rows = conn.execute(
                f"SELECT item_id, trigger_type FROM trigger_classifier_cache "
                f"WHERE classifier_version = ? AND item_id IN ({placeholders})",
                [version, *chunk],
            ).fetchall()
        except Exception as e:
            logger.warning(f"trigger_classifier: cache lookup failed: {e!r}")
            return out
        for r in rows:
            # sqlite3.Row or tuple — handle both
            try:
                out[int(r["item_id"])] = r["trigger_type"]
            except Exception:
                out[int(r[0])] = r[1]
    return out


def _cache_insert(
    conn: sqlite3.Connection,
    rows: list[tuple[int, str]],
    version: str,
) -> int:
    """Insert/replace classifier results into the cross-run cache.

    rows: list of (item_id, trigger_type). Returns the number of rows written.
    """
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO trigger_classifier_cache "
            "(item_id, trigger_type, classified_at, classifier_version) "
            "VALUES (?, ?, ?, ?)",
            [(int(iid), tt, now, version) for iid, tt in rows],
        )
        conn.commit()
        written = len(rows)
    except Exception as e:
        logger.warning(f"trigger_classifier: cache insert failed: {e!r}")
    return written


TRIGGER_TYPE_SYSTEM = """You are a news desk classifier for a US housing-economics daily briefing. Today is {today}.

Your job: classify each input item by its TRIGGER TYPE — what KIND of item it is. The downstream pipeline uses these labels to decide whether the item can anchor a news theme, can only be cited as commentary, or should be dropped entirely.

Choose EXACTLY ONE trigger_type per item from this list:

ACCEPT (these pass through to synthesis):
  - action_event: A specific concrete action happened today or in the last 3 days that the article reports on. A bill cleared a chamber. A deal was announced/closed. A company filed for IPO. An appointment was made. A product was launched (only if from a non-brokerage, non-content-marketing source). A ruling was issued.
  - investigation: The article itself REVEALS new factual information via original reporting. The reporting IS the event (e.g., a major outlet's investigation that uncovers a corporate misconduct pattern; a wire service that discovers a systemic discrimination pattern).
  - official_data: Release of official government or institutional data — federal statistical agencies, central-bank releases, trade-association reports, court records.
  - court: A court filing, ruling, settlement, or charge.
  - breaking_news: An unambiguous news event happening right now (a crash, a disaster, a death of a public figure).
  - commentary: A tweet, social post, substack essay, or newsletter that COMMENTS on something. Includes opinion threads, hot takes, observations, analysis on existing data. CRITICAL: commentary items pass through to synthesis but they are NOT eligible to anchor a theme. They can only be cited AS commentary IN themes that are anchored on real news events (action_event / investigation / official_data / court / breaking_news).

REJECT (dropped before synthesis):
  - opinion: Op-eds, columns from named opinion sections — "argues", "critiques", "says", "thinks". Author's perspective on known facts, not new facts. Even if from a major outlet's opinion vertical — still opinion.
  - retrospective: "[N] years later", "looking back at", "the rise and fall of", "anniversary", "what happened to", "a decade after". The trigger event being referenced is OLD even if the publication is new.
  - recap: The article's news hook can be paraphrased as "[X published/released/announced] something" where X is itself a brokerage/platform/research org NOT doing news (a brokerage study, a real-estate-platform forecast, a listing-portal survey, an ibuyer report). Third-party reporting of brokerage content marketing → still recap → still NOT news.
  - profile: Article profiles a person, company, or place without breaking news. "Meet X", "the rise of Y", "inside Z".
  - analysis: General commentary on known trends without naming a specific event. From a CONSULTANCY, research firm, or data vendor publishing its own "outlook" / "monitor" / "quarterly report" — almost always analysis. Subtle distinction from `commentary`: commentary is on social media or in a personal essay; analysis is published as a formal "report" / "outlook" from a firm with a vested interest in the topic.
  - explainer: "What is X?", "how does Y work?", "your guide to Z".

KEY DISTINCTION — `commentary` vs `opinion` vs `analysis`:
  - SOCIAL items (twitter, bluesky, hackernews) and INDIVIDUAL substack/newsletter ESSAYS are `commentary` (ACCEPT). They survive the filter but get flagged so the synthesizer cites them AS commentary inside event-anchored themes.
  - Items from named OPINION sections of news outlets (any major newspaper's opinion vertical) are `opinion` (REJECT).
  - Items from CONSULTANCIES / research firms publishing their own "research report" are `analysis` (REJECT).

SOURCE HINTS:
  - source='twitter', source='bluesky', source='hackernews' items are almost always `commentary`. Default them to `commentary` unless the post itself ANNOUNCES a real news event (a company / organization / person who IS the news is making a statement — e.g., a Fed official tweeting a policy decision, a CEO tweeting an acquisition, a regulator tweeting an enforcement action). In those rare cases use `breaking_news` / `action_event` / `official_data` as appropriate.
  - source='substack' is nuanced: a substacker writing an opinion essay or hot take is `commentary`; a substacker republishing a chart that conveys NEW official data from a federal statistical agency, central bank, or trade-association release is closer to `official_data` relay. Judge by whether the item conveys a fresh authoritative event or is the writer's reflection on something.
  - source='rss' and source='gmail' items: apply the full taxonomy. These can be any category. Tighten the opinion/recap/analysis gates here — REJECTs from these sources are the bulk of the value the classifier adds.

When unclear, look at the body. If it has a "new" paragraph that introduces a specific event from today/yesterday → action_event / official_data / etc. If the body is reflection/argument/recap of someone else's content from a news/opinion outlet → REJECT. If it's a social post or essay reacting to known facts → `commentary`.

For each item, output its `id`, a `trigger_type` (one of the 12 labels above), a 10-word `justification`, and a boolean `accept` (true if trigger_type is in the ACCEPT list including `commentary`, false otherwise).

Return ONLY a JSON object with this structure:
{{
  "classifications": [
    {{"id": 12345, "trigger_type": "action_event", "justification": "Major-conglomerate acquisition of a national homebuilder in a multi-billion-dollar deal", "accept": true}},
    {{"id": 12346, "trigger_type": "opinion", "justification": "Outlet op-ed critiquing a metro-level housing tax", "accept": false}},
    {{"id": 12347, "trigger_type": "commentary", "justification": "Tweet comparing housing-supply records across two international metros", "accept": true}}
  ]
}}

No prose preamble, no markdown fences. Plain JSON. Classify EVERY input item."""


def _classify_batch(
    client: anthropic.Anthropic,
    batch: list[dict],
    today: str,
) -> dict[int, dict]:
    """Classify a single batch. Returns {item_id: classification_dict}.

    On any failure, returns {} — caller must default to ACCEPT for unclassified.
    """
    lines = []
    for it in batch:
        body = ((it.get("body") or "")[:400]).replace("\n", " ")
        pub = (it.get("published_at") or it.get("collected_at") or "")[:10]
        feed = it.get("feed_name") or ""
        source = (it.get("source") or "").lower()
        author = it.get("author") or ""
        lines.append(
            f"id={it['id']} | source={source} | feed={feed} | author={author} | pub={pub}\n"
            f"  title: {(it.get('title') or '')[:200]}\n"
            f"  url: {it.get('url','')}\n"
            f"  body: {body}"
        )
    user = (
        f"Classify each of these {len(batch)} items by trigger_type. "
        f"Today is {today}. Apply the ACCEPT/REJECT rules strictly. "
        f"Honor the SOURCE HINTS — social/essay items default to `commentary`. "
        f"Return classifications for EVERY id below.\n\n"
        + "\n\n".join(lines)
    )
    try:
        resp = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=8000,
            system=[{
                "type": "text",
                "text": TRIGGER_TYPE_SYSTEM.format(today=today),
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user}],
        )
        try:
            from analysis.anthropic_spend import record_usage as _rec_usage
            _rec_usage(OPUS_MODEL, resp.usage)
        except Exception:
            pass
    except Exception as e:
        logger.warning(
            f"trigger_classifier: Opus API call failed for batch of {len(batch)}: {e}. "
            f"Defaulting all items in batch to ACCEPT."
        )
        return {}

    text = (resp.content[0].text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        parsed = json.loads(text)
    except Exception as e:
        logger.warning(
            f"trigger_classifier: batch JSON parse error: {e}; raw[:300]={text[:300]}. "
            f"Defaulting all items in this batch to ACCEPT."
        )
        return {}

    results: dict[int, dict] = {}
    for c in parsed.get("classifications", []) or []:
        cid = c.get("id")
        if cid is None:
            continue
        try:
            cid = int(cid)
        except Exception:
            continue
        results[cid] = c
    return results


def classify_trigger_types(
    client: anthropic.Anthropic,
    items: list[dict],
    today: Optional[str] = None,
    batch_size: int = 80,
) -> dict[int, dict]:
    """Classify a list of items. Returns {item_id: classification_dict}.

    items: list of item dicts (must have id, title, body, url, etc.)
    today: ISO date string (YYYY-MM-DD). Defaults to today UTC.
    batch_size: ~80 matches the v5 reference run.

    Items the API didn't return a classification for are simply absent from
    the returned dict — apply_trigger_filter() will default them to ACCEPT.
    """
    if not items:
        return {}
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # In-run dedup. Three-stage filter follows:
    #   (1) obvious-opinion feed pre-filter — no API, no cache write
    #   (2) cross-run SQLite cache lookup
    #   (3) Opus API call for whatever remains, then cache the result
    cache: dict[int, dict] = {}
    candidates: list[dict] = []
    seen_ids: set[int] = set()
    for it in items:
        iid = it.get("id")
        if iid is None or iid in seen_ids:
            continue
        seen_ids.add(iid)
        candidates.append(it)

    # Stage 1: opinion-feed pre-filter
    prefiltered = 0
    pending: list[dict] = []
    for it in candidates:
        if _is_obvious_opinion_feed(it):
            cache[int(it["id"])] = {
                "id": it["id"],
                "trigger_type": "opinion",
                "justification": f"feed_name pre-filter ({it.get('feed_name','')})",
                "accept": False,
            }
            prefiltered += 1
        else:
            pending.append(it)
    if prefiltered:
        logger.info(
            f"trigger_classifier: opinion-feed pre-filter caught {prefiltered} "
            f"item(s) — no Opus call"
        )

    # Stage 2: cross-run cache lookup (best-effort; failures degrade silently)
    cache_conn = _open_cache_conn()
    cache_hits = 0
    cached_map: dict[int, str] = {}
    if cache_conn is not None and pending:
        cached_map = _cache_lookup(
            cache_conn, [int(it["id"]) for it in pending], CLASSIFIER_VERSION
        )
        if cached_map:
            still_pending: list[dict] = []
            for it in pending:
                iid = int(it["id"])
                tt = cached_map.get(iid)
                if tt is None:
                    still_pending.append(it)
                    continue
                accept = tt in ACCEPT_TRIGGER_TYPES
                cache[iid] = {
                    "id": iid,
                    "trigger_type": tt,
                    "justification": "(from trigger_classifier_cache)",
                    "accept": accept,
                }
                cache_hits += 1
            pending = still_pending
    if cache_hits:
        logger.info(
            f"trigger_classifier: cross-run cache served {cache_hits} "
            f"item(s) at version={CLASSIFIER_VERSION}"
        )

    # Stage 3: Opus on the remainder
    total = len(pending)
    n_batches = (total + batch_size - 1) // batch_size
    logger.info(
        f"trigger_classifier: classifying {total} item(s) via {OPUS_MODEL} "
        f"across {n_batches} batch(es) of <={batch_size} "
        f"(prefiltered={prefiltered}, cached={cache_hits})"
    )

    fresh_rows: list[tuple[int, str]] = []
    for bi in range(n_batches):
        batch = pending[bi * batch_size:(bi + 1) * batch_size]
        batch_results = _classify_batch(client, batch, today)
        cache.update(batch_results)
        for iid, cls in batch_results.items():
            tt = (cls.get("trigger_type") or "").strip().lower()
            if tt:
                fresh_rows.append((int(iid), tt))
        logger.info(
            f"trigger_classifier: batch {bi+1}/{n_batches} classified "
            f"{len(batch_results)}/{len(batch)} items"
        )

    # Write fresh classifications to the cross-run cache. Pre-filtered items
    # are NOT written (the rule is deterministic from feed_name; re-applying
    # is free).
    if cache_conn is not None and fresh_rows:
        written = _cache_insert(cache_conn, fresh_rows, CLASSIFIER_VERSION)
        if written:
            logger.info(
                f"trigger_classifier: wrote {written} fresh classification(s) "
                f"to trigger_classifier_cache (version={CLASSIFIER_VERSION})"
            )
    return cache


def apply_trigger_filter(
    all_items: list[dict],
    client: Optional[anthropic.Anthropic] = None,
    today: Optional[str] = None,
    batch_size: int = 80,
) -> list[dict]:
    """Pre-synthesis filter: drop items classified as opinion/retrospective/recap/
    profile/analysis/explainer. ALL sources are classified — tweets, bluesky
    posts, substacks, hackernews items, RSS, and gmail newsletters all go
    through the classifier.

    Social/essay items typically classify as `commentary` (ACCEPT). They pass
    through to synthesis but with `_trigger_type='commentary'` so the
    synthesizer knows it cannot anchor a theme on them.

    On any classifier failure (network, parse, API), the affected items default
    to ACCEPT — we never silently lose items because of a hiccup.

    Returns the filtered list (a new list; input is not mutated).
    Each accepted item is annotated with `_trigger_type` and
    `_trigger_justification` so downstream code can introspect why it
    survived.
    """
    if not all_items:
        return all_items

    client = client or anthropic.Anthropic()

    try:
        classifications = classify_trigger_types(
            client, all_items, today=today, batch_size=batch_size
        )
    except Exception as e:
        logger.warning(
            f"trigger_classifier: classify_trigger_types raised {e!r}; "
            f"defaulting all items to ACCEPT."
        )
        classifications = {}

    accepted: list[dict] = []
    rejected: list[dict] = []
    type_counter: dict[str, int] = {}

    for it in all_items:
        cls = classifications.get(it.get("id"))
        if not cls:
            # Default to ACCEPT — never silently lose items
            type_counter["unclassified"] = type_counter.get("unclassified", 0) + 1
            accepted.append({
                **it,
                "_trigger_type": "unclassified",
                "_trigger_justification": "(no classification returned)",
            })
            continue
        tt = (cls.get("trigger_type") or "").strip().lower()
        # Reconcile via the canonical sets — never trust the boolean alone
        if tt in REJECT_TRIGGER_TYPES:
            accept = False
        elif tt in ACCEPT_TRIGGER_TYPES:
            accept = True
        else:
            # Unknown label → ACCEPT (conservative)
            accept = True
        type_counter[tt or "unknown"] = type_counter.get(tt or "unknown", 0) + 1
        annotated = {
            **it,
            "_trigger_type": tt or "unknown",
            "_trigger_justification": cls.get("justification", ""),
        }
        if accept:
            accepted.append(annotated)
        else:
            rejected.append(annotated)

    logger.info(
        f"trigger filter: {len(accepted)} accepted, "
        f"{len(rejected)} rejected — {type_counter}"
    )
    if rejected:
        # Log a few examples so we can sanity-check in production logs
        sample = rejected[:5]
        for it in sample:
            logger.info(
                f"trigger filter REJECTED id={it.get('id')} "
                f"type={it.get('_trigger_type')} "
                f"source={it.get('source')} "
                f"feed={it.get('feed_name')} "
                f"title={(it.get('title') or '')[:120]!r} "
                f"why={it.get('_trigger_justification')!r}"
            )

    # Preserve original ordering: walk all_items once, swap in annotated copies
    # and drop rejected ones.
    accepted_by_id = {it["id"]: it for it in accepted}
    rejected_ids = {it["id"] for it in rejected}
    out: list[dict] = []
    for it in all_items:
        iid = it.get("id")
        if iid in rejected_ids:
            continue
        out.append(accepted_by_id.get(iid, it))
    return out
