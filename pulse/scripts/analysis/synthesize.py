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
# Bumped to 4.8 same day — 4.8 dropped 2026-05-28 with 3x lower pricing
# ($5/M in, $25/M out vs 4.7's $15/$75) AND Anthropic's release notes claim
# meaningfully better reasoning + 4x less likely to let unsupported claims
# slip past. Directly relevant to our editorial-judgment use case.
# max_tokens=32768 is preserved: Opus's max output cap is the same 32K.
MODEL = "claude-opus-4-8"

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
                                # long threads (e.g. CSElmendorf's 14-tweet CA
                                # housing analyses) need all their tweets in
                                # Sonnet's view to make sense as a coherent
                                # argument. Sonnet collapses them into one
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

            # Commentary tag — flagged by the v2 trigger-type classifier.
            # Items marked `[COMMENTARY]` are social/essay items that pass the
            # filter but CANNOT anchor a theme on their own; the system prompt
            # tells Sonnet to cite them only as commentary inside themes
            # anchored on real news events.
            commentary_tag = "[COMMENTARY] " if item.get("_trigger_type") == "commentary" else ""

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
                    f"{commentary_tag}"
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
                    f"  {commentary_tag}{item['_source_display']}: {item['title'][:200]}\n"
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
    two themes' prose, where one author's stat gets paraphrased into two
    nominally-different themes that both link back to the same status URL.
    Pass B keeps the first mention, strips the second.
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


# ─── Social-anchored theme rejector ──────────────────────────────────────────
# Belt-and-braces enforcement of the EVENT-ANCHOR REQUIREMENT prompt rule.
# Sonnet sometimes promotes a chit-chat cluster of tweets to a full theme even
# when the rule explicitly forbids it (briefing #137, 2026-06-03: theme #1 was
# "Tokyo vs Sydney: Does Building Lower Prices?" anchored on
# "@DrCameronMurray posted Tokyo-vs-Sydney price chart; @michael_wiebe posted
# Auckland-vs-São Paulo upzoning puzzle"). Deterministic post-process: scan
# related_news_trigger + platforms for social-only anchors; move rejected
# themes to conversation_roundups (preserving the topic + a condensed summary)
# so the discourse isn't lost — it just doesn't get headline treatment.
_SOCIAL_VERB_PATTERN = re.compile(
    r"^\s*@\w+\s+(posted|tweeted|argued|flagged|noted|wrote|asked|wondered)\b",
    re.IGNORECASE,
)
_SOCIAL_LEAD_PATTERN = re.compile(
    r"^\s*(@\w+|\[?@\w+\]?)",
    re.IGNORECASE,
)
_SOCIAL_ONLY_PLATFORM_NAMES = {"twitter", "x", "bluesky", "hackernews", "hn"}

# Words that signal the trigger is referencing a real published event/report
# even when the platforms list happens to be twitter-only. Used to suppress
# Test 4 false-positives where the trigger names a real anchor (e.g.,
# "John Burns Research and Consulting migration analysis", "Realtor.com
# released its May 2026 report") but the only direct citation Sonnet attached
# is a tweet that flagged it. The keyword must appear OUTSIDE the @handle span.
_NEWS_ANCHOR_KEYWORDS = re.compile(
    r"\b(report|analysis|study|paper|release[ds]?|releasing|"
    r"published|announce[ds]?|announcing|passed|signed|filed|"
    r"ruling|ruled|decision|verdict|bill|legislation|"
    r"investigation|probe|opened|launched|"
    r"data|figures|index|results|earnings|"
    r"working paper|press release|whitepaper)\b",
    re.IGNORECASE,
)


def _trigger_names_news_anchor(trig: str) -> bool:
    """Detect whether the trigger references a real published event/document
    even if the platforms list is twitter-only. We strip out @handles first
    so a string like "@EricFinn flagged John Burns analysis" still trips on
    'analysis' — the handle is only the messenger, not the anchor.
    """
    if not trig:
        return False
    stripped = re.sub(r"@\w+", "", trig)
    return bool(_NEWS_ANCHOR_KEYWORDS.search(stripped))


def _theme_is_social_anchored(theme: dict) -> bool:
    """Return True if a conversation_themes entry fails the event-anchor gate.

    Tests (any one trips the rejection):
      1. trigger starts with an @handle (with or without bracketing)
      2. trigger has the "@X posted/argued/tweeted/etc." anchor verb pattern in
         the first 80 chars
      3. trigger has 2+ @handles — multi-tweet discourse cluster
      4. platforms list contains ONLY social sources (no news outlet, no RSS,
         no Substack — so the only attribution is tweets) AND the trigger
         doesn't reference an external news anchor (report/analysis/bill/etc.)
         by name. The AND-clause guards against false positives where the
         trigger does name a legitimate published event but Sonnet only
         attached a tweet that flagged it.
    """
    trig = (theme.get("related_news_trigger") or "").strip()
    if _SOCIAL_LEAD_PATTERN.match(trig):
        return True
    if _SOCIAL_VERB_PATTERN.search(trig[:80]):
        return True
    at_count = len(re.findall(r"@\w+", trig))
    if at_count >= 2:
        return True
    plats = theme.get("platforms") or []
    plat_names = {
        (p.get("name") or "").lower()
        for p in plats
        if isinstance(p, dict)
    }
    # Test 4: platforms field contains ONLY social sources (no news outlet).
    # Per user direction 2026-06-03, the previous "but trigger names a news
    # anchor" exemption is REMOVED — themes like the SF/John Burns migration
    # one were being preserved because their trigger contained the word
    # "analysis", but a consultancy publishing an analysis is content
    # marketing, not news. Real news themes will have an actual news outlet
    # in their platforms list (e.g., HousingWire, WSJ, Bloomberg), not just
    # Twitter. So platforms-only-social is now a hard reject signal.
    if plat_names and plat_names.issubset(_SOCIAL_ONLY_PLATFORM_NAMES):
        return True
    return False


def _condense_summary_for_roundup(summary: str, max_chars: int = 600) -> str:
    """Condense a theme summary to roundup length. Preserves inline markdown
    links by splitting on sentence boundaries (not by char count mid-sentence).
    """
    if not summary or not isinstance(summary, str):
        return summary or ""
    s = summary.strip()
    if len(s) <= max_chars:
        return s
    # Mask markdown links so dots inside URLs don't break sentence boundaries.
    link_re = re.compile(r"\[[^\]]+\]\([^)]+\)")
    placeholders: list[str] = []

    def _mask(m):
        placeholders.append(m.group(0))
        return f"\x00LINK{len(placeholders) - 1}\x00"

    masked = link_re.sub(_mask, s)
    boundary_re = re.compile(r"([.!?][\"'”’]?)(\s+)(?=[A-Z\x00]|$)")
    marked = boundary_re.sub(lambda m: m.group(1) + "\x01", masked)
    parts = marked.split("\x01")
    out = ""
    for p in parts:
        candidate = (out + " " + p).strip() if out else p.strip()
        if len(candidate) > max_chars and out:
            break
        out = candidate
        if len(out) >= max_chars:
            break
    for j, link in enumerate(placeholders):
        out = out.replace(f"\x00LINK{j}\x00", link)
    return out.strip() or s[:max_chars].rstrip()


def _reject_social_anchored_themes(briefing: dict) -> dict:
    """Deterministic enforcement of the EVENT-ANCHOR rule.

    For each conversation_themes entry, run _theme_is_social_anchored. Themes
    that fail are moved into conversation_roundups (preserving the topic name
    and a condensed version of the summary) so the discourse survives without
    headline treatment. Tracks the rejection count on briefing
    ['_social_anchor_rejections'] so the dashboard can surface it.
    """
    themes = briefing.get("conversation_themes") or []
    if not themes:
        briefing.setdefault("_social_anchor_rejections", 0)
        return briefing

    kept: list[dict] = []
    rejected: list[dict] = []
    for theme in themes:
        if _theme_is_social_anchored(theme):
            rejected.append(theme)
        else:
            kept.append(theme)

    if rejected:
        if briefing.get("conversation_roundups") is None:
            briefing["conversation_roundups"] = []
        for theme in rejected:
            topic_label = (theme.get("theme") or "").strip() or "Untitled discussion"
            condensed = _condense_summary_for_roundup(theme.get("summary") or "")
            briefing["conversation_roundups"].append({
                "topic": topic_label,
                "summary": condensed,
            })
            logger.info(
                f"Rejected social-anchored theme moved to roundups: "
                f"{theme.get('theme')!r}"
            )
        briefing["conversation_themes"] = kept

    briefing["_social_anchor_rejections"] = len(rejected)
    return briefing


