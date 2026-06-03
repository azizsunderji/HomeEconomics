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
import requests

from config import TOPICS, RELEVANCE_THRESHOLD_HIGHLIGHT, SOURCE_WEIGHTS
from store import (
    get_db, get_items_since, get_conversation_items, add_story_opportunity,
    save_briefing, get_collection_stats,
    get_recent_collection_errors,
)
from analysis.convergence import compute_convergence, detect_organic_conversations
from analysis.arc_tracker import detect_narrative_shifts
from analysis.trigger_classifier import apply_trigger_filter as _apply_trigger_filter


logger = logging.getLogger(__name__)

# Upgraded 2026-06-03 per user direction — synthesis is where editorial
# judgment lives; Opus's better rule adherence reduces drift on the new
# directionality/stay-on-event/brokerage rules.
# max_tokens=32768 is preserved: Opus 4.7's max output cap is the same 32K
# as Sonnet 4.6, so the existing streaming call needs no parameter change.
MODEL = "claude-opus-4-7"

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


def _fetch_recent_briefing_themes(conn: sqlite3.Connection, n: int = 2) -> list[dict]:
    """Pull theme titles from the last `n` briefings (most recent first).

    Used to tell the synthesis model what it already led with on prior days,
    so it doesn't re-lead the same story without explicit new news. Each
    returned dict has {date: 'YYYY-MM-DD' (created_at-derived), themes: [titles]}.
    """
    try:
        rows = conn.execute(
            "SELECT id, created_at, content_json FROM briefings "
            "WHERE briefing_type = 'daily' ORDER BY id DESC LIMIT ?",
            (n,),
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            content = json.loads(r["content_json"] or "{}")
        except Exception:
            continue
        themes = content.get("conversation_themes") or content.get("briefing", {}).get("conversation_themes") or []
        titles = [t.get("theme") or t.get("title") or "" for t in themes if isinstance(t, dict)]
        titles = [t for t in titles if t]
        if not titles:
            continue
        date_label = (r["created_at"] or "")[:10]
        out.append({"date": date_label, "id": r["id"], "themes": titles})
    return out


def _format_recent_themes(recent: list[dict]) -> str:
    """Render recently led themes as guidance for the synthesis prompt."""
    if not recent:
        return "No recent briefings on record."
    lines = []
    for entry in recent:
        lines.append(f"### {entry['date']} (briefing #{entry['id']}):")
        for t in entry["themes"]:
            lines.append(f"  - {t}")
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
    """Find a known URL that is the same article as the input.

    Conservative: only matches when the URL paths are identical
    (ignoring trailing slash, query string, and fragment). Same-domain
    URLs with different paths are treated as different articles — the
    previous SequenceMatcher-on-full-URL approach corrupted opaque-hash
    URLs (ft.com/content/<hash>, etc.) by "correcting" one article to
    a different article on the same domain. Better to strip a real-but-
    novel URL than to silently substitute a wrong article.
    (Hardened on 2026-05-23 after Burn-Murdoch FT URL was being swapped
    for a Russia-Beijing FT article every day.)
    """
    if url in known_urls:
        return url
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        path = (parsed.path or "").rstrip("/")
    except Exception:
        return None
    if not domain or not path:
        return None
    for known in known_urls:
        try:
            kp = urlparse(known)
        except Exception:
            continue
        if kp.netloc != domain:
            continue
        if (kp.path or "").rstrip("/") == path:
            return known
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
    for roundup in briefing.get("conversation_roundups", []) or []:
        roundup["summary"] = _clean(roundup.get("summary", ""), roundup.get("topic", ""))
    if briefing.get("conversation_pulse"):
        briefing["conversation_pulse"] = _clean(briefing["conversation_pulse"], "")
    return briefing


# Domains where SequenceMatcher / path-only matching is too dangerous because
# article URLs share long common prefixes (e.g. ft.com/content/<hash>). For
# these domains we MUST NOT fall back to corpus-fuzzy-matching — we go straight
# to HEAD-fetch verification. (May 23 regression: Burn-Murdoch fertility URL
# was being swapped for a Russia-Beijing FT article every day.)
_OPAQUE_SLUG_DOMAINS = {
    "ft.com", "bloomberg.com", "wsj.com", "washingtonpost.com",
    "nytimes.com", "reuters.com", "economist.com",
}

# Real desktop User-Agent so paywalled news sites don't auto-403 a bare
# python-requests client.
_VALIDATOR_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _split_sentences_for_validation(text: str) -> list[str]:
    """Split text into sentences, masking [text](url) markdown links so dots
    inside URLs or link anchors don't trigger false sentence boundaries.
    Mirrors _split_sentences_link_safe used elsewhere in this module.

    Accepts an OPTIONAL closing quote (single, double, or curly) between the
    terminator and the trailing whitespace, e.g. 'scheme.' Yesterday — without
    this, a stripped sentence that ends inside a quotation would swallow the
    next legitimate sentence (caught 2026-05-30: WaPo strip was swallowing
    the next NYT citation)."""
    if not text:
        return []
    link_re = re.compile(r"\[[^\]]+\]\([^)]+\)")
    placeholders: list[str] = []

    def _mask(m):
        placeholders.append(m.group(0))
        return f"\x00LINK{len(placeholders)-1}\x00"

    masked = link_re.sub(_mask, text)
    # Use a sub-with-marker trick instead of pure split, so we can match
    # the optional closing quote as part of the boundary while preserving
    # it on the end of the preceding sentence.
    boundary_re = re.compile(
        r"([.!?][\"'”’]?)(\s+)(?=[A-Z\x00]|$)"
    )
    # Replace boundary with terminator + \x01 marker + (consumed whitespace)
    marked = boundary_re.sub(lambda m: m.group(1) + "\x01", masked)
    raw_parts = marked.split("\x01")
    out = []
    for p in raw_parts:
        for j, link in enumerate(placeholders):
            p = p.replace(f"\x00LINK{j}\x00", link)
        out.append(p.strip())
    return [p for p in out if p]


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.").removeprefix("www.")
    except Exception:
        return ""


def _validate_briefing_urls(briefing: dict, conn: sqlite3.Connection) -> dict:
    """Post-process briefing to validate all URLs against the database.

    Two-phase validation:
      1. Structured `url` fields on theme.platforms[] and substacker_takes[].
      2. Inline markdown links inside prose fields (conversation_pulse,
         conversation_themes[i].summary, conversation_roundups[i].summary,
         substacker_takes[i].take).

    Validation order for any URL not already in the corpus:
      a. _find_best_url_match against known_urls — ONLY if the domain is not
         in _OPAQUE_SLUG_DOMAINS (those domains' shared path prefixes fool
         path-equality matching, regression 2026-05-23).
      b. HTTP HEAD with timeout=5s, allow_redirects=True, real desktop UA:
           - 200/301/302 → accept (head_accepted)
           - 403 → ambiguous (paywall). Keep if corpus already has another
                   url from same domain in last 48h; else strip.
           - 404 / timeout / connection error → strip
      c. If the URL fails validation, the ENTIRE SENTENCE containing the
         markdown link is removed (mode B — Aziz-authorized 2026-05-30).

    Every strip / accept event is logged to the pulse_quality_log SQLite
    table for dashboard visibility.
    """
    known_urls = _get_known_urls(conn)
    audit = {
        "verified": 0,
        "corrected": 0,
        "stripped": 0,
        "head_accepted": 0,
        "head_403_kept": 0,
        "sentences_stripped": 0,
        "corrections": [],
        "sentence_strips": [],
    }

    # Cache HEAD results so repeated URLs in the same briefing don't hit
    # the network multiple times. Maps url -> (verdict, reason) where verdict
    # is one of: 'keep_known', 'keep_head', 'keep_403_paywall', 'strip'.
    head_cache: dict[str, tuple[str, str]] = {}

    briefing_id = briefing.get("_briefing_id")

    def _log_quality(kind: str, context: str, original_url: str,
                     stripped_text: str = "", reason: str = "") -> None:
        try:
            conn.execute(
                "INSERT INTO pulse_quality_log "
                "(briefing_id, kind, context, original_url, stripped_text, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (briefing_id, kind, context, original_url, stripped_text, reason),
            )
        except Exception as e:
            logger.warning(f"pulse_quality_log insert failed ({kind}): {e}")

    def _corpus_has_same_domain_recent(url: str) -> bool:
        """Does the corpus contain ANY url from this domain within the
        known_urls (already filtered to last 48h)?"""
        d = _domain_of(url)
        if not d:
            return False
        for k in known_urls:
            if _domain_of(k) == d:
                return True
        return False

    def _check_url(url: str, context: str) -> tuple[bool, str, Optional[str]]:
        """Return (is_valid, reason, replacement_url_or_None).

        replacement_url is set only when we corpus-correct to a real URL
        (rare — _find_best_url_match returns a same-path different-domain-or-
        canonicalized form). Otherwise replacement is None and the original
        URL stays as-is when valid.
        """
        if not url or not url.startswith("http"):
            return True, "non-http", None
        if url in known_urls:
            audit["verified"] += 1
            return True, "in corpus", None

        # Check cache first
        if url in head_cache:
            verdict, reason = head_cache[url]
            if verdict == "keep_known":
                audit["verified"] += 1
            elif verdict == "keep_head":
                audit["head_accepted"] += 1
            elif verdict == "keep_403_paywall":
                audit["head_403_kept"] += 1
            return (verdict.startswith("keep"), reason, None)

        domain = _domain_of(url)

        # (a) Try corpus path-match ONLY if not opaque-slug domain
        is_opaque = any(domain == d or domain.endswith("." + d)
                        for d in _OPAQUE_SLUG_DOMAINS)
        if not is_opaque:
            best = _find_best_url_match(url, known_urls)
            if best:
                audit["corrected"] += 1
                audit["corrections"].append({
                    "context": context, "original": url, "corrected_to": best,
                })
                logger.info(f"URL corrected: {url[:80]} -> {best[:80]} ({context})")
                _log_quality("url_corrected", context, url,
                             stripped_text=best, reason="corpus path match")
                head_cache[url] = ("keep_known", "corpus path match")
                return True, "corpus path match", best

        # (b) HEAD probe
        try:
            resp = requests.head(
                url, timeout=5, allow_redirects=True,
                headers={"User-Agent": _VALIDATOR_UA, "Accept": "*/*"},
            )
            status = resp.status_code
        except requests.exceptions.Timeout:
            head_cache[url] = ("strip", "HEAD timeout")
            return False, "HEAD timeout", None
        except requests.exceptions.RequestException as e:
            head_cache[url] = ("strip", f"HEAD error: {type(e).__name__}")
            return False, f"HEAD error: {type(e).__name__}", None

        if status in (200, 301, 302):
            audit["head_accepted"] += 1
            _log_quality("head_accept", context, url,
                         reason=f"HEAD {status}")
            head_cache[url] = ("keep_head", f"HEAD {status}")
            return True, f"HEAD {status}", None
        if status == 403:
            # Paywall ambiguity: keep if corpus has same domain recently,
            # else treat as suspicious and strip.
            if _corpus_has_same_domain_recent(url):
                audit["head_403_kept"] += 1
                _log_quality("head_403_paywall_kept", context, url,
                             reason="HEAD 403; same-domain present in corpus")
                head_cache[url] = ("keep_403_paywall",
                                   "HEAD 403; same-domain in corpus")
                return True, "HEAD 403; same-domain in corpus", None
            head_cache[url] = ("strip",
                               "HEAD 403; no same-domain corpus support")
            return False, "HEAD 403; no same-domain corpus support", None
        # 404 / 410 / 5xx etc.
        head_cache[url] = ("strip", f"HEAD {status}")
        return False, f"HEAD {status}", None

    def _validate_struct_url(url: str, context: str) -> str:
        ok, reason, replacement = _check_url(url, context)
        if ok:
            return replacement or url
        # Structured URL failed. We can't strip a "sentence" from a structured
        # field, so just blank it. Caller already handles empty url fields.
        audit["stripped"] += 1
        logger.warning(f"URL stripped (no match): {url[:100]} ({context}) — {reason}")
        _log_quality("strip_link", context, url, reason=reason)
        return ""

    def _validate_prose_field(text: str, context: str) -> str:
        """Walk every [anchor](url) markdown link in `text`. For each URL
        that fails validation, strip the entire sentence containing it.
        Returns the rewritten text (may be ""). Logs each strip to
        audit['sentence_strips'] and the pulse_quality_log table."""
        if not text or not isinstance(text, str):
            return text or ""
        link_re = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
        # Collect all (anchor, url) tuples and their validation result first
        url_results: dict[str, tuple[bool, str, Optional[str]]] = {}
        for m in link_re.finditer(text):
            u = m.group(2)
            if u not in url_results:
                url_results[u] = _check_url(u, context)

        # If every URL passed, apply any corpus-corrections and return.
        bad_urls = {u for u, r in url_results.items() if not r[0]}
        if not bad_urls:
            # Apply replacements (corpus-corrected URLs)
            def _maybe_replace(m):
                anchor, u = m.group(1), m.group(2)
                _, _, replacement = url_results.get(u, (True, "", None))
                if replacement and replacement != u:
                    return f"[{anchor}]({replacement})"
                return m.group(0)
            return link_re.sub(_maybe_replace, text)

        # Otherwise, sentence-strip mode: for every sentence containing a
        # bad URL, drop it entirely and log it.
        sentences = _split_sentences_for_validation(text)
        kept: list[str] = []
        for sent in sentences:
            sent_bad_urls = [u for u in bad_urls
                             if u in sent]  # exact substring check is fine —
            # the URL strings in `bad_urls` came verbatim from `text`.
            if sent_bad_urls:
                for bad in sent_bad_urls:
                    _, reason, _ = url_results[bad]
                    audit["sentences_stripped"] += 1
                    audit["stripped"] += 1
                    audit["sentence_strips"].append({
                        "context": context,
                        "original_url": bad,
                        "stripped_text": sent.strip(),
                        "reason": reason,
                    })
                    logger.warning(
                        f"Sentence stripped ({context}): URL {bad[:80]} "
                        f"failed ({reason}); sentence: {sent.strip()[:120]!r}"
                    )
                    _log_quality(
                        "strip_sentence", context, bad,
                        stripped_text=sent.strip(), reason=reason,
                    )
            else:
                kept.append(sent)

        # Re-join and normalize whitespace.
        rejoined = " ".join(s.strip() for s in kept if s.strip())
        rejoined = re.sub(r"\s+", " ", rejoined).strip()

        # Apply any corpus-corrections on the URLs that survived
        if rejoined:
            def _maybe_replace(m):
                anchor, u = m.group(1), m.group(2)
                _, _, replacement = url_results.get(u, (True, "", None))
                if replacement and replacement != u:
                    return f"[{anchor}]({replacement})"
                return m.group(0)
            rejoined = link_re.sub(_maybe_replace, rejoined)

        return rejoined

    # ─── Phase 1: structured URL fields ───────────────────────────────────
    for i, theme in enumerate(briefing.get("conversation_themes", []) or []):
        for j, plat in enumerate(theme.get("platforms", []) or []):
            if "url" in plat:
                plat["url"] = _validate_struct_url(
                    plat["url"],
                    f"conversation_themes[{i}].platforms[{j}].url",
                )
    for i, take in enumerate(briefing.get("substacker_takes", []) or []):
        if "url" in take:
            take["url"] = _validate_struct_url(
                take["url"], f"substacker_takes[{i}].url"
            )
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

    # ─── Phase 2: inline markdown links in prose fields ──────────────────
    if briefing.get("conversation_pulse"):
        briefing["conversation_pulse"] = _validate_prose_field(
            briefing["conversation_pulse"], "conversation_pulse"
        )
    for i, theme in enumerate(briefing.get("conversation_themes", []) or []):
        if "summary" in theme:
            theme["summary"] = _validate_prose_field(
                theme.get("summary", ""), f"conversation_themes[{i}].summary"
            )
    for i, roundup in enumerate(briefing.get("conversation_roundups", []) or []):
        if "summary" in roundup:
            roundup["summary"] = _validate_prose_field(
                roundup.get("summary", ""), f"conversation_roundups[{i}].summary"
            )
    for i, take in enumerate(briefing.get("substacker_takes", []) or []):
        if "take" in take:
            take["take"] = _validate_prose_field(
                take.get("take", ""), f"substacker_takes[{i}].take"
            )

    # ─── Phase 3: drop themes whose summary was completely stripped ──────
    before_drop = len(briefing.get("conversation_themes", []) or [])
    briefing["conversation_themes"] = [
        t for t in (briefing.get("conversation_themes", []) or [])
        if (t.get("summary") or "").strip()
    ]
    if before_drop != len(briefing["conversation_themes"]):
        logger.warning(
            f"Dropped {before_drop - len(briefing['conversation_themes'])} "
            f"themes left empty after sentence-level URL strip"
        )
    before_drop_cr = len(briefing.get("conversation_roundups", []) or [])
    briefing["conversation_roundups"] = [
        r for r in (briefing.get("conversation_roundups", []) or [])
        if (r.get("summary") or "").strip()
    ]
    if before_drop_cr != len(briefing["conversation_roundups"]):
        logger.warning(
            f"Dropped {before_drop_cr - len(briefing['conversation_roundups'])} "
            f"conversation_roundups left empty after sentence-level URL strip"
        )

    try:
        conn.commit()
    except Exception:
        pass

    briefing["_url_audit"] = audit
    total = audit["verified"] + audit["corrected"] + audit["stripped"] \
        + audit["head_accepted"] + audit["head_403_kept"]
    logger.info(
        f"URL validation: {audit['verified']} verified, "
        f"{audit['corrected']} corrected, "
        f"{audit['head_accepted']} head-accepted, "
        f"{audit['head_403_kept']} 403-paywall-kept, "
        f"{audit['stripped']} stripped "
        f"({audit['sentences_stripped']} sentence-strips) "
        f"(of {total} total)"
    )
    return briefing


# ── Synthesis prompt ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the conversation intelligence system for "Home Economics," a data journalism newsletter about the US housing market and economy by Aziz Sunderji.

Your job: surface the day's most substantive and interesting content for a daily US housing-economics brief. Primary topic: US housing — prices, inventory, mortgages, affordability, zoning/policy, construction supply, urbanism, real estate. Adjacent topics that earn coverage: demographics, internal migration, household formation, mortgage and credit markets, labor markets and regional economies WHERE they affect housing. AI-only items (model releases, AI safety) are out of scope; only include AI when it explicitly ties to housing (AI data centers driving local development, tech-worker geography). Macro/Fed/markets: only as themes IF the housing tie is direct ("Fed pause keeps 30-yr mortgage at 7%" yes; "Fed holds rates" no).

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
        {"name": "[major outlet]", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "[major outlet]", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "[major outlet]", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "[major outlet]", "reply_count": 0, "sentiment": "neutral", "url": "..."},
        {"name": "[major outlet]", "reply_count": 0, "sentiment": "neutral", "url": "..."}
      ],
      "heat_level": "low|medium|high|viral",
      "related_news_trigger": "What news event sparked this conversation, if any. Empty string if organic.",
      "topics": ["topic_key1", "topic_key2"]
    }
  ],

  "conversation_roundups": [
    {
      "topic": "Title-case short topic — e.g. 'Supply trends across the Sun Belt'",
      "summary": "Single paragraph (3-6 sentences, max 110 words) summarizing what people across multiple sources are saying about this topic. Every claim links inline via [text](url). The paragraph is observational — 'the discourse is doing X' — not assertive news. Mix of voices: tweets, substack posts, news commentary. No single trigger required (that's the point — these are themes without one)."
    }
  ],

  "paper_of_the_day": {
    "title": "Verbatim paper title",
    "authors": "First author + 'et al.' if multiple. If single author, just the name.",
    "publication": "NBER Working Paper #####, Brookings, Journal of Housing Economics, etc.",
    "url": "Direct link to the paper",
    "date": "Published date YYYY-MM-DD",
    "summary": "3-5 sentence paragraph for an intelligent non-academic reader. What did they find, how did they show it, why does it matter for US housing. Measured, restrained, data-first tone. No filler phrases, no hedges, no academic jargon. State the finding, then the method in one sentence, then the implication.",
    "key_finding": "Single sentence (max 25 words) capturing the central finding plainly, like a wire-service lede."
  },

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

