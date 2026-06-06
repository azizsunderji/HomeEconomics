"""Email delivery via Resend.

Renders the daily conversation briefing as styled HTML and sends via Resend API.
Conversation-focused format: themes with platform badges, substacker takes,
institutional signal.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path  # noqa: F401  (used by _load_front_pages_json)

import httpx

from config import EMAIL_TO, EMAIL_FROM

logger = logging.getLogger(__name__)


def _load_front_pages_json() -> dict | None:
    """Load headlines.json — front-pages sidecar with per-headline article URLs.

    Tries the local screenshots dir first (dev / local runs), then the cached
    /tmp path, then falls back to the Bluehost URL (GHA / production). Returns
    a dict shaped like:
      { slug: { masthead, url, headlines: [{text, article_url}, ...] } }
    or None if every source failed.
    """
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "data" / "screenshots" / "headlines.json",
        Path("/tmp/front_pages/headlines.json"),
    ]
    for p in candidates:
        try:
            if p.exists():
                return json.loads(p.read_text())
        except Exception as e:
            logger.warning(f"failed to load {p}: {e}")
    try:
        resp = httpx.get(
            "https://home-economics.us/pulse-screenshots/headlines.json",
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"bluehost headlines.json fetch returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"bluehost headlines.json fetch failed: {e}")
    return None


def _esc(text: str) -> str:
    """Escape HTML entities, handling already-encoded input."""
    import html as _html
    import re as _re
    # First unescape any existing entities to avoid double-encoding
    # (RSS feeds often deliver titles with &amp; already encoded)
    text = _html.unescape(str(text))
    # Strip any HTML tags (Google Alerts feeds include <b>...</b> in titles)
    text = _re.sub(r'<[^>]+>', '', text)
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _md_links(text: str) -> str:
    """Convert markdown links [text](url) to HTML <a> tags, escape the rest.
    Also converts double-newlines (\\n\\n) into paragraph breaks (<br><br>) so
    the synthesizer can use them to separate distinct points within a theme."""
    import re
    parts = re.split(r'(\[[^\]]+\]\([^)]+\))', str(text))
    result = []
    for part in parts:
        m = re.match(r'\[([^\]]+)\]\(([^)]+)\)', part)
        if m:
            link_text = _esc(m.group(1))
            url = m.group(2)
            result.append(f'<a href="{url}" target="_blank" style="color: #0BB4FF; text-decoration: none;">{link_text}</a>')
        else:
            escaped = _esc(part)
            escaped = re.sub(r'\n{2,}', '<br><br>', escaped)
            result.append(escaped)
    return ''.join(result)


# Source-type → label lookup for the cited-sources box.
_SOURCE_TYPE_LABEL = {
    "rss":         "RSS",
    "gmail":       "Email",
    "twitter":     "Twitter",
    "bluesky":     "Bluesky",
    "substack":    "Substack",
    "reddit":      "Reddit",
    "hackernews":  "HN",
    "web":         "Web",
}
_SOURCE_TYPE_ORDER = [
    "rss", "gmail", "twitter", "bluesky", "substack",
    "reddit", "hackernews", "web",
]


def _render_cited_sources_box(cited_sources: dict) -> str:
    """Render the per-type list of cited sources, no icons.

    Layout, one row per non-empty type:
        RSS (N): SourceA (c) · SourceB (c) · SourceC ...

    N is the unique-source count for that type and the trailing `(c)`
    after a name appears only when that source was cited more than once.

    Returns an empty string when cited_sources is empty.
    """
    if not cited_sources:
        return ""
    rows = []
    for typ in _SOURCE_TYPE_ORDER:
        names_map = cited_sources.get(typ) or {}
        if not names_map:
            continue
        label = _SOURCE_TYPE_LABEL.get(typ, typ.title())
        unique_count = len(names_map)
        sorted_names = sorted(
            names_map.items(), key=lambda x: (-x[1], x[0].lower())
        )
        parts = []
        for name, n in sorted_names:
            esc_name = _esc(name)
            parts.append(f"{esc_name} ({n})" if n > 1 else esc_name)
        joined = " &middot; ".join(parts)
        rows.append(
            '<div style="margin: 0 0 4px 0;">'
            f'<span style="font-weight: 600; color: #555;">{label}'
            f' ({unique_count}):</span> '
            f'<span style="color: #777;">{joined}</span>'
            '</div>'
        )
    if not rows:
        return ""
    return (
        '<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td bgcolor="#FFFFFF" style="background-color: #fff; padding: 10px 14px; '
        'border-radius: 4px; font-size: 14px; line-height: 1.55; color: #777;">'
        + "".join(rows) +
        '</td></tr></table>'
    )


def _normalize_headline_caps(text: str) -> str:
    """Convert SHOUTY ALL-CAPS print headlines (NYT lead-line style) to
    title case so the front-pages section reads uniformly. Leaves
    naturally mixed-case headlines untouched.

    Threshold: >70% of letters uppercase → title-case it. Uses
    str.title() which is reasonably robust for headline-style content:
    `'U.S. FALLS BEHIND'.lower().title()` → `"U.S. Falls Behind"`.
    """
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return text
    upper_count = sum(1 for c in letters if c.isupper())
    if upper_count / len(letters) > 0.7:
        return text.lower().title()
    return text


def _format_number(n) -> str:
    """Format a number with commas, handling non-numeric gracefully."""
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


def _render_cost_line(apify_cents: float, anthropic_cents: float, by_model: dict) -> str:
    """Small cost-breakdown line under the header.

    Renders nothing if no costs are tracked. Otherwise shows e.g.:
        Cost today: Apify $1.20 · Anthropic $4.50 (Sonnet $0.65 · Haiku $3.85) · Total $5.70
    """
    if not apify_cents and not anthropic_cents:
        return ""
    parts = []
    if apify_cents:
        parts.append(f"Apify <strong>${apify_cents / 100:.2f}</strong>")
    if anthropic_cents:
        anth_str = f"Anthropic <strong>${anthropic_cents / 100:.2f}</strong>"
        # Per-model breakdown if we have it
        sonnet_cents = sum(v.get("cents", 0) for k, v in by_model.items() if "sonnet" in k.lower())
        haiku_cents = sum(v.get("cents", 0) for k, v in by_model.items() if "haiku" in k.lower())
        opus_cents = sum(v.get("cents", 0) for k, v in by_model.items() if "opus" in k.lower())
        sub_parts = []
        if sonnet_cents: sub_parts.append(f"Sonnet ${sonnet_cents / 100:.2f}")
        if haiku_cents:  sub_parts.append(f"Haiku ${haiku_cents / 100:.2f}")
        if opus_cents:   sub_parts.append(f"Opus ${opus_cents / 100:.2f}")
        if sub_parts:
            anth_str += f" ({' · '.join(sub_parts)})"
        parts.append(anth_str)
    total = (apify_cents + anthropic_cents) / 100
    parts.append(f"Total <strong>${total:.2f}</strong>")
    line = " &middot; ".join(parts)
    return f'<p style="color: #888; font-size: 14px; margin: 4px 0 0 0;">Cost today: {line}</p>'


def _heat_badge(level: str) -> str:
    """Render a heat level badge."""
    colors = {
        "viral": ("#F4743B", "#fff"),
        "high": ("#FEC439", "#3D3733"),
        "medium": ("#0BB4FF", "#fff"),
        "low": ("#DADFCE", "#3D3733"),
    }
    bg, fg = colors.get(level, ("#DADFCE", "#3D3733"))
    return (
        f'<span style="display: inline-block; background: {bg}; color: {fg}; '
        f'padding: 2px 8px; border-radius: 10px; font-size: 12px; '
        f'font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">'
        f'{_esc(level)}</span>'
    )


def _platform_badge(platform: dict) -> str:
    """Render a platform badge like [Twitter 89 replies]."""
    name = platform.get("name", "")
    sub = platform.get("subreddit", "")
    comments = platform.get("comment_count", 0)
    replies = platform.get("reply_count", 0)
    sentiment = platform.get("sentiment", "")
    url = platform.get("url", "")

    label_parts = [name.title()]
    if sub:
        label_parts.append(f"r/{sub}")
    if comments:
        label_parts.append(f"{comments} comments")
    elif replies:
        label_parts.append(f"{replies} replies")

    # Sentiment color hint
    sent_color = "#888"
    if sentiment == "bearish":
        sent_color = "#F4743B"
    elif sentiment == "bullish":
        sent_color = "#67A275"
    elif sentiment == "mixed":
        sent_color = "#FEC439"

    label = " ".join(label_parts)

    badge = (
        f'<span style="display: inline-block; background: white; '
        f'border: 1px solid #ddd; border-left: 3px solid {sent_color}; '
        f'padding: 3px 8px; border-radius: 4px; font-size: 15px; '
        f'margin-right: 4px; margin-bottom: 4px; color: #555;">'
    )
    if url:
        badge += f'<a href="{url}" target="_blank" style="color: #555; text-decoration: none;">{_esc(label)}</a>'
    else:
        badge += _esc(label)
    badge += '</span>'
    return badge


def _spacer(height: int = 24) -> str:
    """Email-safe vertical spacer using a table row."""
    return f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td height="{height}" style="line-height:{height}px; font-size: 1px;">&nbsp;</td></tr></table>\n'


def _section_heading(text: str) -> str:
    """Render a section heading as a table with top border."""
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="border-top: 2px solid #3D3733; padding-top: 12px; font-size: 16px; text-transform: uppercase; letter-spacing: 1.5px; color: #888; font-weight: normal; font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif;">'
        f'{_esc(text)}</td></tr></table>\n'
    )


def render_briefing_html(briefing: dict, with_sources_box: bool = False) -> tuple[str, str, int]:
    """Render a conversation briefing dict as styled HTML email.

    Uses table-based layout for Gmail compatibility (Gmail strips <body>
    styles and ignores background on plain <div>s).

    `with_sources_box=False` (default) hides the cited-sources box at the
    top — user wanted it pulled out of the v1 production email
    2026-06-05. v2_runner.py passes True so the v2 diagnostic email he
    gets keeps showing it.

    Returns (html, top_theme_label, theme_count).
    """
    FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

    date = briefing.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    stats = briefing.get("stats_summary", {})
    pulse = briefing.get("conversation_pulse", "")
    themes = briefing.get("conversation_themes", [])
    substacker = briefing.get("substacker_takes", [])
    institutional = briefing.get("_institutional_emails", []) or briefing.get("institutional_signal", [])
    gmail_newsletters = briefing.get("_gmail_newsletters", [])
    headlines = briefing.get("_headlines", [])
    journal_articles = briefing.get("_journal_articles", [])
    starred_emails = briefing.get("_starred_emails", [])
    press_mentions = briefing.get("_press_mentions", [])
    conversation_roundups = briefing.get("conversation_roundups", []) or []
    paper_of_the_day = briefing.get("paper_of_the_day") or None
    collection_errors = briefing.get("_collection_errors", [])
    apify_spend_cents = briefing.get("_apify_spend_cents", 0)

    # Today's Anthropic spend (from anthropic_spend table — populated by
    # record_usage() calls at every messages.create/.stream site).
    try:
        from analysis.anthropic_spend import get_spend_cents as _get_anthropic_spend
        _anth = _get_anthropic_spend()
        anthropic_total_cents = _anth.get("total_cents", 0)
        anthropic_by_model = _anth.get("by_model", {})
    except Exception:
        anthropic_total_cents = 0
        anthropic_by_model = {}

    top_theme = themes[0]["theme"][:60] if themes else "Daily Conversation"
    theme_count = len(themes)

    # Cited-sources breakdown (icon-grouped). Hidden in the v1 production
    # email per user 2026-06-05; visible only when the caller explicitly
    # opts in (v2_runner.py sets with_sources_box=True).
    cited_sources_html = (
        _render_cited_sources_box(stats.get("cited_sources") or {})
        if with_sources_box else ""
    )

    # Build HTML — table-based centering for Gmail
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
/* Stack the front-pages 2-col rows on narrow viewports so the image
   goes full-bleed above the headlines instead of cramming side-by-side. */
@media screen and (max-width: 600px) {{
  .fp-row {{ display: block !important; }}
  .fp-cell {{ display: block !important; width: 100% !important; padding: 0 !important; }}
  .fp-cell-img {{ padding-bottom: 14px !important; }}
  .fp-cell img {{ max-width: 100% !important; width: 100% !important; height: auto !important; }}
}}
</style>
</head>
<body style="margin: 0; padding: 0; background-color: #F6F7F3;">
<center>
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#F6F7F3" style="background-color: #F6F7F3;">
<tr><td align="center">
<table cellpadding="0" cellspacing="0" style="max-width: 700px; width: 100%; font-family: {FONT}; color: #3D3733; line-height: 1.6; font-size: 18px;">
<tr><td style="padding: 20px 24px;">

<!-- HEADER -->
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="border-bottom: 3px solid #3D3733; padding-bottom: 12px;">
  <h1 style="font-size: 24px; margin: 0 0 4px 0; color: #3D3733; letter-spacing: -0.5px; font-family: {FONT};">Pulse</h1>
  <p style="color: #888; font-size: 16px; margin: 0;">{date} &middot; {_format_number(stats.get('total_items_analyzed', 0))} items &middot; {stats.get('conversation_items', 0)} conversations &middot; {stats.get('platforms_active', 0)} platforms</p>
  {_render_cost_line(apify_spend_cents, anthropic_total_cents, anthropic_by_model)}
</td></tr></table>
"""

    html += _spacer(20)

    # ── TECHNICAL ERRORS (if any) ──
    if collection_errors:
        error_lines = []
        for err in collection_errors:
            source = _esc(err.get("source", "unknown"))
            error_msg = _esc(err.get("error", ""))[:200]
            error_lines.append(f"<li style='margin-bottom: 3px;'><strong>{source}:</strong> {error_msg}</li>")
        html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td bgcolor="#FBCAB5" style="background-color: #FBCAB5; padding: 10px 14px; border-radius: 6px; border-left: 3px solid #F4743B; font-size: 16px; line-height: 1.5;">
  <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #F4743B; font-weight: 600; margin-bottom: 4px;">Pipeline Issues</div>
  <ul style="margin: 0; padding-left: 16px; color: #3D3733;">{''.join(error_lines)}</ul>
