#!/usr/bin/env python3
"""Send a Pulse health dashboard message via Resend email.

Reads the body from --text or stdin and POSTs it to the Resend HTTP API as
a simple branded HTML email. Reuses the same Resend account as the daily
Pulse briefing (RESEND_API_KEY repo secret).

Input format (when piped from build_digest.py / build_alert.py):
    Either:
        SUBJECT: <subject line>
        ---
        <body lines>
    Or just plain body lines (in which case --subject is required).

Env:
    RESEND_API_KEY — required unless --dry-run.

Usage:
    python build_digest.py | python send_email.py
    python send_email.py --text "hi" --subject "Pulse Health · TEST"
    python build_digest.py | python send_email.py --dry-run
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from urllib import request, error


DEFAULT_TO = "aziz@home-economics.us"
DEFAULT_FROM = "Pulse Health <onboarding@resend.dev>"
RESEND_API = "https://api.resend.com/emails"

# Brand palette (see Home Economics CLAUDE.md)
BG_CREAM = "#F6F7F3"
BRAND_BLACK = "#3D3733"
BRAND_BLUE = "#0BB4FF"
BORDER_GREY = "#e8e8e8"

FONT_STACK = (
    "'ABC Oracle Edu', 'ABC Oracle', -apple-system, BlinkMacSystemFont, "
    "'Segoe UI', Roboto, sans-serif"
)

DASHBOARD_LINK_TEXT = "home-economics.us/pulse-dashboard"
DASHBOARD_LINK_HREF = "https://home-economics.us/pulse-dashboard"


def _parse_subject_and_body(raw: str, override_subject: str | None) -> tuple[str, str]:
    """Pull a `SUBJECT:` prefix off the top of the raw text, if present.

    Returns (subject, body). --subject CLI arg always wins over an embedded
    SUBJECT: line.
    """
    text = raw.lstrip("﻿").lstrip()
    embedded_subject = None
    body = text

    lines = text.split("\n", 1)
    if lines and lines[0].startswith("SUBJECT:"):
        embedded_subject = lines[0][len("SUBJECT:"):].strip()
        rest = lines[1] if len(lines) > 1 else ""
        # Strip an optional `---` separator and the surrounding blank lines.
        rest = rest.lstrip("\n")
        if rest.startswith("---"):
            after = rest.split("\n", 1)
            rest = after[1] if len(after) > 1 else ""
        body = rest.lstrip("\n")

    subject = override_subject or embedded_subject or ""
    return subject, body.rstrip()


def _is_emoji_status_line(line: str) -> bool:
    """First line is the status line if it begins with one of our status emoji."""
    stripped = line.lstrip()
    return any(stripped.startswith(e) for e in ("🟢", "🟡", "🔴"))


def render_html(body: str) -> str:
    """Wrap a plain-text body in branded HTML.

    - Cream background, max-width 600px, single column.
    - Status emoji on the first line is rendered at 32px.
    - Bullet markers ("- " or "  ") are preserved as text.
    - The footer `📊 home-economics.us/pulse-dashboard` line becomes a clickable link.
    - All other newlines become <br>.
    """
    lines = body.split("\n")

    rendered: list[str] = []
    for idx, raw_line in enumerate(lines):
        line = raw_line
        escaped = html.escape(line, quote=False)

        # First-line status emoji: oversize it.
        if idx == 0 and _is_emoji_status_line(line):
            stripped = line.lstrip()
            emoji = stripped[0]
            remainder = stripped[1:]
            escaped_remainder = html.escape(remainder, quote=False)
            rendered.append(
                f'<span style="font-size: 32px; line-height: 1.2; vertical-align: middle;">{emoji}</span>'
                f'<span style="font-size: 20px; font-weight: 600; vertical-align: middle;">{escaped_remainder}</span>'
            )
            continue

        # Dashboard link line — anywhere it appears.
        if DASHBOARD_LINK_TEXT in line:
            # Replace the URL text with an <a> tag, preserve any prefix (e.g. emoji).
            before, _, after = escaped.partition(html.escape(DASHBOARD_LINK_TEXT))
            link_html = (
                f'<a href="{DASHBOARD_LINK_HREF}" '
                f'style="color: {BRAND_BLUE}; text-decoration: none;">'
                f'{html.escape(DASHBOARD_LINK_TEXT)}</a>'
            )
            rendered.append(f"{before}{link_html}{after}")
            continue

        # Section headings (all-caps lines like "WHAT IT DID"): give them weight.
        stripped_for_heading = line.strip()
        if (
            stripped_for_heading
            and stripped_for_heading == stripped_for_heading.upper()
            and stripped_for_heading.replace(" ", "").replace("'", "").isalpha()
            and len(stripped_for_heading) >= 3
        ):
            rendered.append(
                f'<span style="font-size: 13px; letter-spacing: 1.5px; '
                f'color: #888; font-weight: 600;">{escaped}</span>'
            )
            continue

        rendered.append(escaped)

    # Join with <br>, but leave blank lines as visual spacers.
    body_html = "<br>\n".join(rendered)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: {BG_CREAM};">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="{BG_CREAM}" style="background-color: {BG_CREAM};">
<tr><td align="center" style="padding: 24px 12px;">
<table cellpadding="0" cellspacing="0" style="max-width: 600px; width: 100%; background-color: {BG_CREAM};">
<tr><td style="font-family: {FONT_STACK}; font-size: 16px; line-height: 1.55; color: {BRAND_BLACK}; padding: 8px 16px;">
{body_html}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def send_via_resend(
    api_key: str,
    to: str,
    sender: str,
    subject: str,
    html_body: str,
) -> tuple[bool, str]:
    """POST to Resend. Returns (ok, info)."""
    payload = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html_body,
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        RESEND_API,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return True, f"HTTP {resp.status}"
    except error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            err_body = ""
        return False, f"HTTP {e.code}: {err_body}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=None,
                    help="Message body. If omitted, read from stdin.")
    ap.add_argument("--subject", default=None,
                    help="Email subject. Overrides any SUBJECT: header in the body.")
    ap.add_argument("--to", default=DEFAULT_TO,
                    help=f"Recipient (default {DEFAULT_TO}).")
    ap.add_argument("--from", dest="sender", default=DEFAULT_FROM,
                    help=f"From address (default '{DEFAULT_FROM}').")
    ap.add_argument("--dry-run", action="store_true",
                    help="Render HTML to stdout, do not call Resend.")
    args = ap.parse_args()

    raw = args.text if args.text is not None else sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("ERROR: empty message body.\n")
        sys.exit(2)

    subject, body = _parse_subject_and_body(raw, args.subject)
    if not body.strip():
        sys.stderr.write("ERROR: empty message body after subject parsing.\n")
        sys.exit(2)
    if not subject:
        sys.stderr.write("ERROR: no subject provided (use --subject or 'SUBJECT:' header).\n")
        sys.exit(2)

    html_body = render_html(body)

    if args.dry_run:
        sys.stderr.write(f"DRY RUN — would send to {args.to}\n")
        sys.stderr.write(f"Subject: {subject}\n")
        sys.stdout.write(html_body)
        sys.exit(0)

    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        sys.stderr.write("ERROR: RESEND_API_KEY env var not set.\n")
        sys.exit(2)

    ok, info = send_via_resend(api_key, args.to, args.sender, subject, html_body)
    if not ok:
        sys.stderr.write(f"Resend send failed: {info}\n")
        sys.exit(1)
    sys.stderr.write(f"Resend send ok: {info}\n")


if __name__ == "__main__":
    main()
