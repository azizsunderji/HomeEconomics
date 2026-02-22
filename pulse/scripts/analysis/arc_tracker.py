"""Rolling sentiment/volume arc tracking.

Tracks topic trends over 7-day and 30-day windows.
Detects narrative shifts (e.g., "rates will drop" → "higher for longer").
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from config import TOPICS
from store import get_db, get_items_since, update_topic_arc, get_topic_arc

logger = logging.getLogger(__name__)

SENTIMENT_MAP = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}


def update_arcs(conn: sqlite3.Connection, date: str | None = None) -> dict:
    """Update topic arc entries for today (or a given date).

    Computes item_count, avg_relevance, avg_sentiment, and platform spread
    for each topic from the last 24 hours of classified items.

    Returns dict of topic -> arc summary.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    items = get_items_since(conn, hours=24, min_relevance=0)
    logger.info(f"Computing arcs for {date} from {len(items)} items")

    # Group by topic
    topic_items = defaultdict(list)
    for item in items:
        topics = item.get("topics", "[]")
        if isinstance(topics, str):
            topics = json.loads(topics)
        for topic in topics:
            topic_items[topic].append(item)

    arc_summaries = {}

    for topic in TOPICS:
        items_for_topic = topic_items.get(topic, [])
        if not items_for_topic:
            continue

        # Compute metrics
        relevance_scores = [i.get("relevance_score", 0) or 0 for i in items_for_topic]
        avg_relevance = sum(relevance_scores) / len(relevance_scores)

        sentiments = [
            SENTIMENT_MAP.get(i.get("sentiment", "neutral"), 0)
            for i in items_for_topic
        ]
        avg_sentiment = sum(sentiments) / len(sentiments)

        platforms = list(set(i["source"] for i in items_for_topic))

        # Top items by relevance
        sorted_items = sorted(items_for_topic, key=lambda x: x.get("relevance_score", 0) or 0, reverse=True)
        top_ids = [i["id"] for i in sorted_items[:5]]

        update_topic_arc(
            conn,
            topic=topic,
            date=date,
            item_count=len(items_for_topic),
            avg_relevance=round(avg_relevance, 1),
            avg_sentiment=round(avg_sentiment, 3),
            platforms=platforms,
            top_item_ids=top_ids,
        )

        arc_summaries[topic] = {
            "item_count": len(items_for_topic),
            "avg_relevance": round(avg_relevance, 1),
            "avg_sentiment": round(avg_sentiment, 3),
            "platforms": platforms,
        }

    logger.info(f"Updated arcs for {len(arc_summaries)} topics")
    return arc_summaries


def detect_narrative_shifts(
    conn: sqlite3.Connection,
    lookback_days: int = 14,
    shift_threshold: float = 0.4,
) -> list[dict]:
    """Detect topics where sentiment has shifted significantly.

    Compares the 7-day rolling average to the prior 7-day window.
    A shift > threshold in either direction is flagged.

    Returns list of shift dicts sorted by magnitude.
    """
    shifts = []
    now = datetime.now(timezone.utc)

    for topic, info in TOPICS.items():
        arc_data = get_topic_arc(conn, topic, days=lookback_days)
        if len(arc_data) < 5:  # Need enough data points
            continue

        # Split into recent (last 7 days) and prior (7-14 days ago)
        midpoint = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        recent = [d for d in arc_data if d["date"] >= midpoint]
        prior = [d for d in arc_data if d["date"] < midpoint]

        if not recent or not prior:
            continue

        recent_sentiment = sum(d["avg_sentiment_score"] for d in recent) / len(recent)
        prior_sentiment = sum(d["avg_sentiment_score"] for d in prior) / len(prior)

        recent_volume = sum(d["item_count"] for d in recent)
        prior_volume = sum(d["item_count"] for d in prior)

        sentiment_shift = recent_sentiment - prior_sentiment
        volume_change = (recent_volume / max(prior_volume, 1)) - 1  # % change

        if abs(sentiment_shift) >= shift_threshold:
            direction = "bullish" if sentiment_shift > 0 else "bearish"
            prior_direction = "bullish" if prior_sentiment > 0.2 else "bearish" if prior_sentiment < -0.2 else "neutral"
            recent_direction = "bullish" if recent_sentiment > 0.2 else "bearish" if recent_sentiment < -0.2 else "neutral"

            shifts.append({
                "topic": topic,
                "label": info["label"],
                "prior_sentiment": round(prior_sentiment, 3),
                "recent_sentiment": round(recent_sentiment, 3),
                "shift_magnitude": round(abs(sentiment_shift), 3),
                "shift_direction": direction,
                "narrative_from": prior_direction,
                "narrative_to": recent_direction,
                "volume_change_pct": round(volume_change * 100, 1),
                "recent_volume": recent_volume,
                "prior_volume": prior_volume,
            })

    shifts.sort(key=lambda x: x["shift_magnitude"], reverse=True)
    logger.info(f"Detected {len(shifts)} narrative shifts")
    return shifts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = get_db()
    arcs = update_arcs(conn)
    for topic, summary in sorted(arcs.items(), key=lambda x: x[1]["item_count"], reverse=True)[:10]:
        label = TOPICS[topic]["label"]
        print(f"{label:>25}: {summary['item_count']} items, sentiment {summary['avg_sentiment']:+.2f}, platforms: {', '.join(summary['platforms'])}")
