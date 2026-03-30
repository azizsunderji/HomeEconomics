"""Daily synthesis using Claude Sonnet 4.5.

Generates the structured morning briefing focused on CONVERSATION — what people
are debating, arguing about, and reacting to across Twitter, Bluesky, HN, and Substacks.
News is demoted to context-only.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse

import anthropic

from config import TOPICS, RELEVANCE_THRESHOLD_HIGHLIGHT, SOURCE_WEIGHTS
from store import (
    get_db, get_items_since, get_conversation_items, add_story_opportunity,
    save_briefing, get_collection_stats,
    get_recent_collection_errors,
)
from analysis.convergence import compute_convergence, detect_organic_conversations
from analysis.arc_tracker import detect_narrative_shifts


logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-5-20250929"

# Source quality tiers — INVERTED for conversation pivot
# Tier 1: Organic conversation (the gold)
# Tier 5: Commodity news (context only)
SOURCE_TIERS = {
    # Tier 1: Organic conversation
    "hackernews": 1, "twitter": 1, "bluesky": 1,
    # Tier 2: Peer analysis (Substacks)
    "substack": 2,
    # Tier 3: Institutional research (from Gmail)
    "goldman_sachs": 3, "gs_macro": 3, "fed": 3, "newyorkfed": 3,
    "aei": 3, "bls.gov": 3, "census.gov": 3, "fhfa": 3,
    "freddiemac": 3, "fanniemae": 3, "nber": 3,
    # Tier 4: Quality journalism opinion pieces
    "ft": 4, "nyt": 4, "wsj": 4, "bloomberg": 4, "economist": 4,
    "reuters": 4, "daily_shot": 4,
    "housingwire": 4, "inman": 4, "nar": 4, "realtor": 4,
    # Tier 5: Commodity news
    "google_news": 5, "rss": 5,
}


def _get_source_tier(item: dict) -> int:
    """Determine source tier for an item (conversation-first hierarchy)."""
    source = (item.get("source") or "").lower()
    author = (item.get("author") or "").lower()
    feed = (item.get("feed_name") or "").lower()

    # Conversation sources always Tier 1
    if source in ("hackernews", "twitter", "bluesky"):
        return 1

    # Substacks = Tier 2
    if source == "substack":
        return 2

    # Gmail items — check sender for institutional
    if source == "gmail":
        if any(k in author for k in ["goldman", "gs macro", "pinto", "aei", "fed", "bls", "census"]):
            return 3
        if any(k in author for k in ["ft@", "financial times", "daily shot", "bloomberg"]):
            return 4
        return 3

    # Institutional from RSS
    if any(k in author or k in feed for k in ["goldman", "gs macro"]):
        return 3
    if any(k in author or k in feed for k in ["edward pinto", "aei housing", "aeihousing"]):
        return 3

    # Quality journalism from RSS
    if any(k in author or k in feed for k in [
        "financial times", "ft.com", "unhedged", "new york times", "nytimes",
        "bloomberg", "wsj", "wall street", "economist"
    ]):
        return 4
    if any(k in feed for k in ["housingwire", "inman"]):
        return 4
    if item.get("feed_priority") == "journal":
        return 4

    return SOURCE_TIERS.get(source, 5)


def _get_source_display_name(item: dict) -> str:
    """Get a human-readable source name for display."""
    feed = item.get("feed_name", "")
    author = item.get("author", "")
    source = item.get("source", "")

    if source == "hackernews":
        return "Hacker News"
    if source == "twitter":
        return f"Twitter ({author})" if author else "Twitter"
    if source == "bluesky":
        return f"Bluesky ({author})" if author else "Bluesky"
    if feed:
        return feed
    if source == "gmail" and author:
        match = re.match(r'^"?([^"<]+)"?\s*<', author)
        if match:
            return match.group(1).strip()
        return author.split("<")[0].strip() or author
    if source == "google_news":
        return "Google News"
    return source.title()


def _format_items_for_conversation(items: list[dict], limit: int = 150) -> str:
    """Format items for the conversation-focused synthesis prompt.

    Conversation items get full treatment (body + comments).
    Substacker takes get argument preview.
    News/institutional items get just title + URL, labeled as context.
    """
    for item in items:
        item["_tier"] = _get_source_tier(item)
        item["_source_display"] = _get_source_display_name(item)

    # Sort: conversation first, then by engagement within each tier
    sorted_items = sorted(items, key=lambda x: (
        x["_tier"],
        -(x.get("conversation_signal") or 0),
        -(x.get("num_comments") or 0),
        -(x.get("score") or 0),
    ))

    tier_names = {
        1: "CONVERSATION — Twitter economists, Bluesky, HN debates",
        2: "SUBSTACKER TAKES — Peer Analysis (FEATURE THESE PROMINENTLY, 3-5 minimum)",
        3: "INSTITUTIONAL SIGNAL — AEI, Goldman, Fed, ResiClub, Global Housing Watch (FEATURE KEY FINDINGS)",
        4: "JOURNALISM — Opinion & Analysis",
        5: "NEWS HEADLINES — Google News results for the headlines section",
    }

    by_tier = defaultdict(list)
    for item in sorted_items:
        by_tier[item["_tier"]].append(item)

    lines = []
    count = 0
    for tier in sorted(by_tier.keys()):
        tier_items = by_tier[tier]
        lines.append(f"\n### {tier_names.get(tier, f'TIER {tier}')} ({len(tier_items)} items)")

        for item in tier_items:
            if count >= limit:
                break

            topics = item.get("topics", "[]")
            if isinstance(topics, str):
                topics = json.loads(topics)
            stats = item.get("extracted_stats", "[]")
            if isinstance(stats, str):
                stats = json.loads(stats)
            claims = item.get("verifiable_claims", "[]")
            if isinstance(claims, str):
                try:
                    claims = json.loads(claims)
                except (json.JSONDecodeError, TypeError):
                    claims = []

            if tier == 1:
                # Conversation items: full body + comments (600 chars)
                body_preview = (item.get("body") or "")[:600]
                lines.append(
                    f"  [{item.get('conversation_signal', '?'):>3} conv | {item.get('num_comments', 0)} comments | "
                    f"score {item.get('score', 0)}] {item['_source_display']}: "
                    f"{item['title'][:200]}\n"
                    f"       Topics: {', '.join(topics) if topics else 'unclassified'}\n"
                    f"       URL: {item.get('url', '')}\n"
                    f"       {body_preview}"
                )
            elif tier == 2:
                # Substacker takes: 300 char preview to capture their argument
                body_preview = (item.get("body") or "")[:300]
                lines.append(
                    f"  {item['_source_display']}: {item['title'][:200]}\n"
                    f"       URL: {item.get('url', '')}\n"
                    f"       Preview: {body_preview}"
                )
            elif tier == 3:
                # Institutional: title + URL + body preview + key stats
                # These contain valuable analysis from AEI, Goldman, ResiClub, etc.
                body_preview = (item.get("body") or "")[:400]
                lines.append(
                    f"  {item['_source_display']}: {item['title'][:200]}\n"
                    f"       URL: {item.get('url', '')}\n"
                    f"       Preview: {body_preview}"
                    f"{' | Stats: ' + '; '.join(stats[:2]) if stats else ''}"
                )
            elif tier == 4:
                # Quality journalism: title + body preview for headline summaries
                body_preview = (item.get("body") or "")[:300]
                lines.append(
                    f"  {item['_source_display']}: {item['title'][:200]}\n"
                    f"       URL: {item.get('url', '')}\n"
                    f"       Preview: {body_preview}"
                )
            else:
                # News headlines (Tier 5): title + short preview
                body_preview = (item.get("body") or "")[:200]
                lines.append(
                    f"  {item['_source_display']}: {item['title'][:200]} "
                    f"[URL: {item.get('url', '')}]"
                    f"{chr(10) + '       Preview: ' + body_preview if body_preview else ''}"
                )
            count += 1

    return "\n".join(lines)


def _format_substacker_items(items: list[dict]) -> str:
    """Format Substack newsletter items as a dedicated section for the LLM."""
    if not items:
        return "No Substack newsletters collected in this period."
    lines = []
    for item in items[:10]:
        author = item.get("author", "")
        body_preview = (item.get("body") or "")[:400]
        lines.append(
            f"- {author}: {item.get('title', '')[:200]}\n"
            f"  URL: {item.get('url', '')}\n"
            f"  Preview: {body_preview}"
        )
    return "\n".join(lines)


def _format_rss_headlines(items: list[dict]) -> str:
    """Format RSS feed items as a dedicated section for headlines."""
    if not items:
        return "No RSS feed items collected in this period."
    lines = []
    for item in items[:50]:
        feed = item.get("feed_name", "")
        body_preview = (item.get("body") or "")[:200]
        lines.append(
            f"- [{feed}] {item.get('title', '')[:200]}\n"
            f"  URL: {item.get('url', '')}\n"
            f"  Preview: {body_preview}"
        )
    return "\n".join(lines)


def _format_reporter_items(items: list[dict]) -> str:
    """Format reporter-sourced Google News items."""
    if not items:
        return "No reporter-sourced articles found in this period."
    lines = []
    for item in items[:30]:
        author = item.get("author", "")  # publication name
        tags = item.get("platform_tags", "")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        query = tags[0] if isinstance(tags, list) and tags else ""
        body_preview = (item.get("body") or "")[:200]
        lines.append(
            f"- [{author}] {item.get('title', '')[:200]} (reporter query: {query[:40]})\n"
            f"  URL: {item.get('url', '')}\n"
            f"  Preview: {body_preview}"
        )
    return "\n".join(lines)


def _format_institutional_emails(items: list[dict]) -> str:
    """Format Gmail institutional items as a dedicated section for the LLM."""
    if not items:
        return "No institutional email newsletters collected in this period."
    lines = []
    for item in items[:15]:
        author = item.get("author", "")
        # Clean author display
        match = re.match(r'"?([^"<]+)"?\s*<', author)
        display = match.group(1).strip() if match else author.split("<")[0].strip() or author
        body_preview = (item.get("body") or "")[:400]
        lines.append(
            f"- {display}: {item.get('title', '')[:200]}\n"
            f"  URL: {item.get('url', '')}\n"
            f"  Preview: {body_preview}"
        )
    return "\n".join(lines)



# ── URL validation ────────────────────────────────────────────────────────────

def _get_known_urls(conn: sqlite3.Connection, hours: int = 48) -> set[str]:
    """Get all URLs from recently collected items."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT url FROM items WHERE collected_at >= ? AND url != ''",
        (cutoff,)
    ).fetchall()
    return {r["url"] for r in rows}


