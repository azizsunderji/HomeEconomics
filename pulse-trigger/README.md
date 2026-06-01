# Pulse Cron Trigger

A tiny Cloudflare Worker that fires the Pulse synthesis workflow at exactly
11:00 UTC every day — bypassing GitHub Actions' unreliable cron scheduler.

## Why

GitHub's `schedule:` trigger is best-effort. Under load (typically at the
top of the hour), it can fire 30–90+ minutes late, sometimes more. A
Cloudflare Worker Cron Trigger fires within a second of the scheduled UTC
time, every time.

## How it works

`worker.js` runs on Cloudflare's edge. The `scheduled` handler is invoked
by the cron trigger configured in `wrangler.toml`. It POSTs to the GitHub
`workflow_dispatch` endpoint, which queues the Pulse synthesis workflow
immediately.

The Worker also exposes a manual trigger via HTTP POST (gated by a secret
query-string key) for testing:

```bash
curl -X POST "https://pulse-cron-trigger.<your-subdomain>.workers.dev/?key=<MANUAL_TRIGGER_KEY>"
```

## Deployment (one-time setup, ~10 min)

### Step 1 — Create a GitHub Personal Access Token

1. Visit https://github.com/settings/personal-access-tokens/new (fine-grained PAT)
2. Token name: `Pulse Cron Trigger`
3. Expiration: 1 year (or whatever you prefer)
4. Repository access: **Only select repositories** → `azizsunderji/HomeEconomics`
5. Permissions → Repository permissions → **Actions: Read and write**
6. Generate token. Copy it. (Starts with `github_pat_`.)

### Step 2 — Install Wrangler and deploy

```bash
npm install -g wrangler
cd /Users/azizsunderji/Dropbox/Home\ Economics/HomeEconomics/pulse-trigger
wrangler login   # opens browser, log in to your Cloudflare account
wrangler deploy
```

### Step 3 — Set the two secrets

```bash
wrangler secret put GITHUB_TOKEN
# paste the github_pat_... token, hit enter

wrangler secret put MANUAL_TRIGGER_KEY
# paste any random string (e.g. `openssl rand -hex 16`), hit enter
```

### Step 4 — Verify

```bash
# Manual fire test:
curl -X POST "https://pulse-cron-trigger.<your-subdomain>.workers.dev/?key=<your-MANUAL_TRIGGER_KEY>"
# Should return: workflow_dispatch fired (HTTP 204)

# Watch the workflow get triggered:
gh run list -R azizsunderji/HomeEconomics --workflow="Pulse Daily Synthesis & Email" --limit 1
```

The cron will start firing automatically every day at 11:00 UTC. No further
action required.

## Cost

Free. Cloudflare Workers free plan covers 100,000 requests/day; this Worker
makes one request per day plus occasional manual tests.

## Failure modes

- If Cloudflare is down: the GitHub `schedule:` cron in `pulse-synth.yml`
  still tries at 11:00 / 11:20 / 11:40 UTC as a fallback. Belt-and-suspenders.
- If the GitHub PAT expires: the Worker will start failing. Check Cloudflare
  logs (Workers → pulse-cron-trigger → Logs) or wait for the daily Pulse
  Health email's "WHAT'S WORTH WATCHING" section to surface it.
- If the `pulse-synth.yml` workflow is renamed: update `WORKFLOW_FILE` in
  `wrangler.toml` and redeploy.
