"""Cross-platform convergence scoring.

Detects when the same topic appears across multiple platforms simultaneously.
A topic on 1 platform = noise. Same topic on 4+ platforms = lead story.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from config import TOPICS, CONVERGENCE_ALERT_THRESHOLD
from store import get_db, get_items_since

logger = logging.getLogger(__name__)


def compute_convergence(
    conn: sqlite3.Connection,
    hours: int = 24,
    min_relevance: int = 30,
) -> list[dict]:
    """Compute convergence scores for all topics over the given time window.

    Returns list of topic convergence dicts, sorted by score descending.
    Each dict has:
        - topic: topic key
        - label: human-readable label
        - platforms: list of platforms that mentioned it
        - platform_count: number of distinct platforms
        - total_items: total items across all platforms
        - avg_relevance: average relevance score
        - top_items: highest-relevance items
        - convergence_score: composite score (platform_count * avg_relevance / 100)
    """
    items = get_items_since(conn, hours=hours, min_relevance=min_relevance)

    # Group items by topic and platform
    topic_platforms = defaultdict(lambda: defaultdict(list))  # topic -> platform -> [items]

    for item in items:
        topics = item.get("topics", "[]")
        if isinstance(topics, str):
            topics = json.loads(topics)

        for topic in topics:
            topic_platforms[topic][item["source"]].append(item)

    # Compute convergence for each topic
    results = []
    for topic, platforms in topic_platforms.items():
        all_items = []
        for platform_items in platforms.values():
            all_items.extend(platform_items)

        platform_names = sorted(platforms.keys())
        platform_count = len(platform_names)
        total_items = len(all_items)

        relevance_scores = [
            item.get("relevance_score", 0) or 0
            for item in all_items
        ]
        avg_relevance = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0

        # Top items by relevance
        sorted_items = sorted(all_items, key=lambda x: x.get("relevance_score", 0) or 0, reverse=True)
        top_items = sorted_items[:5]

        # Convergence score: platforms * normalized relevance
        # Large bonus for organic sources — conversation is the primary signal
        organic_bonus = 1.0
        organic_platforms = {"reddit", "bluesky", "hackernews", "twitter"}
        if organic_platforms & set(platform_names):
            organic_bonus = 3.0

        convergence_score = round(
            platform_count * (avg_relevance / 100) * organic_bonus * (min(total_items, 20) / 5),
            2
        )

        label = TOPICS.get(topic, {}).get("label", topic)

        results.append({
            "topic": topic,
            "label": label,
            "platforms": platform_names,
            "platform_count": platform_count,
            "total_items": total_items,
            "avg_relevance": round(avg_relevance, 1),
            "top_items": [
                {
                    "id": i.get("id"),
                    "title": i.get("title", "")[:100],
                    "source": i.get("source"),
                    "relevance_score": i.get("relevance_score"),
                    "url": i.get("url", ""),
                }
                for i in top_items
            ],
            "convergence_score": convergence_score,
            "is_alert_worthy": platform_count >= CONVERGENCE_ALERT_THRESHOLD,
        })

    # Sort by convergence score
    results.sort(key=lambda x: x["convergence_score"], reverse=True)
    return results


def detect_organic_conversations(
    conn: sqlite3.Connection,
    hours: int = 24,
    min_relevance: int = 50,
) -> list[dict]:
    """Find Reddit/Bluesky discussions with no corresponding news article.

    These are conversations with "a life of their own" — organic discussion
    not triggered by a specific news event.
    """
    items = get_items_since(conn, hours=hours, min_relevance=min_relevance)

    # Separate by type
    organic_sources = {"reddit", "bluesky", "hackernews"}
    news_sources = {"google_news", "rss", "twitter"}

    organic_items = [i for i in items if i["source"] in organic_sources]
    news_items = [i for i in items if i["source"] in news_sources]

    # Get news content hashes for comparison
    news_hashes = set()
    for item in news_items:
        news_hashes.add(item.get("content_hash", ""))

    # Find organic items without a corresponding news story
    # (approximation: if content hash doesn't match any news item)
    truly_organic = []
    for item in organic_items:
        if item.get("content_hash", "") not in news_hashes:
            # Additional check: does any news title contain similar words?
            item_words = set(item.get("title", "").lower().split())
            has_news_match = False
            for news in news_items:
                news_words = set(news.get("title", "").lower().split())
                overlap = len(item_words & news_words)
                if overlap >= 3:  # At least 3 shared words
                    has_news_match = True
                    break

            if not has_news_match:
                truly_organic.append(item)

    # Sort by engagement
    truly_organic.sort(key=lambda x: x.get("score", 0), reverse=True)

    logger.info(
        f"Found {len(truly_organic)} organic conversations "
        f"(out of {len(organic_items)} organic items)"
    )

    return truly_organic[:10]  # Return top 10


def detect_active_debates(
    conn: sqlite3.Connection,
    hours: int = 36,
    min_relevance: int = 40,
) -> list[dict]:
    """Find topics where conversation platforms show split bullish/bearish sentiment.

    These are genuine debates — not consensus, but active disagreement.
    """
    items = get_items_since(conn, hours=hours, min_relevance=min_relevance)

    # Only look at conversation sources
    conversation_sources = {"reddit", "hackernews", "twitter", "bluesky"}
    conv_items = [i for i in items if i["source"] in conversation_sources]

    # Group by topic
    topic_sentiments = defaultdict(list)
    for item in conv_items:
        topics = item.get("topics", "[]")
        if isinstance(topics, str):
            topics = json.loads(topics)
        sentiment = item.get("sentiment", "neutral")
        for topic in topics:
            topic_sentiments[topic].append(sentiment)

    debates = []
    for topic, sentiments in topic_sentiments.items():
        if len(sentiments) < 4:
            continue  # Need enough items to detect a split

        bullish = sentiments.count("bullish")
        bearish = sentiments.count("bearish")
        total = len(sentiments)

        # A debate requires both sides represented with at least 20% each
        bull_pct = bullish / total
        bear_pct = bearish / total

        if bull_pct >= 0.2 and bear_pct >= 0.2:
            label = TOPICS.get(topic, {}).get("label", topic)
            debates.append({
                "topic": topic,
                "label": label,
                "total_items": total,
                "bullish_count": bullish,
                "bearish_count": bearish,
                "neutral_count": total - bullish - bearish,
                "split_ratio": round(min(bull_pct, bear_pct) / max(bull_pct, bear_pct), 2),
                "description": (
                    f"{label}: {bullish} bullish vs {bearish} bearish "
                    f"({round(bull_pct*100)}%/{round(bear_pct*100)}% split)"
                ),
            })

    # Sort by how evenly split (closest to 50/50 = most active debate)
    debates.sort(key=lambda x: x["split_ratio"], reverse=True)

    logger.info(f"Found {len(debates)} active debates across {len(topic_sentiments)} topics")
    return debates


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = get_db()
    convergence = compute_convergence(conn)
    for c in convergence[:10]:
        platforms = ", ".join(c["platforms"])
        print(f"[{c['convergence_score']:>6.1f}] {c['label']:>25} | {c['platform_count']} platforms ({platforms}) | {c['total_items']} items")

    print("\n--- Active Debates ---")
    debates = detect_active_debates(conn)
    for d in debates[:5]:
        print(f"  {d['description']}")