def _find_best_url_match(url: str, known_urls: set[str], threshold: float = 0.7) -> Optional[str]:
    """Find the closest matching known URL."""
    if url in known_urls:
        return url
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
    except Exception:
        return None
    same_domain = [u for u in known_urls if domain in u]
    if not same_domain:
        return None
    best_match = None
    best_ratio = 0.0
    for known in same_domain:
        ratio = SequenceMatcher(None, url, known).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = known
    if best_ratio >= threshold:
        return best_match
    return None


def _validate_briefing_urls(briefing: dict, conn: sqlite3.Connection) -> dict:
    """Post-process briefing to validate all URLs against the database."""
    known_urls = _get_known_urls(conn)
    audit = {"verified": 0, "corrected": 0, "stripped": 0, "corrections": []}

    # Trusted publication domains — URLs on these are real even if not in DB
    # (e.g., LLM reconstructs canonical URL from email/RSS metadata)
    trusted_domains = {
        "substack.com", "apricitas.io", "theovershoot.co", "noahpinion.blog",
        "aei.org", "brookings.edu", "nber.org", "federalreserve.gov",
        "bls.gov", "census.gov", "freddiemac.com", "fanniemae.com",
        "goldmansachs.com", "jpmorgan.com", "gs.com", "housingwire.com",
        "redfin.com", "zillow.com", "nar.realtor", "calculatedriskblog.com",
        # Major news publications
        "nytimes.com", "wsj.com", "bloomberg.com", "ft.com", "economist.com",
        "reuters.com", "cnbc.com", "washingtonpost.com", "latimes.com",
        "sfchronicle.com", "bostonglobe.com", "seattletimes.com",
        "fortune.com", "marketwatch.com", "axios.com", "semafor.com",
        "inman.com", "bisnow.com", "therealdeal.com", "costar.com",
        # Newsletter tracking/redirect domains (legitimate email links)
        "beehiiv.com", "prnewswire.com", "paragraph.com",
        "mail.google.com", "thesisdriven.com", "thedailyshot.com",
        "resiclubanalytics.com", "pulsenomics.com", "apollo.com",
        "coachingatcompass.com", "substack.com", "mailchimp.com",
        "sendgrid.net", "hubspot.com", "constantcontact.com",
        # Social media (Twitter roundup)
        "twitter.com", "x.com",
    }

    def validate_url(url: str, context: str) -> str:
        if not url or not url.startswith("http"):
            return url
        if url in known_urls:
            audit["verified"] += 1
            return url
        best = _find_best_url_match(url, known_urls)
        if best:
            audit["corrected"] += 1
            audit["corrections"].append({"context": context, "original": url, "corrected_to": best})
            logger.info(f"URL corrected: {url[:80]} -> {best[:80]} ({context})")
            return best
        # Allow URLs on trusted publication domains (real public URLs
        # even if DB only has redirect/gmail versions)
        try:
            domain = urlparse(url).netloc.lower().lstrip("www.")
            if any(domain == d or domain.endswith("." + d) for d in trusted_domains):
                audit["verified"] += 1
                return url
        except Exception:
            pass
        audit["stripped"] += 1
        logger.warning(f"URL stripped (no match): {url[:100]} ({context})")
        return ""

    # Validate URLs in all sections
    for i, theme in enumerate(briefing.get("conversation_themes", [])):
        for j, plat in enumerate(theme.get("platforms", [])):
            if "url" in plat:
                plat["url"] = validate_url(plat["url"], f"conversation_themes[{i}].platforms[{j}]")
    for i, take in enumerate(briefing.get("substacker_takes", [])):
        if "url" in take:
            take["url"] = validate_url(take["url"], f"substacker_takes[{i}]")
    for i, item in enumerate(briefing.get("twitter_roundup", [])):
        if "url" in item:
            item["url"] = validate_url(item["url"], f"twitter_roundup[{i}]")

    briefing["_url_audit"] = audit
    total = audit["verified"] + audit["corrected"] + audit["stripped"]
    logger.info(f"URL validation: {audit['verified']} verified, {audit['corrected']} corrected, {audit['stripped']} stripped (of {total} total)")
    return briefing