</td></tr></table>
"""
        html += _spacer(16)

    # ── TOP BULLETS (key themes summary) ──
    if themes:
        bullet_html = ""
        for t in themes[:3]:
            bullet_html += f'<li style="margin-bottom: 4px;">{_esc(t.get("theme", ""))}</li>'
        html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td bgcolor="#DADFCE" style="background-color: #DADFCE; padding: 14px 16px; border-radius: 6px; font-size: 16px; line-height: 1.6;">
  <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #67A275; font-weight: 600; margin-bottom: 6px;">Today's Top Themes</div>
  <ul style="margin: 0; padding-left: 18px;">{bullet_html}</ul>
</td></tr></table>
"""
        html += _spacer(24)

    # ── CITED SOURCES (grouped by type, with icons) ──
    if cited_sources_html:
        html += cited_sources_html
        html += _spacer(24)

    # ── FRONT PAGES — side-by-side print snapshot + clickable headlines ──
    # Page snapshots (page-only PNGs, faded to cream) live on Bluehost as
    # JPGs alongside a headlines.json sidecar. JSON shape:
    #   { slug: { masthead, url, headlines: [{text, article_url}, ...] } }
    # We render a 2-column row per paper: image on left (~45%), masthead
    # kicker + headline stack on right (~55%). Each headline is its own
    # clickable <a> when article_url is non-null; otherwise plain text.
    _cb = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    _papers_order = [
        ("nyt", "The New York Times",     "https://www.nytimes.com"),
        ("wsj", "The Wall Street Journal", "https://www.wsj.com"),
        ("lat", "Los Angeles Times",      "https://www.latimes.com"),
        ("hc",  "Houston Chronicle",      "https://www.houstonchronicle.com"),
    ]
    front_pages_data = _load_front_pages_json()
    if front_pages_data:
        html += _section_heading("On the Front Pages")
        html += _spacer(14)
        for slug, label, default_url in _papers_order:
            paper = front_pages_data.get(slug)
            if not paper:
                continue
            masthead = paper.get("masthead", label.upper())
            paper_url = paper.get("url", default_url)
            heads = paper.get("headlines", []) or []

            # Headline <li> stack — clickable when article_url is non-null.
            # Cap at 3 per paper so the column height roughly matches the
            # page snapshot (user feedback 2026-06-04: keep vertically compact).
            li_items = []
            for h in heads[:3]:
                raw_text = (h.get("text") or "").strip()
                if not raw_text:
                    continue
                # User feedback 2026-06-04: drop the shouty all-caps
                # NYT-style lead-headline casing; title-case it instead
                # so all headlines read the same.
                hl_text = _esc(_normalize_headline_caps(raw_text))
                article_url = h.get("article_url") or ""
                if article_url:
                    li_items.append(
                        f'<li style="margin: 0 0 10px 0;">'
                        f'<a href="{article_url}" target="_blank" '
                        f'style="color: #3D3733; text-decoration: none; '
                        f'font-size: 16px; font-weight: 400; line-height: 1.35;">'
                        f'{hl_text}</a></li>'
                    )
                else:
                    li_items.append(
                        f'<li style="margin: 0 0 10px 0; color: #3D3733; '
                        f'font-size: 16px; font-weight: 400; line-height: 1.35;">'
                        f'{hl_text}</li>'
                    )
            headline_list = (
                f'<ul style="list-style: none; padding: 0; margin: 0;">'
                f'{"".join(li_items)}</ul>'
            )

            html += f"""<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom: 24px;"><tr class="fp-row">
<td class="fp-cell fp-cell-img" width="45%" valign="top" style="padding-right: 16px;">
  <a href="{paper_url}" target="_blank"><img src="https://home-economics.us/pulse-screenshots/{slug}.jpg?v={_cb}" alt="{_esc(masthead)} front page" width="100%" style="width: 100%; max-width: 270px; height: auto; display: block;"/></a>
</td>
<td class="fp-cell" width="55%" valign="top">
  {headline_list}
</td>
</tr></table>
"""
        html += _spacer(16)

    # ── CONVERSATION THEMES (main section ~60% of email) ──
    if themes:
        html += _section_heading("News Themes")
        html += _spacer(14)

        for theme in themes[:20]:  # bumped from 6 — Sonnet generates 12-18 themes,
                                   # the previous cap hid >half of them from the email.
                                   # 20 is a safety ceiling; Sonnet self-caps lower.
            heat = theme.get("heat_level", "medium")
            platforms = theme.get("platforms", [])
            trigger = theme.get("related_news_trigger", "")
            topics = theme.get("topics", [])

            # Platform badges
            platform_html = ""
            if platforms:
                badges = [_platform_badge(p) for p in platforms]
                platform_html = f'<div style="margin-top: 8px;">{" ".join(badges)}</div>'

            # News trigger note
            trigger_html = ""
            if trigger:
                trigger_html = f'<div style="font-size: 15px; color: #888; margin-top: 6px; font-style: italic;">Triggered by: {_esc(trigger)}</div>'

            # Topic pills
            topics_html = ""
            if topics:
                pills = " ".join(
                    f'<span style="display: inline-block; background: #F6F7F3; border: 1px solid #ddd; padding: 1px 5px; border-radius: 3px; font-size: 12px; margin-right: 3px; color: #888;">{_esc(t)}</span>'
                    for t in topics[:4]
                )
                topics_html = f'<div style="margin-top: 6px;">{pills}</div>'

            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding-bottom: 18px; border-bottom: 1px solid #e8e8e8;">
  <div style="margin-bottom: 6px;">
    {_heat_badge(heat)}
    <span style="font-size: 18px; font-weight: 600; margin-left: 6px; line-height: 1.3;">{_esc(theme.get('theme', ''))}</span>
  </div>
  <div style="font-size: 17px; color: #555; line-height: 1.5;">{_md_links(theme.get('summary', ''))}</div>
  {platform_html}
  {trigger_html}
  {topics_html}
