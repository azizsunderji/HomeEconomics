#!/usr/bin/env python3
"""Generate a briefing and open the HTML preview."""
import sys
import subprocess

sys.path.insert(0, ".")

from store import get_db
from analysis.synthesize import generate_daily_briefing
from delivery.email_briefing import render_briefing_html
import json

conn = get_db()
print("Generating briefing...")
briefing = generate_daily_briefing(conn)

if "error" in briefing:
    print(f"ERROR: {briefing['error']}")
    raw = briefing.get("raw_response", "")
    if raw:
        print(f"Raw: {raw[:500]}")
else:
    html, headline, count = render_briefing_html(briefing)
    with open("../outputs/briefing_preview.html", "w") as f:
        f.write(html)
    print(f"Done: {count} stories, {len(html)} chars HTML")
    subprocess.run(["open", "../outputs/briefing_preview.html"])
