"""Shared paths and environment for the editor package. Import this first
in every module so `pulse/scripts` (the renderer, variants, subscribers)
is importable."""
from __future__ import annotations

import os
import sys
from pathlib import Path

EDITOR_DIR = Path(__file__).resolve().parent
PULSE_DIR = EDITOR_DIR.parent
SCRIPTS_DIR = PULSE_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(1, str(SCRIPTS_DIR))  # after the editor dir itself (scripts has its own store.py)

# The synced production DB (read-only from the droplet, by the owner's rule).
PULSE_DB = os.environ.get(
    "PULSE_DB", "/home/aziz/Dropbox/Home Economics/Data/Pulse/pulse.db"
)
OWNER_EMAIL = os.environ.get("NOON_OWNER_EMAIL", "aziz@home-economics.us")
BASE_URL = os.environ.get("NOON_BASE_URL", "http://127.0.0.1:8240").rstrip("/")
# shadow  -> the noon send goes to the owner only (free variant unless NOON_SHADOW_TIER=premium)
# subscribers -> the Clerk list (needs CLERK_SECRET_KEY + PULSE_UNSUB_SECRET + RESEND_API_KEY)
SEND_MODE = os.environ.get("NOON_SEND_MODE", "shadow")
SHADOW_TIER = os.environ.get("NOON_SHADOW_TIER", "free")
LOG_DIR = Path(os.environ.get("NOON_LOG_DIR", str(Path.home() / "work" / "noon" / "logs")))
