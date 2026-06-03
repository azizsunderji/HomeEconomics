"""Per-article trigger-type classifier (ported from news_first_test_v5.py).

A pre-synthesis filter that uses Opus to classify each news item by what KIND
of article it is. Items classified as opinion/retrospective/recap/profile/
analysis/explainer are dropped before synthesis sees them. Items classified
as action_event/investigation/official_data/court/breaking_news pass through.

Validated on briefing #136 corpus: catches the City Journal landlord op-ed
("Good Cause Eviction Is Hiking NYC Rents") pattern that would otherwise
sneak into themes.

Scope: only RSS items and Gmail newsletter items get classified. Tweets,
Bluesky posts, substacks, and hackernews are inherently commentary on news
and are passed through untouched (the classifier vocabulary doesn't apply).

On any failure (network, parse, API), items DEFAULT TO ACCEPT — we never
silently drop items because the classifier had a hiccup.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import anthropic


logger = logging.getLogger(__name__)


# Use Opus per the "quality > cost" directive. The classifier is the gatekeeper;
# false-rejects are expensive (lost news) and false-accepts are expensive
# (op-eds in the briefing). Worth the marginal cost over Sonnet.
OPUS_MODEL = "claude-opus-4-7"

# Sources eligible for classification. Inherently-commentary sources are
# excluded — classifying a tweet as "opinion" is meaningless.
NEWS_SOURCES = frozenset({"rss", "gmail"})

ACCEPT_TRIGGER_TYPES = frozenset({
    "action_event", "investigation", "official_data", "court", "breaking_news"
})
REJECT_TRIGGER_TYPES = frozenset({
    "opinion", "retrospective", "recap", "profile", "analysis", "explainer"
})


TRIGGER_TYPE_SYSTEM = """You are a news desk classifier for a US housing-economics daily briefing. Today is {today}.

Your job: classify each input article by its TRIGGER TYPE — what KIND of article it is. The downstream pipeline will then only cluster articles whose trigger type is a real news event.

Choose EXACTLY ONE trigger_type per article from this list:

ACCEPT (real news events — pass through):
  - action_event: A specific concrete action happened today or in the last 3 days that the article reports on. A bill cleared a chamber. A deal was announced/closed. A company filed for IPO. An appointment was made. A product was launched (only if from a non-brokerage, non-content-marketing source). A ruling was issued.
  - investigation: The article itself REVEALS new factual information via original reporting. The reporting IS the event (e.g., WSJ investigation into Allstate paying $0 on claims; Reuters discovers a pattern of mortgage discrimination).
  - official_data: Article is about a release of official government or institutional data — BLS, Census, Fed, NAR/MBA official reports, court records.
  - court: A court filing, ruling, settlement, or charge.
  - breaking_news: An unambiguous news event happening right now (a crash, a disaster, a death of a public figure).

REJECT (NOT news events even if published today — drop these):
  - opinion: Op-eds, columns, "argues", "critiques", "says", "thinks". Author's perspective on known facts, not new facts. Even if from a named outlet (NYT op-ed, FT opinion, City Journal piece) — still opinion.
  - retrospective: "[N] years later", "looking back at", "the rise and fall of", "anniversary", "what happened to", "a decade after". The trigger event being referenced is OLD even if the publication is new.
  - recap: The article's news hook can be paraphrased as "[X published/released/announced] something" where X is itself a brokerage/platform/research org NOT doing news (Redfin study, Zillow forecast, Realtor.com survey, HomeLight report). Third-party reporting of brokerage content marketing → still recap → still NOT news.
  - profile: Article profiles a person, company, or place without breaking news. "Meet X", "the rise of Y", "inside Z".
  - analysis: General commentary on known trends without naming a specific event. "Why housing is broken", "the case for X", "we need more Y".
  - explainer: "What is X?", "how does Y work?", "your guide to Z".

When unclear, look at the article body. If it has a "new" paragraph that introduces a specific event from today/yesterday → ACCEPT. If the body is reflection/argument/recap of someone else's content → REJECT.

For each article, output its `id`, a `trigger_type` (one of the 11 labels above), a 10-word `justification`, and a boolean `accept` (true if trigger_type is in the ACCEPT list, false otherwise).

Return ONLY a JSON object with this structure:
{{
  "classifications": [
    {{"id": 12345, "trigger_type": "action_event", "justification": "Berkshire Hathaway announced acquisition of Taylor Morrison in $8.5B deal", "accept": true}},
    {{"id": 12346, "trigger_type": "opinion", "justification": "City Journal op-ed critiquing LA mansion tax effects on housing", "accept": false}}
  ]
}}

