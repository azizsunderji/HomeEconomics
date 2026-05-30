# Pulse Health Dashboard

Daily email digest of what the Pulse housing pipeline did, caught, missed, and cost.
Delivered the moment the main "Pulse Daily Synthesis & Email" workflow finishes.

**No setup required.** The dashboard uses the same Resend account already configured
for the daily Pulse briefing (`RESEND_API_KEY` repo secret). Emails go to
`aziz@home-economics.us`.

## Files

- `build_digest.py` — renders the daily digest (status + sections) with a `SUBJECT:` header.
- `build_alert.py` — renders a red alert for mid-run failures (separate from the digest).
- `send_email.py` — wraps stdin text in branded HTML and POSTs to Resend.
- `../../../.github/workflows/pulse-dashboard.yml` — runs after every Pulse run and emails the digest. Also sends a red alert on failure.

## Daily flow

1. Main `Pulse Daily Synthesis & Email` workflow runs at 12:06 UTC (12:20 / 12:40 UTC backups).
2. When it finishes, GitHub fires `Pulse Health Dashboard` automatically.
3. The dashboard pulls the latest `pulse.db` from Dropbox, runs `build_digest.py`, and pipes the output to `send_email.py`.
4. If the upstream workflow's conclusion was `failure`, a red alert email also goes out immediately.

## Manual trigger

Force a digest right now (e.g. to test after a change):

```bash
gh workflow run pulse-dashboard.yml -R azizsunderji/HomeEconomics
```

Or render locally without sending (no API key required):

```bash
python pulse/scripts/dashboard/build_digest.py | \
  python pulse/scripts/dashboard/send_email.py --dry-run
```

Render a specific past briefing:

```bash
python pulse/scripts/dashboard/build_digest.py --briefing-id 123
```

## When something looks wrong

- Digest red or never arrived: check `https://github.com/azizsunderji/HomeEconomics/actions/workflows/pulse-dashboard.yml`.
- Email not delivered: confirm `RESEND_API_KEY` is set in repo secrets.
- Quality-log noise: see `pulse/scripts/analysis/synthesize.py` (URL-audit pass).
