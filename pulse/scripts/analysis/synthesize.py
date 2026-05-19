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
import time
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

MODEL = "claude-sonnet-4-6"  # Sonnet 4.6 has native 1M context; 4.5 only had 200K

# Two-tier system:
# Tier 1: All current conversation and journalism — competes equally for themes
#   Twitter, Bluesky, HN, Substacks, RSS feeds, Google News, newspapers
# Tier 2: Institutional research — only pulled in if the finding is directly newsworthy
#   Goldman, AEI, Fed, NBER, BLS, Census data releases
SOURCE_TIERS = {
    "hackernews": 1, "twitter": 1, "bluesky": 1,
    "substack": 1, "google_news": 1, "rss": 1, "gmail": 1,
}

_INSTITUTIONAL_SIGNALS = [
    "goldman", "gs macro", "edward pinto", "aei housing", "aeihousing",
    "federal reserve", "newyorkfed", "bls.gov", "census.gov", "fhfa",
    "freddiemac", "fanniemae", "nber",
    # Academic journals — research papers, not today's news
    "sciencedirect", "journal of housing", "journal of urban", "journal of real estate",
    "tandfonline", "wiley: real estate", "springer", "journal of the american planning",
]


def _normalize_substack_redirect(url: str) -> str:
    """Substack delivers many newsletter feeds as redirect URLs whose decoded
    payload points at the publisher's /subscribe page. Decode the b64 in the
    redirect path; if the destination is a subscribe-only page, trim back to
    the publisher's homepage so the reader can navigate to the actual article.
    Falls back to the raw URL on any failure.
    """
    if not url or "substack.com/redirect/" not in url:
        return url
    try:
        import base64
        import json as _json
        from urllib.parse import urlparse as _up
        # Path format: /redirect/2/<base64-payload>
        path = _up(url).path
        parts = [p for p in path.split("/") if p]
        if len(parts) < 3:
            return url
        payload = parts[-1]
        # Pad if needed for base64
        payload += "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(payload).decode("utf-8", errors="ignore")
        except Exception:
            decoded = base64.b64decode(payload).decode("utf-8", errors="ignore")
        # Decoded is a JSON-ish blob with key "e" = destination URL
        m = re.search(r'"e"\s*:\s*"([^"]+)"', decoded)
        if not m:
            return url
        dest = m.group(1)
        # Strip /subscribe path and query params → publisher homepage
        parsed = _up(dest)
        path_clean = parsed.path
        if path_clean.rstrip("/").endswith("/subscribe"):
            path_clean = path_clean.rstrip("/").rsplit("/subscribe", 1)[0] + "/"
        return f"{parsed.scheme}://{parsed.netloc}{path_clean}"
    except Exception:
        return url


def _get_source_tier(item: dict) -> int:
    """Determine source tier.

    All sources are Tier 1 (compete equally for themes). Tier 2 used to demote
    institutional research as "background only" but that meant Goldman / Fed /
    Census / Urban Institute / AEI items rarely surfaced in conversation_themes
    despite being substantive signal. Per user request, those are now eligible
    to anchor themes the same as any other source.

    The function is kept as a placeholder for future tier logic (e.g. demoting
    truly off-topic content) but currently returns 1 for everything.
    """
    return 1


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


def _format_items_for_conversation(items: list[dict], limit: int = 280) -> str:
    """Format items for the conversation-focused synthesis prompt.

    Conversation items get full treatment (body + comments).
    Substacker takes get argument preview.
    News/institutional items get just title + URL, labeled as context.
    """
    for item in items:
        item["_tier"] = _get_source_tier(item)
        item["_source_display"] = _get_source_display_name(item)

    tier_names = {
        1: "ALL SOURCES — Twitter, Bluesky, HackerNews, Substacks, Gmail (incl. institutional research), Newspapers, RSS (all compete equally for themes)",
    }

    # Single ranking criterion: relevance_score. But reserve slots so
    # substantive long-form content (enriched newspaper articles, newsletters,
    # substacks with real body text) gets guaranteed representation alongside
    # the many shorter tweets. A tweet "competing" with a 5000-char article on
    # equal terms loses because tweets feel complete while truncated articles
    # feel incomplete — so Sonnet picks tweets. The reserve fixes that.
    MAX_PER_AUTHOR_SOCIAL = 8  # per-author cap is now per-THREAD, not per-tweet:
                                # threads (CSElmendorf's 14-tweet CA housing analysis,
                                # nickgerli1's 12-tweet Seattle correction) need all
                                # their tweets in Sonnet's view to make sense as a
                                # coherent argument. Sonnet collapses them into one
                                # summary per author downstream (see prompt rule 12d).
                                # Cap at 8/author to prevent runaway thread spam.
    SUPER_SMART_RESERVED_SLOTS = 80  # guaranteed seats for SuperSmart-tagged items —
                                     # short curated list of must-have voices that
                                     # get included regardless of relevance score
    LONGFORM_RESERVED_SLOTS = 60  # guaranteed seats for long-form with real body
    LONGFORM_MIN_BODY = 1500      # chars — "real body" threshold
    LONGFORM_SOURCES = {"rss", "substack", "gmail"}
    EMAIL_RESERVED_SLOTS = 40     # guaranteed seats for gmail items (institutional research,
                                  # newsletters, columnist emails). Without this, Twitter
                                  # crowded out gmail in Phase 2 because there are 10x more
                                  # tweets. User explicitly flagged that emails with relevant
                                  # content were not reaching the LLM.

    sorted_items = sorted(items, key=lambda x: (
        x["_tier"],
        -(x.get("relevance_score") or 0),
    ))

    reserved_ids = set()
    by_tier = defaultdict(list)

    # Phase 0: SuperSmart items get guaranteed seats (regardless of relevance).
    # These come from the curated SuperSmart Twitter list; user maintains
    # membership in Twitter UI. The point is to never lose their voices
    # to lower-relevance-but-louder accounts.
    def _is_super_smart(item: dict) -> bool:
        tags = item.get("platform_tags", [])
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except Exception: tags = []
        return "super_smart" in (tags or [])

    super_smart_taken = 0
    # Iterate in relevance order so high-relevance super_smart tweets fill first
    for item in sorted_items:
        if super_smart_taken >= SUPER_SMART_RESERVED_SLOTS:
            break
        if not _is_super_smart(item):
            continue
        by_tier[item["_tier"]].append(item)
        reserved_ids.add(id(item))
        super_smart_taken += 1
    if super_smart_taken:
        logger.info(f"SuperSmart reserved: {super_smart_taken} items guaranteed in synthesis input")

    # Phase 1a: fill reserved long-form seats, top-relevance first
    longform_taken = 0
    for item in sorted_items:
        if item["_tier"] != 1 or longform_taken >= LONGFORM_RESERVED_SLOTS:
            continue
        if id(item) in reserved_ids:
            continue  # already grabbed by SuperSmart phase
        src = (item.get("source") or "").lower()
        if src not in LONGFORM_SOURCES:
            continue
        body_len = len(item.get("body") or "")
        if body_len < LONGFORM_MIN_BODY:
            continue
        by_tier[1].append(item)
        reserved_ids.add(id(item))
        longform_taken += 1

    # Phase 1b: fill reserved gmail seats, top-relevance first.
    # This is in addition to the long-form reserve — a long-form gmail item
    # might already be reserved from Phase 1a, in which case it's skipped
    # here. The 40 email slots fill with whatever gmail items remain.
    email_taken = 0
    for item in sorted_items:
        if item["_tier"] != 1 or email_taken >= EMAIL_RESERVED_SLOTS:
            continue
        if id(item) in reserved_ids:
            continue
        if (item.get("source") or "").lower() != "gmail":
            continue
        by_tier[1].append(item)
        reserved_ids.add(id(item))
        email_taken += 1

    # Phase 2: fill remaining slots by pure relevance, with per-author cap on social.
    # Normalize the author key across platforms — @mnolangray on Twitter and
    # @mnolangray.bsky.social on Bluesky are the same person and shouldn't both
    # get separate slots.
    def _author_key(item):
        a = (item.get("author") or "").lower().strip().lstrip("@")
        # Strip platform suffixes so cross-platform handles collapse
        for suffix in (".bsky.social", ".bsky", "@twitter", "@x"):
            if a.endswith(suffix):
                a = a[: -len(suffix)]
        return a

    author_counts: dict[str, int] = {}
    for item in sorted_items:
        if id(item) in reserved_ids:
            continue
        src = (item.get("source") or "").lower()
        if src in ("twitter", "bluesky"):
            akey = _author_key(item)
            if akey:
                n = author_counts.get(akey, 0)
                if n >= MAX_PER_AUTHOR_SOCIAL:
                    continue
                author_counts[akey] = n + 1
        by_tier[item["_tier"]].append(item)

    # Within each tier, group consecutive same-author social items so they
    # appear visually as a thread — Sonnet can see the full thread arc and
    # write ONE summary per author covering all of it.
    def _author_key_for_grouping(item: dict) -> str:
        a = (item.get("author") or "").lower().strip().lstrip("@")
        for suffix in (".bsky.social", ".bsky", "@twitter", "@x"):
            if a.endswith(suffix):
                a = a[: -len(suffix)]
        return a

    for tier in by_tier:
        social_items = [
            i for i in by_tier[tier]
            if (i.get("source") or "").lower() in ("twitter", "bluesky")
        ]
        other_items = [
            i for i in by_tier[tier]
            if (i.get("source") or "").lower() not in ("twitter", "bluesky")
        ]
        # Group social by author (preserving relevance order across groups)
        from collections import OrderedDict
        grouped = OrderedDict()
        for it in social_items:
            grouped.setdefault(_author_key_for_grouping(it), []).append(it)
        # Re-flatten author-by-author, each author's tweets contiguous
        regrouped_social = []
        for k, group in grouped.items():
            # Sort each author's tweets by relevance (highest first within thread)
            group.sort(key=lambda x: -(x.get("relevance_score") or 0))
            regrouped_social.extend(group)
        by_tier[tier] = other_items + regrouped_social

    lines = []
    count = 0
    last_author_key = None  # used to insert thread-grouping markers
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

            source = (item.get("source") or "").lower()
            body = item.get("body") or ""

            if source in ("twitter", "bluesky", "hackernews"):
                # Social: 600 chars covers the full tweet/post in almost all cases.
                # ALL volume signals (likes, retweets, comment count) hidden from
                # Sonnet — user feedback was that any volume-correlated signal
                # biases toward dumb-but-loud accounts. The engagement floor
                # (TWITTER_MIN_LIKES=2) is enforced at collection, so anything
                # reaching Sonnet has at least minimal engagement; beyond that
                # let Sonnet judge on substance (topics + content), not popularity.
                # conversation_signal kept because it's Haiku's quality rating
                # of debate intensity, not a raw count.
                #
                # Thread grouping: consecutive same-author items get a "↪" prefix
                # so Sonnet visually sees them as a thread to summarize together.
                akey = _author_key_for_grouping(item)
                is_continuation = (akey == last_author_key and source in ("twitter", "bluesky"))
                last_author_key = akey
                body_preview = body[:600]
                prefix = "  ↪ " if is_continuation else "  "
                # For HN items, expose BOTH URLs so Sonnet can cite the HN
                # discussion (news.ycombinator.com/item?id=X) separately from
                # the underlying article URL. Without this Sonnet writes
                # "Hacker News commenters" but links to the publisher's site.
                url_block = f"       URL: {item.get('url', '')}\n"
                if source == "hackernews":
                    sid = item.get("source_id", "")
                    if sid.startswith("hn_"):
                        story_id = sid[3:]
                        hn_url = f"https://news.ycombinator.com/item?id={story_id}"
                        url_block = (
                            f"       Article URL: {item.get('url', '')}  (use this to cite the underlying publication)\n"
                            f"       HN Discussion URL: {hn_url}  (use this when citing 'Hacker News commenters' or 'the HN thread')\n"
                        )
                lines.append(
                    f"{prefix}[{item.get('conversation_signal', '?'):>3} conv] "
                    f"{item['_source_display']}: "
                    f"{item['title'][:200]}\n"
                    f"       Topics: {', '.join(topics) if topics else 'unclassified'}\n"
                    f"{url_block}"
                    f"       {body_preview}"
                )
            else:
                # Long-form sources (newspapers, substacks, gmail newsletters):
                # give the LLM 3000 chars of content — enough for the substantive
                # middle of an article, not just the lede.
                body_preview = body[:3000]
                lines.append(
                    f"  {item['_source_display']}: {item['title'][:200]}\n"
                    f"       URL: {item.get('url', '')}\n"
                    f"       {body_preview}"
                    f"{' | Stats: ' + '; '.join(stats[:2]) if stats and tier == 2 else ''}"
                )
            count += 1

    return "\n".join(lines)