</td></tr></table>
"""
            html += _spacer(18)

        html += _spacer(10)

    # Real Estate Headlines section removed 2026-06-02 (streamlining for broad
    # housing audience). The underlying `headlines` items still feed the
    # synthesis input pool as fodder for themes/roundups — they just don't
    # render as their own list section in the email.

    # ── CONVERSATIONS — topical roundups without a single named-event trigger ──
    # Format mirrors AI section: bold topic line, single paragraph below, inline
    # markdown links converted to <a> via _md_links. Sits between News Themes
    # (which require a hard event anchor) and the AI section. Omitted entirely
    # if the field is missing/empty so we never render a bare header.
    if conversation_roundups:
        html += _section_heading(f"Conversations ({len(conversation_roundups)})")
        html += _spacer(14)
        for roundup in conversation_roundups:
            topic = roundup.get("topic", "") or ""
            summary = roundup.get("summary", "") or ""
            if not summary.strip():
                continue
            summary_html = _md_links(summary)
            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding-bottom: 18px; border-bottom: 1px solid #e8e8e8;">
  <div style="font-size: 18px; font-weight: 600; line-height: 1.3; margin-bottom: 6px;">{_esc(topic)}</div>
  <div style="font-size: 17px; color: #555; line-height: 1.6;">{summary_html}</div>
</td></tr></table>
"""
            html += _spacer(14)
        html += _spacer(10)

    # ── PAPER OF THE DAY — single curated academic paper ──
    # Added 2026-06-02. Rendered AFTER conversation_roundups and BEFORE the AI
    # brief. Omitted entirely if paper_of_the_day is null / missing (e.g. no
    # credible journal candidates surfaced from the 30-day window).
    if paper_of_the_day and isinstance(paper_of_the_day, dict) \
            and paper_of_the_day.get("title"):
        html += _section_heading("Paper of the Day")
        html += _spacer(14)
        p_title = _esc(paper_of_the_day.get("title", ""))
        p_authors = _esc(paper_of_the_day.get("authors", ""))
        p_pub = _esc(paper_of_the_day.get("publication", ""))
        p_date = _esc(paper_of_the_day.get("date", ""))
        p_summary = _md_links(paper_of_the_day.get("summary", ""))
        p_key = _esc(paper_of_the_day.get("key_finding", ""))
        p_url = paper_of_the_day.get("url", "") or "#"

        key_block = ""
        if p_key:
            key_block = (
                f'<div style="font-size: 17px; color: #3D3733; font-weight: 600; '
                f'line-height: 1.4; margin: 0 0 10px 0;">{p_key}</div>\n'
            )
        meta_line = p_pub + (f" &middot; {p_date}" if p_date else "")
        html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding-bottom: 14px; border-bottom: 1px solid #e8e8e8;">
  <div style="font-size: 19px; font-weight: 700; line-height: 1.3; margin: 0 0 4px 0;">
    <a href="{p_url}" target="_blank" style="color: #3D3733; text-decoration: none;">{p_title}</a>
  </div>
  <div style="font-size: 15px; color: #555; margin: 0 0 2px 0;">{p_authors}</div>
  <div style="font-size: 14px; color: #888; margin: 0 0 12px 0;">{meta_line}</div>
  {key_block}
  <div style="font-size: 17px; color: #555; line-height: 1.6; margin: 0 0 14px 0;">{p_summary}</div>
  <div style="margin-top: 6px;">
    <a href="{p_url}" target="_blank" style="display: inline-block; background: #0BB4FF; color: #fff; padding: 8px 14px; border-radius: 4px; font-size: 15px; font-weight: 600; text-decoration: none;">Read the paper &rarr;</a>
  </div>