No prose preamble, no markdown fences. Plain JSON. Classify EVERY input article."""


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
        lines.append(
            f"id={it['id']} | feed={feed} | pub={pub}\n"
            f"  title: {(it.get('title') or '')[:200]}\n"
            f"  url: {it.get('url','')}\n"
            f"  body: {body}"
        )
    user = (
        f"Classify each of these {len(batch)} articles by trigger_type. "
        f"Today is {today}. Apply the ACCEPT/REJECT rules strictly. "
        f"Return classifications for EVERY id below.\n\n"
        + "\n\n".join(lines)
    )
    try:
        resp = client.messages.create(
            model=OPUS_MODEL,
            max_tokens=8000,
            system=TRIGGER_TYPE_SYSTEM.format(today=today),
            messages=[{"role": "user", "content": user}],
        )
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

    # Cache by id within a single run — never classify the same item twice
    cache: dict[int, dict] = {}
    pending = []
    seen_ids: set[int] = set()
    for it in items:
        iid = it.get("id")
        if iid is None or iid in seen_ids:
            continue
        seen_ids.add(iid)
        pending.append(it)

    total = len(pending)
    n_batches = (total + batch_size - 1) // batch_size
    logger.info(
        f"trigger_classifier: classifying {total} items across {n_batches} "
        f"batch(es) of <={batch_size} via {OPUS_MODEL}"
    )

    for bi in range(n_batches):
        batch = pending[bi * batch_size:(bi + 1) * batch_size]
        batch_results = _classify_batch(client, batch, today)
        cache.update(batch_results)
        logger.info(
            f"trigger_classifier: batch {bi+1}/{n_batches} classified "
            f"{len(batch_results)}/{len(batch)} items"
        )
    return cache


def apply_trigger_filter(
    all_items: list[dict],
    client: Optional[anthropic.Anthropic] = None,
    today: Optional[str] = None,
    batch_size: int = 80,
) -> list[dict]:
    """Pre-synthesis filter: drop items classified as opinion/retrospective/recap/
    profile/analysis/explainer. Items from sources not in NEWS_SOURCES are
    passed through untouched.

    On any classifier failure (network, parse, API), the affected items default
    to ACCEPT — we never silently lose items because of a hiccup.

    Returns the filtered list (a new list; input is not mutated).
    Each accepted news item is annotated with `_trigger_type` and
    `_trigger_justification` so downstream code can introspect why it
    survived.
    """
    if not all_items:
        return all_items

    news_items = [i for i in all_items if (i.get("source") or "") in NEWS_SOURCES]
    other_items = [i for i in all_items if (i.get("source") or "") not in NEWS_SOURCES]

    if not news_items:
        logger.info("trigger_classifier: no news-source items to classify; pass-through")
        return list(all_items)

    client = client or anthropic.Anthropic()

    try:
        classifications = classify_trigger_types(
            client, news_items, today=today, batch_size=batch_size
        )
    except Exception as e:
        logger.warning(
            f"trigger_classifier: classify_trigger_types raised {e!r}; "
            f"defaulting all news items to ACCEPT."
        )
        classifications = {}

    accepted_news: list[dict] = []
    rejected_news: list[dict] = []
    type_counter: dict[str, int] = {}

    for it in news_items:
        cls = classifications.get(it.get("id"))
        if not cls:
            # Default to ACCEPT — never silently lose items
            type_counter["unclassified"] = type_counter.get("unclassified", 0) + 1
            accepted_news.append({
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
            accepted_news.append(annotated)
        else:
            rejected_news.append(annotated)

    logger.info(
        f"trigger filter: {len(accepted_news)} accepted, "
        f"{len(rejected_news)} rejected — {type_counter}"
    )
    if rejected_news:
        # Log a few examples so we can sanity-check in production logs
        sample = rejected_news[:5]
        for it in sample:
            logger.info(
                f"trigger filter REJECTED id={it.get('id')} "
                f"type={it.get('_trigger_type')} "
                f"feed={it.get('feed_name')} "
                f"title={(it.get('title') or '')[:120]!r} "
                f"why={it.get('_trigger_justification')!r}"
            )

    # Preserve original ordering: walk all_items once, swap in annotated copies
    # for news items, drop rejected ones.
    accepted_by_id = {it["id"]: it for it in accepted_news}
    rejected_ids = {it["id"] for it in rejected_news}
    out: list[dict] = []
    for it in all_items:
        iid = it.get("id")
        if (it.get("source") or "") not in NEWS_SOURCES:
            out.append(it)
            continue
        if iid in rejected_ids:
            continue
        out.append(accepted_by_id.get(iid, it))
    return out