def _format_historical_items(items: list[dict]) -> str:
    """Compressed format for historical (past 6 days) context items.

    Each item rendered as one line: relative date, author/handle, snippet, URL.
    Sonnet uses these to weave the longer arc of a story into today's themes.
    """
    if not items:
        return "No historical context."
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    lines = []
    for item in items:
        # Compute relative day label
        ts_str = item.get("published_at") or item.get("collected_at") or ""
        label = ""
        try:
            ts = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
            days_ago = (now - ts).days
            if days_ago == 0: label = "today"
            elif days_ago == 1: label = "yesterday"
            elif days_ago < 7: label = ts.strftime("%a")  # Mon, Tue, etc.
            else: label = f"{days_ago}d ago"
        except Exception:
            pass
        author = item.get("author") or item.get("feed_name") or item.get("source", "")
        title = (item.get("title") or "")[:90]
        body = (item.get("body") or "")[:80].replace("\n", " ").strip()
        url = item.get("url", "")
        line = f"  [{label}] {author}: {title}"
        if body and len(body) > 30:
            line += f" — {body}"
        line += f"  URL: {url}"
        lines.append(line)
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
    """Get all URLs from recently collected items.

    Also adds synthetic HN-discussion URLs (news.ycombinator.com/item?id=X)
    for every hackernews item, so Sonnet's "Hacker News commenters" citations
    (which we now expose as a separate URL in the synthesis input) pass URL
    validation. Without this, the validator strips them as unknown.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        "SELECT url, source, source_id FROM items WHERE collected_at >= ? AND url != ''",
        (cutoff,)
    ).fetchall()
    urls = {r["url"] for r in rows}
    for r in rows:
        if r["source"] == "hackernews" and (r["source_id"] or "").startswith("hn_"):
            urls.add(f"https://news.ycombinator.com/item?id={r['source_id'][3:]}")
    return urls


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


_REFUSAL_PATTERNS = re.compile(
    r"(i (?:don'?t|do not|can'?t|cannot|am unable to)\s+(?:have|access|provide|offer|see|read|view)|"
    r"(?:the )?(?:full )?(?:email )?(?:content|snippet|preview|article|body)\s+(?:is|isn'?t|is not|seems|appears)\s+"
    r"(?:cut off|truncated|not visible|incomplete|limited|insufficient|unavailable|missing)|"
    r"based on (?:the )?(?:limited|partial|truncated|brief|short)\s+(?:preview|snippet|excerpt)|"
    r"(?:partial|limited)\s+summary|"
    r"without (?:the |access to )?(?:full|more)\s+(?:content|context|details))",
    re.IGNORECASE,
)


_HOUSING_THEME_TOPICS = {
    "homeownership_demographics", "cities_urbanism", "demographics_general",
    "immigration_housing", "ai_and_housing", "happiness_wellbeing",
    "cultural_lifestyle", "politics_housing", "housing_geography",
    "affordability", "housing_policy", "housing_prices", "housing_inventory",
    "mortgage_rates", "construction_supply", "climate_insurance",
    "commercial_real_estate", "tech_geography",
}


def _enforce_housing_focused_themes(briefing: dict) -> dict:
    """Drop themes whose own topic tags have ZERO overlap with the housing-
    related set. Even though the synth prompt asks for 70%+ housing themes,
    Sonnet routinely picks off-topic anchors (Fed/Iran macro, AI scheming,
    3D printer lawsuits) when the input pool runs out of fresh housing
    stories. Belt-and-braces: post-process strip them.

    Items demoted to twitter_roundup if they have a clear anchor handle.
    """
    themes = briefing.get("conversation_themes", []) or []
    if not themes:
        return briefing

    kept: list[dict] = []
    dropped: list[dict] = []
    for theme in themes:
        topics = theme.get("topics", []) or []
        topic_set = {str(t).lower() for t in topics}
        if topic_set & _HOUSING_THEME_TOPICS:
            kept.append(theme)
        else:
            dropped.append(theme)

    if dropped:
        logger.warning(
            f"Housing-focus filter: dropped {len(dropped)} themes with no housing "
            f"topic overlap: {[t.get('theme','')[:50] for t in dropped]}"
        )
    briefing["conversation_themes"] = kept
    return briefing


def _enforce_per_author_theme_cap(briefing: dict) -> dict:
    """Belt-and-braces companion to prompt rule 11b — enforce in two passes:

    Pass A: Drop themes where the PRIMARY ANCHOR (first twitter URL) is a handle
    already anchoring an earlier theme. The dropped theme's substance is demoted
    into twitter_roundup so the content isn't lost.

    Pass B: For any handle cited in 2+ themes (anchor or secondary reference),
    keep the citation in the FIRST theme (its anchor or first mention) and
    strip the URL from secondary references — converts the markdown link to
    plain text in those themes. Preserves prose flow but removes redundant
    Brooks/Hoops links across themes.

    Sonnet routinely violates "max 1 theme per voice" — yesterday's @jonbrooks
    anchored 3 themes; today the same person was cited in pulse + theme 1
    anchor + theme 3 secondary. This function makes the rule actually stick.
    """
    themes = briefing.get("conversation_themes", []) or []
    if not themes:
        return briefing

    # ── Pass A: Drop themes whose primary anchor duplicates an earlier theme ──
    seen_anchors: set[str] = set()
    kept: list[dict] = []
    dropped: list[tuple[str, dict]] = []

    for theme in themes:
        s = theme.get("summary", "") or ""
        m = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/status", s, re.IGNORECASE)
        anchor = m.group(1).lower() if m else None
        if anchor and anchor in seen_anchors:
            dropped.append((anchor, theme))
            continue
        if anchor:
            seen_anchors.add(anchor)
        kept.append(theme)

    if dropped:
        logger.warning(
            f"Per-author cap (anchor): dropped {len(dropped)} themes whose anchor was already "
            f"used: {[a for a, _ in dropped]}"
        )
        roundup = briefing.setdefault("twitter_roundup", [])
        existing_authors = {(e.get("author") or "").lower().lstrip("@") for e in roundup}
        for handle, theme in dropped:
            if handle in existing_authors:
                continue
            link_match = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", theme.get("summary", ""))
            if link_match:
                phrase, url = link_match.group(1), link_match.group(2)
                if handle in url.lower():
                    roundup.append({
                        "author": f"@{handle}",
                        "summary": f"[{phrase}]({url})",
                        "tweet_count": 1,
                    })
                    existing_authors.add(handle)

    briefing["conversation_themes"] = kept

    # ── Pass B: Drop secondary citation SENTENCES across themes ──
    # Earlier version only stripped the URL but left duplicate sentences ("CAYIMBY
    # notes every California governor candidate is now claiming to be pro-housing"
    # appeared verbatim in two themes). The user saw two identical CAYIMBY posts.
    # Now we drop the entire sentence containing a secondary citation — link,
    # @handle mention, or proper-name reference — so the secondary theme prose
    # flows without the redundancy.
    # "Primary theme" for a handle = the theme where this handle is the FIRST
    # URL anchor (i.e., the theme is structurally about them). If a handle
    # appears in multiple themes but isn't the anchor of any, fall back to the
    # first theme that mentions them.
    handle_first_theme: dict[str, int] = {}
    handle_anchor_theme: dict[str, int] = {}
    for i, theme in enumerate(briefing["conversation_themes"]):
        s = theme.get("summary", "") or ""
        # First URL match = this theme's anchor
        anchor_match = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/status", s, re.IGNORECASE)
        anchor_handle = anchor_match.group(1).lower() if anchor_match else None
        if anchor_handle and anchor_handle not in handle_anchor_theme:
            handle_anchor_theme[anchor_handle] = i
        # Track first-mention for handles never anchored
        for m in re.finditer(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/status", s, re.IGNORECASE):
            h = m.group(1).lower()
            if h not in handle_first_theme:
                handle_first_theme[h] = i
    # Prefer anchor-theme as primary; fall back to first-mention
    for h, idx in handle_anchor_theme.items():
        handle_first_theme[h] = idx

    def _split_sentences_link_safe(text: str) -> list[str]:
        """Split into sentences, masking markdown links so dots inside URLs
        don't trigger false sentence boundaries."""
        link_re = re.compile(r"\[[^\]]+\]\([^)]+\)")
        placeholders: list[str] = []
        def _mask(m):
            placeholders.append(m.group(0))
            return f"\x00LINK{len(placeholders)-1}\x00"
        masked = link_re.sub(_mask, text)
        parts = re.split(r"(?<=[.!?])\s+", masked)
        out = []
        for p in parts:
            for j, link in enumerate(placeholders):
                p = p.replace(f"\x00LINK{j}\x00", link)
            out.append(p)
        return out

    secondary_drops = 0
    for i, theme in enumerate(briefing["conversation_themes"]):
        s = theme.get("summary", "") or ""
        sentences = _split_sentences_link_safe(s)
        kept_sentences: list[str] = []
        for sent in sentences:
            drop = False
            for handle, first_idx in handle_first_theme.items():
                if i == first_idx:
                    continue  # primary theme — keep everything
                # Check for any mention: URL link to this handle's status,
                # or @handle, or the bare handle name (case-insensitive)
                handle_url_re = (
                    r"https?://(?:www\.)?(?:twitter|x)\.com/"
                    + re.escape(handle) + r"/status"
                )
                if (re.search(handle_url_re, sent, re.IGNORECASE)
                    or re.search(r"@" + re.escape(handle) + r"\b", sent, re.IGNORECASE)
                    or re.search(r"\b" + re.escape(handle) + r"\b", sent, re.IGNORECASE)):
                    drop = True
                    break
            if drop:
                secondary_drops += 1
            else:
                kept_sentences.append(sent)
        theme["summary"] = " ".join(kept_sentences).strip()

    if secondary_drops:
        logger.warning(
            f"Per-author cap (secondary): dropped {secondary_drops} duplicate "
            f"sentences from themes (handle already cited in earlier theme)"
        )

    # If a theme's prose was completely emptied by dedup, drop the whole theme
    before_drop = len(briefing["conversation_themes"])
    briefing["conversation_themes"] = [
        t for t in briefing["conversation_themes"]
        if (t.get("summary") or "").strip()
    ]
    if before_drop != len(briefing["conversation_themes"]):
        logger.warning(
            f"Dropped {before_drop - len(briefing['conversation_themes'])} themes "
            f"left empty after secondary-sentence dedup"
        )

    return briefing