</td></tr></table>
"""
        html += _spacer(24)

    # ── AI BRIEF + TWITTER ROUNDUP SECTIONS REMOVED 2026-06-02 ──
    # The dedicated ai_brief paragraph and Twitter / Bluesky roundup sections
    # were removed. AI-only items are now out of scope; housing-tied AI content
    # gets woven into themes via inline citation. Substantive tweets surface
    # in conversation_themes or conversation_roundups based on whether they
    # anchor on a named, dated event.

    # ── NEWSLETTERS SECTION REMOVED 2026-06-02 ──
    # The dedicated substacker_takes / Newsletters section was removed in favor
    # of routing newsletter content into themes, conversation_roundups, or
    # ai_brief based on subject matter. The synthesis prompt (rule 5) now
    # instructs Sonnet to weave newsletter content inline rather than collect
    # it into a dedicated section. The `substacker` local variable above is
    # retained for backward compatibility with older briefings (no-op when
    # empty) but the rendering block has been removed.

    # Institutional Signal, Academic Journals, and From Your Email sections
    # removed 2026-06-02 (streamlining for broad housing audience). The
    # underlying items still feed the synthesis input pool as fodder for
    # themes/roundups and for the Paper of the Day pick — they just don't
    # render as their own list sections in the email.

    # ── HOME ECONOMICS IN THE NEWS (press mentions) ──
    if press_mentions:
        html += _section_heading("Home Economics in the News")
        html += _spacer(10)

        for mention in press_mentions[:10]:
            url = mention.get("url", "")
            headline_text = _esc(mention.get('headline', ''))
            source_name = _esc(mention.get('source', ''))
            date_str = _esc(mention.get('date', ''))

            if url:
                headline_link = f'<a href="{url}" target="_blank" style="color: #3D3733; text-decoration: none;">{headline_text}</a>'
            else:
                headline_link = headline_text

            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size: 17px; padding: 4px 0; border-bottom: 1px solid #f0f0f0; line-height: 1.45;">
  <span style="font-weight: 600; color: #0BB4FF; font-size: 15px;">{source_name}</span>
  {headline_link}
  {f'<span style="color: #888; font-size: 15px;"> ({date_str})</span>' if date_str else ''}
</td></tr></table>
"""
        html += _spacer(24)

    # ── FOOTER ──
    url_audit = briefing.get("_url_audit", {})
    url_audit_str = ""
    if url_audit:
        parts = []
        if url_audit.get("verified"):
            parts.append(f"{url_audit['verified']} verified")
        if url_audit.get("corrected"):
            parts.append(f"{url_audit['corrected']} corrected")
        if url_audit.get("stripped"):
            parts.append(f"<span style='color: #F4743B;'>{url_audit['stripped']} stripped</span>")
        if parts:
            url_audit_str = f'<p style="font-size: 15px; color: #ccc; margin: 4px 0 0 0;">URLs: {" &middot; ".join(parts)}</p>'

    html += f"""
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="border-top: 2px solid #3D3733; padding-top: 12px;">
  <p style="font-size: 15px; color: #aaa; margin: 0;">
    {_format_number(stats.get('total_items_analyzed', 0))} items analyzed
    &middot; {stats.get('conversation_items', 0)} conversations
    &middot; {stats.get('platforms_active', 0)} platforms
    &middot; 36h window
  </p>
  {url_audit_str}
  <p style="font-size: 15px; color: #aaa; margin: 8px 0 0 0; text-align: center;">
    Pulse &middot; Home Economics &middot;
    <a href="https://github.com/azizsunderji/HomeEconomics/actions" target="_blank" style="color: #0BB4FF;">View logs</a>
  </p>
</td></tr></table>

</td></tr>
</table>
</td></tr>
</table>
</center>
</body>
</html>"""

    return html, top_theme, theme_count


