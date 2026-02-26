"""Email delivery via Resend.

Renders the daily conversation briefing as styled HTML and sends via Resend API.
Conversation-focused format: themes with platform badges, notable claims
with data reality checks, substacker takes, institutional signal.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import EMAIL_TO, EMAIL_FROM

logger = logging.getLogger(__name__)


def _esc(text: str) -> str:
    """Escape HTML entities."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _format_number(n) -> str:
    """Format a number with commas, handling non-numeric gracefully."""
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


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
        f'padding: 2px 8px; border-radius: 10px; font-size: 10px; '
        f'font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">'
        f'{_esc(level)}</span>'
    )


def _platform_badge(platform: dict) -> str:
    """Render a platform badge like [Reddit r/REBubble 234 comments]."""
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
        f'padding: 3px 8px; border-radius: 4px; font-size: 11px; '
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
    return f'<table width="100%" cellpadding="0" cellspacing="0"><tr><td height="{height}" style="line-height:{height}px; font-size:1px;">&nbsp;</td></tr></table>\n'


def _section_heading(text: str) -> str:
    """Render a section heading as a table with top border."""
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="border-top: 2px solid #3D3733; padding-top: 12px; font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px; color: #888; font-weight: normal; font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, sans-serif;">'
        f'{_esc(text)}</td></tr></table>\n'
    )


def render_briefing_html(briefing: dict) -> tuple[str, str, int]:
    """Render a conversation briefing dict as styled HTML email.

    Uses table-based layout for Gmail compatibility (Gmail strips <body>
    styles and ignores background on plain <div>s).

    Returns (html, top_theme_label, theme_count).
    """
    FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

    date = briefing.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    stats = briefing.get("stats_summary", {})
    pulse = briefing.get("conversation_pulse", "")
    themes = briefing.get("conversation_themes", [])
    claims = briefing.get("notable_claims", [])
    reality = briefing.get("data_reality_check", {})
    substacker = briefing.get("substacker_takes", [])
    institutional = briefing.get("institutional_signal", [])
    twitter_roundup = briefing.get("twitter_roundup", [])
    collection_errors = briefing.get("_collection_errors", [])
    apify_spend_cents = briefing.get("_apify_spend_cents", 0)

    top_theme = themes[0]["theme"][:60] if themes else "Daily Conversation"
    theme_count = len(themes)

    # Source breakdown
    source_breakdown = stats.get("source_breakdown", {})
    source_str = " &middot; ".join(
        f"{k}: {v}" for k, v in sorted(source_breakdown.items(), key=lambda x: -x[1])
    ) if source_breakdown else ""

    # Build HTML — table-based centering for Gmail
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #F6F7F3;">
<center>
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#F6F7F3" style="background-color: #F6F7F3;">
<tr><td align="center">
<table cellpadding="0" cellspacing="0" style="max-width: 700px; width: 100%; font-family: {FONT}; color: #3D3733; line-height: 1.55; font-size: 14px;">
<tr><td style="padding: 20px 24px;">

<!-- HEADER -->
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="border-bottom: 3px solid #3D3733; padding-bottom: 12px;">
  <h1 style="font-size: 22px; margin: 0 0 4px 0; color: #3D3733; letter-spacing: -0.5px; font-family: {FONT};">Pulse</h1>
  <p style="color: #888; font-size: 12px; margin: 0;">{date} &middot; {_format_number(stats.get('total_items_analyzed', 0))} items &middot; {stats.get('conversation_items', 0)} conversations &middot; {stats.get('platforms_active', 0)} platforms{f' &middot; Apify: ${apify_spend_cents / 100:.2f}' if apify_spend_cents else ''}</p>
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
<td bgcolor="#FBCAB5" style="background-color: #FBCAB5; padding: 10px 14px; border-radius: 6px; border-left: 3px solid #F4743B; font-size: 12px; line-height: 1.5;">
  <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #F4743B; font-weight: 600; margin-bottom: 4px;">Pipeline Issues</div>
  <ul style="margin: 0; padding-left: 16px; color: #3D3733;">{''.join(error_lines)}</ul>