def _dedup_cross_theme_citations(briefing: dict) -> dict:
    """Pass B only — drop sentences that re-cite a handle/URL already cited in
    an earlier theme. Does NOT drop whole themes (that's Pass A, which was
    too aggressive — disabled per 2026-05-11 user feedback). This catches the
    real failure mode: the same fact (same tweet URL) showing up verbatim in
    two themes' prose. Example caught 2026-05-13:
    Theme 4: "Earlier this week, @nickgerli1 noted that existing home sales
              over the first four months of 2026 were the lowest since 2009"
    Theme 5: "Yesterday, @nickgerli1 flagged that early 2026 existing-home
              sales were the weakest since 2009"
    Both link to the same status URL — Pass B keeps the first, strips the second.
    """
    themes = briefing.get("conversation_themes", []) or []
    if not themes:
        return briefing

    # Build handle → first-mention-theme-index
    handle_first_theme: dict[str, int] = {}
    handle_anchor_theme: dict[str, int] = {}
    for i, theme in enumerate(themes):
        s = theme.get("summary", "") or ""
        anchor_match = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/status", s, re.IGNORECASE)
        anchor_handle = anchor_match.group(1).lower() if anchor_match else None
        if anchor_handle and anchor_handle not in handle_anchor_theme:
            handle_anchor_theme[anchor_handle] = i
        for m in re.finditer(r"(?:twitter|x)\.com/([A-Za-z0-9_]+)/status", s, re.IGNORECASE):
            h = m.group(1).lower()
            if h not in handle_first_theme:
                handle_first_theme[h] = i
    for h, idx in handle_anchor_theme.items():
        handle_first_theme[h] = idx

    def _split_sentences_link_safe(text: str) -> list[str]:
        link_re = re.compile(r"\[[^\]]+\]\([^)]+\)")
        placeholders: list[str] = []
        def _mask(m):
            placeholders.append(m.group(0))
            return f"\x00LINK{len(placeholders)-1}\x00"
        masked = link_re.sub(_mask, text)
        parts = re.split(r"(?<=[.!?])\s+", masked)
        out = []
        for p in parts:
            for j, link in enumerate(placeholders):
                p = p.replace(f"\x00LINK{j}\x00", link)
            out.append(p)
        return out

    secondary_drops = 0
    for i, theme in enumerate(themes):
        s = theme.get("summary", "") or ""
        sentences = _split_sentences_link_safe(s)
        kept_sentences: list[str] = []
        for sent in sentences:
            drop = False
            for handle, first_idx in handle_first_theme.items():
                if i == first_idx:
                    continue
                handle_url_re = (
                    r"https?://(?:www\.)?(?:twitter|x)\.com/"
                    + re.escape(handle) + r"/status"
                )
                if (re.search(handle_url_re, sent, re.IGNORECASE)
                    or re.search(r"@" + re.escape(handle) + r"\b", sent, re.IGNORECASE)):
                    drop = True
                    break
            if drop:
                secondary_drops += 1
            else:
                kept_sentences.append(sent)
        theme["summary"] = " ".join(kept_sentences).strip()

    if secondary_drops:
        logger.warning(
            f"Cross-theme dedup: dropped {secondary_drops} sentences that re-cited "
            f"a handle already mentioned in an earlier theme"
        )

    # If a theme's prose was completely emptied by dedup, drop the whole theme
    before_drop = len(briefing["conversation_themes"])
    briefing["conversation_themes"] = [
        t for t in briefing["conversation_themes"]
        if (t.get("summary") or "").strip()
    ]
    if before_drop != len(briefing["conversation_themes"]):
        logger.warning(
            f"Dropped {before_drop - len(briefing['conversation_themes'])} themes "
            f"left empty after cross-theme dedup"
        )

    return briefing


def _strip_refusal_meta(briefing: dict) -> dict:
    """Replace any refusal-style meta-narration with a clean title-based fallback.

    Sonnet sometimes hallucinates "I don't have access to the full email content
    (the snippet is cut off)" when a newsletter body is teaser-only. The reader
    sees this as broken output. Detect those phrases and fall back to a single
    neutral sentence drawn from the title.
    """
    def _clean(text: str, title: str = "") -> str:
        if not text or not isinstance(text, str):
            return text
        if not _REFUSAL_PATTERNS.search(text):
            return text
        if title:
            t = title.strip().rstrip(".!?")
            cleaned = f"{t}."
        else:
            cleaned = ""
        logger.warning(f"Stripped refusal meta-narration; fallback: {cleaned[:80]!r}")
        return cleaned

    for take in briefing.get("substacker_takes", []) or []:
        take["take"] = _clean(take.get("take", ""), take.get("title", ""))
    for theme in briefing.get("conversation_themes", []) or []:
        theme["summary"] = _clean(theme.get("summary", ""), theme.get("theme", ""))
    if briefing.get("ai_brief"):
        briefing["ai_brief"] = _clean(briefing["ai_brief"], "")
    if briefing.get("conversation_pulse"):
        briefing["conversation_pulse"] = _clean(briefing["conversation_pulse"], "")
    for entry in briefing.get("twitter_roundup", []) or []:
        entry["summary"] = _clean(entry.get("summary", ""), "")
    return briefing


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
        # If Sonnet dropped the URL (or it was empty), look up the original item
        # by title and inject its URL. Covers the substack-redirect case where
        # Sonnet over-prunes — better to send the reader to a subscribe page
        # (better than nothing) than leave the entry unclickable.
        if not take.get("url"):
            t_title = (take.get("title") or "").strip()
            if t_title:
                try:
                    row = conn.execute(
                        "SELECT url FROM items WHERE source IN ('substack','rss','gmail') "
                        "AND substr(title,1,80) = ? "
                        "AND collected_at > datetime('now', '-5 day') "
                        "ORDER BY length(body) DESC LIMIT 1",
                        (t_title[:80],),
                    ).fetchone()
                    if row and row["url"]:
                        raw = row["url"]
                        # Substack delivers many feeds as redirect URLs whose
                        # decoded destination is a /subscribe page. Decode the
                        # b64 payload, strip /subscribe and query params, leaves
                        # the publisher's homepage (where the article lives).
                        cleaned = _normalize_substack_redirect(raw)
                        take["url"] = cleaned
                        logger.info(f"  substacker_takes: backfilled URL for '{t_title[:50]}' → {cleaned[:80]}")
                except Exception as e:
                    logger.warning(f"  substacker_takes URL backfill failed: {e}")
    # twitter_roundup URLs are now inline markdown links in the summary field
    # — no top-level URL to validate

    briefing["_url_audit"] = audit
    total = audit["verified"] + audit["corrected"] + audit["stripped"]
    logger.info(f"URL validation: {audit['verified']} verified, {audit['corrected']} corrected, {audit['stripped']} stripped (of {total} total)")
    return briefing


# ── Synthesis prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the conversation intelligence system for "Home Economics," a data journalism newsletter about the US housing market and economy by Aziz Sunderji.

Your job: surface the day's most substantive and interesting content for a daily US housing-economics brief. Primary topic: US housing — prices, inventory, mortgages, affordability, zoning/policy, construction supply, urbanism, real estate. Adjacent topics that earn coverage: demographics, internal migration, household formation, mortgage and credit markets, labor markets and regional economies WHERE they affect housing. AI items go in the dedicated `ai_brief` section, NOT in conversation_themes (unless they explicitly tie to housing, like AI data centers driving local development or tech-worker geography). Macro/Fed/markets: only as themes IF the housing tie is direct ("Fed pause keeps 30-yr mortgage at 7%" yes; "Fed holds rates" no).

CRITICAL: Quality over volume. A substantive thread with 4 thoughtful replies is FAR more valuable than anonymous comments saying "economy is rigged." Prioritize substantive discussions over populist venting. Do NOT preferentially feature any specific Twitter or Bluesky account — every voice in the input competes on merit. What matters is what was said, not who said it.

All sources you receive are valid — Twitter, Bluesky, HN, Substack newsletters, newspaper articles, RSS feeds, institutional research, academic journals. Treat them equally on merit; substance is the only currency. Don't apply different bars to different platforms.

## Output Format

Return a JSON object:

{
  "date": "YYYY-MM-DD",

  "conversation_pulse": "4-8 sentences (longer is fine when it adds substance — don't compress for brevity): what is the dominant debate right now and where does opinion split? Be concrete and factual — name the data points, name the people. NO filler phrases like 'the mood is cautious' or 'markets are watching closely'. State what happened and who disagrees about what.",

  "conversation_themes": [
    {
      "theme": "Short label (5-8 words max)",
      "summary": "Factual summary with inline markdown links. When you mention a specific tweet, paper, article, or Substack post, link the relevant phrase using [text](url). Example: '[one economist argues](url) that housing starts will rebound, while an [NBER working paper](url) finds national labs generate regional development through knowledge spillovers.' Lead with specific claims, data, or arguments. Name authors when relevant but do NOT preferentially cite the same handful of accounts across themes — spread attribution across the full set of voices in the input. No meta-commentary, no filler.",
      "platforms": [
        {"name": "twitter", "reply_count": 89, "sentiment": "mixed", "url": "..."},
        {"name": "bluesky", "reply_count": 12, "sentiment": "bullish", "url": "..."},
        {"name": "WSJ", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "Bloomberg", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "FT", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "NYT", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "Reuters", "reply_count": 0, "sentiment": "neutral", "url": "..."}
      ],
      "heat_level": "low|medium|high|viral",
      "related_news_trigger": "What news event sparked this conversation, if any. Empty string if organic.",
      "topics": ["topic_key1", "topic_key2"]
    }
  ],

  "twitter_roundup": [
    {
      "author": "@handle",
      "summary": "1-2 sentences (max 50 words). One inline link per distinct tweet, but LINK ANCHOR MUST BE 1-2 WORDS — typically the verb of attribution ('argued', 'pitched', 'noted', 'criticized', 'flagged') or a tight noun phrase. The rest of the sentence stays in plain prose. The reader should see mostly black text with short blue link words sprinkled in. What MUST NOT happen: a whole descriptive phrase or sentence wrapped in the link. No t.co URLs. Examples: single — '[Argued](url) rent growth is bottoming out as 2024 leases reset.'  multi — '[Pitched](url1) courtyard block designs enabling 40–60 units per PNW lot, and [argued](url2) the future of cities is mixed-use clusters rather than hub-and-spoke.'  BAD: '[Pitched courtyard block designs enabling 40-60 units per PNW lot](url1)' (whole phrase wrapped → reads as a wall of blue)."
    }
  ],

  "substacker_takes": [
    {
      "author": "Name (Publication)",
      "title": "Article title",
      "take": "Their specific ARGUMENT — not just the topic. What position are they staking out? e.g., 'argues that supply constraints in Austin are structural, not cyclical, and prices will rebound within 18 months.'",
      "url": "..."
    }
  ],

  "ai_brief": "ONE coherent 4-6 sentence paragraph summarizing today's most interesting AI-related developments across Twitter, newsletters, and substacks. Pull from ALL AI sources: the Twitter accounts in AI_ROUNDUP_ACCOUNTS (@trq212, @claudeai, @felixrieseberg, @bcherny, @emollick, @CaseyNewton, @kevinroose), AI substacks (Understanding AI, One Useful Thing, Stratechery, Zvi, Simon Willison, SemiAnalysis, Dwarkesh, Import AI/Jack Clark, Platformer), and AI newsletter emails (Superhuman, The Neuron, FT's AI Shift by John Burn-Murdoch). Use inline markdown links [text](url) for EVERY claim — link the specific phrase to the original source. Lead with the most substantive development (model release, research finding, industry shift), then cover secondary items. Write it as flowing prose, not bullet points. Example: '[Anthropic released](url) their new Mythos model with unprecedented cybersecurity capabilities, prompting [Zvi to argue](url) it represents a meaningful shift toward autonomous systems. Meanwhile, [Ethan Mollick noted](url) that GPT-5 reasoning traces are getting more sophisticated...'",

  "stats_summary": {
    "total_items_analyzed": N,
    "conversation_items": N,
    "platforms_active": N,
    "source_breakdown": {"Hacker News": N, "Twitter": N, "Bluesky": N, "Substack": N, ...}
  }
}

## Rules