# Regex matching the forbidden bridge phrases listed in the SYSTEM_PROMPT's
# FORBIDDEN BRIDGE WORDS rule. Each match marks the start of welded
# post-bridge content that must be peeled off the theme summary. Note: bare
# \bmeanwhile\b and \balso\b are deliberately excluded — they can legitimately
# continue an event-anchored narrative; only the welded forms ("meanwhile in
# the discourse", "also today") match here.
FORBIDDEN_BRIDGE_PATTERN = re.compile(
    # Two forms:
    # (a) BRIDGE PHRASES that are intrinsically welds. Match anywhere.
    # (b) ADVERBIAL "separately" only counts as a bridge when used as a
    #     sentence-leading conjunction with a comma after it ("Separately,").
    #     Adverbial use mid-sentence ("CT and NY are separately moving on...")
    #     is legitimate and should not trigger a strip.
    r"(?:"
    r"(?<![\w-])separately\s*[,:]|"                          # (b) Separately, / Separately:
    r"\bin a separate (?:development|thread|move|step|effort)\b|"
    r"\bon a separate (?:front|track|note)\b|"
    r"\bon another track\b|"
    r"\bapart from (?:this|the)\b|"
    r"\bmeanwhile in the discourse\b|"
    r"\balso today\b|"
    r"\bbeyond (?:that|the)\b|"
    r"\bin broader (?:\w+ )?discussion\b|"
    r"\bmore broadly\b|"
    r"\bthe discourse (?:pushed back|moved on|shifted|turned to)\b|"
    r"\bthe wider conversation\b|"
    r"\bon the other side of the country\b|"
    r"\bin a related but distinct\b"
    r")",
    re.IGNORECASE,
)


def _replace_welder_with_paragraph_break(text: str) -> tuple[str, int]:
    """Replace each FORBIDDEN_BRIDGE phrase + its enclosing connector clause
    with a paragraph break ("\\n\\n").

    Strategy:
      1. Find a bridge phrase match (e.g., "Separately,", "The discourse
         pushed back on local control:").
      2. Walk BACKWARD from the match to the previous sentence terminator
         (the period/question mark/exclamation that ends the previous
         distinct point). Everything between that boundary and the match
         is the welder clause's lead-in (usually nothing — the welder
         tends to start a new sentence).
      3. Walk FORWARD from the match to the first comma/colon/semicolon
         or sentence-terminator. That marks the END of the welder clause.
         Everything between START and END is the welder phrase + lead-in
         (e.g., "Separately, " or "The discourse pushed back on local
         control: ").
      4. Replace [START..END) with "\\n\\n". This deletes the welder
         language and inserts a visual paragraph break so the reader
         sees two distinct points instead of falsely-connected prose.

    Markdown links inside the text are masked first so periods inside
    URLs don't confuse the sentence-boundary walk.

    Returns (new_text, replacement_count). The loop has a safety cap of
    20 replacements to avoid pathological non-progress.
    """
    if not text or not FORBIDDEN_BRIDGE_PATTERN.search(text):
        return text, 0

    # Mask markdown links so dots inside URLs/anchors don't read as
    # sentence terminators.
    link_re = re.compile(r"\[[^\]]+\]\([^)]+\)")
    placeholders: list[str] = []

    def _mask(m):
        placeholders.append(m.group(0))
        return f"\x00LINK{len(placeholders)-1:04d}\x00"

    masked = link_re.sub(_mask, text)

    def _unmask(s: str) -> str:
        out = s
        for j, link in enumerate(placeholders):
            out = out.replace(f"\x00LINK{j:04d}\x00", link)
        return out

    out = masked
    count = 0
    while count < 20:
        m = FORBIDDEN_BRIDGE_PATTERN.search(out)
        if not m:
            break
        match_start = m.start()
        match_end = m.end()
        matched_text = m.group(0)

        # Walk BACKWARD: find the most recent sentence terminator (. ! ?)
        # before the match. If none, the welder is at the start of the text.
        clause_start = 0
        for term in (". ", ".\n", "? ", "?\n", "! ", "!\n"):
            idx = out.rfind(term, 0, match_start)
            if idx >= 0:
                end_of_term = idx + len(term)
                if end_of_term > clause_start:
                    clause_start = end_of_term

        # Walk FORWARD to find the end of the welder clause.
        # Two cases:
        #   (A) The regex already captured the connector + its terminator
        #       (e.g., "Separately,"). The matched text ends in a
        #       terminator character; we just skip any trailing whitespace.
        #   (B) The regex matched only the lead-in of the welder
        #       (e.g., "The discourse pushed back" — the rest of the
        #       clause "on local control:" still needs to be consumed).
        #       Walk forward for the nearest comma/colon/semicolon
        #       within a small window, bounded by the next sentence
        #       terminator (so we never consume real downstream content).
        if matched_text and matched_text[-1] in ",:;.!?":
            clause_end = match_end
        else:
            window_end = min(match_end + 100, len(out))
            next_sent_end = -1
            for term in (". ", ".\n", "? ", "?\n", "! ", "!\n"):
                idx = out.find(term, match_end)
                if idx >= 0 and (next_sent_end < 0 or idx < next_sent_end):
                    next_sent_end = idx
            if next_sent_end >= 0:
                window_end = min(window_end, next_sent_end)
            clause_end = -1
            for ch in (",", ":", ";"):
                idx = out.find(ch, match_end, window_end)
                if idx >= 0 and (clause_end < 0 or idx < clause_end):
                    clause_end = idx + 1
            if clause_end < 0:
                # No clause terminator found within window — drop only the
                # welder match itself, leave the downstream prose alone.
                clause_end = match_end
        # Skip trailing whitespace so the next paragraph starts cleanly.
        while clause_end < len(out) and out[clause_end] in " \t\n":
            clause_end += 1

        replacement = "\n\n" if clause_start > 0 else ""
        new_out = out[:clause_start] + replacement + out[clause_end:]
        if new_out == out:
            break
        out = new_out
        count += 1

    out = re.sub(r"[ \t]+\n\n", "\n\n", out)
    out = re.sub(r"\n\n[ \t]+", "\n\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return _unmask(out), count


def _strip_forbidden_bridges(briefing: dict) -> dict:
    """Deterministic enforcement of the BRIDGE WORDS rule.

    The synthesizer is instructed (SYSTEM_PROMPT rule on bridge words) to
    NEVER use connective phrases ("Separately,", "Meanwhile,", "The
    discourse pushed back on…", etc.) to weld two distinct points into
    one paragraph. When it slips and uses one anyway, this post-processor
    replaces the welder with a paragraph break ("\\n\\n") so the reader
    sees two distinct points instead of falsely-connected prose.

    Applies to conversation_themes[i].summary AND
    conversation_roundups[i].summary. Tracks
    briefing['_forbidden_bridge_strips'] for dashboard surfacing.
    """
    strip_count = 0

    for theme in briefing.get("conversation_themes") or []:
        summary = theme.get("summary") or ""
        if not summary:
            continue
        new_summary, n = _replace_welder_with_paragraph_break(summary)
        if n > 0:
            theme["summary"] = new_summary
            strip_count += n
            logger.info(
                f"Replaced {n} welder phrase(s) with paragraph breaks in "
                f"theme {theme.get('theme')!r}"
            )

    for roundup in briefing.get("conversation_roundups") or []:
        rsum = roundup.get("summary") or ""
        if not rsum:
            continue
        new_rsum, n = _replace_welder_with_paragraph_break(rsum)
        if n > 0:
            roundup["summary"] = new_rsum
            strip_count += n
            logger.info(
                f"Replaced {n} welder phrase(s) with paragraph breaks in "
                f"roundup {roundup.get('topic')!r}"
            )

    briefing["_forbidden_bridge_strips"] = strip_count
    return briefing


# ────────────────────────────────────────────────────────────────────────────
# Substack URL unwrapping.
#
# Substack delivery emails wrap article URLs in tracking redirects:
#   https://substack.com/redirect/2/<base64-of-{"e":"<actual-url>"}>
# where the "e" inner URL is typically a /subscribe page whose `next` query
# parameter holds the actual article URL. Synth gets the redirect URL,
# can't tell what the underlying article is, and won't surface clean
# citation links. Worse, the model often skips the citation entirely when
# the URL looks ugly.
#
# This unwraps such URLs at synthesis time so the model sees + cites the
# real article URL.
# ────────────────────────────────────────────────────────────────────────────

import base64 as _base64_su
import urllib.parse as _urlparse_su

_SUBSTACK_REDIRECT_RE = re.compile(
    r"^https?://substack\.com/redirect/\d+/([A-Za-z0-9+/=_-]+)"
)
_URL_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "r", "next", "s", "publication_id", "post_id", "triedRedirect",
    "isFreemail", "token",
}


def _strip_tracking_params(url: str) -> str:
    try:
        parts = _urlparse_su.urlsplit(url)
        q = [
            (k, v) for k, v in _urlparse_su.parse_qsl(parts.query)
            if k not in _URL_TRACKING_PARAMS
        ]
        new_q = _urlparse_su.urlencode(q)
        return _urlparse_su.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_q, "")
        )
    except Exception:
        return url