def send_email(
    briefing: dict,
    to: str = EMAIL_TO,
) -> bool:
    """Render and send the daily briefing email.

    Returns True if sent successfully.
    """
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.error("RESEND_API_KEY not set — cannot send email")
        return False

    html, top_theme, theme_count = render_briefing_html(briefing)
    date = briefing.get("date", datetime.now(timezone.utc).strftime("%b %d"))

    # Subject line: top conversation theme, not top news headline
    subject = f"Pulse: {top_theme} + {theme_count - 1} more | {date}"
    if theme_count <= 1:
        subject = f"Pulse: {top_theme} | {date}"

    # Retry up to 3 times on transient failures (network, 4xx, 5xx)
    import time as _time
    last_error = None
    for attempt in range(3):
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": EMAIL_FROM,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                logger.info(f"Email sent successfully: {subject}")
                return True
            # Log the response body so we can see Resend's actual error
            logger.warning(
                f"Resend returned {resp.status_code} on attempt {attempt + 1}/3: "
                f"{resp.text[:500]}"
            )
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            # Don't retry on auth errors — they won't resolve
            if resp.status_code in (401, 403):
                break
        except Exception as e:
            logger.warning(f"Email send attempt {attempt + 1}/3 failed: {e}")
            last_error = str(e)

        if attempt < 2:
            _time.sleep(5 * (attempt + 1))  # 5s, then 10s backoff

    logger.error(f"Failed to send email after 3 attempts: {last_error}")
    return False


if __name__ == "__main__":
    # Test render with a sample
    sample = {"date": "2026-02-19", "stats_summary": {"total_items_analyzed": 0, "platforms_active": 0, "conversation_items": 0}}
    html, _, _ = render_briefing_html(sample)
    print(f"HTML length: {len(html)} chars")
