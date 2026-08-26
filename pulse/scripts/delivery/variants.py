"""Free/premium email variant transforms for the Pulse daily email.

Two pure functions over rendered email HTML:

  make_free_variant(html)   — free-tier email: every EXTERNAL link is
                              rewritten to the upgrade wall so free
                              readers see the briefing but can't click
                              through to sources. No source URL may
                              leak anywhere (hrefs, title/alt
                              attributes, plain-text URLs).

  scrub_archive_links(html) — premium email: archive.ph / archive.today
                              snapshot links (which can enter the
                              pipeline via tweet-link unwrapping or
                              enrich_links captured from snapshot
                              pages) are replaced with the original
                              source URL when it is recoverable from
                              the archive URL itself, else the anchor
                              is unwrapped to plain text.

Both operate on tag/text segments split with a conservative regex — no
HTML re-serialization, so untouched markup passes through byte-for-byte
(important for email-client-fragile inline-styled HTML).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# ── URL allowlist (free variant) ────────────────────────────────────────
# Links to our own properties survive in the free email: the upgrade
# wall itself, unsubscribe endpoints, and hosted assets. Everything
# else external gets walled.
ALLOWED_DOMAINS = (
    "homeeconomics.us",
    "home-economics.us",
)

UPGRADE_WALL_URL = "https://homeeconomics.us/pulse/upgrade?src=email"

# Schemes that are never rewritten (not external content links).
_SAFE_SCHEMES = ("mailto:", "tel:", "sms:", "cid:", "data:")

# Archive-snapshot domains (premium scrub). archive.today rotates
# through several mirror TLDs.
ARCHIVE_DOMAINS = (
    "archive.ph", "archive.today", "archive.is", "archive.md",
    "archive.li", "archive.vn", "archive.fo",
)

# Split HTML into tag / text segments. Tags never nest, so this simple
# alternation is safe for rendered email HTML.
_TAG_SPLIT_RE = re.compile(r"(<[^>]*>)")
_HREF_RE = re.compile(r"""(\bhref\s*=\s*)(["'])(.*?)\2""",
                      re.IGNORECASE | re.DOTALL)
_TITLE_ALT_RE = re.compile(r"""(\b(?:title|alt)\s*=\s*)(["'])(.*?)\2""",
                           re.IGNORECASE | re.DOTALL)
_TEXT_URL_RE = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)


