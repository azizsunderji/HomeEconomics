"""Render a draft exactly the way the send does.

This mirrors v4b_runner._render_lunch_variants / _lunch_footer /
_lunch_subject without importing v4b_runner (which pulls in numpy,
hdbscan and the Anthropic client). If the runner's send path changes,
change this too — the point of the editor preview is that what the
owner sees is what goes out.
"""
from __future__ import annotations

import re
from datetime import datetime

import paths  # noqa: F401  (sys.path setup)
from delivery.email_lunch import SIGNUP_URL, render_lunch_html
from delivery.variants import make_free_variant, scrub_archive_links

PRODUCT_NAME = "News at Noon"
EMAIL_FROM = "News at Noon <pulse@home-economics.us>"
OWN_DOMAINS = ("homeeconomics.substack.com", "home-economics.us", "homeeconomics.us")
PULSE_POSTAL_ADDRESS = "Home Economics, 12 East 49th Street, 11th floor, New York, NY 10017"


def _protect_own_links(html: str) -> tuple[str, dict]:
    keep: dict = {}

    def _sub(m):
        href = m.group(1)
        if any(d in href for d in OWN_DOMAINS):
            key = f"__OWNLINK_{len(keep)}__"
            keep[key] = href
            return f'href="{key}"'
        return m.group(0)

    return re.sub(r'href="([^"]+)"', _sub, html), keep


def _restore_own_links(html: str, keep: dict) -> str:
    for key, href in keep.items():
        html = html.replace(f'href="{key}"', f'href="{href}"')
    return html


def render_variants(draft: dict) -> tuple[str, str, str]:
    """(premium_html, free_html, top_title) — same steps as the runner."""
    import auth
    draft = dict(draft, _web_key=auth.web_token())  # premium "Read on the web" link
    premium_html, top, _n = render_lunch_html(draft, tier="premium")
    premium_html = scrub_archive_links(premium_html)
    free_raw, _t, _n2 = render_lunch_html(draft, tier="free")
    free_raw = scrub_archive_links(free_raw)
    protected, keep = _protect_own_links(free_raw)
    free_html = _restore_own_links(make_free_variant(protected), keep)
    return premium_html, free_html, top or ""


def render_social(draft: dict) -> str:
    """HTML for the public PDF (posted on social media): the free edition
    with sign-up copy, walled links pointing at the sign-up page rather
    than the upgrade wall. Same protect/restore of own-domain links as
    the free email; no web key (the social edition never unlocks premium)."""
    raw, _t, _n = render_lunch_html(draft, tier="social")
    raw = scrub_archive_links(raw)
    protected, keep = _protect_own_links(raw)
    return _restore_own_links(make_free_variant(protected, wall_url=SIGNUP_URL), keep)


def with_footer(html: str, unsub_url: str | None) -> str:
    parts = []
    if unsub_url:
        parts.append(f'<a href="{unsub_url}" style="color:#888888;">Unsubscribe</a>')
    if PULSE_POSTAL_ADDRESS:
        parts.append(PULSE_POSTAL_ADDRESS)
    if not parts:
        return html
    footer = (
        '<div style="max-width:600px;margin:24px auto 0;padding:16px 24px 24px;'
        'font-size:12px;color:#888888;text-align:center;">'
        f"You&rsquo;re receiving {PRODUCT_NAME} at this address. "
        + " &middot; ".join(parts) + "</div>"
    )
    idx = html.lower().rfind("</body>")
    return html[:idx] + footer + html[idx:] if idx != -1 else html + footer


def date_label(date: str) -> str:
    """'2026-09-03' -> 'Thursday, September 3, 2026'."""
    return datetime.strptime(date[:10], "%Y-%m-%d").strftime("%A, %B %-d, %Y")


def subject(date: str) -> str:
    return f"{PRODUCT_NAME}: {date_label(date)}"


def preview(draft: dict, tier: str) -> str:
    premium_html, free_html, _ = render_variants(draft)
    return with_footer(premium_html if tier == "premium" else free_html, None)


def free_count(draft: dict) -> tuple[int, int]:
    """(shown_in_free, total) — the numbers the free banner will print."""
    entries = [e for e in (draft.get("entries") or []) if isinstance(e, dict)]
    shown = sum(1 for e in entries if e.get("tier") != "premium")
    return shown, len(entries)