</td></tr></table>
"""
        html += _spacer(16)

    # ── CONVERSATION PULSE (mood box) ──
    if pulse:
        html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td bgcolor="#DADFCE" style="background-color: #DADFCE; padding: 14px 16px; border-radius: 6px; font-size: 14px; line-height: 1.6;">
  <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #67A275; font-weight: 600; margin-bottom: 6px;">Conversation Pulse</div>
  {_esc(pulse)}
</td></tr></table>
"""
        html += _spacer(24)

    # ── SOURCE BREAKDOWN ──
    if source_str:
        html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td bgcolor="#FFFFFF" style="background-color: #fff; padding: 8px 12px; border-radius: 4px; font-size: 11px; color: #999;">
  <span style="font-weight: 600; color: #888;">Sources:</span> {source_str}
</td></tr></table>
"""
        html += _spacer(24)

    # ── CONVERSATION THEMES (main section ~60% of email) ──
    if themes:
        html += _section_heading("Conversation Themes")
        html += _spacer(14)

        for theme in themes[:6]:
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
                trigger_html = f'<div style="font-size: 11px; color: #888; margin-top: 6px; font-style: italic;">Triggered by: {_esc(trigger)}</div>'

            # Topic pills
            topics_html = ""
            if topics:
                pills = " ".join(
                    f'<span style="display: inline-block; background: #F6F7F3; border: 1px solid #ddd; padding: 1px 5px; border-radius: 3px; font-size: 10px; margin-right: 3px; color: #888;">{_esc(t)}</span>'
                    for t in topics[:4]
                )
                topics_html = f'<div style="margin-top: 6px;">{pills}</div>'

            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding-bottom: 18px; border-bottom: 1px solid #e8e8e8;">
  <div style="margin-bottom: 6px;">
    {_heat_badge(heat)}
    <span style="font-size: 16px; font-weight: 600; margin-left: 6px; line-height: 1.3;">{_esc(theme.get('theme', ''))}</span>
  </div>
  <div style="font-size: 13px; color: #555; line-height: 1.5;">{_esc(theme.get('summary', ''))}</div>
  {platform_html}
  {trigger_html}
  {topics_html}
</td></tr></table>
"""
            html += _spacer(18)

        html += _spacer(10)

    # ── TWITTER ROUNDUP ──
    if twitter_roundup:
        html += _section_heading("Twitter Roundup")
        html += _spacer(14)

        for voice in twitter_roundup[:15]:
            url = voice.get("url", "")
            author = _esc(voice.get("author", ""))
            take = _esc(voice.get("take", ""))

            author_html = f'<a href="{url}" target="_blank" style="color: #0BB4FF; text-decoration: none; font-weight: 600;">{author}</a>' if url else f'<span style="font-weight: 600;">{author}</span>'

            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size: 13px; padding: 5px 0; border-bottom: 1px solid #f0f0f0; line-height: 1.45;">
  {author_html} <span style="color: #555;">{take}</span>
</td></tr></table>
"""
        html += _spacer(24)

    # ── NOTABLE CLAIMS + DATA REALITY CHECK ──
    if claims:
        html += _section_heading("Notable Claims vs. Reality")
        html += _spacer(14)

        for claim in claims[:5]:
            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td bgcolor="#FFFFFF" style="background-color: #fff; padding: 12px; border-radius: 6px; border-left: 3px solid #0BB4FF;">
  <div style="font-size: 14px; font-weight: 600; margin-bottom: 4px;">&ldquo;{_esc(claim.get('claim', ''))}&rdquo;</div>
  <div style="font-size: 11px; color: #888; margin-bottom: 6px;">Circulating on: {_esc(claim.get('source', ''))}</div>
  <div style="font-size: 13px; color: #555; line-height: 1.5; border-top: 1px solid #eee; padding-top: 6px;">
    <span style="color: #0BB4FF; font-weight: 600; font-size: 10px; text-transform: uppercase;">Data Check</span><br>
    {_esc(claim.get('data_lake_check', ''))}
  </div>
</td></tr></table>
"""
            html += _spacer(14)

        html += _spacer(14)

    # ── DATA REALITY CHECK (standalone section if present) ──
    if reality and reality.get("summary"):
        stats_html = ""
        for stat in reality.get("key_stats", [])[:5]:
            stats_html += f"""<div style="font-size: 12px; padding: 4px 0; border-top: 1px solid #f0f0f0;">
    <span style="font-weight: 600;">{_esc(stat.get('stat', ''))}</span>
    <span style="color: #888;"> &mdash; {_esc(stat.get('relevance', ''))}</span>
  </div>
"""
        html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td bgcolor="#FFFFFF" style="background-color: #fff; padding: 12px 14px; border-radius: 6px; border: 1px solid #ddd;">
  <div style="font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: #0BB4FF; font-weight: 600; margin-bottom: 6px;">Data Reality Check</div>
  <div style="font-size: 13px; color: #555; line-height: 1.5; margin-bottom: 8px;">{_esc(reality['summary'])}</div>
  {stats_html}