2. QUOTE REAL PEOPLE BY NAME. "[Analyst] argues the labor market is weakening faster than the Fed acknowledges" is useful. "Users are panicking" is not. Focus on substantive discussions, not populist venting.

3. NEWS ARTICLES AS THEME ANCHORS. Newspaper articles, RSS feeds, and journalism can anchor themes independently — a major newspaper investigation or wire-service exclusive does NOT need to be generating Twitter/Bluesky chatter to appear as a theme. Include it if the story is substantive and interesting on its own merits. That said, if the same story is also sparking economist debate on social media, fold both together as one theme.

4. REAL URLS ONLY. Every source must include the actual URL from the collected items. Never fabricate URLs.

4b. CITATION ANCHOR MUST MATCH URL. When you write "[ENTITY did/said/projects/argues X](url)", the URL must point to that entity's own post, paper, or tweet — not to a secondary source that happens to mention the entity. If you only have the secondary source (e.g., you read about Urban Institute's research in a [substack] essay), either:
  (a) attribute it honestly: "[per [substack]](secondary-url), the Urban Institute projects X" so the link's target matches what the link says, OR
  (b) drop the attribution entirely: "rental restrictions would disrupt 72,000 units/year" with no byline
What you MUST NOT do: "[The Urban Institute projects X](secondary-url)" — that makes the reader think they're clicking through to Urban Institute when they're really going to a secondary outlet. This is a form of misattribution. Every anchor text + URL pairing must be internally consistent.