1. TOPIC PRIORITIES (driven by user-defined weights — see pulse/data/topic_weights.json):

   The user has assigned priority weights (0-100) to ~23 topics. The classifier has already used these weights to score each item. Items with the highest weights should drive the majority of the briefing.

   TOP PRIORITY (weight 90+) — feature these prominently:
   - homeownership_demographics (100): generational gaps, first-time buyers, household formation, family dynamics
   - cities_urbanism (100): city policy, transit, walkability, urban form
   - demographics_general (100): birth rates, fertility, internal migration, population trends
   - immigration_housing (95): immigration effects on housing demand and labor
   - ai_and_housing (95): tech worker geography, AI-driven housing demand
   - happiness_wellbeing (95): happiness research, social outcomes, anglosphere unhappiness
   - cultural_lifestyle (95): time use, lifestyle commentary, dual-career families
   - politics_housing (95): housing politics, voter dynamics
   - housing_geography (90): metro-by-metro analysis, suburbs vs cities, regional patterns
   - affordability (90): price-to-income, rent burden, buy vs rent math
   - housing_policy (90): zoning, YIMBY, rent control
   - ai_general (90): AI tools, model releases, AI industry
   - tech_geography (90): where tech jobs are, tech hiring/firing by metro

   SECONDARY (weight 70-89) — include regularly:
   - housing_prices (80), housing_inventory (80), mortgage_rates (80)
   - construction_supply (75), international (75)

   OCCASIONAL (weight 50-69) — include if interesting:
   - climate_insurance (65), commercial_real_estate (60)

   LOW PRIORITY (weight 40-49) — include sparingly, only if exceptional:
   - pure_fed_macro (40): Jobs reports, CPI/PCE, Fed policy — only if explicitly tied to housing
   - markets_finance (40): Stock markets, bonds — only if a real housing story
   - tech_general (40): Generic tech news

   How to use these weights:
   - Lead with topics weighted 95+
   - When in doubt, prefer the higher-weighted topic
   - Items already classified with relevance scores reflecting these weights — items above 70 should predominate
   - For pure_fed_macro / markets_finance items: only include if the framing is explicitly about HOUSING implications. Generic Fed speeches, jobs reports, and inflation prints should be EXCLUDED unless the item ties them to housing market dynamics.

2. QUOTE REAL PEOPLE BY NAME. "Claudia Sahm argues the labor market is weakening faster than the Fed acknowledges" is useful. "Users are panicking" is not. Focus on substantive discussions, not populist venting.

3. NEWS ARTICLES AS THEME ANCHORS. Newspaper articles, RSS feeds, and journalism can anchor themes independently — a major NYT investigation or WSJ exclusive does NOT need to be generating Twitter/Bluesky chatter to appear as a theme. Include it if the story is substantive and interesting on its own merits. That said, if the same story is also sparking economist debate on social media, fold both together as one theme.

4. REAL URLS ONLY. Every source must include the actual URL from the collected items. Never fabricate URLs.

4b. CITATION ANCHOR MUST MATCH URL. When you write "[ENTITY did/said/projects/argues X](url)", the URL must point to that entity's own post, paper, or tweet — not to a secondary source that happens to mention the entity. If you only have the secondary source (e.g., you read about Urban Institute's research in a Slow Boring essay), either:
  (a) attribute it honestly: "[per Slow Boring](secondary-url), the Urban Institute projects X" so the link's target matches what the link says, OR
  (b) drop the attribution entirely: "rental restrictions would disrupt 72,000 units/year" with no byline
What you MUST NOT do: "[The Urban Institute projects X](slow-boring-url)" — that makes the reader think they're clicking through to Urban Institute when they're really going to Slow Boring. This is a form of misattribution. Every anchor text + URL pairing must be internally consistent.

4c. ATTRIBUTED QUOTES MUST BE HYPERLINKED — AND THE LINK MUST POINT TO WHERE THE CLAIM ORIGINATED. Any time you attribute a direct quote, statement, or specific data point to a named person or organization, the attribution MUST be wrapped in a hyperlink. Plain-text attribution ("Lawrence Yun said affordability has improved") with no link is unacceptable — the reader has no way to verify or read more.

WHICH URL TO LINK depends on where the claim originated:
  - **Twitter/Bluesky tweet** → link the tweet's URL. The tweet IS the source. Do NOT link to an article that summarizes the tweet; link directly to the tweet itself.
  - **Article quote** (person quoted IN an article) → link the article. The article IS the source — the person didn't publish their statement themselves; they spoke to the reporter.
  - **Substack / newsletter / column** → link the post itself.
  - **Working paper / report** → link the paper's own URL (PDF, abstract page, or publisher landing).

Examples by source type:
  (a) Tweet attribution: "[@nickgerli1 noted](https://x.com/nickgerli1/status/...) that existing-home sales were the lowest YTD pace since 2009" — link the TWEET.
  (b) Article quote: "[NAR chief economist Lawrence Yun told HousingWire](https://housingwire.com/...) that affordability has improved" — link the ARTICLE.
  (c) Anchor on the verb: "Yun [said](https://housingwire.com/...) affordability has improved" — link the article via the verb.
  (d) Anchor on the publication: "[per HousingWire](https://housingwire.com/...), Yun said affordability has improved" — link the article via the publication.

@HANDLE ATTRIBUTIONS ALWAYS NEED A LINK. The pattern "per @handle" / "@handle noted" / "@handle argued" / "@handle flagged" written as plain text (no markdown link wrapping any part of it) is the most common 4c violation. If you reference a Twitter or Bluesky handle by their data point or argument, the tweet URL MUST be linked — typically the cleanest form is to wrap the verb: "@nickgerli1 [noted](tweet_url) that...". If you don't have the tweet URL in your corpus, drop the attribution entirely and state the fact directly without naming the handle.

LINK THE EXAMINING SOURCE, NOT THE UNDERLYING SOURCE IT DRAWS ON. When you write a construction like "X's newsletter examined Y," "X's article covered Y," "X analyzed Y," "X's piece argued Y," the link MUST point to X's piece — the SUBJECT of the sentence — not to Y or to whatever underlying material X drew on. The reader expects to click through to X's analysis, not to the raw source X used.

Failure example (2026-05-19 brief): "Brandon Donnelly's newsletter examined [whether home prices and social media are the reasons we're having even fewer babies](FT-url), drawing on the FT's coverage." — The anchor points to the FT, but the sentence is about Donnelly's newsletter. Reader clicks expecting Donnelly's piece and lands at the FT instead.

Required fix: "Brandon Donnelly's newsletter [examined](DONNELLY-newsletter-url) whether home prices and social media are the reasons we're having even fewer babies, drawing on [the FT's coverage](FT-url)." — Two links: one to Donnelly (the subject doing the examining), one to the FT (the underlying source). If you only have ONE URL available, it MUST be the URL of the entity that's the subject of the sentence. Drop the secondary reference if you can't link it; never substitute the secondary URL for the primary.

This rule generalizes to any "X did/examined/argued Y" construction where X is named and X's content is the subject. Link X's content. The exception is when the sentence frames it as "according to/per [secondary source], X said Y" — in that case the link is on the secondary (because that's where the reader will read X's quoted statement).

What you MUST NOT do: write "Yun said X" or "@nickgerli1 noted X" as plain text. If you cited HousingWire two sentences earlier, that link does NOT cover a later separate Yun attribution — each attributed claim needs its own link. This rule applies to direct quotes, paraphrased positions, and specific data points (e.g., "Goldman tracked Q2 at 1.6%" — link Goldman; "the median price hit $417,700" — only an unattributed fact like this can stand without a link).

VAGUE REFERENCES STILL REQUIRE LINKS. Any reference to a specific paper, report, study, dataset, or analysis MUST be hyperlinked — even when you do NOT name it explicitly. Phrasings like "an NBER working paper examined X," "a Goldman note argued Y," "Census data showed Z," "research from the Urban Institute found W" all count: the reader has no other handle to find the source if you don't link it. The link is MORE important when the reference is vague, not less. Failure example: "Yesterday, an NBER working paper flagged in the feed examined gender gaps" — with no link, the reader has no way to find which paper. Correct: "Yesterday, [an NBER working paper](url) flagged in the feed examined gender gaps." If the source isn't in your corpus, don't reference it at all — drop the attribution and make the claim directly: "Gender gaps in education are widening" rather than "[unsourced] research showed gender gaps are widening."

4d. PRESERVE TECHNICAL PRECISION WHEN PARAPHRASING. When a source uses a specific technical term — especially in housing/economics where similar-sounding terms describe different things — KEEP THAT EXACT TERM. Do not generalize, smooth, or simplify it. The reader is sophisticated and the distinctions matter.

Housing pipeline (most common trap):
- "permits issued" / "units authorized" ≠ "construction"
- "units authorized but not started" ≠ "rising construction" — it's the OPPOSITE (permits piling up because builders aren't breaking ground)
- "housing starts" ≠ "completions" ≠ "under construction"
- "new home sales" ≠ "existing home sales"
Other common conflations to avoid:
- "rent growth" ≠ "asking rent" ≠ "effective rent" ≠ "rent prices"
- "median price" ≠ "median sale price" ≠ "median list price"
- "mortgage rate" ≠ "mortgage application volume" ≠ "mortgage origination"
- "construction spending" ≠ "construction starts"
- "vacancy rate" ≠ "rental vacancy rate" ≠ "homeowner vacancy rate"

When summarizing a chart, quote the chart's exact label rather than describing it loosely. Example failure: a tweet shows a chart titled "Units Authorized but Not Started," and the writeup says "construction has risen" — that is wrong (the chart shows the GAP between authorized and started, which means construction did NOT keep pace with permits). Correct version: "Units authorized but not started has climbed to record levels — permits piling up faster than builders are breaking ground."

If you're paraphrasing data and uncertain whether you're preserving the technical distinction, quote the original exactly with quotation marks.

Social-media actions also need precise verbs. The platform action determines what you can claim happened. Don't upgrade a public reply into something it isn't.
- "replied to" / "quote-tweeted" / "responded to" / "asked in reply to" / "tagged in a thread" — these describe specific public actions
- "told directly" / "spoke with" / "DM'd" / "messaged" — these imply private/direct communication we virtually never have evidence of from public scrapes
A public reply on X is NOT "telling someone directly." Failure example: "@nickgerli1 told Rep. @PramilaJayapal directly that Seattle housing supply is exploding" — wrong, that was a public reply. Correct: "@nickgerli1 replied to Rep. @PramilaJayapal that Seattle housing supply is exploding." If you're unsure from the tweet's metadata, use the neutral verb "wrote."

4e. NAMED PUBLICATIONS REQUIRE PROVENANCE PRESERVATION. When you reference a NAMED PUBLICATION by its title — institutional reports, monthly monitors, press releases, working papers, etc. (e.g., "ICE Mortgage Monitor," "NAR Existing-Home Sales Report," "Fed Beige Book," "Goldman US Daily," "FHFA HPI release," "BLS CPI release", "CBO budget outlook") — the URL MUST be on the publisher's own domain. If you only have a secondary source covering it, your prose MUST do BOTH:
  (a) preserve the original publisher's attribution (the data came from them, not the secondary), AND
  (b) make the secondary source's role visible (summary / analysis / report on).

Acceptable forms:
  • "[CalculatedRisk's summary of the May ICE Mortgage Monitor](secondary-url) showed annual home price growth of 0.9% in April"
  • "The May ICE Mortgage Monitor showed 0.9% annual home price growth, [per CalculatedRisk's analysis](secondary-url)"
  • "CalculatedRisk [summarized](secondary-url) the May ICE Mortgage Monitor, which reported 0.9% annual home price growth"
  • If you can't honor both, DROP the link: "The May ICE Mortgage Monitor showed 0.9% annual home price growth."

