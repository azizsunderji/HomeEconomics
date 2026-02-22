"""Weekly contrarian analysis.

Takes the week's top repeated claims from social media and news,
cross-references against data lake summaries, and identifies where
popular narrative contradicts what the data actually shows.

Runs once per week (Sunday 8 AM ET).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import anthropic

from config import TOPICS
from store import get_db, get_items_since, save_briefing, add_story_opportunity
from analysis.crosswalk import get_datasets_for_topics

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"


SYSTEM_PROMPT = """You are a contrarian analysis engine for "Home Economics," a data-driven newsletter about the US housing market.

Your job: examine the week's most-repeated claims on social media and news, then identify which ones are WRONG or MISLEADING based on the data lake summaries provided.

The editor has access to extensive housing, economic, and demographic datasets. When popular narrative says X but the data shows Y, that's a high-value newsletter opportunity.

## Output Format

Return a JSON object:
{
  "week_of": "YYYY-MM-DD",
  "top_claims": [
    {
      "claim": "The claim as commonly stated (e.g., 'Austin's housing market is crashing')",
      "frequency": N,  // estimated mentions across platforms
      "platforms": ["reddit", "twitter", ...],
      "data_reality": "What the data actually shows",
      "data_sources": ["specific_file.parquet — what it contains"],
      "mismatch_score": 0-100,  // how wrong is the popular narrative? 0=accurate, 100=completely wrong
      "story_pitch": {
        "headline": "Contrarian headline for newsletter",
        "angle": "1-2 sentence pitch",
        "time_estimate": "3-5 hours",
        "urgency": "high|normal|low"
      }
    }
  ],
  "narrative_consensus": [
    {
      "topic": "...",
      "consensus": "What most people agree on this week",
      "is_data_supported": true/false,
      "nuance": "What the consensus gets right/wrong"
    }
  ]
}

## Important Guidelines
- Only flag genuine mismatches, not small disagreements
- "Data reality" must reference SPECIFIC files and what they'd show — don't make up statistics
- A mismatch_score of 80+ means the popular narrative is substantially wrong
- Focus on claims where the editor has UNIQUE data advantages (Redfin metro data, migration flows, FHFA HPI, etc.)
- Sort by mismatch_score descending
- Maximum 10 claims, 5 consensus items
- The best contrarian stories are ones where conventional wisdom is directionally wrong, not just imprecise"""


def run_weekly_contrarian(
    conn: sqlite3.Connection,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    """Run the weekly contrarian analysis.

    Analyzes the past 7 days of classified items and cross-references
    popular claims against data lake capabilities.

    Returns structured contrarian analysis dict.
    """
    client = client or anthropic.Anthropic()

    # Get the week's items
    items = get_items_since(conn, hours=168, min_relevance=40)  # 7 days = 168 hours

    # Group by topic to find most-discussed themes
    topic_counts = {}
    for item in items:
        topics = item.get("topics", "[]")
        if isinstance(topics, str):
            topics = json.loads(topics)
        for topic in topics:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    top_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_topic_keys = [t[0] for t in top_topics]

    # Get relevant data lake files
    data_refs = get_datasets_for_topics(top_topic_keys)

    # Get high-engagement items (most repeated claims come from high-engagement posts)
    high_engagement = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:30]

    logger.info(
        f"Contrarian analysis: {len(items)} items, "
        f"top topics: {[t[0] for t in top_topics[:5]]}"
    )

    # Build prompt
    item_summaries = []
    for item in high_engagement:
        stats = item.get("extracted_stats", "[]")
        if isinstance(stats, str):
            stats = json.loads(stats)
        item_summaries.append(
            f"[{item['source']}] [rel:{item.get('relevance_score', 0)}] "
            f"[sentiment:{item.get('sentiment', 'neutral')}] "
            f"[score:{item.get('score', 0)}]\n"
            f"  {item['title'][:150]}\n"
            f"  Stats: {'; '.join(stats[:3]) if stats else 'none'}"
        )

    user_content = f"""## This Week's Most-Discussed Topics (by item count)

{chr(10).join(f'- {TOPICS.get(t, {}).get("label", t)}: {c} items' for t, c in top_topics)}

## Highest-Engagement Items This Week

{chr(10).join(item_summaries)}

## Available Data Lake Files

{json.dumps(data_refs[:25], indent=2)}

Analyze these for contrarian opportunities. Where is popular narrative wrong?"""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        response_text = response.content[0].text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]

        analysis = json.loads(response_text)

        # Save high-mismatch claims as story opportunities
        for claim in analysis.get("top_claims", []):
            if claim.get("mismatch_score", 0) >= 60:
                pitch = claim.get("story_pitch", {})
                add_story_opportunity(
                    conn,
                    headline=pitch.get("headline", claim["claim"]),
                    topic=claim.get("topic", "contrarian"),
                    summary=json.dumps(claim),
                    data_sources=claim.get("data_sources", []),
                    urgency=pitch.get("urgency", "normal"),
                    time_estimate=pitch.get("time_estimate", ""),
                    contrarian_angle=claim.get("data_reality", ""),
                )

        # Save as weekly briefing
        briefing_id = save_briefing(conn, "weekly", analysis)
        analysis["_briefing_id"] = briefing_id

        logger.info(f"Generated weekly contrarian analysis (ID: {briefing_id})")
        return analysis

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse contrarian response: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"Contrarian analysis error: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = get_db()
    analysis = run_weekly_contrarian(conn)
    print(json.dumps(analysis, indent=2))