4c. ATTRIBUTED QUOTES MUST BE HYPERLINKED — AND THE LINK MUST POINT TO WHERE THE CLAIM ORIGINATED. Any time you attribute a direct quote, statement, or specific data point to a named person or organization, the attribution MUST be wrapped in a hyperlink. Plain-text attribution ("[chief economist] said affordability has improved") with no link is unacceptable — the reader has no way to verify or read more.

WHICH URL TO LINK depends on where the claim originated:
  - **Twitter/Bluesky tweet** → link the tweet's URL. The tweet IS the source. Do NOT link to an article that summarizes the tweet; link directly to the tweet itself.
  - **Article quote** (person quoted IN an article) → link the article. The article IS the source — the person didn't publish their statement themselves; they spoke to the reporter.
  - **Substack / newsletter / column** → link the post itself.
  - **Working paper / report** → link the paper's own URL (PDF, abstract page, or publisher landing).

Examples by source type:
  (a) Tweet attribution: "[@analyst-handle noted](https://x.com/analyst-handle/status/...) that existing-home sales were the lowest YTD pace since 2009" — link the TWEET.
  (b) Article quote: "[NAR chief economist told [outlet]](https://[outlet-domain]/...) that affordability has improved" — link the ARTICLE.
  (c) Anchor on the verb: "[chief economist] [said](https://[outlet-domain]/...) affordability has improved" — link the article via the verb.
  (d) Anchor on the publication: "[per [outlet]](https://[outlet-domain]/...), [chief economist] said affordability has improved" — link the article via the publication.