def unwrap_substack_redirect(url: str) -> str:
    """Decode a Substack tracking-redirect URL to the underlying article
    URL. Returns the input URL unchanged when it isn't a Substack redirect
    or when decoding fails."""
    if not url:
        return url
    m = _SUBSTACK_REDIRECT_RE.match(url)
    if not m:
        return url
    blob = m.group(1)
    b = blob + "=" * ((4 - len(blob) % 4) % 4)
    try:
        decoded = _base64_su.b64decode(
            b, altchars=b"-_", validate=False
        ).decode("utf-8", "replace")
    except Exception:
        return url
    # The decoded blob is JSON followed by Substack's JWT signature
    # (".<sig>"). Truncate at the first closing brace and parse.
    end = decoded.find("}")
    if end < 0:
        return url
    try:
        data = json.loads(decoded[: end + 1])
    except Exception:
        return url
    inner = (data.get("e") or "").strip()
    if not inner.startswith("http"):
        return url
    # Prefer the `next` param when present — that's the actual article
    # URL the subscribe wall would redirect to after sign-in.
    try:
        inner_parts = _urlparse_su.urlsplit(inner)
        qs = dict(_urlparse_su.parse_qsl(inner_parts.query))
        nxt = (qs.get("next") or "").strip()
        if nxt.startswith("http"):
            return _strip_tracking_params(nxt)
    except Exception:
        pass
    return _strip_tracking_params(inner)


def unwrap_item_urls(items: list[dict]) -> int:
    """Walk a list of item dicts, unwrap any Substack-redirect URLs in
    place. Returns the count of URLs that were changed."""
    changed = 0
    for it in items:
        u = it.get("url") or ""
        new_u = unwrap_substack_redirect(u)
        if new_u != u:
            it["url"] = new_u
            changed += 1
    return changed


# Sentence-leading time-shift phrases that should start a new paragraph.
_TIME_SHIFT_LEAD_RE = re.compile(
    r"^\s*(?:"
    r"Earlier this week|Earlier in the week|Earlier this month|"
    r"Last week|Last month|This morning|This afternoon|This week|"
    r"Yesterday|This evening|Last night|Tonight|"
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"In (?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)"
    r")\s*,",
    re.IGNORECASE,
)


def _enforce_paragraph_breaks(briefing: dict) -> dict:
    """Insert `\\n\\n` paragraph breaks into theme/roundup summaries so
    prose reads as several short paragraphs instead of one wall of text.

    Rules (applied at every sentence boundary, in order):
      1. Break BEFORE a sentence that starts with a markdown link
         `[text](url)` when the current paragraph already has 2+
         sentences. (A new linked voice → new paragraph.)
      2. Break BEFORE a sentence whose leading clause is a time-shift
         phrase ("Earlier this week,", "Monday,", etc.).
      3. Hard cap: a paragraph never runs longer than 3 sentences. If
         we'd start a 4th sentence in the same paragraph, break first.

    Respects existing `\\n\\n` breaks in the source text.

    The user explicitly asked for short, readable paragraphs (2026-06-04
    feedback: "the news paragraphs never have para breaks…why? it should
    break them apart like a natural writer"). The prompt-rule equivalent
    of this (added the previous day) is being ignored by the model —
    this post-processor enforces the structure deterministically.
    """
    inserts = 0

    def _break_long_paragraph(text: str) -> tuple[str, int]:
        # _split_sentences_for_validation masks markdown links so dots in
        # URLs/anchor text don't cause spurious sentence boundaries.
        sentences = _split_sentences_for_validation(text)
        if len(sentences) <= 1:
            return text, 0
        out_paragraphs: list[str] = []
        current: list[str] = []
        local_inserts = 0
        for i, raw in enumerate(sentences):
            sent = raw.strip()
            if not sent:
                continue
            if current:
                starts_with_link = sent.startswith("[")
                has_time_shift = bool(_TIME_SHIFT_LEAD_RE.match(sent))
                at_ceiling = len(current) >= 3
                if (
                    (starts_with_link and len(current) >= 2)
                    or has_time_shift
                    or at_ceiling
                ):
                    out_paragraphs.append(" ".join(current))
                    current = []
                    local_inserts += 1
            current.append(sent)
        if current:
            out_paragraphs.append(" ".join(current))
        return "\n\n".join(out_paragraphs), local_inserts

    def _process(summary: str) -> tuple[str, int]:
        if not summary or len(summary) < 80:
            return summary, 0
        paragraphs = re.split(r"\n{2,}", summary)
        rebuilt: list[str] = []
        local_inserts = 0
        for para in paragraphs:
            new_para, n = _break_long_paragraph(para.strip())
            local_inserts += n
            rebuilt.append(new_para)
        return "\n\n".join(rebuilt), local_inserts

    for theme in briefing.get("conversation_themes") or []:
        s = theme.get("summary") or ""
        new_s, n = _process(s)
        if n > 0:
            theme["summary"] = new_s
            inserts += n
            logger.info(
                f"inserted {n} paragraph break(s) in theme "
                f"{theme.get('theme')!r}"
            )

    for roundup in briefing.get("conversation_roundups") or []:
        s = roundup.get("summary") or ""
        new_s, n = _process(s)
        if n > 0:
            roundup["summary"] = new_s
            inserts += n
            logger.info(
                f"inserted {n} paragraph break(s) in roundup "
                f"{roundup.get('topic')!r}"
            )

    briefing["_paragraph_breaks_inserted"] = inserts
    return briefing


# ────────────────────────────────────────────────────────────────────────────
# Auto-link bare @handles in theme/roundup prose.
#
# The SYSTEM_PROMPT requires every cited voice — including historical
# attributions like "@mikesimonsen flagged Monday" — to be wrapped in a
# markdown link. The model still occasionally emits a bare @handle (no
# enclosing link), especially for historical-context citations.
#
# Going-forward rule (user directive 2026-06-03): if a handle is mentioned,
# it MUST be linked, and the link should point to the specific thing being
# referenced. This post-processor scans summaries for bare @handles, looks
# the handle up in the items corpus (most recent tweet from that author),
# and injects a markdown link in place. Falls back to the author's profile
# URL when the corpus has no matching items.
# ────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────
# Cited-sources breakdown for the email header.
# ────────────────────────────────────────────────────────────────────────────

# Small lookup of common publication domains → display names. Anything
# not in this map shows as the bare domain root. Extend as needed.
_DOMAIN_PUBLICATION = {
    "nytimes.com": "NYT",
    "wsj.com": "WSJ",
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "washingtonpost.com": "Washington Post",
    "theglobeandmail.com": "Globe and Mail",
    "reuters.com": "Reuters",
    "cnbc.com": "CNBC",
    "politico.com": "Politico",
    "axios.com": "Axios",
    "vox.com": "Vox",
    "theatlantic.com": "The Atlantic",
    "city-journal.org": "City Journal",
    "nber.org": "NBER",
    "aei.org": "AEI",
    "newyorkfed.org": "NY Fed",
    "federalreserve.gov": "Federal Reserve",
    "housingwire.com": "HousingWire",
    "brickunderground.com": "Brick Underground",
    "costar.com": "CoStar",
    "redfin.com": "Redfin",
    "zillow.com": "Zillow",
    "realtor.com": "Realtor",
    "altosresearch.com": "Altos Research",
    "calculatedrisk.com": "Calculated Risk",
    "mansionglobal.com": "Mansion Global",
    "marketwatch.com": "MarketWatch",
    "ap.org": "AP",
    "apnews.com": "AP",
    "npr.org": "NPR",
    "bbc.com": "BBC",
    "guardian.co.uk": "The Guardian",
    "theguardian.com": "The Guardian",
    "economist.com": "The Economist",
    "semafor.com": "Semafor",
    "heatmap.news": "Heatmap",
    "noahpinion.blog": "Noahpinion",
    "stratechery.com": "Stratechery",
    "nakedcapitalism.com": "Naked Capitalism",
}


def _domain_root(url: str) -> str:
    """Strip subdomains down to root (e.g.
    'libertystreeteconomics.newyorkfed.org' → 'newyorkfed.org'). Handles
    common two-part TLDs (.co.uk) heuristically."""
    try:
        d = urlparse(url).netloc.lower().removeprefix("www.")
        parts = d.split(".")
        if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "ac"):
            return ".".join(parts[-3:])
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return d
    except Exception:
        return ""


def _classify_url_only(url: str) -> tuple[str, str]:
    """URL-only fallback when an item isn't in the corpus. Returns
    (source_type, display_name)."""
    try:
        d = urlparse(url).netloc.lower().removeprefix("www.")
        path = urlparse(url).path or ""
    except Exception:
        return "web", "?"
    if "twitter.com" in d or d == "x.com" or d.endswith(".x.com"):
        parts = path.lstrip("/").split("/")
        handle = parts[0] if parts and parts[0] else "?"
        return "twitter", f"@{handle}"
    if "bsky.app" in d or "bsky.social" in d:
        parts = path.lstrip("/").split("/")
        if parts and parts[0] == "profile" and len(parts) > 1:
            return "bluesky", f"@{parts[1]}"
        return "bluesky", f"@{parts[0]}" if parts and parts[0] else "@?"
    if d.endswith(".substack.com"):
        sub = d[:-len(".substack.com")]
        return "substack", sub.capitalize()
    if "reddit.com" in d:
        m = re.match(r"/r/([^/]+)", path)
        return "reddit", f"r/{m.group(1)}" if m else "Reddit"
    if "news.ycombinator.com" in d:
        return "hackernews", "HN"
    root = _domain_root(url)
    return "web", _DOMAIN_PUBLICATION.get(root, root)


