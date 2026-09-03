#!/usr/bin/env python3
"""Refresh the tracked publications snapshot used by the News at Noon
"From Home Economics" section (delivery/he_publications.json). Run from
pulse/scripts/ on a machine that can reach Substack, then commit the file.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from delivery import email_lunch as L  # noqa: E402

items = L._parse_feed(L._fetch_feed_text())
if not items:
    sys.exit("feed returned no items; snapshot left unchanged")
L.HE_PUBLICATIONS_CACHE.write_text(json.dumps(
    {"fetched_at": datetime.now(timezone.utc).isoformat(), "feed": L.HE_FEED_URL,
     "items": items[:20]}, indent=2, ensure_ascii=False))
print(f"wrote {L.HE_PUBLICATIONS_CACHE} ({len(items[:20])} items; newest {items[0].get('date')})")
