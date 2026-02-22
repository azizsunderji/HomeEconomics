"""Haiku batch classification of collected items.

Uses Claude Haiku 4.5 to classify each item with:
- Topic tags (from 20-topic taxonomy)
- Relevance score (0-100)
- Named entities (metros, states, people)
- Extracted statistics
- Sentiment (bullish/bearish/neutral)
- Content type (news_report, opinion_analysis, organic_discussion, data_release, institutional_research)
- Conversation signal (0-100)
- Verifiable claims
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Optional

import anthropic

from config import TOPICS
from store import get_db, get_unclassified, update_classification, update_conversation_classification

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_BATCH_SIZE = 20  # Items per API call (batched in prompt)


def _build_taxonomy_text() -> str:
    """Build a compact topic taxonomy for the prompt."""
    lines = []
    for key, info in TOPICS.items():
        keywords = ", ".join(info["keywords"][:5])
        lines.append(f"- {key}: {info['label']} (e.g., {keywords})")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You are a content classifier for a housing/economics data journalism newsletter called "Home Economics."

Your job: classify each item with topic tags, relevance, entities, statistics, sentiment, content type, conversation signal, and verifiable claims.

## Topic Taxonomy
{_build_taxonomy_text()}

## Scoring Guidelines
- Relevance (0-100): This newsletter covers the UNITED STATES housing market and economy.
  - 90-100: Directly about US housing market data, prices, rates, inventory
  - 80-89: US housing policy, zoning, affordability analysis, mortgage industry
  - 70-79: US macroeconomics that affects housing: Fed policy, tariffs, inflation, employment, GDP, fiscal policy, immigration policy
  - 60-69: Broader US economic analysis by economists/analysts (even if not directly about housing — if an economist known for macro work is commenting on the economy, it's relevant)
  - 50-59: General US economic trends, labor market, demographics, consumer sentiment
  - 30-49: Loosely related (general finance, international economics with US implications)
  - 0-29: Not relevant (tech, entertainment, sports, unrelated)
  - IMPORTANT: Content about non-US housing markets (Nigeria, UK, Ireland, India, etc.) scores 0-10 regardless of topic match. We ONLY cover the US market. Look for clues: naira (₦), pounds (£), non-US place names (Lagos, Lekki, Dublin, Mumbai), non-US political figures, pidgin English, Yoruba names, etc.
  - IMPORTANT: Tweets/posts from known economists (Jason Furman, Arpit Gupta, Justin Wolfers, Claudia Sahm, Paul Krugman, etc.) about US economic policy should score 60+ even if they don't mention housing directly. These are the intellectual conversations our readers care about.

- Sentiment:
  - "bullish" = positive for housing/economy (prices rising, rates falling, strong growth)
  - "bearish" = negative (prices falling, rates rising, recession signals)
  - "neutral" = informational, mixed, or unclear

- Content Type (one of):
  - "news_report" = straight news coverage, AP/Reuters-style reporting
  - "opinion_analysis" = opinion pieces, commentary, editorial analysis
  - "organic_discussion" = Reddit threads, forum posts, social media debates
  - "data_release" = official data releases (BLS, Census, FHFA, etc.)
  - "institutional_research" = research from Goldman, AEI, Fed, think tanks

- Conversation Signal (0-100):
  How much organic debate/discussion does this represent?
  - 90-100: Heated Reddit/HN thread with many opinionated comments, active debate
  - 70-89: Substantial discussion with mixed viewpoints, many replies
  - 50-69: Moderate discussion, some back-and-forth
  - 30-49: Light discussion, mostly agreement or few comments
  - 0-29: No real discussion (dry news article, press release, data dump)

- Verifiable Claims: Extract specific factual assertions that could be checked against data.
  e.g., "Austin home prices dropped 20%", "mortgage rates hit highest since 2008"

## Output Format
For each item, return a JSON object:
{{
  "id": <item_id>,
  "topics": ["topic_key1", "topic_key2"],
  "relevance_score": <0-100>,
  "entities": ["Austin, TX", "Federal Reserve"],
  "extracted_stats": ["mortgage rates hit 7.2%", "inventory up 15% YoY"],
  "sentiment": "bullish|bearish|neutral",
  "content_type": "news_report|opinion_analysis|organic_discussion|data_release|institutional_research",
  "conversation_signal": <0-100>,
  "verifiable_claims": ["Austin prices dropped 20%", "rates highest since 2008"]
}}

Return a JSON array of these objects, one per item. Nothing else."""