@HANDLE ATTRIBUTIONS ALWAYS NEED A LINK. The pattern "per @handle" / "@handle noted" / "@handle argued" / "@handle flagged" written as plain text (no markdown link wrapping any part of it) is the most common 4c violation. If you reference a Twitter or Bluesky handle by their data point or argument, the tweet URL MUST be linked — typically the cleanest form is to wrap the verb: "@analyst-handle [noted](tweet_url) that...". If you don't have the tweet URL in your corpus, drop the attribution entirely and state the fact directly without naming the handle.

LINK THE EXAMINING SOURCE, NOT THE UNDERLYING SOURCE IT DRAWS ON. When you write a construction like "X's newsletter examined Y," "X's article covered Y," "X analyzed Y," "X's piece argued Y," the link MUST point to X's piece — the SUBJECT of the sentence — not to Y or to whatever underlying material X drew on. The reader expects to click through to X's analysis, not to the raw source X used.

Failure example: "[Substack author]'s newsletter examined [whether home prices and social media are the reasons we're having even fewer babies]([outlet]-url), drawing on [the outlet]'s coverage." — The anchor points to the underlying outlet, but the sentence is about the newsletter. Reader clicks expecting the newsletter and lands at the outlet instead.

Required fix: "[Substack author]'s newsletter [examined]([newsletter]-url) whether home prices and social media are the reasons we're having even fewer babies, drawing on [the outlet's coverage]([outlet]-url)." — Two links: one to the newsletter (the subject doing the examining), one to the outlet (the underlying source). If you only have ONE URL available, it MUST be the URL of the entity that's the subject of the sentence. Drop the secondary reference if you can't link it; never substitute the secondary URL for the primary.

This rule generalizes to any "X did/examined/argued Y" construction where X is named and X's content is the subject. Link X's content. The exception is when the sentence frames it as "according to/per [secondary source], X said Y" — in that case the link is on the secondary (because that's where the reader will read X's quoted statement).

What you MUST NOT do: write "[economist] said X" or "@analyst-handle noted X" as plain text. If you cited [outlet] two sentences earlier, that link does NOT cover a later separate [economist] attribution — each attributed claim needs its own link. This rule applies to direct quotes, paraphrased positions, and specific data points (e.g., "Goldman tracked Q2 at 1.6%" — link Goldman; "the median price hit $417,700" — only an unattributed fact like this can stand without a link).

VAGUE REFERENCES STILL REQUIRE LINKS. Any reference to a specific paper, report, study, dataset, or analysis MUST be hyperlinked — even when you do NOT name it explicitly. Phrasings like "an NBER working paper examined X," "a Goldman note argued Y," "Census data showed Z," "research from the Urban Institute found W" all count: the reader has no other handle to find the source if you don't link it. The link is MORE important when the reference is vague, not less. Failure example: "Yesterday, an NBER working paper flagged in the feed examined gender gaps" — with no link, the reader has no way to find which paper. Correct: "Yesterday, [an NBER working paper](url) flagged in the feed examined gender gaps." If the source isn't in your corpus, don't reference it at all — drop the attribution and make the claim directly: "Gender gaps in education are widening" rather than "[unsourced] research showed gender gaps are widening."

NO PRONOUN-CHAINED ATTRIBUTION ACROSS SENTENCE BOUNDARIES. Pronouns ("He", "She", "They", "The author", "The same account") may ONLY refer back to the source named in the same sentence. The instant you start a new sentence with a new claim from a DIFFERENT source, you MUST re-introduce that source by name/handle with its own fresh hyperlink. Chaining a pronoun across a sentence break to a claim by a different author is misattribution — exactly as bad as putting the wrong handle in the link.

Failure example: "[@handle1 offered](url) a softer read: 'Another solid week for pending home sales' — suggesting the data may be more mixed than the headline implies. He posted a separate claim — there is NOT a housing shortage, there is a housing MISMATCH, with 148M housing units for only 134M households." — The "He" reads as @handle1, but the housing-mismatch claim was actually @handle2's tweet. The pronoun bridged across two different authors, fabricating an attribution.

Required fix: "[@handle1 offered](handle1-url) a softer read: 'Another solid week for pending home sales.' Separately, [@handle2 argued](handle2-url) America has a housing mismatch rather than a shortage — 148M housing units for only 134M households, with the surplus skewed toward luxury rather than starter homes." — Each author introduced explicitly, each with their own link.

If you can't link the second claim to its actual source URL, DROP the attribution entirely and state the underlying fact without naming any author. Never reuse the prior sentence's source via pronoun to cover a claim from a different source. When in doubt, repeat the handle/name — verbosity is always preferable to misattribution.

NO FLOATING EDITORIAL COMMENTARY. Sentences with no inline link and no clear source attribution are forbidden inside theme summaries. Examples of what you MUST NOT write: "Read that again." "Worse than 2008." "Yet sellers are still pricing homes like it's 2021 with 3% rates." "The signal is clear." "This is meaningful." These are your own voice editorializing — even when they feel like natural connective tissue between cited claims. Every sentence in a theme summary must either (a) wrap a cited claim in a hyperlink, or (b) be neutral framing/scene-setting derived directly from the cited material with no new claim of its own. If you find yourself adding a punchy unsourced sentence to make the prose flow better, delete it.

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
A public reply on X is NOT "telling someone directly." Failure example: "@analyst-handle told Rep. @congressperson directly that Seattle housing supply is exploding" — wrong, that was a public reply. Correct: "@analyst-handle replied to Rep. @congressperson that Seattle housing supply is exploding." If you're unsure from the tweet's metadata, use the neutral verb "wrote."

4e. NAMED PUBLICATIONS REQUIRE PROVENANCE PRESERVATION. When you reference a NAMED PUBLICATION by its title — institutional reports, monthly monitors, press releases, working papers, etc. (e.g., "ICE Mortgage Monitor," "NAR Existing-Home Sales Report," "Fed Beige Book," "Goldman US Daily," "FHFA HPI release," "BLS CPI release", "CBO budget outlook") — the URL MUST be on the publisher's own domain. If you only have a secondary source covering it, your prose MUST do BOTH:
  (a) preserve the original publisher's attribution (the data came from them, not the secondary), AND
  (b) make the secondary source's role visible (summary / analysis / report on).

