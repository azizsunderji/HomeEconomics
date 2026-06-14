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
    Cluster, MIN_CLUSTER_SIZE,
)
from analysis.synthesize import SYSTEM_PROMPT as V1_SYSTEM_PROMPT
from analysis.synthesize import _compute_cited_sources
from delivery.email_briefing import render_briefing_html

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_DB = os.environ.get(
    "PULSE_DB", "/Users/azizsunderji/Dropbox/Home Economics/Data/Pulse/pulse.db"
)
DEFAULT_TO = "aziz@home-economics.us"
EMAIL_FROM = "Pulse V3 <onboarding@resend.dev>"
SONNET_MODEL = "claude-opus-4-8"  # match v1

SUBCLUSTER_MODEL = "claude-haiku-4-5-20251001"

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
  - Write in v1's measured, restrained, data-first tone. Lead with the substantive claim or argument. Inline-link every attribution to the cited item's URL using markdown `[anchor text](url)` syntax.
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


def _format_cluster_for_sonnet(cluster: Cluster) -> str:
    """Render the cluster's items as a JSON array Sonnet can reason
    about. Mirrors what v1's user_content uses for items."""
    items = []
    for it in cluster.items:
        items.append({
            "id": it.id,
            "source": it.source,
            "url": it.url or "",
            "title": (it.title or "")[:300],
            "body": (it.body or "")[:1500],
            "author": it.author or "",
            "published_at": str(it.published_at) if it.published_at else "",
            "feed_name": it.feed_name or "",
        })
    return json.dumps(items, indent=2, default=str)


def write_roundup_for_cluster(
    cluster: Cluster,
    anthropic_client: Optional[anthropic.Anthropic] = None,
) -> Optional[dict]:
    """Sonnet writes ONE conversation_roundups entry for this cluster
    (or returns None to skip)."""
    if anthropic_client is None:
        anthropic_client = anthropic.Anthropic()

    system_prompt = V3_ROUNDUP_WRITER_PREFIX + V1_SYSTEM_PROMPT
    user_content = (
        f"Cluster ID: {cluster.cluster_id} (size={cluster.size})\n\n"
        f"CLUSTER ITEMS:\n{_format_cluster_for_sonnet(cluster)}\n\n"
        f"Write the conversation_roundups entry for this cluster, "
        f"or return {{\"skip\": true, \"reason\": \"...\"}}."
    )

    try:
        response_text = ""
        with anthropic_client.messages.stream(
            model=SONNET_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                response_text += text
            try:
                _final = stream.get_final_message()
                from analysis.anthropic_spend import record_usage as _rec_usage
                _rec_usage(SONNET_MODEL, _final.usage)
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
    """Construct the v3 briefing dict — v1 scaffold + v3 roundups.
    v1's conversation_themes (the News Themes section) is preserved
    unchanged; we only replace conversation_roundups (the Conversations
    section)."""
    v3 = json.loads(json.dumps(v1))  # deep copy
    v3["conversation_roundups"] = v3_roundups
    v3["_v3_meta"] = {
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
        f"[Pulse V3 roundups] {n_roundups} clustered roundups "
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

    # Stage 1-3: load + embed + cluster
    t0 = time.time()
    items = load_corpus(conn, hours=args.lookback_hours, end=end_dt)
    print(f"corpus: {len(items)} items after filter")
    embs = embed_corpus(items)
    clusters = cluster_items(items, embs, min_cluster_size=args.min_cluster_size)
    print(f"clusters: {len(clusters)} (largest size={max((c.size for c in clusters), default=0)})")
    stats = {
        "lookback_hours": args.lookback_hours,
        "min_cluster_size": args.min_cluster_size,
        "max_roundups": args.max_roundups,
        "items_after_filter": len(items),
        "clusters_total": len(clusters),
        "embed_cluster_seconds": round(time.time() - t0, 1),
    }

    # Stage 4: housing-check until we hit max_roundups housing-relevant
    # clusters. No top-N sampling — that was v2's bug; the accidental-
    # landlords cluster was beyond top-15.
    print(f"housing-checking up to {len(clusters)} clusters...")
    anth = anthropic.Anthropic()
    housing_clusters = []
    for c in clusters:
        if is_housing_relevant(c, anthropic_client=anth):
            housing_clusters.append(c)
            print(f"  cluster {c.cluster_id} (n={c.size}) -> housing-relevant")
        if len(housing_clusters) >= args.max_roundups:
            break
    stats["housing_relevant_clusters"] = len(housing_clusters)
    print(f"housing clusters: {len(housing_clusters)}")

    # Stage 5: Sonnet writes ONE conversation_roundups entry per cluster
    print(f"writing roundups for {len(housing_clusters)} clusters (Sonnet per cluster)...")
    roundups: list[dict] = []
    for i, c in enumerate(housing_clusters):
        print(f"  [{i+1}/{len(housing_clusters)}] cluster {c.cluster_id} n={c.size} -> Sonnet...")
        r = write_roundup_for_cluster(c, anthropic_client=anth)
        if r is not None:
            roundups.append(r)
            print(f"    OK: '{r.get('topic', '?')[:60]}'")
        else:
            print(f"    skipped")
    stats["roundups_written"] = len(roundups)
    stats["pipeline_seconds"] = round(time.time() - t0, 1)

    print(f"\n=== v3 stats: {json.dumps(stats, indent=2)} ===\n")
    print(f"v3 roundups ({len(roundups)}):")
    for r in roundups:
        print(f"  - [{r.get('_cluster_size')}] {r.get('topic', '?')[:80]}")

    # Stage 6-7: build briefing, render, send
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
                "VALUES ('daily_v3_hybrid', ?, ?)",
                (json.dumps(v3, default=str),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            print(f"stored v3 briefing as id={cur.lastrowid}")


if __name__ == "__main__":
    main()