def _compute_cited_sources(briefing: dict, conn: sqlite3.Connection) -> dict:
    """Walk every markdown-linked URL in the briefing prose + theme
    platforms[], classify each by source-type and display name, and
    return grouped counts:
        { "rss":      { "HousingWire": 3, "NY Fed": 2, ... },
          "twitter":  { "@cayimby": 2, "@dbroockman": 1, ... },
          ... }

    URLs are looked up in the items table first (gives source-type +
    feed_name/author); fall back to URL-host classification when an
    item isn't in the corpus.
    """
    cited_url_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    urls: list[str] = []
    urls.extend(cited_url_re.findall(briefing.get("conversation_pulse", "") or ""))
    for t in briefing.get("conversation_themes", []) or []:
        urls.extend(cited_url_re.findall(t.get("summary", "") or ""))
        for p in t.get("platforms", []) or []:
            u = (p.get("url") or "").strip()
            if u:
                urls.append(u)
    for r in briefing.get("conversation_roundups", []) or []:
        urls.extend(cited_url_re.findall(r.get("summary", "") or ""))
    paper = briefing.get("paper_of_the_day") or {}
    if isinstance(paper, dict):
        u = (paper.get("url") or "").strip()
        if u:
            urls.append(u)

    grouped: dict[str, dict[str, int]] = {}
    for url in urls:
        if not url:
            continue
        try:
            row = conn.execute(
                "SELECT source, author, feed_name FROM items "
                "WHERE url = ? LIMIT 1",
                (url,),
            ).fetchone()
        except Exception:
            row = None

        if row:
            src_type = (row["source"] or "web").lower()
            if src_type in ("twitter", "bluesky"):
                display = (row["author"] or "").strip()
                if display and not display.startswith("@"):
                    display = "@" + display
                if not display:
                    _, display = _classify_url_only(url)
            elif src_type in ("rss", "substack", "gmail"):
                display = (row["feed_name"] or "").strip()
                if not display:
                    _, display = _classify_url_only(url)
            elif src_type == "reddit":
                display = (row["feed_name"] or "").strip()
                if not display:
                    _, display = _classify_url_only(url)
            elif src_type == "hackernews":
                display = "HN"
            else:
                _, display = _classify_url_only(url)
                src_type = "web"
        else:
            src_type, display = _classify_url_only(url)

        if not display:
            display = "?"
        grouped.setdefault(src_type, {})
        grouped[src_type][display] = grouped[src_type].get(display, 0) + 1

    return grouped


_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9_])(@[A-Za-z0-9_]{2,20})\b")
_MD_LINK_SPAN_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")

# Weekday names that signal a time-stamped historical attribution. When a
# bare @handle is followed by one of these within ~40 chars, we try to
# locate a tweet from that author on that weekday, not just the most recent.
_WEEKDAY_HINTS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _lookup_handle_url(
    conn: sqlite3.Connection, handle: str, weekday_hint: Optional[int] = None
) -> Optional[str]:
    """Return the best-fit corpus URL for a @handle, or None if no match.

    Strategy:
      1. If weekday_hint is provided, prefer the most recent tweet from
         that author published on that weekday.
      2. Otherwise, return the most recent tweet from that author.
      3. If the author has zero items in the corpus, return None — the
         caller leaves the bare @handle in place (user directive: do NOT
         fall back to a profile URL; if we can't match a specific cited
         item, don't manufacture a link).
    """
    handle_norm = handle.lower().lstrip("@")
    if weekday_hint is not None:
        rows = conn.execute(
            "SELECT url, published_at FROM items "
            "WHERE source='twitter' "
            "  AND lower(author) IN (?, ?) "
            "  AND url IS NOT NULL AND url != '' "
            "ORDER BY published_at DESC LIMIT 30",
            (f"@{handle_norm}", handle_norm),
        ).fetchall()
        for r in rows:
            try:
                dt = r[1]
                if not dt:
                    continue
                day = datetime.fromisoformat(
                    dt.replace("Z", "+00:00")
                ).weekday()
                if day == weekday_hint:
                    return r[0]
            except Exception:
                continue
    row = conn.execute(
        "SELECT url FROM items "
        "WHERE source='twitter' "
        "  AND lower(author) IN (?, ?) "
        "  AND url IS NOT NULL AND url != '' "
        "ORDER BY published_at DESC LIMIT 1",
        (f"@{handle_norm}", handle_norm),
    ).fetchone()
    if row and row[0]:
        return row[0]
    return None


def _autolink_bare_handles(
    briefing: dict, conn: sqlite3.Connection
) -> dict:
    """Wrap bare @handles in theme/roundup summaries with markdown links.

    A bare @handle is one that does NOT fall inside an existing
    [text](url) markdown link span. For each, look up the most recent
    matching tweet from the author in the items corpus and inject
    [@handle](url). When a weekday hint immediately follows the handle
    ("@mikesimonsen flagged Monday"), prefer a tweet from that weekday.

    Tracks briefing['_autolinked_handles'] for surveillance.
    """
    linked_count = 0

    def _link_span_ranges(text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in _MD_LINK_SPAN_RE.finditer(text)]

    def _is_inside_link(pos: int, ranges: list[tuple[int, int]]) -> bool:
        for s, e in ranges:
            if s <= pos < e:
                return True
        return False

    def _weekday_near(text: str, end: int) -> Optional[int]:
        # Look at up to 50 chars AFTER the handle for a weekday hint.
        window = text[end : end + 50].lower()
        # Match standalone weekday word
        wm = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", window)
        if wm:
            return _WEEKDAY_HINTS[wm.group(1)]
        return None

    def _process(summary: str) -> tuple[str, int]:
        if not summary or "@" not in summary:
            return summary, 0
        link_ranges = _link_span_ranges(summary)
        bare: list[tuple[int, int, str]] = []
        for m in _HANDLE_RE.finditer(summary):
            if _is_inside_link(m.start(), link_ranges):
                continue
            bare.append((m.start(), m.end(), m.group(1)))
        if not bare:
            return summary, 0
        out = summary
        n = 0
        for start, end, handle in reversed(bare):
            weekday = _weekday_near(out, end)
            url = _lookup_handle_url(conn, handle, weekday)
            if not url:
                continue
            replacement = f"[{handle}]({url})"
            out = out[:start] + replacement + out[end:]
            n += 1
        return out, n

    for theme in briefing.get("conversation_themes") or []:
        s = theme.get("summary", "")
        new_s, n = _process(s)
        if n > 0:
            theme["summary"] = new_s
            linked_count += n
            logger.info(
                f"Auto-linked {n} bare handle(s) in theme {theme.get('theme')!r}"
            )

    for roundup in briefing.get("conversation_roundups") or []:
        s = roundup.get("summary", "")
        new_s, n = _process(s)
        if n > 0:
            roundup["summary"] = new_s
            linked_count += n
            logger.info(
                f"Auto-linked {n} bare handle(s) in roundup {roundup.get('topic')!r}"
            )

    briefing["_autolinked_handles"] = linked_count
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

CRITICAL — COMMENTARY ITEMS:
Items prefixed with `[COMMENTARY]` in the input are social posts or essay-style content (tweets, Bluesky posts, HN threads, individual substack essays) that a pre-synthesis classifier has flagged as discourse on existing news, NOT news events themselves. They CANNOT anchor a conversation_themes entry. They can ONLY be cited as commentary INSIDE a theme whose anchor is a separate non-[COMMENTARY] item — an action_event, official_data release, court ruling, investigation, or breaking news story. If you find yourself wanting to build a theme entirely from [COMMENTARY] items (e.g., three tweets about Tokyo housing without any underlying news article from today), DROP IT — move that discourse to conversation_roundups instead. A theme anchored on a [COMMENTARY] item is a structural lie about what the day's news actually was; the post-processor will reject it.

## Output Format

Return a JSON object:

{
  "date": "YYYY-MM-DD",

  "conversation_pulse": "4-8 sentences (longer is fine when it adds substance — don't compress for brevity): what is the dominant debate right now and where does opinion split? Be concrete and factual — name the data points, name the people. NO filler phrases like 'the mood is cautious' or 'markets are watching closely'. State what happened and who disagrees about what.",

  "conversation_themes": [
    {
      "theme": "Short label (5-8 words max)",
      "summary": "Factual summary with inline markdown links. When you mention a specific tweet, paper, article, or Substack post, link the relevant phrase using [text](url). Example: '[one economist argues](url) that housing starts will rebound, while [a working paper](url) finds national labs generate regional development through knowledge spillovers.' Lead with specific claims, data, or arguments. Name authors when relevant but do NOT preferentially cite the same handful of accounts across themes — spread attribution across the full set of voices in the input. No meta-commentary, no filler.",
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
    "publication": "<publication identifier — working paper series + number, peer-reviewed journal name, or institutional report title>",
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

4b. CITATION ANCHOR MUST MATCH URL. When you write "[ENTITY did/said/projects/argues X](url)", the URL must point to that entity's own post, paper, or tweet — not to a secondary source that happens to mention the entity. If you only have the secondary source (e.g., you read about a primary entity's research in a secondary essay), either:
  (a) attribute it honestly: "[per [secondary outlet]](secondary-url), [primary entity] projects X" so the link's target matches what the link says, OR
  (b) drop the attribution entirely: state the underlying fact without naming a source
What you MUST NOT do: "[[primary entity] projects X](secondary-url)" — that makes the reader think they're clicking through to the primary entity when they're really going to a secondary outlet. This is a form of misattribution. Every anchor text + URL pairing must be internally consistent.

4c. ATTRIBUTED QUOTES MUST BE HYPERLINKED — AND THE LINK MUST POINT TO WHERE THE CLAIM ORIGINATED. Any time you attribute a direct quote, statement, or specific data point to a named person or organization, the attribution MUST be wrapped in a hyperlink. Plain-text attribution ("[chief economist] said affordability has improved") with no link is unacceptable — the reader has no way to verify or read more.

WHICH URL TO LINK depends on where the claim originated:
  - **Twitter/Bluesky tweet** → link the tweet's URL. The tweet IS the source. Do NOT link to an article that summarizes the tweet; link directly to the tweet itself.
  - **Article quote** (person quoted IN an article) → link the article. The article IS the source — the person didn't publish their statement themselves; they spoke to the reporter.
  - **Substack / newsletter / column** → link the post itself.
  - **Working paper / report** → link the paper's own URL (PDF, abstract page, or publisher landing).

Examples by source type:
  (a) Tweet attribution: "[@analyst-handle noted](https://x.com/analyst-handle/status/...) that existing-home sales were the lowest YTD pace since 2009" — link the TWEET.
  (b) Article quote: "[[trade-association] chief economist told [outlet]](https://[outlet-domain]/...) that affordability has improved" — link the ARTICLE.
  (c) Anchor on the verb: "[chief economist] [said](https://[outlet-domain]/...) affordability has improved" — link the article via the verb.
  (d) Anchor on the publication: "[per [outlet]](https://[outlet-domain]/...), [chief economist] said affordability has improved" — link the article via the publication.

@HANDLE ATTRIBUTIONS ALWAYS NEED A LINK. The pattern "per @handle" / "@handle noted" / "@handle argued" / "@handle flagged" written as plain text (no markdown link wrapping any part of it) is the most common 4c violation. If you reference a Twitter or Bluesky handle by their data point or argument, the tweet URL MUST be linked — typically the cleanest form is to wrap the verb: "@analyst-handle [noted](tweet_url) that...". If you don't have the tweet URL in your corpus, drop the attribution entirely and state the fact directly without naming the handle.

LINK THE EXAMINING SOURCE, NOT THE UNDERLYING SOURCE IT DRAWS ON. When you write a construction like "X's newsletter examined Y," "X's article covered Y," "X analyzed Y," "X's piece argued Y," the link MUST point to X's piece — the SUBJECT of the sentence — not to Y or to whatever underlying material X drew on. The reader expects to click through to X's analysis, not to the raw source X used.

Failure example: "[Substack author]'s newsletter examined [whether home prices and social media are the reasons we're having even fewer babies]([outlet]-url), drawing on [the outlet]'s coverage." — The anchor points to the underlying outlet, but the sentence is about the newsletter. Reader clicks expecting the newsletter and lands at the outlet instead.

Required fix: "[Substack author]'s newsletter [examined]([newsletter]-url) whether home prices and social media are the reasons we're having even fewer babies, drawing on [the outlet's coverage]([outlet]-url)." — Two links: one to the newsletter (the subject doing the examining), one to the outlet (the underlying source). If you only have ONE URL available, it MUST be the URL of the entity that's the subject of the sentence. Drop the secondary reference if you can't link it; never substitute the secondary URL for the primary.

This rule generalizes to any "X did/examined/argued Y" construction where X is named and X's content is the subject. Link X's content. The exception is when the sentence frames it as "according to/per [secondary source], X said Y" — in that case the link is on the secondary (because that's where the reader will read X's quoted statement).

What you MUST NOT do: write "[economist] said X" or "@analyst-handle noted X" as plain text. If you cited [outlet] two sentences earlier, that link does NOT cover a later separate [economist] attribution — each attributed claim needs its own link. This rule applies to direct quotes, paraphrased positions, and specific data points (e.g., "[an investment bank] tracked Q2 at 1.6%" — link the bank's note; "the median price hit $417,700" — only an unattributed fact like this can stand without a link).

VAGUE REFERENCES STILL REQUIRE LINKS. Any reference to a specific paper, report, study, dataset, or analysis MUST be hyperlinked — even when you do NOT name it explicitly. Phrasings like "a working paper examined X," "an investment-bank note argued Y," "federal data showed Z," "research from a think tank found W" all count: the reader has no other handle to find the source if you don't link it. The link is MORE important when the reference is vague, not less. Failure example: "Yesterday, a working paper flagged in the feed examined gender gaps" — with no link, the reader has no way to find which paper. Correct: "Yesterday, [a working paper](url) flagged in the feed examined gender gaps." If the source isn't in your corpus, don't reference it at all — drop the attribution and make the claim directly: "Gender gaps in education are widening" rather than "[unsourced] research showed gender gaps are widening."

NO PRONOUN-CHAINED ATTRIBUTION ACROSS SENTENCE BOUNDARIES. Pronouns ("He", "She", "They", "The author", "The same account") may ONLY refer back to the source named in the same sentence. The instant you start a new sentence with a new claim from a DIFFERENT source, you MUST re-introduce that source by name/handle with its own fresh hyperlink. Chaining a pronoun across a sentence break to a claim by a different author is misattribution — exactly as bad as putting the wrong handle in the link.

Failure example: "[@handle1 offered](url) a softer read: 'Another solid week for pending home sales' — suggesting the data may be more mixed than the headline implies. He posted a separate claim — there is NOT a housing shortage, there is a housing MISMATCH, with 148M housing units for only 134M households." — The "He" reads as @handle1, but the housing-mismatch claim was actually @handle2's tweet. The pronoun bridged across two different authors, fabricating an attribution.

Required fix: "[@handle1 offered](handle1-url) a softer read: 'Another solid week for pending home sales.' Separately, [@handle2 argued](handle2-url) America has a housing mismatch rather than a shortage — 148M housing units for only 134M households, with the surplus skewed toward luxury rather than starter homes." — Each author introduced explicitly, each with their own link.

If you can't link the second claim to its actual source URL, DROP the attribution entirely and state the underlying fact without naming any author. Never reuse the prior sentence's source via pronoun to cover a claim from a different source. When in doubt, repeat the handle/name — verbosity is always preferable to misattribution.

NO FLOATING EDITORIAL COMMENTARY. Sentences with no inline link and no clear source attribution are forbidden inside theme summaries. Examples of what you MUST NOT write: "Read that again." "Worse than 2008." "Yet sellers are still pricing homes like it's 2021 with 3% rates." "The signal is clear." "This is meaningful." These are your own voice editorializing — even when they feel like natural connective tissue between cited claims. Every sentence in a theme summary must either (a) wrap a cited claim in a hyperlink, or (b) be neutral framing/scene-setting derived directly from the cited material with no new claim of its own. If you find yourself adding a punchy unsourced sentence to make the prose flow better, delete it.

EVERY ATTRIBUTION MUST BE LINKED — @HANDLES AND PUBLICATIONS ALIKE (HARD GATE). Any time you cite a source — whether a Twitter/Bluesky handle ("@handle1 noted X"), a named publication ("[Outlet A] reported Y", "[Outlet B] flagged Z", "[Outlet C] covered W"), or a historical reference ("[handle] flagged Monday", "earlier this week [outlet] argued") — the attribution MUST be wrapped in a markdown link `[text](url)` pointing to the SPECIFIC article, tweet, or post being referenced. A bare attribution with no link is a HARD violation.

Failure mode you MUST avoid: linking ONE reference to a publication early in a paragraph and then dropping the link on the SECOND, THIRD, etc. references to the same publication — even when those later references are about different articles/facts. Each fact gets its own link. Linking one story from a publication does NOT give you a free pass on a later separate story from the same publication — those are two different articles and BOTH must be linked.

Concrete failure example to avoid: A theme paragraph linked "[an outlet reported](url-A) ...[Story 1]..." then later in the same paragraph wrote "[The outlet] reported [Story 2] details with NO link on that second attribution." Required fix: "[The outlet reported](url-of-Story-2-article) [Story 2 details]..." — a SECOND, DIFFERENT URL for the second article. If you do not have a URL for the second cited article, DROP the attribution entirely and state the underlying fact directly — never write a bare unlinked "[outlet] reported..." just to add a source name.

The same rule covers historical attributions: "Monday, [X warned](url)..." / "Tuesday, [an analyst flagged](url)..." / "earlier this week, [Y argued](url)..." MUST carry the link to that specific past post/article. If you reference "what someone said last week" or any past event, the actual post/article being referenced MUST be linked inline. NEVER write a historical attribution without the link — the reader cannot verify what you're paraphrasing otherwise.

Decision rule: if you find yourself typing the name of an outlet or a handle without an accompanying URL, STOP — either (a) supply the specific URL for that specific fact, or (b) delete the attribution and state the fact without naming a source. Those are the only two options. There is no third path where "I'll mention the source without a link because I already linked them earlier" — that's the violation.

BREAK INTO PARAGRAPHS LIKE A HUMAN WRITES (HARD GATE — TOP COMPLAINT 2026-06-03). Theme and roundup summaries are NOT walls of text. They MUST be broken into multiple short paragraphs separated by `\\n\\n` whenever the prose covers more than one distinct point, voice, or sub-topic.

Concrete rules for paragraph breaks inside conversation_themes[i].summary AND conversation_roundups[i].summary:

  (1) NO PARAGRAPH MAY RUN LONGER THAN ~3 SENTENCES OR ~75 WORDS. If your draft paragraph has 4+ sentences, find the natural break point and split. Each paragraph should fit on a phone screen without scrolling.

  (2) A NEW VOICE = A NEW PARAGRAPH. Every time you introduce a new author/handle/outlet that wasn't the subject of the previous sentence, start a new paragraph. Example: a paragraph ending with "[@handle1 reported](url) the bill passed" and the next claim citing @handle2 — `\\n\\n` between them, then `[@handle2 noted](url) ...`.

  (3) A NEW DATA POINT FROM A NEW SOURCE = A NEW PARAGRAPH. Even when the topic is the same (e.g., insurance pricing), if one paragraph cites an industry trade outlet and the next cites a regional Federal Reserve bank, those are two paragraphs. The shared topic does not make them one paragraph.

  (4) A TIME-SHIFT = A NEW PARAGRAPH. "Earlier this week," / "Monday," / "Tuesday," — anything moving between today's lede and historical context starts a new paragraph. DON'T weld today's news and earlier-this-week context into one paragraph.

  (5) A NEW LEVEL OF GOVERNMENT, NEW MECHANISM, OR NEW MARKET = A NEW PARAGRAPH. Federal → state → local: separate paragraphs. Supply story → demand story: separate paragraphs. National data → regional data: separate paragraphs.

  (6) THE BLANK LINE IS THE TRANSITION. Do NOT write a transitional word at the start of the new paragraph (no "Separately,", "Meanwhile,", "On a separate track,", etc.). The blank line between paragraphs is itself the transition. Start each new paragraph with the substantive claim — a name, a number, a verb.

Write the way a thoughtful human journalist writes for a daily newsletter: short paragraphs, each one a discrete beat. A theme/roundup summary with 3-5 voices or sub-points should be 3-5 short paragraphs, not one mega-paragraph.

CONCRETE FAILURE EXAMPLE (the kind of wall-of-text to avoid — DO NOT DO THIS):

  "[State]'s housing pain is broadening. [[Outlet A] reported](url1) [Builder A] slashed margins to clear inventory while [[Outlet B] flagged](url2) [Builder B] is opening a new community despite the slowdown — a bet on long-term migration even as near-term sales weaken. [@handle1 noted](url3) [State]'s existing-home inventory hit a decade high. [@handle2 warned](url4) that [metro] rents are now falling year-over-year, the first metro to flip negative since 2022. Earlier this week, [[Outlet C] covered](url5) a trade-association settlement timeline pushing implementation to Q4. [@handle3 argued](url6) the bigger picture is overbuilding in coastal markets while the interior lags."

REQUIRED FIX (same content, broken like a human writes):

  "[State]'s housing pain is broadening. [[Outlet A] reported](url1) [Builder A] slashed margins to clear inventory, while [[Outlet B] flagged](url2) [Builder B] opening a new community — a bet on long-term migration even as near-term sales weaken.

  [@handle1 noted](url3) [State]'s existing-home inventory hit a decade high. [@handle2 warned](url4) that [metro] rents are now falling year-over-year, the first metro to flip negative since 2022.

  Earlier this week, [[Outlet C] covered](url5) the revised settlement timeline pushing implementation to Q4.

  [@handle3 argued](url6) the bigger picture is overbuilding in coastal markets while the interior lags."

That fix took ONE mega-paragraph and produced four short paragraphs, each a discrete beat: builder strategy / inventory & rent data / settlement-timeline news / the bigger-picture take. Each one a discrete beat on the same theme. No transition words; the blank lines do the work.

Apply this rule to EVERY theme summary and EVERY roundup summary. If your draft has any paragraph running 4+ sentences, you have not yet finished writing it.

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

4e. NAMED PUBLICATIONS REQUIRE PROVENANCE PRESERVATION. When you reference a NAMED PUBLICATION by its title — institutional reports, monthly monitors, press releases, working papers, federal statistical releases, central-bank publications, congressional budget outlooks, etc. — the URL MUST be on the publisher's own domain. If you only have a secondary source covering it, your prose MUST do BOTH:
  (a) preserve the original publisher's attribution (the data came from them, not the secondary), AND
  (b) make the secondary source's role visible (summary / analysis / report on).

Acceptable forms:
  • "[[secondary blog]'s summary of the May [Publication]](secondary-url) showed annual home price growth of 0.9% in April"
  • "The May [Publication] showed 0.9% annual home price growth, [per [secondary blog]'s analysis](secondary-url)"
  • "[secondary blog] [summarized](secondary-url) the May [Publication], which reported 0.9% annual home price growth"
  • If you can't honor both, DROP the link: "The May [Publication] showed 0.9% annual home price growth."

NEVER do any of these:
  • "[May [Publication]](secondary-url)" — link domain mismatches the named entity; reader feels deceived clicking through
  • "[[secondary blog] reports home price growth was 0.9%](secondary-url)" — attributes the DATA to the secondary when it is summarizing the primary publication; this is data-source misattribution
  • "[[secondary blog]](secondary-url) reports home price growth was 0.9%" — same data-source misattribution

The fix is ALWAYS: name BOTH the original publisher AND the secondary you actually link to. Two names, one link, both roles visible.

5. NEWSLETTERS AND SUBSTACKS ARE INPUTS, NOT A DEDICATED OUTPUT SECTION (UPDATED 2026-06-02). The dedicated `substacker_takes` output field has been REMOVED. Do NOT output a substacker_takes field — if you do, it will be discarded before rendering. Newsletter / substack / single-author RSS columnist items provided in the "Newsletters" input section below are still VALUABLE INPUT — route them as follows:
   - If the newsletter post DIRECTLY COMMENTS on a candidate news event of the day, weave it into that theme's commentary with inline [author or outlet name](url) citation, same convention as other theme citations.
   - If the post is a topical discussion without a single event anchor, route it into the relevant `conversation_roundup` (same prose-with-inline-link format as ai_brief).
   - For AI-focused substack/newsletter posts, weave them into `ai_brief`.
   - Skip newsletters whose content is purely promotional, off-topic, or too thin to summarize.
This is a structural change: newsletter content is now distributed across themes/roundups based on subject matter, not collected into a single dedicated section.

5b. NEVER NARRATE INSUFFICIENT CONTENT. If a source's preview is short or teaser-only, infer the take from the title and any partial body you have, then write a confident one-sentence summary. NEVER write phrases like "I don't have access to the full content", "the snippet is cut off", "based on the limited preview", "I cannot offer specifics", or "partial summary". The reader will see this as broken output. If you genuinely can't infer anything beyond the title, write a single neutral sentence based on the title alone — no meta-commentary. This rule applies to conversation_themes, conversation_roundups, paper_of_the_day, and every other section.

5c. PAPER OF THE DAY. Pick ONE academic paper from the journal-feed input (working-paper series and peer-reviewed journals covering housing, urban economics, regional science, housing policy, etc.) as today's Paper of the Day. Selection criteria, in order: (1) most directly relevant to US housing, mortgages, zoning, demographics-of-housing, or affordability; (2) most interesting / surprising / counterintuitive findings; (3) methodologically sound; (4) not too inside-baseball for a general reader. Write the `summary` field in Pulse's measured, restrained, data-first tone — state the finding, then the method in one sentence, then the implication for US housing. The `key_finding` is a single wire-service-style lede sentence (max 25 words). If NO journal candidate is credible for the day (e.g., all candidates are non-housing or TOCs), set `paper_of_the_day` to null and the renderer will omit the section. Bias toward recency (papers <7 days old slightly preferred) but don't pick a marginal recent paper over a strong one from earlier in the 30-day window.

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
       - A data release ([trade association / brokerage / federal statistical agency] released X data showing Y)
       - A court filing or ruling
       - A named research paper or institutional report ([working paper series #####, think-tank report titled Z])
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
       • Citing tangentially related stats or data points from DIFFERENT events (an M&A theme cannot include "and meanwhile mortgage rates ticked up" unless that's directly tied to the deal).
       • Using the event as a "jumping-off point" for broader discourse (a Boomer/Millennial homeownership theme that drifts into fertility crisis, smartphones, social isolation — every sentence after the data point belongs in conversation_roundups, not this theme).
       • Including commentary from voices who didn't react to THIS event but who tweeted about a related topic the same day.
     Concrete test: for each sentence in a theme summary, ask "Is this sentence directly about the anchor event, or is it tangential discourse using the event as a hook?" If tangential → cut from theme, move to conversation_roundups.
     GOOD example (M&A deal): "[Acquirer] acquired [Target]. [Outlet A] noted the unusual structural framing. [Outlet B] reported the price premium. Critics on [platform] reacted to the strategic plan." — every sentence directly about the deal.
     BAD example (drift): "[Brokerage] published Boomer/Millennial homeownership data. The data echoes [analyst]'s note that first-time buyers are now 40. Meanwhile WaPo's opinion section linked fertility decline to smartphones. [Researcher] called it a radical experiment in social isolation." — only the first sentence is about the event; the rest is tangential drift.
   - **BROKERAGE/PLATFORM CONTENT MARKETING IS NEVER NEWS — NEVER AN ANCHOR.** A brokerage, listing portal, iBuyer, or real-estate platform publishing its own "research" / "study" / "report" / "forecast" / "data analysis" / "survey" / "insights" is content marketing, not news. Categories whose own publications can NEVER anchor a news theme: residential brokerages (national and tech-enabled), listing portals, iBuyer/instant-offer platforms, online mortgage marketplaces, real-estate-tech "insights" arms. When one of these is the source of an article's news hook, REJECT as an anchor candidate — route the data into a conversation_roundup if interesting, or drop entirely. Their content can still be CITED as commentary in themes anchored on legitimate news events (e.g., a Fed action theme can quote a brokerage's reading of the impact), but they cannot themselves be the anchor.
   - **BROKERAGE-RECAP-VIA-THIRD-PARTY IS NEVER NEWS.** Extension of the above. A third-party outlet REPORTING on a brokerage's study is STILL content marketing wearing a news disguise. If the article's news hook can be paraphrased as "[Brokerage] published / released / announced X" — where the brokerage IS the source of the data and the third party is just relaying it — it's a recap and NOT a news anchor. Allow only when the third party adds substantial original reporting: interviews with parties NOT employed by the brokerage, independent data sources, court records, investigative findings. The test: take the third-party headline, replace the brokerage name with "[a real estate platform]" — if the headline still reads like news, it's news; if it reads like "[a real estate platform] put out a press release," it's a recap.
   - **MULTI-DAY NEWS ARCS ARE VALID.** A news event from 1-3 days ago that's still being actively discussed today CAN anchor a theme. Example: a major M&A deal announced on a Sunday; the following Monday and Tuesday commentary on it is a legitimate theme. The test is not "did the event happen today" but "is there fresh substantive commentary today that addresses the event." If today's input contains 3+ items (across news outlets, analysts, social discourse) directly addressing a recent event, that event can anchor today's theme even if the original news was a few days back. Cap is ~5 days — past that, the event is stale even if someone is still talking about it.
   - Use the FULL article body when present in the input (enriched articles have substantial body text — quote specifics, not just topics)
   - **Weave historical context with explicit time stamps.** When a topic touches something already discussed this week, cite the relevant historical voice from the "Past 6 Days" section with a date stamp: "Tuesday, [an economist argued](url)..." or "earlier this week [an analyst warned](url)...". Never use a historical item without a date marker — the reader needs to instantly tell what's fresh vs context. Today's items don't need a date stamp (they're implicitly today).
   - **CRITICAL: historical context must match the theme's specific topic, geography, and country.** Don't weld an Australian migration statistic into a Canadian unemployment theme, or a NYC rent freeze argument into a San Francisco housing theme, just because both have "international" or "housing_policy" tags. Before citing a historical voice, verify: (a) same country/metro, (b) same specific topic (rent control ≠ inclusionary zoning ≠ permitting reform), (c) same direction of argument. If a historical item is about a different country or a tangentially-related topic, leave it out — better to have no historical citation than a misleading one. Recurring failure pattern: an historical handle on Country A's housing/migration data being welded into a Country B theme just because the topic tags overlap.
Label each theme's anchor platforms accurately: use "rss" or "substack" or the newspaper name when that's the anchor, "twitter" or "bluesky" when those anchor it.

**No theme-count target. Only include themes anchored on real news events with substantial direct commentary.** Don't pad. If a candidate has no real anchor or no commentary, drop it.

**CONSOLIDATE NEAR-DUPLICATE THEMES.** Before finalizing the theme list, ask: "are any two themes telling the same story from different vendor angles?" If two themes both anchor on (a) the same data period (same month / same release window) AND (b) the same housing-market dynamic (sales + prices, supply + demand, rents + vacancy, mortgage rates + affordability, etc.), MERGE them into one theme that holds the tension inside. Cite all sources inline; don't split because the data came from different vendors. Example: an April existing-home-sales theme (trade-association data + trade publication + major outlet coverage) and an April home-price-growth theme (a mortgage-data report + a housing analyst's summary blog) are ONE story — "April: sales soft, prices firm" — not two. The reader thinks in market dynamics, not data-vendor categories. Splitting them makes the brief feel like the same story got coverage twice. When the data period or the dynamic differ meaningfully (e.g., April sales but March CPI shelter; national sales but a Bay Area-specific price piece), keep them separate.

**SPLIT WELDED-UNRELATED THEMES.** Complement to the consolidation rule above. After drafting, scan each theme: if it has two paragraphs (or two sentence-clusters) describing DIFFERENT mechanisms or DIFFERENT markets, and the only thing linking them is a shared region label, a shared word in the headline, or a contrast frame, they are TWO THEMES, not one. SPLIT them. The diagnostic test: if the transition between the two sub-clusters requires a disjunctive frame ("On a separate track…", "The picture looks different in…", "Meanwhile in [region]…", "By contrast in [market]…", "On the other side of the country…"), that's the model reaching for a bridge between two stories that don't actually belong together. Failure example: combining a "Texas exurb growth driven by permitting regime" story and a "Florida home prices correcting after pandemic overshoot" story under one theme — these are different mechanisms (supply elasticity vs. demand withdrawal), different markets (Texas vs. Florida), and the only link is "both Sunbelt." Correct: two separate themes. Theme paragraphs split with `\\n\\n` are for genuine sub-clusters of ONE story (e.g., permits → starts → completions in the same release window, or rents → income → freeze proposal in the same market), not for two stories sharing a region tag. When in doubt, split. The reader prefers two crisp themes over one welded conglomerate.

**FORBIDDEN BRIDGE WORDS (HARD GATE).** Never use the following transitional phrases INSIDE a single conversation_themes summary. Their presence is proof you are welding two unrelated stories under one theme.

  - "Separately,"
  - "In a separate development"
  - "On a separate front"
  - "On a separate track"
  - "On another track"
  - "Apart from this"
  - "Apart from the [anchor],"
  - "Meanwhile in the discourse"
  - "Meanwhile,"  (when followed by a different topic/region/mechanism, not a continuation of the same event)
  - "Also today"
  - "Beyond that"
  - "Beyond the [anchor],"
  - "In broader [topic] discussion"
  - "More broadly,"
  - "The discourse pushed back on"
  - "The discourse moved on"
  - "The wider conversation"
  - "On the other side of the country"
  - "In a related but distinct thread"

If you find yourself reaching for any of these to transition between paragraphs/sentences in a SINGLE theme summary, that is a hard signal you have two themes, not one. Either:
  (a) Split into two separate conversation_themes entries (if both anchor on real, distinct news events), OR
  (b) Move the post-bridge content to conversation_roundups, keeping only the pre-bridge anchor-grounded content in the theme.

NEVER use any of these phrases inside a theme summary. The reader experiences these phrases as the model admitting "I'm welding two unrelated things together." Each is a structural failure.

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

    (b3) DISJUNCTIVE TRANSITIONS — DROP THE TRANSITION WORDS, KEEP THE PARAGRAPH BREAK. When you have two distinct points within the same theme or roundup, do NOT use connective transition words at all — even at the start of a new paragraph. Just insert a paragraph break (`\\n\\n`) and start the new paragraph plainly with the next claim. The paragraph break itself signals to the reader "this is a distinct point on the same topic." Adding a transition word on top of the paragraph break ("Separately, X argued..." or "On a separate track, Y noted...") makes the reader think you're claiming a logical relationship between the two points — that's the failure mode. Let the paragraph break stand alone.

    Examples of what to STRIP from the start of a new paragraph (use `\\n\\n` plus the bare claim instead):
        - "Separately," / "On a separate track:" / "On a different track:" / "On a parallel track,"
        - "At the federal level," / "At the state level," / "At the city level," / "On the policy side,"  (UNLESS the level-of-government label is genuinely the substantive lede for the new paragraph; in that case rewrite without the bare connective)
        - "Earlier this week," / "Yesterday," / "Last week," (only fold time-shifts into prose when the time-shift IS the substantive claim; otherwise drop)
        - "On a different note," / "Switching to," / "Turning to,"
        - "Meanwhile," / "Elsewhere,"
        - "The discourse pushed back on," / "The wider conversation," / "More broadly,"

    Failure pattern to AVOID: one welded paragraph that read "@analyst-handle's framing that the political moment has shifted to '2026: It's affordability, stupid.' Separately, [a city council member]'s newsletter announced [the mayor]'s executive budget includes an additional $5 billion for affordable housing… At the federal level, [a congressman] appeared on [outlet] to discuss the bipartisan 21st Century ROAD to Housing Act… Earlier this week, Saturday, @handle flagged that the House version of ROAD expands…" — that paragraph contains THREE buried disjunctive transitions ("Separately," "At the federal level," "Earlier this week,") that should have been three paragraph breaks WITHOUT those transition words. Required fix: four short paragraphs, one per topic cluster (election politics / NYC city policy / federal legislation / historical context on the federal bill). Each new paragraph begins with the substantive claim, NOT a transition word. The blank line between paragraphs IS the transition.

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

15b. ROUNDUP TOPIC COHERENCE (HARD GATE). Every voice you cite in a roundup MUST be specifically engaging with the SAME SPECIFIC TOPIC named in the roundup's `topic` field. The roundup is NOT a junk drawer for "voices saying housing-adjacent things today." Concrete test: for each voice you're about to cite, ask "is this voice making an argument that DIRECTLY bears on [the exact topic in the roundup's name], or just on a vaguely-related broader theme?" If just vaguely related, drop the voice.

   FORBIDDEN PATTERNS:
   - Roundup titled "SF Upzoning and Inclusionary Zoning Reform" → CANNOT include a voice arguing suburbs are subsidized (different argument), or rent-control research (different policy), even though both are "housing-policy adjacent." Those voices belong in DIFFERENT roundups or get dropped.
   - Roundup titled "Sun Belt insurance pricing" → CANNOT include voices on Midwest property taxes, even though both are "housing-cost adjacent."
   - Roundup titled "Mortgage rates and Fed expectations" → CANNOT include voices on credit card debt or auto loans, even though all involve interest rates.

   The bridge-words rule applies equally to roundups: never use "Separately," "Meanwhile," "The discourse pushed back on," "More broadly," "Beyond that," etc. as transitions inside a single roundup. If two voices share only a category label (both YIMBY, both housing-adjacent, both real-estate), that is NOT enough — they need to be in actual conversation with each other about the SAME specific question/event.

   If you cannot defend EACH voice's inclusion in the roundup against this test, drop it. Better to have a 3-voice tight roundup than a 6-voice junk drawer.

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

    # Unwrap Substack tracking-redirect URLs to the underlying article
    # URLs. Without this, citations point to .../substack.com/redirect/...
    # which the model treats as unciteable and frequently drops entirely
    # (user-flagged 2026-06-05 case: Aziz's own Luxury Boom post used as
    # roundup anchor but not cited because the URL was a redirect blob).
    _unwrapped = unwrap_item_urls(all_items)
    if _unwrapped:
        logger.info(f"unwrapped {_unwrapped} substack redirect URL(s) before synth")
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

    # v5 trigger-type classifier: per-item Opus pass that drops items
    # classified as opinion / retrospective / recap / profile / analysis /
    # explainer BEFORE synthesis sees them. Catches op-ed patterns (e.g. the
    # City Journal "Good Cause Eviction" landlord piece in briefing #136) that
    # otherwise sneak into themes.
    #
    # As of v2 (2026-06-03), ALL sources are classified — not just rss+gmail.
    # Social and essay items typically classify as `commentary`, which is an
    # ACCEPT category: they pass through to synthesis BUT are tagged with
    # _trigger_type='commentary' so the synthesizer treats them as commentary
    # to be cited inside event-anchored themes, never as a theme anchor of
    # their own. Briefing #137 motivated this — the "Tokyo vs Sydney" theme
    # was anchored on viral @DrCameronMurray tweets that passed through the
    # social whitelist unchecked.
    #
    # On any classifier failure (network, parse, API), the affected items
    # default to ACCEPT — we never silently lose items.
    try:
        before = len(all_items)
        all_items = _apply_trigger_filter(all_items, client=client)
        dropped = before - len(all_items)
        if dropped:
            logger.info(f"Trigger-type filter dropped {dropped} items classified as opinion/retrospective/recap/profile/analysis/explainer")
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
        # same supporting voice). The failure mode was a single author's stat
        # appearing verbatim in two themes that linked to the same status URL.
        briefing = _dedup_cross_theme_citations(briefing)

        # Drop themes with no housing-topic overlap. Sonnet drifts off-topic
        # toward macro/AI/tech filler when its prompt has a high theme count.
        briefing = _enforce_housing_focused_themes(briefing)

        # Deterministic EVENT-ANCHOR enforcement. The SYSTEM_PROMPT lists
        # social-only triggers as INVALID, but Sonnet ignores it when the
        # discourse is rich enough (briefing #137: Tokyo-vs-Sydney). Move
        # any theme whose anchor is a tweet/handle cluster to roundups
        # BEFORE URL validation, so any URLs carried into the roundup still
        # get checked.
        briefing = _reject_social_anchored_themes(briefing)

        # Deterministic FORBIDDEN BRIDGE WORDS enforcement. The SYSTEM_PROMPT
        # forbids ~20 transitional phrases ("Separately,", "On a separate
        # front", etc.) inside any single theme summary, but the model
        # routinely ignores it (briefing #136 East NY → SF supervisors via
        # "Separately, @dbroockman noted..."). This scans each theme summary
        # for those phrases, truncates at the bridge sentence, and moves the
        # tail to conversation_roundups. Runs AFTER social-anchor rejection
        # (so we don't waste cycles on themes that are about to disappear)
        # and BEFORE URL validation (so any URLs carried into the new
        # roundup entries still get validated).
        briefing = _strip_forbidden_bridges(briefing)

        # Deterministic paragraph-break enforcement. User feedback
        # 2026-06-04: "the news paragraphs never have para breaks…why? it
        # should break them apart like a natural writer." The prompt rule
        # added the previous day is being ignored by the model. This
        # post-processor inserts \\n\\n at sentence boundaries when a
        # paragraph hits >3 sentences, when a new linked attribution
        # arrives after 2+ sentences, or when a time-shift transition
        # starts. Renderer already converts \\n\\n to <br><br>.
        briefing = _enforce_paragraph_breaks(briefing)

        # Auto-link bare @handles in theme/roundup prose. The model
        # occasionally drops the markdown link on a cited handle —
        # especially for historical/"earlier this week" attributions
        # like "@mikesimonsen flagged Monday" (briefing #137, observed
        # 2026-06-03). This scans summaries, finds bare @handles outside
        # any existing link span, and wraps them in [@handle](url) where
        # the URL points to the most recent matching tweet from the
        # corpus. If a weekday hint follows the handle, prefer a tweet
        # from that weekday. If no corpus match exists, leave the bare
        # @handle in place (user directive: do not fabricate a profile
        # URL just to satisfy the rule).
        briefing = _autolink_bare_handles(briefing, conn)

        # Validate all URLs against the database
        briefing = _validate_briefing_urls(briefing, conn)

        # Inject the human-readable source breakdown
        if "stats_summary" not in briefing:
            briefing["stats_summary"] = {}
        briefing["stats_summary"]["source_breakdown"] = dict(source_display_counts.most_common(20))
        briefing["stats_summary"]["total_items_analyzed"] = len(all_items)
        briefing["stats_summary"]["conversation_items"] = len(conversation_items)
        briefing["stats_summary"]["platforms_active"] = len(set(i["source"] for i in all_items))

        # CITED-sources breakdown — sources that actually appear in the
        # briefing's markdown links (not just everything we ingested).
        # Grouped by source-type with named publications inside. Lookup
        # each cited URL in the items table to get its source-type +
        # display name; URL-only classification fallback for publisher
        # URLs the model added by hand (e.g., bloomberg.com, ft.com).
        briefing["stats_summary"]["cited_sources"] = _compute_cited_sources(briefing, conn)

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