Acceptable forms:
  • "[[housing analyst blog]'s summary of the May ICE Mortgage Monitor](secondary-url) showed annual home price growth of 0.9% in April"
  • "The May ICE Mortgage Monitor showed 0.9% annual home price growth, [per [housing analyst blog]'s analysis](secondary-url)"
  • "[housing analyst blog] [summarized](secondary-url) the May ICE Mortgage Monitor, which reported 0.9% annual home price growth"
  • If you can't honor both, DROP the link: "The May ICE Mortgage Monitor showed 0.9% annual home price growth."

NEVER do any of these:
  • "[ICE May Mortgage Monitor](secondary-url)" — link domain mismatches the named entity; reader feels deceived clicking through
  • "[[housing analyst blog] reports home price growth was 0.9%](secondary-url)" — attributes the DATA to the secondary when it is summarizing ICE; this is data-source misattribution
  • "[[housing analyst blog]](secondary-url) reports home price growth was 0.9%" — same data-source misattribution

The fix is ALWAYS: name BOTH the original publisher AND the secondary you actually link to. Two names, one link, both roles visible.

5. NEWSLETTERS AND SUBSTACKS ARE INPUTS, NOT A DEDICATED OUTPUT SECTION (UPDATED 2026-06-02). The dedicated `substacker_takes` output field has been REMOVED. Do NOT output a substacker_takes field — if you do, it will be discarded before rendering. Newsletter / substack / single-author RSS columnist items provided in the "Newsletters" input section below are still VALUABLE INPUT — route them as follows:
   - If the newsletter post DIRECTLY COMMENTS on a candidate news event of the day, weave it into that theme's commentary with inline [author or outlet name](url) citation, same convention as other theme citations.
   - If the post is a topical discussion without a single event anchor, route it into the relevant `conversation_roundup` (same prose-with-inline-link format as ai_brief).
   - For AI-focused substack/newsletter posts, weave them into `ai_brief`.
   - Skip newsletters whose content is purely promotional, off-topic, or too thin to summarize.
This is a structural change: newsletter content is now distributed across themes/roundups based on subject matter, not collected into a single dedicated section.

5b. NEVER NARRATE INSUFFICIENT CONTENT. If a source's preview is short or teaser-only, infer the take from the title and any partial body you have, then write a confident one-sentence summary. NEVER write phrases like "I don't have access to the full content", "the snippet is cut off", "based on the limited preview", "I cannot offer specifics", or "partial summary". The reader will see this as broken output. If you genuinely can't infer anything beyond the title, write a single neutral sentence based on the title alone — no meta-commentary. This rule applies to conversation_themes, conversation_roundups, paper_of_the_day, and every other section.

5c. PAPER OF THE DAY. Pick ONE academic paper from the journal-feed input (NBER working papers, Journal of Housing Economics, Journal of Urban Economics, Real Estate Economics, Regional Science and Urban Economics, Housing Policy Debate, Cities, etc.) as today's Paper of the Day. Selection criteria, in order: (1) most directly relevant to US housing, mortgages, zoning, demographics-of-housing, or affordability; (2) most interesting / surprising / counterintuitive findings; (3) methodologically sound; (4) not too inside-baseball for a general reader. Write the `summary` field in Pulse's measured, restrained, data-first tone — state the finding, then the method in one sentence, then the implication for US housing. The `key_finding` is a single wire-service-style lede sentence (max 25 words). If NO journal candidate is credible for the day (e.g., all candidates are non-housing or TOCs), set `paper_of_the_day` to null and the renderer will omit the section. Bias toward recency (papers <7 days old slightly preferred) but don't pick a marginal recent paper over a strong one from earlier in the 30-day window.

6. THEMES: Quality over count. NO target theme count — produce only as many themes as the day's actual news events justify. Each theme must be anchored on a real news event with substantial direct commentary; if it's not, drop it (do NOT pad to hit a target). These are the most substantive stories of the day. This section ABSORBS what used to be a separate "Headlines" section. Each theme can be:
   - A news story with multiple outlets covering it (weave the actual reporting from the article BODIES, not just headlines, with inline source links to each outlet)
   - A cross-platform debate (multiple voices arguing about something)
   - A data release or research finding
   - A combination of the above