NEVER do any of these:
  • "[ICE May Mortgage Monitor](secondary-url)" — link domain mismatches the named entity; reader feels deceived clicking through
  • "[CalculatedRisk reports home price growth was 0.9%](secondary-url)" — attributes the DATA to CalculatedRisk when CR is summarizing ICE; this is data-source misattribution
  • "[CalculatedRisk](secondary-url) reports home price growth was 0.9%" — same data-source misattribution

The fix is ALWAYS: name BOTH the original publisher AND the secondary you actually link to. Two names, one link, both roles visible.

5. SUBSTACKER TAKES COME FROM THE PROVIDED NEWSLETTER/COLUMNIST SECTION. The substacker_takes section is EXCLUSIVELY for items from the "Newsletters" section above (which now includes Substack newsletters, Gmail newsletters, AND single-author RSS columnists like Jonathan Levin or Sarah O'Connor). Do NOT include Twitter commentators or generic news headlines. Use the URL provided with each item (even if it's a redirect link). For each take, summarize their specific ARGUMENT — not just the topic. "Erdmann argues builders are underbuilding relative to population growth" is good. "Erdmann wrote about housing supply" is not. IMPORTANT: Include a take for EVERY newsletter/columnist item provided. Do not cherry-pick — summarize all of them.

5b. NEVER NARRATE INSUFFICIENT CONTENT. If a newsletter's preview is short or teaser-only, infer the take from the title and any partial body you have, then write a confident one-sentence summary. NEVER write phrases like "I don't have access to the full content", "the snippet is cut off", "based on the limited preview", "I cannot offer specifics", or "partial summary". The reader will see this as broken output. If you genuinely can't infer anything beyond the title, write a single neutral sentence based on the title alone (e.g., "Argues that relationship-building beats AI tools and clever subject lines as the most underrated PR skill for real estate reporters.") — no meta-commentary. This rule applies to substacker_takes, ai_brief, twitter_roundup, and any other section.

6. THEMES: 8-14 themes, with a STRONG PREFERENCE for fewer, richer themes over more, thinner ones. Quality over count. These are the most substantive stories of the day. This section ABSORBS what used to be a separate "Headlines" section — so it must comprehensively cover today's news (especially housing/real-estate) AND today's social conversation. Each theme can be:
   - A news story with multiple outlets covering it (weave the actual reporting from the article BODIES, not just headlines, with inline source links to each outlet)
   - A cross-platform debate (multiple voices arguing about something)
   - A data release or research finding
   - A combination of the above
Coverage rules:
   - Real estate / housing / urbanism: cover EVERY substantive story — don't skip housing stories just to make room for other topics
   - **At least 70% of themes must be housing/urbanism/demographics/affordability-tagged.** If your themes drift toward AI, macro, or generic tech as you approach the count target, STOP — better to have 8 housing-focused themes than 14 with 8 housing + 6 off-topic. The reader signed up for housing economics, not generic news.
   - AI-only items (model releases, AI safety, AI politics that don't tie to housing/labor/geography) belong in `ai_brief`, NOT in conversation_themes. The ai_brief section is the dedicated outlet for those.
   - Macro/international items (Fed, oil prices, Canadian jobs, etc.) only belong as themes IF they explicitly tie to housing impact. A Canadian unemployment number is not a theme; "Canadian unemployment hits 6.9%, putting downward pressure on Toronto housing demand" is. Same for the Fed: "Fed holds rates higher" is not a theme; "Fed pause keeps 30-year mortgage rates near 7%" is.
   - **Tech_general items (3D printers, software lawsuits, generic tech news) NEVER anchor themes.** The classifier may assign tech_general topic to a high-engagement HN thread; ignore the engagement and skip these. They don't belong in this briefing at all unless they have explicit housing or AI-and-housing relevance.
   - Other beats: include the most substantive 2-4 stories if they pass the bar (high-quality demographics, urbanism, geography). Politics only if directly housing-related.
   - When multiple outlets cover the same news event, ONE theme covers them all with EVERY substantive source linked inline. Do NOT cap at 2-3 sources — a widely-covered story may warrant 5-8 inline citations. Example: "[WSJ](url) and [FT](url) report X, while [Bloomberg](url) emphasizes Y; [The Economist](url) frames it as Z, [Reuters](url) adds specific data, [Slow Boring](url) argues against the consensus, and [Conor Sen on Twitter](url) calls it overblown." If 6+ outlets covered the story substantively, cite all 6+. Stop only when sources start repeating the same angle without adding anything.
   - Use the FULL article body when present in the input (enriched articles have substantial body text — quote specifics, not just topics)
   - **Weave historical context with explicit time stamps.** When a topic touches something already discussed this week, cite the relevant historical voice from the "Past 6 Days" section with a date stamp: "Tuesday, [Brad Setser argued](url)..." or "earlier this week [Conor Sen warned](url)...". Never use a historical item without a date marker — the reader needs to instantly tell what's fresh vs context. Today's items don't need a date stamp (they're implicitly today).
   - **CRITICAL: historical context must match the theme's specific topic, geography, and country.** Don't weld an Australian migration statistic into a Canadian unemployment theme, or a NYC rent freeze argument into a San Francisco housing theme, just because both have "international" or "housing_policy" tags. Before citing a historical voice, verify: (a) same country/metro, (b) same specific topic (rent control ≠ inclusionary zoning ≠ permitting reform), (c) same direction of argument. If a historical item is about a different country or a tangentially-related topic, leave it out — better to have no historical citation than a misleading one. Recent failure: Sonnet wrote "Canadian unemployment...Thursday, @AvidCommentator noted that Treasury forecasts missed the migration surge: 'At the 2022 Federal Budget...'" — but @AvidCommentator was discussing AUSTRALIAN migration numbers, not Canadian. The cite was geographically wrong.
Label each theme's anchor platforms accurately: use "rss" or "substack" or the newspaper name when that's the anchor, "twitter" or "bluesky" when those anchor it.

**Better to have 8 substantive housing themes than 14 themes diluted with off-topic content.** Don't pad to hit the count target. If today's news truly lacks 12+ housing stories, accept fewer themes and let ai_brief cover AI items.

**CONSOLIDATE NEAR-DUPLICATE THEMES.** Before finalizing the theme list, ask: "are any two themes telling the same story from different vendor angles?" If two themes both anchor on (a) the same data period (same month / same release window) AND (b) the same housing-market dynamic (sales + prices, supply + demand, rents + vacancy, mortgage rates + affordability, etc.), MERGE them into one theme that holds the tension inside. Cite all sources inline; don't split because the data came from different vendors. Example: an April existing-home-sales theme (NAR / HousingWire / CNBC) and an April home-price-growth theme (ICE Mortgage Monitor / CalculatedRisk) are ONE story — "April: sales soft, prices firm" — not two. The reader thinks in market dynamics, not data-vendor categories. Splitting them makes the brief feel like the same story got coverage twice. When the data period or the dynamic differ meaningfully (e.g., April sales but March CPI shelter; national sales but a Bay Area-specific price piece), keep them separate.

**SPLIT WELDED-UNRELATED THEMES.** Complement to the consolidation rule above. After drafting, scan each theme: if it has two paragraphs (or two sentence-clusters) describing DIFFERENT mechanisms or DIFFERENT markets, and the only thing linking them is a shared region label, a shared word in the headline, or a contrast frame, they are TWO THEMES, not one. SPLIT them. The diagnostic test: if the transition between the two sub-clusters requires a disjunctive frame ("On a separate track…", "The picture looks different in…", "Meanwhile in [region]…", "By contrast in [market]…", "On the other side of the country…"), that's the model reaching for a bridge between two stories that don't actually belong together. Failure example: combining a "Texas exurb growth driven by permitting regime" story and a "Florida home prices correcting after pandemic overshoot" story under one theme — these are different mechanisms (supply elasticity vs. demand withdrawal), different markets (Texas vs. Florida), and the only link is "both Sunbelt." Correct: two separate themes. Theme paragraphs split with `\\n\\n` are for genuine sub-clusters of ONE story (e.g., permits → starts → completions in the same release window, or rents → income → freeze proposal in the same market), not for two stories sharing a region tag. When in doubt, split. The reader prefers two crisp themes over one welded conglomerate.

**ONE QUESTION / ONE MECHANISM PER THEME.** Finer-grain partner to the split rule. Before writing each theme, name the SINGLE question it answers or the SINGLE mechanism it describes (e.g., "Why is Texas the fastest-growing state?" → permitting regime + MUDs; "Why are exurbs winning within metros?" → remote work + cost of space). Every claim in the theme must bear on that specific question/mechanism. Even if two questions share the same data point or the same lead-in observation, if they have different MECHANISMS as answers, they belong in separate themes. Failure example: a "Texas exurbs are #1" theme that mixes (i) the permitting-regime explanation for why Texas dominates state rankings and (ii) the remote-work explanation for why exurbs are absorbing metro growth nationwide. Both answers technically point to Celina being #1 in the data, but they're answering two different questions through two different mechanisms — so they are two themes, not one. The reader's mental model is "one theme = one mechanism = one cause being explained." When you find yourself oscillating between two threads inside one paragraph, that's the model trying to weld two answers under one umbrella because the data overlaps; resist that and split. This rule strengthens 6b in a finer grain — 6b catches two-paragraph welds across markets; this rule catches within-paragraph welds across mechanisms.

**NO CAP ON PLATFORMS PER THEME.** The platforms[] field is not limited to 2-4 entries. If 12 outlets covered a story (WSJ, NYT, FT, Bloomberg, Reuters, Slow Boring, Brick Underground, The City, Gothamist, Politico, Twitter threads, etc.), list ALL 12. The 7-entry example in the JSON schema is illustrative, not a ceiling. Same applies to inline source citations in the summary text — cite every outlet that added a distinct angle.

**CLARITY OVER BREVITY.** Themes can run a bit longer when the story warrants it. Sonnet tends to compress when more detail would actually help the reader understand the nuance. Don't sacrifice an important data point, a quoted argument, or context about what's at stake just to keep a theme short. A theme summary that runs 4-6 sentences with specific data and substantive analysis is better than a tight 2-sentence skim that loses the substance. The reader is reading a paid daily housing brief — they want depth, not headlines.

7. SINGLE TWEETS DO NOT MAKE SOCIAL THEMES. A lone tweet asking a question, making an observation, or endorsing someone else's argument is NOT a theme on its own — put it in twitter_roundup instead. (This rule applies to social-anchored themes only. News-anchored themes don't need cross-platform debate; a single substantial article is enough to anchor a theme.) For a Twitter or Bluesky thread to anchor a theme, you need at least one of: (a) multiple accounts engaging with the same question, (b) the tweet is responding to or commenting on a concrete news story or data release, or (c) the tweet itself has substantial replies/engagement.