def classify_batch(
    items: list[dict],
    client: Optional[anthropic.Anthropic] = None,
) -> list[dict]:
    """Classify a batch of items using Haiku.

    Args:
        items: List of item dicts from SQLite
        client: Optional Anthropic client (created if not provided)

    Returns:
        List of classification dicts with id, topics, relevance_score, etc.
    """
    if not items:
        return []

    client = client or anthropic.Anthropic()

    # Build the user message with all items
    item_texts = []
    for item in items:
        text = f"[ID: {item['id']}] [{item['source']}] {item['title']}"
        if item.get("body"):
            # Truncate body to keep prompt manageable
            body_preview = item["body"][:500].replace("\n", " ")
            text += f"\n{body_preview}"
        item_texts.append(text)

    user_content = f"Classify these {len(items)} items:\n\n" + "\n\n---\n\n".join(item_texts)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )

        # Parse the response
        response_text = response.content[0].text.strip()

        # Handle potential markdown code blocks
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            # Remove trailing ``` if present
            if "```" in response_text:
                response_text = response_text[:response_text.index("```")]

        # Try parsing as-is first
        try:
            classifications = json.loads(response_text)
            return classifications
        except json.JSONDecodeError:
            pass

        # Recovery: if JSON was truncated, try to salvage complete items
        # Find the last complete object (ending with })
        last_brace = response_text.rfind("}")
        if last_brace > 0:
            # Try to close the array
            truncated = response_text[:last_brace + 1].rstrip().rstrip(",") + "]"
            if not truncated.startswith("["):
                truncated = "[" + truncated
            try:
                classifications = json.loads(truncated)
                logger.warning(f"Recovered {len(classifications)} items from truncated JSON response")
                return classifications
            except json.JSONDecodeError:
                pass

        logger.error(f"Failed to parse classification response, no recovery possible")
        logger.debug(f"Raw response tail: ...{response_text[-300:]}")
        return []

    except Exception as e:
        logger.error(f"Classification API error: {e}")
        return []


def run_classification(
    db_path: Optional[str] = None,
    batch_size: int = MAX_BATCH_SIZE,
    max_items: int = 500,
) -> int:
    """Classify all unclassified items in the database.

    Returns: number of items classified.
    """
    conn = get_db(db_path)
    client = anthropic.Anthropic()
    total_classified = 0

    unclassified = get_unclassified(conn, limit=max_items)
    logger.info(f"Found {len(unclassified)} unclassified items")

    # Process in batches
    for i in range(0, len(unclassified), batch_size):
        batch = unclassified[i:i + batch_size]
        logger.info(f"Classifying batch {i // batch_size + 1} ({len(batch)} items)")

        classifications = classify_batch(batch, client=client)

        # Apply classifications to database
        classified_ids = set()
        for cls in classifications:
            item_id = cls.get("id")
            if item_id is None:
                continue

            try:
                update_classification(
                    conn,
                    item_id=item_id,
                    topics=cls.get("topics", []),
                    relevance_score=cls.get("relevance_score", 0),
                    entities=cls.get("entities", []),
                    extracted_stats=cls.get("extracted_stats", []),
                    sentiment=cls.get("sentiment", "neutral"),
                )
                update_conversation_classification(
                    conn,
                    item_id=item_id,
                    content_type=cls.get("content_type", ""),
                    conversation_signal=cls.get("conversation_signal", 0),
                    verifiable_claims=cls.get("verifiable_claims", []),
                )
                classified_ids.add(item_id)
                total_classified += 1
            except Exception as e:
                logger.warning(f"Error updating classification for item {item_id}: {e}")

        # Log items that weren't classified (API might have skipped some)
        batch_ids = {item["id"] for item in batch}
        missed = batch_ids - classified_ids
        if missed:
            logger.warning(f"Batch missed {len(missed)} items: {missed}")

    logger.info(f"Classification complete: {total_classified} items classified")
    return total_classified


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    classified = run_classification()
    print(f"Classified {classified} items")