Coverage rules:
   - Real estate / housing / urbanism: cover EVERY substantive story — don't skip housing stories just to make room for other topics
   - **At least 70% of themes must be housing/urbanism/demographics/affordability-tagged.** If your themes drift toward AI, macro, or generic tech, STOP — drop the off-topic items rather than keep them for variety. The reader signed up for housing economics, not generic news.
   - AI-only items (model releases, AI safety, AI politics that don't tie to housing/labor/geography) are OUT OF SCOPE for this briefing — skip them entirely. Only include AI when it explicitly ties to housing or labor/geography.
   - Macro/international items (Fed, oil prices, Canadian jobs, etc.) only belong as themes IF they explicitly tie to housing impact. A Canadian unemployment number is not a theme; "Canadian unemployment hits 6.9%, putting downward pressure on Toronto housing demand" is. Same for the Fed: "Fed holds rates higher" is not a theme; "Fed pause keeps 30-year mortgage rates near 7%" is.
   - **Tech_general items (3D printers, software lawsuits, generic tech news) NEVER anchor themes.** The classifier may assign tech_general topic to a high-engagement HN thread; ignore the engagement and skip these. They don't belong in this briefing at all unless they have explicit housing or AI-and-housing relevance.
   - Other beats: include the most substantive 2-4 stories if they pass the bar (high-quality demographics, urbanism, geography). Politics only if directly housing-related.
   - When multiple outlets cover the same news event, ONE theme covers them all with EVERY substantive source linked inline. Do NOT cap at 2-3 sources — a widely-covered story may warrant 5-8 inline citations. Example: "[[outlet A]](url) and [[outlet B]](url) report X, while [[outlet C]](url) emphasizes Y; [[outlet D]](url) frames it as Z, [[outlet E]](url) adds specific data, [[independent newsletter]](url) argues against the consensus, and [[analyst-handle on Twitter]](url) calls it overblown." If 6+ outlets covered the story substantively, cite all 6+. Stop only when sources start repeating the same angle without adding anything.
   - **EVENT-ANCHOR REQUIREMENT (HARD GATE FOR NEWS THEMES).** Every conversation_themes entry MUST be anchored on a single named, dated event. Valid triggers:
       - A publication (named outlet released a story/report on date X)
       - An announcement or deal (company A acquires company B; person X announced Y)
       - A government action (bill cleared the [chamber]; agency Z issued a ruling)
       - A data release (NAR / [brokerage data source] / Census released X data showing Y)
       - A court filing or ruling
       - A named research paper or institutional report (NBER working paper #####, Brookings report titled Z)
     If you cannot complete the sentence "Today, [specific entity] [specific action] [specific thing]" in 15 words or fewer, it is NOT a news theme. Move the material to conversation_roundups instead.
     INVALID as themes (move to conversation_roundups):
       - Tweets across multiple states/cities discussing a topic without a single named trigger
       - Retrospectives, anniversary investigations, "look back at" pieces with no new news hook
       - General topical discourse ("housing supply is doing X" without naming a specific report)
       - Op-eds and commentary that aren't TIED to a specific event from today
     Each conversation_themes entry's `related_news_trigger` field is now MANDATORY and must name the specific event. If it would read "ongoing discussion about X" or "various tweets about Y", the entry is invalid as a theme — move it to conversation_roundups.
   - **THEME GENERATION DIRECTIONALITY (MANDATORY).** Themes are generated FROM news items, NOT from discourse. Your loop is:
       1. **Identify news items first** — work through the day's actual news events (deals, bills, rulings, official data releases, investigations).
       2. **Attach discourse second** — for each news item, gather only the commentary that DIRECTLY references that specific event (mentions it by name, links to its primary URL, or directly responds to its claims). If enough direct commentary exists, write the theme. If not, drop the candidate.
     You may NOT do the inverse:
       - You may NOT find a rich discourse cluster in the input, then search the news list for any event that vaguely fits, then anchor the theme on that event. This produces themes whose body has nothing to do with the cited anchor — a structural lie.
       - You may NOT use a candidate event as a cosmetic anchor for a theme whose actual content is about something else. The anchor must match the content.
       - You may NOT reconstruct themes from discourse about articles that were excluded as candidate events (e.g., retrospectives, op-eds, recaps of brokerage content). If the article was excluded as a news item, the discourse about it does NOT belong as a theme. It can go in conversation_roundups, never as a synthesized news theme.
     The reader's mental model: "this theme is ABOUT [the news event named in related_news_trigger]." If the theme's actual content is about something else, the anchor is a fiction — and the theme should not exist.
   - **STAY ON EVENT (HARD GATE FOR THEME BODIES).** Complement to the directionality rule. Once a theme is anchored on an event, its summary must directly report on that event and its IMMEDIATE corroborating commentary. Forbidden body-drift patterns:
       • Citing tangentially related stats or data points from DIFFERENT events (a Berkshire/Taylor Morrison theme cannot include "and meanwhile mortgage rates ticked up" unless that's directly tied to the deal).
       • Using the event as a "jumping-off point" for broader discourse (a Boomer/Millennial homeownership theme that drifts into fertility crisis, smartphones, social isolation — every sentence after the data point belongs in conversation_roundups, not this theme).
       • Including commentary from voices who didn't react to THIS event but who tweeted about a related topic the same day.
     Concrete test: for each sentence in a theme summary, ask "Is this sentence directly about the anchor event, or is it tangential discourse using the event as a hook?" If tangential → cut from theme, move to conversation_roundups.
     GOOD example (Berkshire deal): "Berkshire acquired Taylor Morrison. [Outlet A] noted the unusual centralization framing. [Outlet B] reported the price premium. Critics on [platform] reacted to the centralization plan." — every sentence directly about the deal.
     BAD example (drift): "[Brokerage] published Boomer/Millennial homeownership data. The data echoes [analyst]'s note that first-time buyers are now 40. Meanwhile WaPo's opinion section linked fertility decline to smartphones. [Researcher] called it a radical experiment in social isolation." — only the first sentence is about the event; the rest is tangential drift.
   - **BROKERAGE/PLATFORM CONTENT MARKETING IS NEVER NEWS — NEVER AN ANCHOR.** A brokerage or real-estate platform publishing its own "research" / "study" / "report" / "forecast" / "data analysis" is content marketing, not news. Hard list of sources whose own publications can NEVER anchor a news theme: Redfin (blog and research), Zillow (research and forecasts), Realtor.com (mediaroom, research, surveys), HomeLight (surveys/reports), Compass (insights), Opendoor, Offerpad, Knock, Orchard, Roofstock, Better.com (formerly Better Mortgage). When one of these is the source of an article's news hook, REJECT as an anchor candidate — route the data into a conversation_roundup if interesting, or drop entirely. Their content can still be CITED as commentary in themes anchored on legitimate news events (e.g., a Fed action theme can quote Redfin's reading of the impact), but they cannot themselves be the anchor.
   - **BROKERAGE-RECAP-VIA-THIRD-PARTY IS NEVER NEWS.** Extension of the above. A third-party outlet (Axios, Bloomberg, NYT, etc.) REPORTING on a brokerage's study is STILL content marketing wearing a news disguise. If the article's news hook can be paraphrased as "[Brokerage] published / released / announced X" — where the brokerage IS the source of the data and the third party is just relaying it — it's a recap and NOT a news anchor. Allow only when the third party adds substantial original reporting: interviews with parties NOT employed by the brokerage, independent data sources, court records, investigative findings. The test: take the third-party headline, replace the brokerage name with "[a real estate platform]" — if the headline still reads like news, it's news; if it reads like "[a real estate platform] put out a press release," it's a recap.
   - **MULTI-DAY NEWS ARCS ARE VALID.** A news event from 1-3 days ago that's still being actively discussed today CAN anchor a theme. Example: the Berkshire/Taylor Morrison acquisition was announced Sunday May 31; Monday and Tuesday's commentary on it is a legitimate theme. The test is not "did the event happen today" but "is there fresh substantive commentary today that addresses the event." If today's input contains 3+ items (across news outlets, analysts, social discourse) directly addressing a recent event, that event can anchor today's theme even if the original news was a few days back. Cap is ~5 days — past that, the event is stale even if someone is still talking about it.
   - Use the FULL article body when present in the input (enriched articles have substantial body text — quote specifics, not just topics)
   - **Weave historical context with explicit time stamps.** When a topic touches something already discussed this week, cite the relevant historical voice from the "Past 6 Days" section with a date stamp: "Tuesday, [an economist argued](url)..." or "earlier this week [an analyst warned](url)...". Never use a historical item without a date marker — the reader needs to instantly tell what's fresh vs context. Today's items don't need a date stamp (they're implicitly today).
   - **CRITICAL: historical context must match the theme's specific topic, geography, and country.** Don't weld an Australian migration statistic into a Canadian unemployment theme, or a NYC rent freeze argument into a San Francisco housing theme, just because both have "international" or "housing_policy" tags. Before citing a historical voice, verify: (a) same country/metro, (b) same specific topic (rent control ≠ inclusionary zoning ≠ permitting reform), (c) same direction of argument. If a historical item is about a different country or a tangentially-related topic, leave it out — better to have no historical citation than a misleading one. Recent failure: Sonnet wrote "Canadian unemployment...Thursday, @handle noted that Treasury forecasts missed the migration surge: 'At the 2022 Federal Budget...'" — but @handle was discussing AUSTRALIAN migration numbers, not Canadian. The cite was geographically wrong.
Label each theme's anchor platforms accurately: use "rss" or "substack" or the newspaper name when that's the anchor, "twitter" or "bluesky" when those anchor it.

**No theme-count target. Only include themes anchored on real news events with substantial direct commentary.** Don't pad. If a candidate has no real anchor or no commentary, drop it.

**CONSOLIDATE NEAR-DUPLICATE THEMES.** Before finalizing the theme list, ask: "are any two themes telling the same story from different vendor angles?" If two themes both anchor on (a) the same data period (same month / same release window) AND (b) the same housing-market dynamic (sales + prices, supply + demand, rents + vacancy, mortgage rates + affordability, etc.), MERGE them into one theme that holds the tension inside. Cite all sources inline; don't split because the data came from different vendors. Example: an April existing-home-sales theme (NAR / [trade publication] / [major outlet]) and an April home-price-growth theme (ICE Mortgage Monitor / [housing analyst blog]) are ONE story — "April: sales soft, prices firm" — not two. The reader thinks in market dynamics, not data-vendor categories. Splitting them makes the brief feel like the same story got coverage twice. When the data period or the dynamic differ meaningfully (e.g., April sales but March CPI shelter; national sales but a Bay Area-specific price piece), keep them separate.

**SPLIT WELDED-UNRELATED THEMES.** Complement to the consolidation rule above. After drafting, scan each theme: if it has two paragraphs (or two sentence-clusters) describing DIFFERENT mechanisms or DIFFERENT markets, and the only thing linking them is a shared region label, a shared word in the headline, or a contrast frame, they are TWO THEMES, not one. SPLIT them. The diagnostic test: if the transition between the two sub-clusters requires a disjunctive frame ("On a separate track…", "The picture looks different in…", "Meanwhile in [region]…", "By contrast in [market]…", "On the other side of the country…"), that's the model reaching for a bridge between two stories that don't actually belong together. Failure example: combining a "Texas exurb growth driven by permitting regime" story and a "Florida home prices correcting after pandemic overshoot" story under one theme — these are different mechanisms (supply elasticity vs. demand withdrawal), different markets (Texas vs. Florida), and the only link is "both Sunbelt." Correct: two separate themes. Theme paragraphs split with `\\n\\n` are for genuine sub-clusters of ONE story (e.g., permits → starts → completions in the same release window, or rents → income → freeze proposal in the same market), not for two stories sharing a region tag. When in doubt, split. The reader prefers two crisp themes over one welded conglomerate.

**ONE QUESTION / ONE MECHANISM PER THEME.** Finer-grain partner to the split rule. Before writing each theme, name the SINGLE question it answers or the SINGLE mechanism it describes (e.g., "Why is Texas the fastest-growing state?" → permitting regime + MUDs; "Why are exurbs winning within metros?" → remote work + cost of space). Every claim in the theme must bear on that specific question/mechanism. Even if two questions share the same data point or the same lead-in observation, if they have different MECHANISMS as answers, they belong in separate themes. Failure example: a "Texas exurbs are #1" theme that mixes (i) the permitting-regime explanation for why Texas dominates state rankings and (ii) the remote-work explanation for why exurbs are absorbing metro growth nationwide. Both answers technically point to Celina being #1 in the data, but they're answering two different questions through two different mechanisms — so they are two themes, not one. The reader's mental model is "one theme = one mechanism = one cause being explained." When you find yourself oscillating between two threads inside one paragraph, that's the model trying to weld two answers under one umbrella because the data overlaps; resist that and split. This rule strengthens 6b in a finer grain — 6b catches two-paragraph welds across markets; this rule catches within-paragraph welds across mechanisms.

**NO CAP ON PLATFORMS PER THEME.** The platforms[] field is not limited to 2-4 entries. If 12 outlets covered a story (multiple major outlets, independent newsletters, local outlets, political press, Twitter threads, etc.), list ALL 12. The 7-entry example in the JSON schema is illustrative, not a ceiling. Same applies to inline source citations in the summary text — cite every outlet that added a distinct angle.

**CLARITY OVER BREVITY.** Themes can run a bit longer when the story warrants it. Sonnet tends to compress when more detail would actually help the reader understand the nuance. Don't sacrifice an important data point, a quoted argument, or context about what's at stake just to keep a theme short. A theme summary that runs 4-6 sentences with specific data and substantive analysis is better than a tight 2-sentence skim that loses the substance. The reader is reading a paid daily housing brief — they want depth, not headlines.

7. SINGLE TWEETS DO NOT MAKE SOCIAL THEMES. A lone tweet asking a question, making an observation, or endorsing someone else's argument is NOT a theme on its own — fold it into a conversation_roundup if topical, or drop it. (This rule applies to social-anchored themes only. News-anchored themes don't need cross-platform debate; a single substantial article is enough to anchor a theme.) For a Twitter or Bluesky thread to anchor a theme, you need at least one of: (a) multiple accounts engaging with the same question, (b) the tweet is responding to or commenting on a concrete news story or data release, or (c) the tweet itself has substantial replies/engagement.