</td></tr></table>
"""
        html += _spacer(28)

    # ── SUBSTACKER TAKES ──
    if substacker:
        html += _section_heading("Substacker Takes")
        html += _spacer(14)

        for take in substacker[:7]:
            url = take.get("url", "")
            title_text = _esc(take.get('title', ''))
            if url:
                title_link = f'<a href="{url}" target="_blank" style="color: #0BB4FF; text-decoration: none;">{title_text}</a>'
            else:
                title_link = title_text

            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="padding-bottom: 14px; border-bottom: 1px solid #e8e8e8;">
  <div style="font-size: 14px;">
    <span style="font-weight: 600;">{_esc(take.get('author', ''))}</span>:
    {title_link}
  </div>
  <div style="font-size: 13px; color: #555; margin-top: 6px; line-height: 1.5;">{_esc(take.get('take', ''))}</div>
</td></tr></table>
"""
            html += _spacer(14)

        html += _spacer(14)

    # ── INSTITUTIONAL SIGNAL (compact 1-line-per-item) ──
    if institutional:
        html += _section_heading("Institutional Signal")
        html += _spacer(10)

        for item in institutional[:5]:
            url = item.get("url", "")
            headline = _esc(item.get('headline', ''))
            source_name = _esc(item.get('source', ''))
            key_num = item.get('key_number', '')

            link = f'<a href="{url}" target="_blank" style="color: #0BB4FF; text-decoration: none;">{headline}</a>' if url else headline

            html += f"""<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="font-size: 13px; padding: 4px 0; border-bottom: 1px solid #f0f0f0;">
  <span style="font-weight: 600; color: #888;">{source_name}</span>: {link}
  {f'<span style="color: #0BB4FF; font-weight: 600; margin-left: 4px;">{_esc(key_num)}</span>' if key_num else ''}
</td></tr></table>
"""
        html += _spacer(28)

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
            url_audit_str = f'<p style="font-size: 11px; color: #ccc; margin: 4px 0 0 0;">URLs: {" &middot; ".join(parts)}</p>'

    html += f"""
<table width="100%" cellpadding="0" cellspacing="0"><tr>
<td style="border-top: 2px solid #3D3733; padding-top: 12px;">
  <p style="font-size: 11px; color: #aaa; margin: 0;">
    {_format_number(stats.get('total_items_analyzed', 0))} items analyzed
    &middot; {stats.get('conversation_items', 0)} conversations
    &middot; {stats.get('platforms_active', 0)} platforms
    &middot; 36h window
  </p>
  {url_audit_str}
  <p style="font-size: 11px; color: #aaa; margin: 8px 0 0 0; text-align: center;">
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
        resp.raise_for_status()
        logger.info(f"Email sent successfully: {subject}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


if __name__ == "__main__":
    # Test render with a sample
    sample = {"date": "2026-02-19", "stats_summary": {"total_items_analyzed": 0, "platforms_active": 0, "conversation_items": 0}}
    html, _, _ = render_briefing_html(sample)
    print(f"HTML length: {len(html)} chars")
