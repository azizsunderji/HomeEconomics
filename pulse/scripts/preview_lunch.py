#!/usr/bin/env python3
"""Render (and optionally send) News at Noon design previews.

Usage (after `source ~/.pulse_dev_env`):
    python preview_lunch.py                          # both tiers from the sample JSON
    python preview_lunch.py --json PATH --tier free
    python preview_lunch.py --id 298 --tier premium  # briefing row from PULSE_DB
    python preview_lunch.py --to aziz@home-economics.us

Output files: {--out}/noon_free.html and/or {--out}/noon_premium.html.

Variant pipeline matches v4b_runner._render_lunch_variants:
    premium = scrub_archive_links(render_lunch_html(b, "premium"))
    free    = make_free_variant(scrub_archive_links(render_lunch_html(b, "free")))
Sends get the News at Noon compliance footer via v4b_runner._lunch_footer
with a demo unsubscribe URL, use v4b_runner.EMAIL_FROM, and go out through
v3_1_runner._post_resend.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from delivery.email_lunch import render_lunch_html  # noqa: E402
from delivery.variants import make_free_variant, scrub_archive_links  # noqa: E402

DEFAULT_JSON = Path.home() / "work" / "v4_scratch" / "v4b_run4.json"
DEFAULT_OUT = Path.home() / "work" / "v4_scratch"
PREVIEW_UNSUB_URL = "https://homeeconomics.us/api/pulse/unsubscribe?u=user_PREVIEW&t=demo"


def load_briefing(args: argparse.Namespace) -> dict:
    if args.id is not None:
        db = os.environ.get("PULSE_DB")
        if not db:
            sys.exit("PULSE_DB is not set (source ~/.pulse_dev_env)")
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT content_json FROM briefings WHERE id = ?", (args.id,)
        ).fetchone()
        if not row:
            conn.close()
            sys.exit(f"no briefing with id {args.id} in {db}")
        data = json.loads(row[0])
        if "_own_posts" not in data:
            from delivery.own_posts import load_own_posts
            data["_own_posts"] = load_own_posts(conn)
        conn.close()
        return data
    path = Path(args.json).expanduser()
    return json.loads(path.read_text())


def render_tier(briefing: dict, tier: str) -> tuple[str, str, int]:
    html, top_title, n = render_lunch_html(briefing, tier=tier)
    html = scrub_archive_links(html)
    if tier == "free":
        html = make_free_variant(html)
    return html, top_title, n


def send_preview(html: str, tier: str, top_title: str, to: str,
                 from_addr: str | None) -> bool:
    # Imported lazily: v4b_runner pulls in anthropic/numpy and is only
    # needed for sending. EMAIL_FROM / PRODUCT_NAME / _lunch_footer are the
    # News at Noon versions (the v3_1 ones still say "Pulse").
    from v3_1_runner import _post_resend
    from v4b_runner import EMAIL_FROM, PRODUCT_NAME, _lunch_footer, _noon_date_str
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        sys.exit("RESEND_API_KEY is not set (source ~/.pulse_dev_env)")
    subject = f"[DESIGN PREVIEW – {tier.upper()}] {PRODUCT_NAME}: {_noon_date_str()}"
    payload = {
        "from": from_addr or EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "html": _lunch_footer(html, PREVIEW_UNSUB_URL),
    }
    ok = _post_resend(api_key, "https://api.resend.com/emails", payload)
    print(f"  send {tier} -> {to}: {'ok' if ok else 'FAILED'}  subject={subject!r}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--json", default=str(DEFAULT_JSON),
                     help="path to a v4b briefing JSON (default: %(default)s)")
    src.add_argument("--id", type=int, help="briefings.id to load content_json from PULSE_DB")
    ap.add_argument("--tier", choices=["free", "premium", "both"], default="both")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory (default: %(default)s)")
    ap.add_argument("--to", help="send previews to this address via Resend")
    ap.add_argument("--from", dest="from_addr", default=None,
                    help="override the From header (default: v4b_runner.EMAIL_FROM)")
    args = ap.parse_args()

    briefing = load_briefing(args)
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    tiers = ["free", "premium"] if args.tier == "both" else [args.tier]

    rc = 0
    for tier in tiers:
        html, top_title, n = render_tier(briefing, tier)
        out_path = out_dir / f"noon_{tier}.html"
        out_path.write_text(html)
        print(f"{tier:8s} {out_path}  ({len(html):,} bytes, {n} entries, top={top_title!r})")
        if args.to:
            if not send_preview(html, tier, top_title, args.to, args.from_addr):
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