# ── Synthesis prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the conversation intelligence system for "Home Economics," a data journalism newsletter about the US housing market and economy by Aziz Sunderji.

Your job: surface what SMART PEOPLE are DEBATING, ARGUING ABOUT, and REACTING TO — not what news headlines say, and not populist doomposting. The editor can scan newspapers himself. What he can't easily see is the intellectual conversation among economists, housing analysts, and policy thinkers on Twitter, Bluesky, in Substacks, and on Hacker News.

CRITICAL: Quality over volume. A thread from Arpit Gupta, Jason Furman, or Claudia Sahm with 40 thoughtful replies is FAR more valuable than anonymous comments saying "economy is rigged." Prioritize:
1. Economist/analyst debates on Twitter and Bluesky (even if reply counts are modest)
2. Substacker arguments and newsletter analysis
3. Institutional research and data releases
4. Thoughtful HN discussions
5. Populist sentiment only if it reveals genuine trends (NOT just rage-bait)

You receive items from multiple platforms, ranked by INTELLECTUAL value:
- Tier 1 (PRIMARY): Economist/analyst conversation — Twitter debates among economists, HN discussions, Bluesky
- Tier 2: Substacker analysis — peer newsletters with specific arguments
- Tier 3: Institutional research — Goldman, AEI, Fed (from email)
- Tier 4: Quality journalism — opinion pieces with specific arguments
- Tier 5: News headlines — Google News + RSS feeds (use for the headlines section)

