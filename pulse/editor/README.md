# News at Noon editor (`pulse/editor/`)

Owner-only web editor for the daily draft, plus the 12:15 ET send. Runs on
the droplet as user-level systemd units (see `install.sh`); the Python
renderer (`delivery/email_lunch.py`) is imported directly, so the preview
is byte-for-byte what gets sent.

Flow each day
1. 11:00 UTC GitHub Actions builds the v4b brief and stores it in `pulse.db`,
   which syncs to the droplet.
2. `noon-ingest.timer` (every 10 min, 11:00–15:59 UTC) copies today's brief
   into `~/work/noon/noon_drafts.db` and emails the owner a one-tap edit link.
3. The owner edits (or not) at `https://noon.homeeconomics.us`.
4. `noon-send.timer` at 12:15 America/New_York sends the draft, unless it is
   Held or already sent manually. `NOON_SEND_MODE=shadow` sends to the owner
   only; `subscribers` sends to the Clerk list.

Environment: `~/.noon_env` (chmod 600) — NOON_SECRET, NOON_PASSWORD,
NOON_BASE_URL, NOON_SEND_MODE, NOON_SHADOW_TIER, NOON_OWNER_EMAIL, PULSE_DB
(the synced DB, read-only), RESEND_API_KEY, and for subscriber mode
CLERK_SECRET_KEY + PULSE_UNSUB_SECRET.

Draft JSON = the stored brief minus bulk keys, plus `intro` (standfirst),
per-entry `tier` (free|premium), `_deleted_entries`, and `date` set to the
send date. Summaries stay markdown (`[verb](url)`), so the renderer's link
rules apply to edits too.

Dev: `ssh -L 8240:127.0.0.1:8240 vps` then open http://127.0.0.1:8240.
Logs: `~/work/noon/logs/`. CLI: `python cli.py ingest|send|render|test`.