8. ONE TOPIC PER THEME. Do NOT group unrelated threads or voices into one theme just to reduce count. If Winton ARK is talking about AI and photography employment, and Arindube is making a separate argument about AI asset valuations, those are TWO separate themes — not one. Only group threads together when they are genuinely part of the SAME conversation (people replying to each other, referencing each other's points). Three separate people talking about three separate things on the same broad topic is NOT one theme.

9. HEAT LEVELS: "viral" = 500+ comments across platforms, "high" = active debate with strong opinions, "medium" = noticeable discussion, "low" = a few mentions.

10. KEEP IT UNDER 30,000 CHARACTERS. The headlines section alone will be substantial — that's fine.

11. SKIP IRRELEVANT NOISE. Do not feature: Nigerian/international housing stories, memes about landlords, generic "economy is rigged" venting, partisan political rants with no economic substance.

11b. SPREAD ATTRIBUTION — soft guideline. Prefer diverse voices across themes; a single account anchoring 4+ themes feels like one person's feed, not the day's housing conversation. But it IS fine for the same account to appear in 2-3 themes when they made multiple substantively different arguments on different topics (e.g., @CSElmendorf making a separate case on SB 79 transit upzoning AND on SB 1383 labor mandates AND on the builder's remedy — three distinct policy stories, three legitimate anchorings). The risk to avoid: same account cited in 2+ themes for what's essentially the same point reworded. Use editorial judgment, not a hard cap.

12. TWITTER/BLUESKY ROUNDUP: A scannable bullet list of accounts (from EITHER Twitter or Bluesky) that had something notable but did NOT appear in conversation_themes. CRITICAL RULES:
    a. Do NOT include any voice you already covered in conversation_themes — this section is strictly the overflow.
    b. ONE entry per account. The "summary" field is 1-2 sentences (max 50 words). Multiple inline links are allowed when the account tweeted on multiple distinct topics — link each anchor phrase to the specific tweet it describes. If the account had a THREAD (multiple consecutive tweets — see thread markers below), summarize the whole thread's argument and link to the most central or earliest tweet.
    c. Aim for 15-25 accounts. Skip anyone with nothing notable — do not pad with low-signal tweets.
    d. Prioritize: contrarian views, data-backed claims, novel arguments. Topic focus matches the brief's broad housing-economics scope (housing, demographics, internal migration, household formation, mortgage/credit markets, labor markets and regional economies tied to housing, urbanism, AI when tied to housing).

12b. THREAD HANDLING (critical for roundup AND themes): The input groups consecutive same-author tweets together — items prefixed with "  ↪" are CONTINUATIONS of a thread anchored by the previous "  [conv]" item from the same author. Treat the whole thread as ONE coherent argument, NOT as separate items. Twitter threads are how substantive analysis happens — picking one fragment ("Take SB 79 for example", "/13") strips the context that makes the analysis make sense. So:
    - For roundup: write ONE entry per author summarizing the thread's overall argument (max 40 words, with one inline markdown link to the thread's anchor tweet).
    - For themes: when a thread anchors a theme, your summary should reflect the thread's full arc, not just one tweet's claim. Cite the anchor tweet's URL.
    - Never include the same author's thread spread across multiple themes or as multiple roundup entries — one author = one summary.

13. WRITING STYLE: Be direct and factual. NO AI slop. Avoid these patterns:
    - "People aren't arguing X; they're watching Y" — just state what they're arguing
    - "The conversation centers on whether..." — just state the disagreement
    - "Sentiment is cautious/mixed/nervous" — instead say WHO thinks WHAT
    - "The broader mood is..." — cut this entirely
    - Any sentence that could apply to any topic on any day is filler. Delete it.
    - Write like a wire service, not a podcast host. Facts and attributions only.

13b. LOGICAL HONESTY IN THEME PROSE — the single biggest failure mode in the current output is welding loosely-related datapoints into a paragraph whose transitions imply a causal/logical/temporal relationship the underlying facts don't support. The result reads smoothly but is cognitively painful because the reader's brain keeps tripping on links that aren't there. Three rules to prevent this:

    (a) PLAN THE LOGIC BEFORE WRITING. Inside each theme, before drafting the summary, mentally list the distinct claims you're about to make and (i) GROUP them by subtopic, (ii) label every pair of claims with one of: {*causes / drives*, *supports / is evidence for*, *contrasts with / is in tension with*, *independent — same topic, no direct link*}. The transitions you use in the final prose must honor those labels. If two claims are independent, the prose must say so plainly — do not invent a connective gesture that pretends they're related.

    (a2) CLUSTER ADJACENT CLAIMS BY SUBTOPIC — DO NOT INTERLEAVE. Within a paragraph, all claims about the same subtopic must sit adjacent before the prose moves to a different subtopic. The frequent failure mode is interleaving: e.g., Texas-model claim → contrast with California → ANOTHER Texas-model claim — which forces the reader to re-page into the Texas frame after just being pulled into the contrast. Correct ordering: cluster all the Texas-model claims first, then make the California contrast as the closing move. The "comparison" or "contrast" move in a paragraph almost always belongs LAST, after the primary subject is fully developed — not wedged in the middle. Same rule applies to "earlier-this-week" historical citations: don't interleave them with present-day claims; finish the present-day point, then add the historical context, then continue.

    (b) THEME SUMMARIES CAN BE TWO OR THREE SHORT PARAGRAPHS, NOT ONE WELDED PARAGRAPH. When a theme has two or three genuinely distinct sub-clusters of facts (e.g., permits data + price reactions + a separate policy debate), split them into separate paragraphs separated by a blank line (use `\\n\\n` in the JSON string). Still prose — no bullet points — but the paragraph break signals "new sub-point" cleanly and lets each paragraph be internally coherent. One welded paragraph that strings unrelated points together with fake transitions is the failure mode; 2–3 short topical paragraphs is the fix. Keep ONE paragraph when the points really are one continuous argument. Within each paragraph, rule (a2) still applies — cluster, don't interleave.

    (b2) PARAGRAPH-BREAK TRIGGERS — DEFAULT TO BREAKING, NOT WELDING. The bias should be PRO-paragraph-break, not anti. Insert `\\n\\n` (paragraph break) whenever ANY of these is true between two adjacent sentences:
        - Different MECHANISM (e.g., oil supply shock → bond markets → Fed expectations vs. property-tax/insurance trends → escrow shortfalls — these are different cause-effect chains even though both pressure household budgets)
        - Different DATA SOURCE or different INSTITUTION publishing the data (FT/JPMorgan/Bloomberg on bond markets vs. CNBC/Cotality/Selma Hepp on escrow)
        - Different ACTORS (Fed officials and bond traders vs. insurance industry and county assessors)
        - Different UNIT OF ANALYSIS (per-household annual fuel cost vs. per-month escrow shortfall vs. nominal mortgage rate)
        - Different GEOGRAPHY or SCALE (national gas prices vs. Florida-and-Colorado-specific escrow shocks)
        - Different LEVEL OF GOVERNMENT (city council policy vs. state legislature vs. federal bill — never weld these into one paragraph)
        - Different TIME WINDOW (last 30 days of mortgage data vs. 2026-projected escrow shortfall vs. since-2019 trend; today's news vs. "earlier this week" historical context)
        - The transition phrase you would otherwise write requires a hedging noun: "a related X," "a parallel Y," "a similar dynamic," "another dimension of the squeeze," "a connected story" — if you reach for one of these, the bridge isn't real; break instead.

    (b3) DISJUNCTIVE TRANSITIONS *ARE* PARAGRAPH BREAKS, NOT INLINE GESTURES. This is the single most common rule violation. If you reach for ANY of the following transition phrases, that phrase MUST appear at the START of a new paragraph (preceded by `\\n\\n`), NOT buried mid-paragraph. The act of writing the transition is itself the signal that you should have already inserted a paragraph break:
        - "Separately," / "On a separate track:" / "On a different track:" / "On a parallel track,"
        - "At the federal level," / "At the state level," / "At the city level," / "On the policy side,"
        - "Earlier this week," / "Yesterday," / "Last week," (any time-shift transition)
        - "On a different note," / "Switching to," / "Turning to,"
        - "Meanwhile," / "Elsewhere," (these are banned inline per (c) anyway, but if you find yourself needing them, the answer is `\\n\\n`)

    Failure pattern to AVOID (from 2026-05-19 brief on NYT Editorial / Housing theme): one welded paragraph that read "@conorsen.bsky.social's framing that the political moment has shifted to '2026: It's affordability, stupid.' Separately, NYC Council Member Lincoln Restler's newsletter announced Mayor Mamdani's executive budget includes an additional $5 billion for affordable housing… At the federal level, Rep. French Hill appeared on CNBC to discuss the bipartisan 21st Century ROAD to Housing Act… Earlier this week, Saturday, @BalanceCrafting flagged that the House version of ROAD expands…" — that paragraph contains THREE buried disjunctive transitions ("Separately," "At the federal level," "Earlier this week,") that should have been three paragraph breaks. Required fix: four short paragraphs, one per topic cluster (election politics / NYC city policy / federal legislation / historical context on the federal bill). Each transition starts a new paragraph; none appears mid-sentence.

    Concrete failure to AVOID (from 2026-05-18 brief): "the FT calculates the Iran war has cost consumers $41.5bn extra in fuel since late February — $316 per household, with gas at $4.51 nationally. CNBC details a related squeeze: about 65% of escrow accounts are projected to be short in 2026 because of jumps in property taxes and homeowners insurance, with the average shortfall at $2,157..." — these are two stories welded with "a related squeeze." The Iran-war-→-fuel chain (commodity / bond market / Fed narrative) and the property-tax-and-insurance-→-escrow chain (insurer pricing / county assessments / mortgage servicing) share NO mechanism. Different data, different actors, different time window. REQUIRED FIX: paragraph break before "CNBC" — and the new paragraph stands on its own without the "related" framing.

    (b4) PARAGRAPH LENGTH CEILING. Independent of the trigger rules above: NO theme summary should be ONE paragraph longer than ~7 sentences. If your draft has a paragraph that runs 8+ sentences, the failure mode is welding by length; find the natural break point (apply b2/b3 triggers) and split. Long welded paragraphs are unreadable in the email rendering — better to ship four 3-sentence paragraphs than one 12-sentence wall.

    (c) BANNED FAKE-CONNECTIVE PHRASES. These are the smooth-sounding transitions the model defaults to when it has nothing logical to bridge between two sentences. Do NOT use them:
        - "Meanwhile," / "Elsewhere," / "Separately, in a similar vein,"
        - "This echoes" / "This is consistent with" / "This mirrors"
        - "Building on this," / "On a related note,"
        - "The picture that emerges is..." / "Taken together..." / "All told,"
        - "There's a sense that..." / "It feels like..."
        - "Adding to the debate," / "Adding context,"
        - "[Source] details a related X" / "a related squeeze / pressure / dynamic / story / picture / piece"
        - "a parallel X" / "a similar dynamic" / "another dimension of [the same thing]"
        - "Compounding this," / "Stacking on top of this,"
        - "Another piece of the puzzle is..." / "Adding to the picture,"
        - "On a connected front," / "On a parallel track,"
    When two points are genuinely independent within a theme, use honest disjunctive signals instead: "Separately:" / "On a different track:" / "Unrelated but on the same beat:". When two points ARE connected, name the connection explicitly: "This is the supply-side mirror of..." / "Which helps explain why..." / "Cutting against this," / "The counter-argument from [X] is...". The reader should always be able to tell, from the transition alone, whether the next sentence is causally connected to the previous one or just adjacent to it. Reminder: "a related X" is ALWAYS a code-smell for welding — when in doubt, break to a new paragraph.

14. ALL SECTIONS ARE MANDATORY. Your JSON output MUST include ALL of these keys: conversation_themes, twitter_roundup, substacker_takes, ai_brief. If you omit any section, the briefing is broken. substacker_takes should include a take for EVERY Substack newsletter provided — summarize all of them, not just a few.

15. AI_BRIEF: Scan the input for ALL AI-related content — tweets from @trq212, @claudeai, @felixrieseberg, @bcherny, @emollick, @CaseyNewton, @kevinroose; substack posts from Understanding AI, One Useful Thing, Stratechery, Zvi, Simon Willison, SemiAnalysis, Dwarkesh, Import AI, Platformer; and emails from Superhuman, The Neuron, FT AI Shift. Synthesize into ONE coherent paragraph (4-6 sentences) with inline markdown links to each source. Do NOT duplicate content that's in conversation_themes — the ai_brief is for AI-specific items that wouldn't make it into a main theme.

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
    all_items = get_items_since(conn, hours=24, min_relevance=0)
    # All hand-curated sources (Twitter, Bluesky, RSS) use a low threshold —
    # these are hand-picked accounts/feeds so even off-topic items are worth seeing.
    # Google News and other bulk sources use a higher threshold.
    # hackernews items are community-curated by upvotes; treat them like any other
    # curated source with a low (10) relevance threshold rather than the bulk-source
    # 30-bar that was filtering most of HN out
    # Per-source floor map: HN bumped from 10 → 30 because viral non-housing
    # threads (3D printer lawsuits, generic tech) were anchoring themes despite
    # tech_general topic and weight=40 ("include sparingly only if exceptional").
    # HN isn't curated the way Twitter follows are; we scrape the front page.
    SOURCE_FLOOR = {
        "twitter": 10, "bluesky": 10, "rss": 10, "substack": 10, "gmail": 10,
        "hackernews": 30,
    }
    BULK_FLOOR = 30  # google_news and other bulk sources

    def _is_super_smart_item(i: dict) -> bool:
        tags = i.get("platform_tags", [])
        if isinstance(tags, str):
            try: tags = json.loads(tags)
            except Exception: tags = []
        return "super_smart" in (tags or [])

    # SuperSmart items bypass the relevance floor entirely — they're a curated
    # must-include list. Without this, low-relevance SuperSmart tweets (e.g.,
    # Nate Silver on NBA, Ezra Klein on tacos) were getting dropped at the
    # relevance threshold before reaching the Phase 0 reservation pass.
    relevant_items = [
        i for i in all_items
        if _is_super_smart_item(i)
        or (i.get("relevance_score") or 0) >= SOURCE_FLOOR.get(i.get("source"), BULK_FLOOR)
    ]
    convergence = compute_convergence(conn, hours=24)
    shifts = detect_narrative_shifts(conn)
    organic = detect_organic_conversations(conn, hours=24)
    stats = get_collection_stats(conn, hours=24)

    # Source breakdown with human-readable names
    source_display_counts = Counter()
    for item in all_items:
        source_display_counts[_get_source_display_name(item)] += 1

    # Conversation item counts
    conversation_items = [i for i in all_items if (i.get("conversation_signal") or 0) >= 30]

    # Get collection errors for transparency
    collection_errors = get_recent_collection_errors(conn, hours=24)

    # Substacker / columnist items: any source where an individual writer is
    # making an argument worth summarizing. Three sources qualify:
    #   1. source = substack (COMPETITOR_SUBSTACKS)
    #   2. source = gmail with sender in GMAIL_NEWSLETTER_SENDERS
    #   3. source = rss with feed_name shaped like a single-author column
    #      (e.g. "Jonathan Levin - Bloomberg Opinion Columnist") — but NOT
    #      academic journals or general news/aggregator feeds
    substacker_items = []
    seen_titles = set()
    JUNK_TITLE_PATTERNS = (
        "subscriber", "unsubscription", "payment receipt",
        "discussion thread", "open thread", "sunday thread",
        "saturday discussion", "chat thread", "mailbag",
    )

    def _is_columnist_feed(feed_name: str) -> bool:
        """Heuristic: feed name matches 'Author Name - Publication' pattern."""
        if " - " not in feed_name:
            return False
        name_part = feed_name.split(" - ")[0].strip()
        words = name_part.split()
        # Need at least first + last name, both capitalized
        if len(words) < 2:
            return False
        return all(w and w[0].isupper() for w in words[:3])

    for i in all_items:
        src = i.get("source")
        if src not in ("substack", "gmail", "rss"):
            continue
        author_lower = (i.get("author") or "").lower()
        if "aziz" in author_lower or "home-economics" in author_lower:
            continue
        title_lower = (i.get("title") or "").strip().lower()
        if any(p in title_lower for p in JUNK_TITLE_PATTERNS):
            continue

        # Source-specific qualification
        if src == "substack":
            pass  # always include (these are curated competitor substacks)
        elif src == "gmail":
            from config import GMAIL_NEWSLETTER_SENDERS
            sender = (i.get("author") or "").lower()
            if not any(p in sender for p in GMAIL_NEWSLETTER_SENDERS):
                continue
        elif src == "rss":
            if i.get("feed_priority") == "journal":
                continue  # academic papers aren't "takes"
            feed_name = (i.get("feed_name") or "").strip()
            if not _is_columnist_feed(feed_name):
                continue

        title_key = title_lower[:60]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        substacker_items.append(i)

    substacker_items.sort(key=lambda x: -(x.get("relevance_score") or 0))
    logger.info(f"Newsletter items: {len(substacker_items)} (Substack RSS + Gmail newsletters, deduped)")

    # Past 6 days of context — items NOT in today's pool, compressed format,
    # so themes can weave the longer arc ("as Conor Sen argued Tuesday…").
    # Sonnet doesn't need full bodies — just a title, author, and link.
    historical_items_full = get_items_since(conn, hours=24*7, min_relevance=20)
    today_ids = {i["id"] for i in all_items}
    historical_items = [i for i in historical_items_full if i["id"] not in today_ids]
    # Cap at top 150 by relevance — combined with 280 today + newsletters + JSON output,
    # we need to stay well under 200K total (input + output) to leave room for the
    # 32K-token JSON response. 250 historical hit model_context_window_exceeded.
    historical_items = historical_items[:150]
    logger.info(f"Historical context: {len(historical_items)} items from past 6 days (compressed format)")

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
When citing a tweet or Bluesky post, use the @handle exactly as it appears — do NOT translate to a real name or guess who the person is.

{_format_items_for_conversation(relevant_items, limit=280)}

## Newsletters — SUBSTACKER TAKES (use ONLY these for the substacker_takes section)
These are newsletter articles (Substack + email newsletters). Populate substacker_takes from this list. Use the URL provided with each item. Summarize EVERY one.

{_format_substacker_items(substacker_items)}

## Past 6 Days — HISTORICAL CONTEXT (week-long arc, NOT today)
These items are from the prior 6 days, NOT today. Use them to weave the longer arc into today's themes — when today's news touches a topic that's been discussed earlier in the week, cite the relevant historical voice with a clear time stamp ("Tuesday, [Brad Setser](url) warned..."; "earlier this week, [Conor Sen](url) argued..."; "[The FT noted Friday](url)..."). Always make the time-stamp explicit in the prose so the reader knows what's fresh vs context. Do NOT use historical items as the anchor of a theme — today's items must anchor; historical context is connective tissue.

{_format_historical_items(historical_items)}

## Cross-Platform Convergence (topics appearing on 3+ platforms)

{json.dumps(convergence[:10], indent=2, default=str) if convergence else "No convergence detected."}

## Narrative Shifts (topics where sentiment changed significantly)

{json.dumps(shifts[:5], indent=2, default=str) if shifts else "No significant shifts."}

## Organic Conversations (discussions with no news trigger)

{json.dumps([{"title": o["title"][:100], "source": o["source"], "score": o.get("score", 0), "url": o.get("url", "")} for o in organic[:10]], indent=2) if organic else "None detected."}

Generate the daily briefing JSON. LEAD WITH CONVERSATION — what are people debating, arguing about, reacting to? News is context only."""

    try:
        # Retry the streaming call if the connection drops mid-response.
        # Anthropic's API intermittently closes streams ~5-6 min in (peer-closed
        # / incomplete chunked read) — without retry, that single transient
        # failure kills the whole pipeline and the morning email never lands.
        import httpx as _httpx
        last_err = None
        for attempt in range(3):
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
                try:
                    from analysis.anthropic_spend import record_usage as _rec_usage
                    _rec_usage(MODEL, final.usage)
                except Exception:
                    pass
                break  # success
            except (_httpx.RemoteProtocolError, _httpx.ReadError, _httpx.ReadTimeout,
                    anthropic.APIConnectionError) as transient:
                last_err = transient
                logger.warning(
                    f"Sonnet stream attempt {attempt + 1}/3 failed ({type(transient).__name__}: "
                    f"{str(transient)[:100]}) — retrying"
                )
                time.sleep(5 * (attempt + 1))  # 5s, 10s, 15s
        else:
            # All retries exhausted
            raise last_err

        response_text = response_text.strip()

        if final.stop_reason == "max_tokens":
            logger.warning(f"Response truncated at max_tokens ({len(response_text)} chars).")
        logger.info(f"Synthesis response: {len(response_text)} chars, stop_reason={final.stop_reason}")

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
                # Use Haiku for repair — Sonnet was taking 7+ min and timing out
                # on streaming (peer-closed). Haiku 4.5 has 200K input context
                # and 64K output, so a 40K-char fix fits comfortably. Streaming
                # to be safe on long inputs.
                try:
                    fixed_text = ""
                    with client.messages.stream(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=32768,
                        messages=[{"role": "user", "content": (
                            "The following JSON has a syntax error. Fix ONLY the JSON syntax "
                            "(escape quotes, close brackets, fix commas) without changing, "
                            "dropping, or truncating ANY content. Every section and item "
                            "in the original must be preserved exactly. "
                            "Return ONLY the fixed JSON, no explanation.\n\n"
                            + response_text
                        )}],
                    ) as fix_stream:
                        for chunk in fix_stream.text_stream:
                            fixed_text += chunk
                        _fix_final = fix_stream.get_final_message()
                    try:
                        from analysis.anthropic_spend import record_usage as _rec_usage
                        _rec_usage("claude-haiku-4-5-20251001", _fix_final.usage)
                    except Exception:
                        pass
                    fixed_text = fixed_text.strip()
                    if fixed_text.startswith("```"):
                        fixed_text = fixed_text.split("```")[1]
                        if fixed_text.startswith("json"):
                            fixed_text = fixed_text[4:]
                        if "```" in fixed_text:
                            fixed_text = fixed_text[:fixed_text.index("```")]
                    briefing = json.loads(fixed_text.strip())
                    logger.info(
                        f"JSON repair succeeded (Sonnet fix: "
                        f"{len(response_text)} → {len(fixed_text)} chars)"
                    )
                except Exception as fix_err:
                    logger.error(f"JSON repair also failed: {fix_err}")
                    raise e  # Re-raise the original error

        # === POST-PROCESSING: Fix common Sonnet omissions ===

        def _clean_summary_text(text: str) -> str:
            """Clean t.co URLs and excess whitespace from a Twitter summary string.

            Sonnet sometimes wraps raw tweet bodies as link text, so we strip
            t.co URLs from inside the link text while preserving the markdown
            link structure.
            """
            import re as _re
            if not text:
                return text
            # Replace t.co URLs and bare URLs INSIDE link text only ([...]) — leave
            # the URL part of links (...) untouched.
            def _strip_in_link_text(match):
                link_text = match.group(1)
                url = match.group(2)
                # Strip t.co URLs from the link text
                cleaned = _re.sub(r'https?://t\.co/\S*', '', link_text)
                # Strip truncated URLs at end (e.g. "https://t")
                cleaned = _re.sub(r'https?://[^\s\]]*$', '', cleaned)
                # Collapse whitespace and trim
                cleaned = _re.sub(r'\s+', ' ', cleaned).strip()
                return f'[{cleaned}]({url})'
            text = _re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _strip_in_link_text, text)
            # Also strip any t.co URLs that appear OUTSIDE of links (rare)
            # Use a careful approach: split on links, clean the non-link parts
            parts = _re.split(r'(\[[^\]]+\]\([^)]+\))', text)
            for i in range(len(parts)):
                if not (parts[i].startswith('[') and '](' in parts[i]):
                    parts[i] = _re.sub(r'https?://t\.co/\S*', '', parts[i])
                    parts[i] = _re.sub(r'\s+', ' ', parts[i])
            text = ''.join(parts).strip()
            # Replace ". " separator (old format) with " " for cleaner reading
            text = _re.sub(r'\)\.\s*\[', ') [', text)
            return text

        # 0. Clean twitter_roundup summaries (strip t.co URLs from link text)
        # AND enforce the "ONE sentence, max ~30 words" rule — Sonnet routinely
        # ignores it for high-volume authors (zerohedge, VladTheInflator) and
        # writes paragraph-length summaries. Truncate at sentence boundary.
        def _truncate_summary(text: str, max_words: int = 45, max_chars: int = 500) -> str:
            if not text:
                return text
            if len(text) < 200:
                return text
            # Mask markdown links so dots inside URLs don't trigger sentence
            # boundaries. Restore them after truncation.
            link_re = re.compile(r"\[[^\]]+\]\([^)]+\)")
            placeholders = []
            def _mask(m):
                placeholders.append(m.group(0))
                return f"\x00LINK{len(placeholders)-1}\x00"
            masked = link_re.sub(_mask, text)
            # Pick the first sentence (or first-N-words if no boundary found)
            m = re.match(r"([^.!?]+[.!?])", masked)
            if m and len(m.group(1).split()) <= max_words:
                chosen = m.group(1).strip()
            else:
                chosen = " ".join(masked.split()[:max_words]).rstrip(",.;:") + "…"
            # Restore links
            for i, link in enumerate(placeholders):
                chosen = chosen.replace(f"\x00LINK{i}\x00", link)
            # Final char hard-cap: even if word count is fine, if expanded
            # character length is excessive (long markdown URLs), cut at the
            # last sentence/clause boundary that fits.
            if len(chosen) > max_chars:
                # Try to break at a markdown link end "](url)" boundary
                cut = chosen.rfind(")", 0, max_chars)
                if cut > max_chars * 0.5:
                    chosen = chosen[:cut + 1]
                else:
                    chosen = chosen[:max_chars].rstrip(",.;: ") + "…"
            return chosen

        def _shorten_link_anchors(text: str, max_anchor_words: int = 2) -> str:
            """Rewrite [longphrase](url) → [verb](url) longphrase-remainder.

            User wants short link anchors (1-2 words, typically the verb) with
            the descriptive remainder in plain prose. Sonnet ignores this rule
            in prose generation (added 2026-05-12; still violated 2026-05-13)
            so we enforce it programmatically here.
            """
            def _rewrite(m):
                anchor = m.group(1)
                url = m.group(2)
                words = anchor.split()
                if len(words) <= max_anchor_words:
                    return m.group(0)
                # Keep first word(s) as anchor, push remainder to plain text
                kept = " ".join(words[:max_anchor_words])
                remainder = " ".join(words[max_anchor_words:])
                return f"[{kept}]({url}) {remainder}"
            return re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", _rewrite, text)

        for entry in briefing.get("twitter_roundup", []):
            if "summary" in entry:
                entry["summary"] = _clean_summary_text(entry["summary"])
                entry["summary"] = _shorten_link_anchors(entry["summary"])
                entry["summary"] = _truncate_summary(entry["summary"])

        # 1. Deduplicate twitter_roundup and split out AI Roundup accounts
        try:
            from config import AI_ROUNDUP_ACCOUNTS
            ai_handles = {f"@{h.lower()}" for h in AI_ROUNDUP_ACCOUNTS}
        except ImportError:
            ai_handles = set()

        # Build the set of URLs already used in conversation themes — these
        # should NOT appear again in the twitter roundup.
        theme_urls = set()
        theme_handles = set()  # normalized handles used in theme platform entries
        import re as _re_theme
        for theme in briefing.get("conversation_themes", []):
            for p in theme.get("platforms", []):
                u = p.get("url", "")
                if u:
                    theme_urls.add(u)
            # Also scan the summary markdown for twitter URLs and handles
            summary = theme.get("summary", "") or ""
            for m in _re_theme.finditer(r'https?://(?:twitter\.com|x\.com)/(\w+)/status/(\d+)', summary):
                handle, status_id = m.group(1), m.group(2)
                theme_urls.add(f"https://twitter.com/{handle}/status/{status_id}")
                theme_urls.add(f"https://x.com/{handle}/status/{status_id}")
                theme_handles.add(handle.lower())

        roundup = briefing.get("twitter_roundup", [])
        if roundup:
            seen_authors = set()
            deduped = []
            ai_roundup = []
            skipped_theme_dup = 0
            for entry in roundup:
                author = (entry.get("author") or "").lower().strip().lstrip("@")
                if not author:
                    continue
                if author in seen_authors:
                    continue
                # Skip if this account is already featured in a conversation theme
                if author in theme_handles:
                    skipped_theme_dup += 1
                    continue
                # Check if any URL in the summary is already in a theme
                summary_text = entry.get("summary", "") or ""
                entry_urls = set(_re_theme.findall(r'https?://(?:twitter\.com|x\.com)/\w+/status/\d+', summary_text))
                if entry_urls and entry_urls.issubset(theme_urls):
                    skipped_theme_dup += 1
                    continue
                seen_authors.add(author)
                if f"@{author}" in ai_handles or author in {h.lstrip("@") for h in ai_handles}:
                    ai_roundup.append(entry)
                else:
                    deduped.append(entry)
            briefing["_ai_roundup"] = ai_roundup
            if skipped_theme_dup:
                logger.info(f"Twitter roundup: skipped {skipped_theme_dup} entries already in conversation themes")
            briefing["twitter_roundup"] = deduped

        def _build_fallback_summary(tweets: list, author: str = "") -> str:
            """Haiku prose summary for supplement entries."""
            import re as _re2
            import anthropic as _anthropic2
            display = author  # Always show the raw @handle — no name translation
            tweet_lines = []
            for i, t in enumerate(tweets[:8], 1):
                body = t.get("body") or t.get("title", "")
                body = _re2.sub(r'https?://t\.co/\S*', '', body)
                body = _re2.sub(r'https?://\S+', '', body)
                body = _re2.sub(r'\s+', ' ', body).strip()[:280]
                if body:
                    tweet_lines.append(f"[{i}] {body} (url: {t.get('url','')})")
            if not tweet_lines:
                return ""
            try:
                resp = _anthropic2.Anthropic().messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=300,
                    messages=[{"role": "user", "content": (
                        f"The following tweets from {display} are PROVIDED IN FULL BELOW. "
                        f"Write a brief summary (max 35 words). For each distinct claim, "
                        f"the LINK ANCHOR must be ONE OR TWO WORDS — typically the verb of "
                        f"attribution (argued, noted, criticized, flagged, urged, pitched) "
                        f"or a tight two-word noun phrase. The rest of the claim stays in "
                        f"plain prose AROUND the link, not inside it. "
                        f"GOOD: '[argued](url1) rents are bottoming, [criticized](url2) education "
                        f"funding cuts, [urged](url3) House support for HR-2'. "
                        f"BAD: '[argued rents are bottoming](url1), [criticized education funding cuts](url2)' "
                        f"— do NOT wrap the whole claim phrase in the link. The reader should see "
                        f"mostly BLACK prose with short 1-2-word BLUE link anchors sprinkled in. "
                        f"If only one tweet, ONE link with a 1-2-word verb anchor is fine. "
                        f"No paragraphs. No meta-commentary. "
                        f"Do NOT say you can't access Twitter — the content is below.\n\n"
                        f"Tweets:\n" + "\n".join(tweet_lines)
                    )}],
                )
                try:
                    from analysis.anthropic_spend import record_usage as _rec_usage
                    _rec_usage("claude-haiku-4-5-20251001", resp.usage)
                except Exception:
                    pass
                out = resp.content[0].text.strip()
                # Reject Haiku hallucinations
                bad_starts = (
                    "i don't have access", "i cannot access",
                    "i'm unable to", "i am unable to",
                    "to help you", "could you", "please provide",
                )
                if any(out.lower().startswith(p) for p in bad_starts):
                    raise RuntimeError("haiku refusal")
                return out
            except Exception:
                # Fallback: just the title of the highest-scored tweet with a link
                top = max(tweets, key=lambda t: t.get("relevance_score") or 0)
                title = (top.get("title") or "")[:120].strip()
                url = top.get("url", "")
                if url and title:
                    return f"[{title}]({url})"
                return title or ""

        # 2. Supplement twitter_roundup if Sonnet returned fewer than 15 accounts
        roundup_authors = {(e.get("author") or "").lower().strip() for e in briefing.get("twitter_roundup", [])}
        theme_urls = set()
        for theme in briefing.get("conversation_themes", []):
            for p in theme.get("platforms", []):
                if p.get("url"):
                    theme_urls.add(p["url"])
        if len(briefing.get("twitter_roundup", [])) < 15:
            # Group supplemental tweets by author
            supplement_by_author = {}
            for item in relevant_items:
                if item.get("source") != "twitter":
                    continue
                if (item.get("relevance_score") or 0) < 40:
                    continue
                author = (item.get("author") or "").strip()
                author_key = author.lower()
                if author_key in roundup_authors:
                    continue
                if item.get("url", "") in theme_urls:
                    continue
                supplement_by_author.setdefault(author_key, []).append(item)

            # Sort by total relevance score
            sorted_authors = sorted(supplement_by_author.items(),
                                    key=lambda x: -sum(i.get("relevance_score", 0) for i in x[1]))
            for author_key, tweets in sorted_authors:
                display_author = tweets[0].get("author", "").strip()
                if not display_author.startswith("@"):
                    display_author = f"@{display_author}"
                summary = _build_fallback_summary(tweets, display_author)
                if summary:
                    # Apply same length cap as Sonnet entries get
                    summary = _truncate_summary(summary)
                    # Programmatic safety net: even with the explicit "1-2 word
                    # anchor" prompt, Haiku still sometimes wraps full claim
                    # phrases. Enforce 1-2 word anchors here so the supplement
                    # entries match the Sonnet entries' formatting.
                    summary = _shorten_link_anchors(summary)
                    roundup_authors.add(author_key)
                    briefing.setdefault("twitter_roundup", []).append({
                        "author": display_author,
                        "summary": summary,
                        "tweet_count": len(tweets),
                    })
                if len(briefing["twitter_roundup"]) >= 25:
                    break

        # Strip refusal-style meta-narration ("I don't have access...") that
        # Sonnet sometimes produces when a newsletter body is teaser-only.
        briefing = _strip_refusal_meta(briefing)

        # Per-author theme cap is now a SOFT prompt rule, not a programmatic
        # enforcement. User feedback 2026-05-11: "fine if one account shows up
        # in multiple themes — multi-scrape ensures diversity; this was mainly
        # a problem when there were limited citations per theme." We keep the
        # function defined (in case we re-enable later) but disabled here.
        # briefing = _enforce_per_author_theme_cap(briefing)

        # Cross-theme citation dedup IS enabled — catches the case where the
        # SAME tweet/fact is re-cited in two themes' prose (different topics,
        # same supporting voice). User feedback 2026-05-13: @nickgerli1's
        # "existing home sales since 2009" fact appeared verbatim in two themes.
        briefing = _dedup_cross_theme_citations(briefing)

        # Drop themes with no housing-topic overlap. Sonnet drifts off-topic
        # toward macro/AI/tech filler when its prompt has a high theme count.
        briefing = _enforce_housing_focused_themes(briefing)

        # Validate all URLs against the database
        briefing = _validate_briefing_urls(briefing, conn)

        # Copy AI-related substacker takes into the unified AI section.
        # They also remain in the main substacker_takes list so the reader
        # sees AI-newsletter writers in both places.
        try:
            from config import AI_SUBSTACK_AUTHORS
            ai_authors_lc = [a.lower() for a in AI_SUBSTACK_AUTHORS]
            ai_substacks = []
            for take in briefing.get("substacker_takes", []):
                author = (take.get("author") or "").lower()
                if any(a in author for a in ai_authors_lc):
                    ai_substacks.append(take)
            # substacker_takes stays unchanged — dual presence is intentional
            briefing["_ai_substacks"] = ai_substacks
            if ai_substacks:
                logger.info(f"AI substacks routed to AI section: {len(ai_substacks)}")
        except ImportError:
            briefing["_ai_substacks"] = []

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