8. ONE TOPIC PER THEME. Do NOT group unrelated threads or voices into one theme just to reduce count. If Winton ARK is talking about AI and photography employment, and Arindube is making a separate argument about AI asset valuations, those are TWO separate themes — not one. Only group threads together when they are genuinely part of the SAME conversation (people replying to each other, referencing each other's points). Three separate people talking about three separate things on the same broad topic is NOT one theme.

9. HEAT LEVELS: "viral" = 500+ comments across platforms, "high" = active debate with strong opinions, "medium" = noticeable discussion, "low" = a few mentions.

10. KEEP IT UNDER 30,000 CHARACTERS. The headlines section alone will be substantial — that's fine.

11. SKIP IRRELEVANT NOISE. Do not feature: Nigerian/international housing stories, memes about landlords, generic "economy is rigged" venting, partisan political rants with no economic substance.

11b. SPREAD ATTRIBUTION — soft guideline. Prefer diverse voices across themes; a single account anchoring 4+ themes feels like one person's feed, not the day's housing conversation. But it IS fine for the same account to appear in 2-3 themes when they made multiple substantively different arguments on different topics (e.g., @analyst-handle making a separate case on SB 79 transit upzoning AND on SB 1383 labor mandates AND on the builder's remedy — three distinct policy stories, three legitimate anchorings). The risk to avoid: same account cited in 2+ themes for what's essentially the same point reworded. Use editorial judgment, not a hard cap.

12. THREAD HANDLING (critical for themes): The input groups consecutive same-author tweets together — items prefixed with "  ↪" are CONTINUATIONS of a thread anchored by the previous "  [conv]" item from the same author. Treat the whole thread as ONE coherent argument, NOT as separate items. Twitter threads are how substantive analysis happens — picking one fragment ("Take SB 79 for example", "/13") strips the context that makes the analysis make sense. When a thread anchors a theme, your summary should reflect the thread's full arc, not just one tweet's claim. Cite the anchor tweet's URL. Never include the same author's thread spread across multiple themes — one author = one summary.

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
        - Different DATA SOURCE or different INSTITUTION publishing the data ([major outlet]/[bank]/[major outlet] on bond markets vs. [major outlet]/[data vendor]/[chief economist] on escrow)
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

    Failure pattern to AVOID: one welded paragraph that read "@analyst-handle's framing that the political moment has shifted to '2026: It's affordability, stupid.' Separately, [a city council member]'s newsletter announced [the mayor]'s executive budget includes an additional $5 billion for affordable housing… At the federal level, [a congressman] appeared on [outlet] to discuss the bipartisan 21st Century ROAD to Housing Act… Earlier this week, Saturday, @handle flagged that the House version of ROAD expands…" — that paragraph contains THREE buried disjunctive transitions ("Separately," "At the federal level," "Earlier this week,") that should have been three paragraph breaks. Required fix: four short paragraphs, one per topic cluster (election politics / NYC city policy / federal legislation / historical context on the federal bill). Each transition starts a new paragraph; none appears mid-sentence.

    Concrete failure to AVOID: "[a major outlet] calculates the Iran war has cost consumers $41.5bn extra in fuel since late February — $316 per household, with gas at $4.51 nationally. [Another major outlet] details a related squeeze: about 65% of escrow accounts are projected to be short in 2026 because of jumps in property taxes and homeowners insurance, with the average shortfall at $2,157..." — these are two stories welded with "a related squeeze." The Iran-war-→-fuel chain (commodity / bond market / Fed narrative) and the property-tax-and-insurance-→-escrow chain (insurer pricing / county assessments / mortgage servicing) share NO mechanism. Different data, different actors, different time window. REQUIRED FIX: paragraph break before the second outlet — and the new paragraph stands on its own without the "related" framing.

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

14. MANDATORY SECTIONS. Your JSON output MUST include these keys: conversation_pulse, conversation_themes, conversation_roundups, and paper_of_the_day (when a credible candidate exists; otherwise set paper_of_the_day to null). The substacker_takes, twitter_roundup, and ai_brief fields have been REMOVED — do NOT output them; if you do, they will be discarded. conversation_roundups may be an empty list ONLY if every notable topic of the day cleanly anchors on a single event (rare); usually expect 3-5 entries.

15. CONVERSATION_ROUNDUPS: This section is the home for topical discourse that does NOT have a single named, dated event trigger. The format is ONE coherent paragraph per topic, every claim hyperlinked inline via [text](url), no bullet points. Each summary is 3-6 sentences (max 110 words), observational rather than assertive — describing what the discourse is doing, not breaking news. Mix voices across sources: tweets, Bluesky posts, substacks, and news commentary. Aim for 3-5 roundups per day total, each on a DISTINCT topic — don't make three roundups all about supply. Same housing-economics scope as conversation_themes (housing, urbanism, affordability, demographics, mortgage/credit, labor and regional economies tied to housing). Examples of legitimate roundup topics: "Supply trends across the Sun Belt", "State-level rent-control proposals", "The brokerage / MLS antitrust war", "Insurance pricing in coastal markets", "Office-to-residential conversion progress". The same logical-honesty and citation rules from conversation_themes apply: no fake connective transitions, every attributed claim hyperlinked, named publications get publisher-domain URLs. Do NOT duplicate material already covered in conversation_themes — roundups are STRICTLY for topics that lack a single event anchor. If a roundup would be better as a news theme (i.e., you CAN complete "Today, X did Y" in 15 words), move it to conversation_themes instead.

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
    # Apply Twitter author blocklist at synthesis time. The same blocklist
    # gates new tweets from being collected (twitter_apify.py), but pre-existing
    # items already in the DB from earlier collection runs would otherwise leak
    # through. Match is case-insensitive on the @handle (without "@").
    try:
        from config import TWITTER_AUTHOR_BLOCKLIST as _TW_BL
        _bl_lower = {h.lower().lstrip("@") for h in _TW_BL}
        before = len(all_items)
        all_items = [
            i for i in all_items
            if not (
                i.get("source") == "twitter"
                and (i.get("author") or "").lower().lstrip("@") in _bl_lower
            )
        ]
        dropped = before - len(all_items)
        if dropped:
            logger.info(f"Blocklist filter dropped {dropped} Twitter items from blocklisted authors: {sorted(_bl_lower)}")
    except Exception as e:
        logger.warning(f"Twitter author blocklist filter skipped: {e}")

    # v5 trigger-type classifier: per-article Opus pass that drops items
    # classified as opinion / retrospective / recap / profile / analysis /
    # explainer BEFORE synthesis sees them. Catches op-ed patterns (e.g. the
    # City Journal "Good Cause Eviction" landlord piece in briefing #136) that
    # otherwise sneak into themes. Scope: news sources only (rss + gmail) —
    # tweets, bluesky, substacks, hackernews are inherently commentary and are
    # passed through. On any classifier failure (network, parse, API), the
    # affected items default to ACCEPT — we never silently lose items.
    try:
        before = len(all_items)
        all_items = _apply_trigger_filter(all_items, client=client)
        dropped = before - len(all_items)
        if dropped:
            logger.info(f"Trigger-type filter dropped {dropped} news items classified as opinion/retrospective/recap/profile/analysis/explainer")
    except Exception as e:
        logger.warning(f"Trigger-type classifier skipped (raised {e!r}); all items pass through to synthesis")

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

    # Pull the theme titles from the last 2 briefings so the model knows
    # what it already led with. Without this, recurring high-relevance items
    # (e.g. a user's own forwarded Pulse email containing yesterday's lead
    # theme) can cause the same story to be led two days in a row. Caught
    # on 2026-05-23: "Why Families Leave Cities" was lead theme #1 on
    # 5/22 and 5/23 because the 5/22 briefing was forwarded to a friend
    # and the reply thread re-entered the inbox as a high-relevance item.
    recent_briefing_themes = _fetch_recent_briefing_themes(conn, n=2)
    if recent_briefing_themes:
        logger.info(
            "Recent briefing themes loaded for anti-repetition guidance: "
            + ", ".join(f"#{b['id']} ({len(b['themes'])} themes)" for b in recent_briefing_themes)
        )

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

## Newsletters (INPUT — route into themes / roundups; no dedicated section)
These are newsletter articles (Substack + email newsletters). The dedicated substacker_takes output section has been removed (see rule 5) — DO NOT output a substacker_takes field. Instead: when a newsletter directly comments on a candidate event, weave it into that theme's commentary with inline [author](url) citation; when it's topical discussion without a single event anchor, route it into a conversation_roundup; skip the rest.

{_format_substacker_items(substacker_items)}

## Past 6 Days — HISTORICAL CONTEXT (week-long arc, NOT today)
These items are from the prior 6 days, NOT today. Use them to weave the longer arc into today's themes — when today's news touches a topic that's been discussed earlier in the week, cite the relevant historical voice with a clear time stamp ("Tuesday, [an economist](url) warned..."; "earlier this week, [an analyst](url) argued..."; "[a major outlet noted Friday](url)..."). Always make the time-stamp explicit in the prose so the reader knows what's fresh vs context. Do NOT use historical items as the anchor of a theme — today's items must anchor; historical context is connective tissue.

{_format_historical_items(historical_items)}

## RECENTLY LED THEMES — DO NOT RE-LEAD WITHOUT NEW NEWS
These are the conversation_themes from prior daily briefings. Treat this list as the "already-covered" pile. RULES:
1. Do NOT re-lead theme #1 with a story that already appeared as theme #1 (or as ANY top-3 theme) in the most recent prior briefing. The lead must be fresh.
2. A repeat theme is only acceptable when today brings genuinely new news that materially advances the story (e.g., a new court ruling, a new data release, a new statement from a key actor) — and in that case, the theme title must explicitly reflect the new development, and the summary must lead with the new news, not rehash prior coverage.
3. The user's own writing (Substack posts, forwarded emails, reply threads about a Pulse briefing) is NOT new news. If today's high-relevance items include forwards/replies of yesterday's briefing or comments on a recent Home Economics post, that does NOT justify repeating the theme. Move on to other news.
4. If a topic appeared in BOTH prior briefings already, deprioritize it heavily today — it has had its run unless there is a major new event.

{_format_recent_themes(recent_briefing_themes)}

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

        # twitter_roundup and ai_brief sections have been REMOVED (2026-06-02) —
        # the dedicated roundup output is gone. Discard any twitter_roundup /
        # ai_brief Sonnet emits, in case the model produces them despite the
        # prompt instructions.
        if "twitter_roundup" in briefing:
            briefing.pop("twitter_roundup", None)
        if "ai_brief" in briefing:
            briefing.pop("ai_brief", None)

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