def _domain_of(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except ValueError:
        return ""
    return host.split(":")[0].removeprefix("www.")


def _is_allowlisted(url: str) -> bool:
    host = _domain_of(url)
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def _is_external_link(url: str) -> bool:
    """True when the URL is an external http(s) content link that the
    free variant must wall. Relative URLs, fragments, mailto:/tel:/…,
    and allowlisted own-domain links are not external."""
    u = url.strip()
    if not u or u.startswith("#"):
        return False
    low = u.lower()
    if any(low.startswith(s) for s in _SAFE_SCHEMES):
        return False
    if not (low.startswith("http://") or low.startswith("https://")):
        return False  # relative or unknown-scheme — leave alone
    return not _is_allowlisted(u)


def _is_archive_url(url: str) -> bool:
    host = _domain_of(url)
    return any(host == d or host.endswith("." + d) for d in ARCHIVE_DOMAINS)


# ── free variant ────────────────────────────────────────────────────────

def make_free_variant(html: str) -> str:
    """Rewrite every external content URL in a rendered premium email to
    the upgrade wall. Leaves <img src> / asset URLs, own-domain links
    (homeeconomics.us / home-economics.us), mailto:, and relative URLs
    untouched. Also scrubs URLs from title/alt attributes and from
    plain text so no source URL leaks in the free tier."""
    out: list[str] = []
    for seg in _TAG_SPLIT_RE.split(html):
        if seg.startswith("<"):
            low = seg.lower()
            if low.startswith("<a") and (low[2:3].isspace() or low[2:3] == ">"):
                # Anchor tag: wall external hrefs.
                seg = _HREF_RE.sub(
                    lambda m: (m.group(1) + m.group(2) + UPGRADE_WALL_URL
                               + m.group(2))
                    if _is_external_link(m.group(3)) else m.group(0),
                    seg,
                )
            # Any tag: scrub URLs that appear inside title="…" / alt="…".
            seg = _TITLE_ALT_RE.sub(
                lambda m: m.group(1) + m.group(2) + _TEXT_URL_RE.sub(
                    lambda u: "" if _is_external_link(u.group(0))
                    else u.group(0),
                    m.group(3),
                ) + m.group(2),
                seg,
            )
            out.append(seg)
        else:
            # Text node: replace bare external URLs with the wall URL.
            out.append(_TEXT_URL_RE.sub(
                lambda u: UPGRADE_WALL_URL
                if _is_external_link(u.group(0)) else u.group(0),
                seg,
            ))
    return "".join(out)


def make_free_text(text: str) -> str:
    """Free-variant transform for a plain-text email part (if one is
    ever added): every external URL becomes the upgrade wall URL."""
    return _TEXT_URL_RE.sub(
        lambda u: UPGRADE_WALL_URL if _is_external_link(u.group(0))
        else u.group(0),
        text,
    )


# ── premium archive.ph scrub ────────────────────────────────────────────

# Original-URL recovery from archive snapshot URLs. Recoverable shapes
# embed the source URL in the path:
#   https://archive.ph/o/AbC12/https://www.wsj.com/…   (outlink on a snapshot)
#   https://archive.ph/newest/https://www.wsj.com/…    (newest-snapshot query)
#   https://archive.ph/2026.08.01-120000/https://…     (dated snapshot)
# Bare snapshot hashes (https://archive.ph/AbC12) carry no original URL.
# Match against the FULL archive URL (not just urlparse().path) so a
# query string on the embedded original ("…/https://x.com/a?b=1")
# survives recovery.
_EMBEDDED_URL_RE = re.compile(
    r"^[a-z][a-z0-9+.-]*://[^/]+/.*?(https?://.+)$", re.IGNORECASE)

# Full anchor element whose href points at an archive domain. Anchors in
# the rendered email are simple inline links (no nested <a>), so a
# non-greedy match to the first </a> is safe.
_ANCHOR_RE = re.compile(r"""<a\b[^>]*\bhref\s*=\s*(["'])(.*?)\1[^>]*>(.*?)</a>""",
                        re.IGNORECASE | re.DOTALL)


def _recover_original(archive_url: str) -> str | None:
    """Extract the original source URL embedded in an archive snapshot
    URL, or None when the URL is a bare snapshot hash."""
    m = _EMBEDDED_URL_RE.match(archive_url.strip())
    if not m:
        return None
    candidate = m.group(1).strip()
    # Sanity: recovered thing must itself parse to a non-archive host.
    if not _domain_of(candidate) or _is_archive_url(candidate):
        return None
    return candidate


def scrub_archive_links(html: str) -> str:
    """Premium-email scrub: replace archive.ph/.today/.is/… anchor hrefs
    with the recoverable original source URL; when the original is not
    recoverable, unwrap the anchor to its inner text. Bare archive URLs
    in text nodes are likewise replaced or removed."""
    def _fix_anchor(m: re.Match) -> str:
        href, inner = m.group(2), m.group(3)
        if not _is_archive_url(href):
            return m.group(0)
        original = _recover_original(href)
        if original:
            return _HREF_RE.sub(
                lambda hm: hm.group(1) + hm.group(2) + original + hm.group(2),
                m.group(0), count=1)
        return inner  # not recoverable — drop the link, keep the text

    html = _ANCHOR_RE.sub(_fix_anchor, html)

    # Bare archive URLs in text segments (outside tags).
    def _fix_text_url(m: re.Match) -> str:
        url = m.group(0)
        if not _is_archive_url(url):
            return url
        return _recover_original(url) or ""

    out: list[str] = []
    for seg in _TAG_SPLIT_RE.split(html):
        out.append(seg if seg.startswith("<")
                   else _TEXT_URL_RE.sub(_fix_text_url, seg))
    return "".join(out)