## Output Format

Return a JSON object:

{
  "date": "YYYY-MM-DD",

  "conversation_pulse": "3-4 sentences: mood/debate weather report. What is the overall tone of housing conversations right now? Are people panicking, cautiously optimistic, arguing about X vs Y? This is NOT a news summary — it's a read on the conversational temperature.",

  "conversation_themes": [
    {
      "theme": "Short label (5-8 words max)",
      "summary": "What people are saying across platforms. Quote actual thread titles, note the tenor of comments. Are people bullish or bearish? Is there genuine disagreement? What specific claims are being made?",
      "platforms": [
        {"name": "twitter", "reply_count": 89, "sentiment": "mixed", "url": "..."},
        {"name": "bluesky", "reply_count": 12, "sentiment": "bullish", "url": "..."}
      ],
      "heat_level": "low|medium|high|viral",
      "related_news_trigger": "What news event sparked this conversation, if any. Empty string if organic.",
      "topics": ["topic_key1", "topic_key2"]
    }
  ],

  "twitter_roundup": [
    {
      "author": "@handle",
      "take": "1-2 sentence summary of their specific point or argument",
      "url": "tweet URL"
    }
  ],

  "substacker_takes": [
    {
      "author": "Name (Publication)",
      "title": "Article title",
      "take": "Their specific ARGUMENT — not just the topic. What position are they staking out? e.g., 'Erdmann argues that supply constraints in Austin are structural, not cyclical, and prices will rebound within 18 months.'",
      "url": "..."
    }
  ],

  "stats_summary": {
    "total_items_analyzed": N,
    "conversation_items": N,
    "platforms_active": N,
    "source_breakdown": {"Hacker News": N, "Twitter": N, "Bluesky": N, "Substack": N, ...}
  }
}

