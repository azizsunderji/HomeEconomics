# News at Noon editor (`pulse/editor/`)

Owner-only web editor for the daily draft, plus the noon ET send (timer fires 11:59 ET so it lands at noon). Runs on
the droplet as user-level systemd units (see `install.sh`); the Python
renderer (`delivery/email_lunch.py`) is imported directly, so the preview
is byte-for-byte what gets sent.

Flow each day
1. 11:00 UTC GitHub Actions builds the v4b brief and stores it in `pulse.db`,
   which syncs to the droplet.
2. `noon-ingest.timer` (Monday to Friday, every 10 min, 11:00–15:59 UTC) copies today's brief
   into `~/work/noon/noon_drafts.db` and emails the owner a one-tap edit link.
3. The owner edits (or not) at `https://noon.homeeconomics.us`.
4. `noon-send.timer` at 11:59 America/New_York, Monday to Friday, sends the draft, unless it is
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

## Morning signup report (`signup_report.py`)

`noon-report.timer` runs `signup_report.py` at 07:00 America/New_York every day
(Persistent=true; log `~/work/noon/logs/report.log`). It reads the Clerk user list and
emails NOON_OWNER_EMAIL "News at Noon signups: <weekday, month day>": free signups,
premium subscriptions and unsubscribes in the last 24 hours (email, time ET, source; for
unsubscribes also how long they were subscribed), then totals (subscribers now, free and
premium; signups, premium and unsubscribes over 7 days; signups by source over 7 days and
since launch). Reply-To is the owner, like the edition.

Fields it reads (all written by the portal): `pulseNewsletter.{subscribed, since,
unsubscribedAt, source}` and `tools.{pulse, pulseSince}`. `source` is the `?src=` tag on
the link the reader arrived with (`homeeconomics.us/noon?src=x-0908`), or `ref:<host>`
from the referrer when there was no tag; `tools.pulseSince` is stamped by the Stripe
webhook the first time `tools.pulse` turns on (from 2026-09-08; earlier premium
subscriptions carry no timestamp and only appear in the totals).

CLI: `python signup_report.py [--hours N] [--to EMAIL] [--dry-run [--out FILE]]`
(`--dry-run` writes the HTML and prints its path instead of sending).

Installing the units by hand (install.sh restarts the editor, which is the owner's call):
`cp systemd/noon-report.* ~/.config/systemd/user/ && systemctl --user daemon-reload &&
systemctl --user enable --now noon-report.timer`.
