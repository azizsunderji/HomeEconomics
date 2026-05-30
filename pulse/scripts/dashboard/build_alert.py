#!/usr/bin/env python3
"""Build a RED-status alert message for an in-flight Pulse failure.

Used when something breaks mid-run — separate from the daily digest. The
calling workflow decides the --kind. Output goes to stdout (with a
SUBJECT: header + `---` separator); pipe to send_email.py to deliver
via Resend.

Usage:
    python build_alert.py --kind gha_failure --context "Sonnet timeout"
    python build_alert.py --kind hallucination_block --context "..." \
        --briefing-id 123
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

KINDS = {
    "gha_failure": {
        "what": "The AI write-up crashed",
        "next": "backup run scheduled at 8:20am and 8:40am ET — likely to catch it.\nI'll send another update if it stays broken.",
    },
    "hallucination_block": {
        "what": "A fabricated link was caught in the write-up",
        "next": "The bad sentence was removed before the email went out. No action needed; flagging for your awareness.",
    },
    "zero_items_critical_feed": {
        "what": "A critical feed returned zero items",
        "next": "Check whether the feed is down or the credentials need refreshing.",
    },
    "budget_exhausted": {
        "what": "A spend cap was hit mid-run",
        "next": "Twitter collection paused for the rest of today. Volume may be lower than usual.",
    },
}


def _now_label() -> str:
    now = datetime.now(ET)
    # e.g. "8:12am ET, May 30"
    time_str = now.strftime("%-I:%M%p ET").lower().replace("am et", "am ET").replace("pm et", "pm ET")
    date_str = now.strftime("%b %-d")
    return f"{time_str}, {date_str}"


def build(kind: str, context: str, briefing_id: int | None) -> tuple[str, str]:
    info = KINDS.get(kind, {
        "what": "Something went wrong",
        "next": "Investigating.",
    })

    lines = []
    lines.append("🔴 Pulse alert — something broke")
    lines.append(f"What: {info['what']}")
    lines.append(f"When: {_now_label()}")
    if context:
        lines.append(f"Why: {context}")
    if briefing_id:
        lines.append(f"Briefing: #{briefing_id}")
    lines.append(f"Next: {info['next']}")

    subject = f"Pulse ALERT · {info['what']}"
    return subject, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=list(KINDS.keys()))
    ap.add_argument("--context", default="",
                    help="Free-text explanation of what went wrong.")
    ap.add_argument("--briefing-id", type=int, default=None)
    args = ap.parse_args()
    subject, body = build(args.kind, args.context, args.briefing_id)
    sys.stdout.write(f"SUBJECT: {subject}\n---\n{body}\n")


if __name__ == "__main__":
    main()