## Rules

1. INTELLECTUAL CONVERSATION FIRST. What are economists and housing analysts debating? A Twitter exchange between Arpit Gupta and Jason Furman about whether tariffs will push mortgage rates up is the gold standard. Lead with the smart conversation.

2. QUOTE REAL PEOPLE BY NAME. "Claudia Sahm argues the labor market is weakening faster than the Fed acknowledges" is useful. "Users are panicking" is not. Focus on substantive discussions, not populist venting.

3. NEWS IN CONVERSATION THEMES. If mainstream media articles spark genuine debate among economists on Twitter/Bluesky, include that debate as a conversation theme. But do NOT include news articles that aren't generating conversation.

4. REAL URLS ONLY. Every source must include the actual URL from the collected items. Never fabricate URLs.

5. SUBSTACKER TAKES MUST COME FROM SUBSTACK NEWSLETTERS ONLY. The substacker_takes section is EXCLUSIVELY for items from the "Substack Newsletters" section above. Do NOT include Twitter commentators or any other source. Use the URL provided with each Substack item (even if it's a redirect link). For each take, summarize their specific ARGUMENT — not just the topic. "Erdmann argues builders are underbuilding relative to population growth" is good. "Erdmann wrote about housing supply" is not. IMPORTANT: Include a take for EVERY Substack newsletter provided. Do not cherry-pick — summarize all of them.

6. CONVERSATION THEMES: 3-6 themes max. Each must have platform evidence. At least 2 themes should involve economist/analyst voices.

8. ONE TOPIC PER THEME. Do NOT group unrelated threads or voices into one theme just to reduce count. If Winton ARK is talking about AI and photography employment, and Arindube is making a separate argument about AI asset valuations, those are TWO separate themes — not one. Only group threads together when they are genuinely part of the SAME conversation (people replying to each other, referencing each other's points). Three separate people talking about three separate things on the same broad topic is NOT one theme.

9. HEAT LEVELS: "viral" = 500+ comments across platforms, "high" = active debate with strong opinions, "medium" = noticeable discussion, "low" = a few mentions.

10. KEEP IT UNDER 30,000 CHARACTERS. The headlines section alone will be substantial — that's fine.

11. SKIP IRRELEVANT NOISE. Do not feature: Nigerian/international housing stories, memes about landlords, generic "economy is rigged" venting, partisan political rants with no economic substance.

12. TWITTER ROUNDUP: Feature 20-30 individual economist/analyst voices in the twitter_roundup section. This is a quick-scan section so the reader can see what specific people are saying. CRITICAL RULES:
    a. Do NOT include any tweet or voice you already covered in conversation_themes. If @jasonfurman's thread was featured as a conversation theme, do NOT put him in the twitter roundup too. Use the roundup to surface DIFFERENT voices and takes that didn't make it into the themes.
    b. Include a DIVERSE range of voices — aim for 20+ DIFFERENT handles. Do not over-index on any 2-3 accounts (e.g., do not feature the same person in multiple entries). Spread across different perspectives and expertise areas.
    c. Each entry should name the author (@handle), summarize their specific take in 1-2 sentences, and include the tweet URL.
    d. Prioritize: contrarian views, data-backed claims, novel arguments, and lesser-known voices the reader might not follow.
    e. You can include more than one tweet per person if they made multiple substantive points on different topics.

13. ALL SECTIONS ARE MANDATORY. Your JSON output MUST include ALL of these keys with populated arrays: conversation_themes, twitter_roundup, substacker_takes. If you omit any section, the briefing is broken. substacker_takes should include a take for EVERY Substack newsletter provided — summarize all of them, not just a few.

"""


def generate_daily_briefing(
    conn: sqlite3.Connection,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    """Generate the full daily briefing (conversation-focused).

    Returns structured briefing dict.
    """
    client = client or anthropic.Anthropic()

    # Gather all inputs
    all_items = get_items_since(conn, hours=36, min_relevance=0)
    # Curated sources (Twitter, Bluesky) get a lower threshold since they come
    # from hand-picked accounts — even off-topic tweets from economists are worth seeing.
    # Other sources (RSS, Google News) use the standard threshold.
    curated_sources = {"twitter", "bluesky"}
    relevant_items = [
        i for i in all_items
        if (i.get("relevance_score") or 0) >= (10 if i.get("source") in curated_sources else 30)
    ]
    convergence = compute_convergence(conn, hours=36)
    shifts = detect_narrative_shifts(conn)
    organic = detect_organic_conversations(conn, hours=36)
    stats = get_collection_stats(conn, hours=36)

    # Source breakdown with human-readable names
    source_display_counts = Counter()
    for item in all_items:
        source_display_counts[_get_source_display_name(item)] += 1

    # Conversation item counts
    conversation_items = [i for i in all_items if (i.get("conversation_signal") or 0) >= 30]

    # Get collection errors for transparency
    collection_errors = get_recent_collection_errors(conn, hours=36)

    # Substacker items (from RSS feeds + Gmail-detected Substack newsletters)
    # Dedupe by title, exclude user's own posts
    substacker_items = []
    seen_titles = set()
    for i in all_items:
        if i["source"] != "substack":
            continue
        author_lower = (i.get("author") or "").lower()
        if "aziz" in author_lower or "home-economics" in author_lower:
            continue
        title_lower = (i.get("title") or "").strip().lower()
        if any(p in title_lower for p in ["subscriber", "unsubscription", "payment receipt", "discussion thread", "open thread", "sunday thread", "saturday discussion", "chat thread", "mailbag"]):
            continue
        title_key = title_lower[:60]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        substacker_items.append(i)
    # Also include Gmail newsletter senders (Brandon Donnelly, FT Unhedged, etc.)
    from config import GMAIL_NEWSLETTER_SENDERS
    for i in all_items:
        if i.get("source") != "gmail":
            continue
        sender = (i.get("author") or "").lower()
        if not any(p in sender for p in GMAIL_NEWSLETTER_SENDERS):
            continue
        title_lower = (i.get("title") or "").strip().lower()
        title_key = title_lower[:60]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        substacker_items.append(i)

    substacker_items.sort(key=lambda x: -(x.get("relevance_score") or 0))
    logger.info(f"Newsletter items: {len(substacker_items)} (Substack RSS + Gmail newsletters, deduped)")

    # Log source breakdown for relevant items
    relevant_source_counts = Counter(i.get("source", "?") for i in relevant_items)
    logger.info(f"Relevant items by source: {dict(relevant_source_counts.most_common())}")
    # Log Twitter author diversity
    twitter_authors = set(i.get("author", "") for i in relevant_items if i.get("source") == "twitter")
    logger.info(f"Unique Twitter authors in relevant items: {len(twitter_authors)}")

    logger.info(
        f"Synthesis inputs: {len(all_items)} total items ({len(relevant_items)} above threshold, "
        f"{len(conversation_items)} conversation items), "
        f"{len(convergence)} convergence topics"
    )

    user_content = f"""## Today's Collected Items — {len(all_items)} total, {len(relevant_items)} above relevance threshold, {len(conversation_items)} with active conversation

{_format_items_for_conversation(relevant_items, limit=150)}

## Newsletters — SUBSTACKER TAKES (use ONLY these for the substacker_takes section)
These are newsletter articles (Substack + email newsletters). Populate substacker_takes from this list. Use the URL provided with each item. Summarize EVERY one.

{_format_substacker_items(substacker_items)}

## Cross-Platform Convergence (topics appearing on 3+ platforms)

{json.dumps(convergence[:10], indent=2, default=str) if convergence else "No convergence detected."}

## Narrative Shifts (topics where sentiment changed significantly)

{json.dumps(shifts[:5], indent=2, default=str) if shifts else "No significant shifts."}

## Organic Conversations (discussions with no news trigger)

{json.dumps([{"title": o["title"][:100], "source": o["source"], "score": o.get("score", 0), "url": o.get("url", "")} for o in organic[:10]], indent=2) if organic else "None detected."}

Generate the daily briefing JSON. LEAD WITH CONVERSATION — what are people debating, arguing about, reacting to? News is context only."""

    try:
        response_text = ""
        with client.messages.stream(
            model=MODEL,
            max_tokens=32768,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            for text in stream.text_stream:
                response_text += text
            final = stream.get_final_message()

        response_text = response_text.strip()

        if final.stop_reason == "max_tokens":
            logger.warning(f"Response truncated at max_tokens ({len(response_text)} chars).")

        # Handle markdown code blocks
        if response_text.startswith("```"):
            parts = response_text.split("```")
            if len(parts) >= 2:
                json_part = parts[1]
                if json_part.startswith("json"):
                    json_part = json_part[4:]
                response_text = json_part.strip()

        try:
            briefing = json.loads(response_text)
        except json.JSONDecodeError as e:
            # Attempt repair: common LLM JSON issues
            logger.warning(f"Initial JSON parse failed ({e}), attempting repair...")
            repaired = response_text

            # Fix: truncated response — try to close open structures
            if repaired.count('{') > repaired.count('}'):
                # Find the last complete object/array and close remaining braces
                depth_brace = repaired.count('{') - repaired.count('}')
                depth_bracket = repaired.count('[') - repaired.count(']')
                # Trim to last complete string value (find last untruncated quote)
                last_quote = repaired.rfind('"')
                if last_quote > 0:
                    # Check if we're mid-value — look for the pattern ": " before it
                    before = repaired[:last_quote + 1]
                    repaired = before + ']' * depth_bracket + '}' * depth_brace

            # Fix: unescaped quotes inside strings — try a lenient approach
            # by asking Haiku to fix the JSON
            try:
                briefing = json.loads(repaired)
                logger.info("JSON repair succeeded (bracket closing)")
            except json.JSONDecodeError:
                logger.warning("Bracket repair failed, asking Haiku to fix JSON...")
                try:
                    fix_resp = client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=16384,
                        messages=[{"role": "user", "content": (
                            "The following JSON has a syntax error. Fix ONLY the JSON syntax "
                            "(escape quotes, close brackets, fix commas) without changing any content. "
                            "Return ONLY the fixed JSON, no explanation.\n\n"
                            + response_text[:14000]
                        )}],
                    )
                    fixed_text = fix_resp.content[0].text.strip()
                    if fixed_text.startswith("```"):
                        fixed_text = fixed_text.split("```")[1]
                        if fixed_text.startswith("json"):
                            fixed_text = fixed_text[4:]
                        if "```" in fixed_text:
                            fixed_text = fixed_text[:fixed_text.index("```")]
                    briefing = json.loads(fixed_text.strip())
                    logger.info("JSON repair succeeded (Haiku fix)")
                except Exception as fix_err:
                    logger.error(f"JSON repair also failed: {fix_err}")
                    raise e  # Re-raise the original error

        # === POST-PROCESSING: Fix common Sonnet omissions ===

        # 1. Deduplicate twitter_roundup — one entry per author
        roundup = briefing.get("twitter_roundup", [])
        if roundup:
            seen_authors = set()
            deduped = []
            for entry in roundup:
                author = (entry.get("author") or "").lower().strip()
                if author and author not in seen_authors:
                    seen_authors.add(author)
                    deduped.append(entry)
            if len(deduped) < len(roundup):
                logger.info(f"Twitter roundup deduped: {len(roundup)} → {len(deduped)} entries")
            briefing["twitter_roundup"] = deduped

        # 2. Supplement twitter_roundup if Sonnet returned fewer than 20
        roundup_authors = {(e.get("author") or "").lower().strip() for e in briefing.get("twitter_roundup", [])}
        theme_urls = set()
        for theme in briefing.get("conversation_themes", []):
            for p in theme.get("platforms", []):
                if p.get("url"):
                    theme_urls.add(p["url"])
        if len(briefing.get("twitter_roundup", [])) < 20:
            twitter_supplement = [
                i for i in relevant_items
                if i.get("source") == "twitter"
                and (i.get("relevance_score") or 0) >= 60
                and (i.get("author") or "").lower().strip() not in roundup_authors
                and i.get("url", "") not in theme_urls
            ]
            twitter_supplement.sort(key=lambda x: -(x.get("relevance_score") or 0))
            for item in twitter_supplement:
                author = (item.get("author") or "").strip()
                author_key = author.lower()
                if author_key in roundup_authors:
                    continue
                roundup_authors.add(author_key)
                body = (item.get("body") or "")[:150]
                briefing.setdefault("twitter_roundup", []).append({
                    "author": author if author.startswith("@") else f"@{author}",
                    "take": body if body else item.get("title", "")[:150],
                    "url": item.get("url", ""),
                })
                if len(briefing["twitter_roundup"]) >= 30:
                    break

        # Validate all URLs against the database
        briefing = _validate_briefing_urls(briefing, conn)

        # Inject the human-readable source breakdown
        if "stats_summary" not in briefing:
            briefing["stats_summary"] = {}
        briefing["stats_summary"]["source_breakdown"] = dict(source_display_counts.most_common(20))
        briefing["stats_summary"]["total_items_analyzed"] = len(all_items)
        briefing["stats_summary"]["conversation_items"] = len(conversation_items)
        briefing["stats_summary"]["platforms_active"] = len(set(i["source"] for i in all_items))

        # Attach collection errors for email transparency
        if collection_errors:
            briefing["_collection_errors"] = [
                {"source": e["source"], "error": e["error"], "time": e["started_at"]}
                for e in collection_errors
            ]

        # Save the briefing
        briefing_id = save_briefing(conn, "daily", briefing)
        briefing["_briefing_id"] = briefing_id

        logger.info(f"Generated daily briefing (ID: {briefing_id})")
        return briefing

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse synthesis response: {e}")
        logger.debug(f"Raw response (first 2000 chars): {response_text[:2000]}")
        return {"error": str(e), "raw_response": response_text[:2000]}
    except Exception as e:
        logger.error(f"Synthesis API error: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    conn = get_db()
    briefing = generate_daily_briefing(conn)
    print(json.dumps(briefing, indent=2))
