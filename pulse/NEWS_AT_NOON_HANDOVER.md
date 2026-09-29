# News at Noon — handover (written 2026-09-03, end of day)

This is the state of the product formerly called Pulse, now **News at Noon**, plus the
detailed brief for the two features to build next: the **editing interface** and the
**signup / upgrade pages**. Everything described as "done" is committed on `main` of
`azizsunderji/HomeEconomics` unless stated otherwise.

---

## 1. What exists and where

### Repos and machines
- **Pipeline repo:** `azizsunderji/HomeEconomics` (private). Daily brief code under `pulse/`.
  Live clone on the droplet at `/home/aziz/work/HomeEconomics` (`ssh vps`, host 104.236.210.18,
  user aziz). The Dropbox copy on the Mac is stale — do not edit it.
- **Portal repo (site):** `homeeconomics/portal` → Vercel project `home-economics/portal`,
  live at `https://homeeconomics.us`. Clone on the droplet at `/home/aziz/work/portal`.
  Clerk (auth + user metadata), Stripe (billing), Resend (email) already wired for the Pro Map.
- **Database:** SQLite `pulse.db` in Dropbox (`Data/Pulse/pulse.db`, ~660 MB), synced to the
  droplet at `/home/aziz/Dropbox/Home Economics/Data/Pulse/pulse.db`. GitHub Actions rclones it
  in and out on every run. Table `briefings(id, briefing_type, content_json, created_at)`;
  table `items` (the corpus: twitter, bluesky, rss, google_news, gmail, substack, hackernews;
  `engagement_raw` JSON has likes for tweets).
- **Droplet dev loop:** `source ~/.pulse_dev_env` → venv + ANTHROPIC/OPENAI/RESEND keys +
  `PULSE_DB=/home/aziz/work/pulse_dev.db` (a Sept-1 copy; safe to write). Never write the
  Dropbox DB from the droplet. Scratch outputs in `~/work/v4_scratch/`.

### Pipeline (how a brief is made)
- `.github/workflows/pulse-daily.yml` — scrapers, 4×/day.
- `.github/workflows/pulse-synth.yml` — 11:00 UTC (7am ET): v1 synthesis (`analysis/synthesize.py`,
  Opus, writes `briefing_type='daily'`), then the send step, gated by the **repo variable
  `PULSE_PIPELINE`**:
  - `v3_1` (or unset): old v3.1 runner sends (legacy).
  - **`v4b` (current):** `pulse/scripts/v4b_runner.py --to aziz@home-economics.us` — shadow send to
    the owner only, premium variant, stores `briefing_type='daily_v4b_attach'`.
  - Rollback: `gh variable set PULSE_PIPELINE --body v3_1 --repo azizsunderji/HomeEconomics`.
  - Cutover to subscribers = remove `--to` from that step (the runner then calls
    `send_lunch_to_subscribers`, which reads the Clerk list).
- **v4b design** (`pulse/scripts/v4b_runner.py`, README `V4B_README.md`): v1 themes are the news
  backbone; embedding/HDBSCAN clusters of social posts attach to the nearest theme (cosine ≥ 0.55
  or URL overlap) and the theme is rewritten with them integrated (per-item Haiku relevance gate,
  length ceiling with one retry, paragraphs preserved); unattached coherent clusters become
  standalone entries; one ranked list `v4b["entries"]` (each: title, summary (markdown with
  links), trigger, anchor_type, origin, rank, score, sources, news_outlets, item_ids…).
  ~$1.4/run. `v4_runner.py` (cluster-first) exists for comparison only.
- **Never drop a link:** `v4_runner.postprocess_entries` restores an entry's pre-validation text
  whenever synthesize's URL validator would strip a sentence (Substack answers HEAD with 403 —
  11 of the 14 sentences stripped since late Aug were Substack). `_url_audit` in each stored
  brief lists what validation did.
- Writer prompts (`V4B_REWRITE_PREFIX`, `V4_ENTRY_WRITER_PREFIX`) carry the house rules: link only
  the reporting verb; name the platform before an @handle; cite an author's newsletter over
  their tweets.

### Email template (`pulse/scripts/delivery/email_lunch.py`)
`render_lunch_html(briefing, tier="premium"|"free") -> (html, top_title, n)`. Used only by v4b;
the old `email_briefing.py` is untouched. Settled design (all owner decisions):
- White background, **no rules/borders anywhere**, ink `#3D3733`, brand blue `#0BB4FF`, light
  `#F6F7F3`. System sans for body (`FONT`); **Georgia serif** for the standfirst and all section
  heads (`HEAD_FONT`).
- Masthead: HE logo PNG 100px (`https://homeeconomics.us/logo-email.png`, in portal `public/`),
  54px gap, title "News at Noon" (text; `WORDMARK_URL` slot for a graphic), date line.
- **Standfirst:** 2–3 sentences (≤320 chars) of the v1 `conversation_pulse`, 20px Georgia, no
  heading; `briefing["intro"]` overrides it verbatim (this is what the editor will write).
- Free tier only: the upgrade box (light, no border) directly under the standfirst, and again at
  the bottom; text "You're reading the free edition of News at Noon. Links are disabled, and
  N of today's M themes are only in the premium edition. Upgrade →" — **N and M are already
  computed dynamically** from the withheld/shown split. After the shown entries: a box "More in
  the premium edition" listing withheld titles (no numbers), then "On the Front Pages" after a
  72px gap.
- **Tiering:** `entry.get("tier") == "premium"` is honoured when any entry carries a `tier` key
  (this is the hook for the editor); otherwise the top `FREE_ENTRY_COUNT = 5` by rank are free.
  Free links are walled by `variants.make_free_variant` (own domains kept) → `UPGRADE_URL`
  (currently `https://homeeconomics.us/pulse/upgrade`, to be renamed).
- Entries: number + title on one line (two-cell row, hanging indent), 19px bold sans; 40px apart;
  summary with links; pills beneath = **exactly the cited sources**, names from
  `delivery/source_names.json` (host → display name, ~115 entries; extend freely).
- **Links:** ink text with a 2px **blue** underline (owner still deciding blue vs black); the link
  sits on the verb of the clause the source supports — rules in `_narrow_link_anchors`
  (reporting verbs first, then past/progressive/present; phrasal verbs keep their particle;
  relative clauses defer to the main verb; quoted titles link their noun; only lowercase words
  are verbs; @handles are never links; "On X, / On Bluesky," inserted by `_name_platforms`).
  Guard in `_body_links`: if narrowing would lose any URL (other than a handle's profile link)
  the original links are used. Regression tests live in the scratch patch scripts
  (`~/work/v4_scratch/patch_links*.py`) and `~/work/v4_scratch/audit_links.py <briefing id>`
  prints every anchor that is not a bare verb/noun — run it after touching the rules.
- Section heads: all 24px Georgia ink ("On the Front Pages", "Paper of the Day", "From Home
  Economics"); subsections 12px letter-spaced sans caps in ink.
- Front pages: four images only, 2×2 on desktop, single column on mobile (media query
  `.fp-row/.fp-cell`), each linking to the Freedom Forum PDF. `capture_frontpages.py` now
  ranks headlines by size band → bold → position (headlines are no longer rendered anyway).
- From Home Economics: Recent Publications (Substack feed via curl; tracked snapshot
  `delivery/he_publications.json`, live cache gitignored; `refresh_he_publications.py`), Tools
  (Pro Map blurb, `PRO_MAP_URL`), Home Economics in the News (`_press_mentions` — empty because the
  collector's Google News RSS search returns nothing; see open items), **Recent posts**
  (`delivery/own_posts.py`: @azizsunderji's tweets from `items`, last 5 days, by likes; appears
  only once the owner adds himself to the scraped Pulse X list `2046263290972582212`).
- Footer: "News at Noon · Home Economics" plus the compliance footer added at send time
  (`v4b_runner._lunch_footer`: unsubscribe link + postal address
  "Home Economics, 12 East 49th Street, 11th floor, New York, NY 10017").
- Subject: `News at Noon: Thursday, September 3, 2026` (US Eastern); From
  `News at Noon <pulse@home-economics.us>`.
- Preview/resend any stored brief: `python preview_lunch.py --id <id> --tier both --to <email>`
  with `PULSE_DB` pointing at the DB that holds it. Verifier:
  `python ~/work/v4_scratch/noon_verify.py` (checks the rendered HTML in `~/work/v4_scratch/`).

### Billing (already provisioned)
- Stripe (live): product **`prod_VBJzy3ke780ycX`** (still named "Pulse" — rename in the
  dashboard), prices **`price_1UAxLFGXFv3s1ifApjjNprjy` = $18/mo**,
  **`price_1UAxLFGXFv3s1ifAH6Dz6woI` = $180/yr**.
- Vercel env (all environments): `STRIPE_PRICE_PULSE_MONTHLY`, `STRIPE_PRICE_PULSE_ANNUAL`,
  `PULSE_UNSUB_SECRET` (same value as the GitHub Actions secret).
- GitHub Actions secrets: `CLERK_SECRET_KEY`, `PULSE_UNSUB_SECRET` (both consumed by
  `pulse-synth.yml`'s v4b step via `delivery/subscribers.py`).
- Clerk metadata contract (pipeline side, `delivery/subscribers.py`): a subscriber is a Clerk user
  with `public_metadata.pulseNewsletter.subscribed == true`; premium = `public_metadata.tools.pulse`
  truthy; unsubscribe token = `HMAC-SHA256(PULSE_UNSUB_SECRET, clerk_user_id)` hex, URL
  `https://homeeconomics.us/api/pulse/unsubscribe?u=<id>&t=<token>` (GET confirms, POST performs;
  RFC 8058 List-Unsubscribe headers are set).
- **Portal PR #9 (`homeeconomics/portal`, branch `pulse-product`, ON HOLD, do not merge as is):**
  rebased to three pure-Pulse commits on current main (old tip `9ab5db7`): `/pulse` page (free
  email signup + premium checkout mirroring Pro Map), `/pulse/upgrade` wall, `api/pulse/subscribe`,
  `api/pulse/unsubscribe`, Pulse in `src/lib/billing.ts`, `PulseSignupForm`, `PulseUpgrade`,
  `PulseCheckoutSuccess`, `ManageBillingButton`. `next build` passed; endpoints smoke-tested.
  It is the starting point for feature 2 after a rename.

### Masthead graphic
Six editorial-style illustration concepts (gpt-image-2, the current SOTA; prompts in
`2026_09_02_NewsAtNoon_Logo/scripts/generate_concepts.py`, `EDITORIAL_STYLE`) are in
`2026_09_02_NewsAtNoon_Logo/outputs/editorial_*.png`. Owner liked the style ("this is the idea");
no pick yet. When chosen: host the PNG in portal `public/`, set `WORDMARK_URL`/an illustration
slot in the masthead. Rejected: clock-as-O wordmarks, dandy characters, code-drawn clocks.

---

## 2. Open decisions (owner's)
1. Daily shadow tier: currently premium to aziz@; switch to free (`--tier free` in the workflow step)?
2. Body link underline: blue (current) or black.
3. Press mentions: rewrite `collectors/press_mentions.py` (Google News RSS *search* returns 0 items
   even for control queries; Brave Search API key exists in GH secrets). Needs a yes — existing file.
4. Owner to add **@azizsunderji** to the Pulse X list so Recent posts populates.
5. Send time: decided 11:59 ET (lands at noon), not yet implemented (see feature 1 — it falls out of the editor design).

---

## 3. Feature 1 — the editing interface (spec)

### What the owner asked for (verbatim intent)
- Works nicely on desktop **and mobile**.
- Everything editable except the fixed chrome: title, date, and the free-edition box.
  Editable: the standfirst, every entry's title and summary, the paper of the day, the
  From Home Economics blurbs if desired.
- Hyperlinks are first-class: existing links editable/removable; **new links insertable on any
  word**, including a word typed fresh after deleting a paragraph.
- Per-entry **free/premium selection**; the free box's "N of M themes" must reflect it (renderer
  already does, via `entry.tier`).
- Delete entries; presumably reorder.
- Workflow: draft arrives ~7am ET, owner edits, **send goes out at 11:59 ET (lands at noon) whether or not it was
  edited** ("if not edited, still send").

### Recommended architecture (keep the Python renderer; the droplet is always on)
- **Draft store on the droplet.** After the 11:00 UTC run, the v4b brief lands in the synced
  `pulse.db` (`daily_v4b_attach`). A small service on the droplet copies today's row into an
  editable draft table (`drafts(date, json, updated_at, status)`) in a separate SQLite
  (`/home/aziz/work/noon_drafts.db`), or reads it lazily on first open.
- **Editor backend = FastAPI on the droplet** (new dir `pulse/editor/`), behind a shared secret
  header or Clerk JWT verification: `GET /draft/{date}`, `PUT /draft/{date}` (whole JSON),
  `POST /draft/{date}/render?tier=` → HTML (calls `render_lunch_html` + variants, exactly what
  will be sent), `POST /draft/{date}/send-test` (to aziz), `POST /draft/{date}/publish` (marks
  approved; optional "send now"). Expose via Caddy/nginx with TLS on a subdomain (e.g.
  `noon-api.homeeconomics.us` → droplet), or via a Vercel route that proxies with the secret.
- **Editor UI in the portal** (Next.js, Clerk-gated to the owner's user id) at `/admin/noon`:
  a single-column, mobile-first page. Content model is the brief JSON; summaries stay
  **markdown** (`[verb](url)` links) so the renderer's link rules still apply. UI: one card per
  entry (drag to reorder, delete, tier toggle Free/Premium with the live "N of M" count), title
  input, summary editor. For links on mobile the reliable approach is a small toolbar over a
  plain textarea (Insert link = wrap selection in `[…](url)`, Remove link), plus a **preview
  pane** that calls `/render` so the owner sees the real email (both tiers, switchable). A
  contenteditable WYSIWYG is nicer but fragile on iOS; if used, store as markdown via a
  converter and keep the textarea as fallback.
- **Send at 11:59 ET (lands at noon) from the droplet**: a cron (or the same FastAPI app's scheduler) at 16:15
  UTC loads the draft (edited or not), renders both tiers, and sends via the existing
  `send_lunch_to_subscribers` (needs `RESEND_API_KEY`, `CLERK_SECRET_KEY`, `PULSE_UNSUB_SECRET` in
  the droplet's env file — the GH secrets are not on the droplet today). The GH Actions v4b step
  then stops sending (or keeps the shadow to aziz only) — controlled by a variable so rollback is
  one command. Weekend behaviour: no v1 run on weekends? (check the synth schedule; today it runs
  daily).
- **Rendering in the editor must use the same code path as the send** (render_lunch_html +
  scrub/wall + footer) so what the owner previews is what goes out. Add `intro` to the JSON when
  the standfirst is edited (renderer already prefers it).
- Auth: Clerk session on the portal page; the portal API route forwards to the droplet with a
  server-side secret; the droplet checks the secret. Nobody else can reach the editor.
- Nice-to-haves: "Regenerate this entry" (re-run the writer for one cluster), version history,
  a diff against the auto draft.

### Files likely touched
New: `pulse/editor/` (FastAPI app, systemd unit, Caddyfile), portal `src/app/admin/noon/*`,
portal `src/app/api/admin/noon/*` (proxy). Existing-file edits needing the owner's yes:
`pulse-synth.yml` (stop the GH send / keep shadow), possibly `subscribers.py` (rename keys).

---

## 4. Feature 2 — signup and upgrade pages on the site (spec)

- Start from PR #9 (branch `pulse-product` in `homeeconomics/portal`) and **rename** everything
  user-visible from Pulse to News at Noon: routes `/pulse` → `/noon` (or `/news-at-noon`),
  `/pulse/upgrade` → `/noon/upgrade`, `api/pulse/*` → `api/noon/*`, copy, component names,
  the billing catalogue entry (`src/lib/billing.ts`: tool key `pulse` → `noon`, product name).
- The Clerk metadata keys (`publicMetadata.pulseNewsletter`, `publicMetadata.tools.pulse`) are
  read by the pipeline's `delivery/subscribers.py` and written by the portal's subscribe route
  and Stripe webhook. Renaming them is a **coordinated change in both repos** (`subscribers.py`
  is an existing file → ask) — or keep the internal keys as-is and rename only user-facing
  strings (simplest; recommended for launch).
- Pipeline constants to update at the same time: `email_lunch.UPGRADE_URL`,
  `subscribers.UNSUBSCRIBE_BASE` (if the API path moves), and the portal's return URLs.
- Stripe: rename product `prod_VBJzy3ke780ycX` to "News at Noon" (dashboard); prices unchanged.
- Pages: `/noon` — hero with the masthead illustration, what it is, free signup (email → Clerk
  user with `pulseNewsletter.subscribed=true`), premium $18/mo / $180/yr checkout (existing
  `api/checkout` with the Pulse price ids; webhook sets `tools.pulse`), "already subscribed?
  manage" (customer portal). `/noon/upgrade` — the wall every free-edition link lands on:
  restate the premium offer, checkout buttons, and (nice) today's withheld titles.
- Then: merge, set the three Vercel vars (already set), test the loop with a fresh email, do one
  paid test purchase (100%-off promo code) to see `tools.pulse` flip and the premium variant
  follow, add a Clerk email-only signup smoke test.
- Launch order: editor first (so the owner's byline is real), then the pages, then cutover
  (`--to` removed / droplet send), then announce.

---

## 5. Working conventions that saved time
- Ship multi-line Python to the droplet as files (`scp` to `~/work/v4_scratch/`), never inline in
  `ssh '…'` — quoting broke three times.
- Every renderer change: apply → `noon_verify.py` → `audit_links.py 303` → render both tiers →
  commit (message explains the owner's rule) → push → `preview_lunch.py --to aziz@…`.
- Patches assert on unique anchors so they cannot double-apply; the link patches exit non-zero on
  any failed self-test and the chain reverts.
- The owner's standing rules: create new files rather than editing existing ones; ask before
  touching existing files; surface anomalies; no bold/colour/italics for emphasis; no dark lines;
  plain English in messages; don't ask questions the code can answer.

---

## 6. Status update — 2026-09-03 evening: Feature 1 is built and running

`pulse/editor/` (commits d277fcd, 4cc0ee9) is live on the droplet. Architecture chosen: the
whole editor lives on the droplet (FastAPI + a plain-JS mobile-first page), with its own
password login and a per-day magic link in the "draft ready" email — not a Clerk-gated portal
page. Reason: one repo, one language, no Vercel deploy or proxy, and the preview is the exact
send code path. `pulse/editor/README.md` documents the daily flow, env file, and CLI.

- Units (user systemd, lingering on): `noon-editor.service` (127.0.0.1:8240), `noon-ingest.timer`
  (11:00–15:59 UTC every 10 min; builds today's draft from the synced DB read-only and emails
  the edit link once), `noon-send.timer` (11:59 America/New_York; sends unless Held or already
  sent; emails an alert on failure). Env: `~/.noon_env` (NOON_SEND_MODE=shadow → owner only,
  free tier).
- Editor verified by a headless Playwright run at 1280×900 and 390×844 touch: login, link
  count parity DOM↔markdown, edit/autosave/version, insert link, unlink, tier toggle with live
  count, reorder with ranks, delete/restore, hold/resume, previews, stale-save 409, reset.
- The draft sets the masthead `date` to the send date; the stored brief carries the previous
  day's date (latent bug in the shadow sends until now).
- Module is `drafts.py`, not `store.py`: `pulse/scripts/store.py` shadows that name.

Still needs the owner: GoDaddy A record `noon.homeeconomics.us → 104.236.210.18`; permission to
add `import /etc/caddy/conf.d/*.caddy` to `/etc/caddy/Caddyfile` (snippet in
`pulse/editor/caddy/noon.caddy`); permission to change the v4b workflow step to `--no-send`
(the 7am GH shadow then stops and the droplet's 12:15 send replaces it); CLERK_SECRET_KEY and
PULSE_UNSUB_SECRET in `~/.noon_env` before `NOON_SEND_MODE=subscribers`. Until DNS:
`ssh -L 8240:127.0.0.1:8240 vps` → http://127.0.0.1:8240.

---

## 7. Status update — 2026-09-03 night: site live, loop tested, one switch from launch

- **Portal**: PR #9 merged (3d3e46d) plus 4fa1b2e. Live: https://homeeconomics.us/noon (free signup +
  premium checkout), /noon/upgrade (the wall; /pulse and /pulse/upgrade 308-redirect there),
  `api/pulse/subscribe` and `api/pulse/unsubscribe` unchanged (internal names kept: Clerk keys
  `pulseNewsletter.subscribed` / `tools.pulse`, env `STRIPE_PRICE_PULSE_*`, `PULSE_UNSUB_SECRET`).
  Checkout sets `payment_method_collection: if_required` (a 100%-off code needs no card).
- **Stripe**: product renamed "News at Noon"; coupon `NOONTEST100` with promo code `NOONTEST`
  (100% off forever, 3 redemptions, 1 used).
- **Secrets on the droplet** (`~/.noon_env`): CLERK_SECRET_KEY (copied from the Clerk dashboard via
  clipboard → ssh, never through the chat) and PULSE_UNSUB_SECRET (rotated 2026-09-03; the same new
  value is in Vercel prod/preview/dev via `vercel env add` and in the GitHub secret). Vercel
  refuses to `env pull` sensitive values — they come back as the literal `[SENSITIVE]`.
- **End-to-end test (all passed)**: free signup via API → Clerk user flagged; signed unsubscribe
  link (generated on the droplet) → confirmation page → POST → flag cleared; re-subscribe; sign-in
  by email code; checkout with NOONTEST → webhook set `tools.pulse` → droplet sees premium=True.
  Test account: aziz.sunderji+noontest@gmail.com (premium, $0 forever) — keep it as a live check.
  The owner's own account aziz@home-economics.us is flagged subscribed + premium.
- **Cutover (owner's call)**: on the droplet set `NOON_SEND_MODE=subscribers` in `~/.noon_env`
  (timers read it at run time; `systemctl --user restart noon-editor.service` for the app). Until
  then the 12:15 send goes to the owner only. Rollback: set it back to `shadow`.
- Chrome-automation note: checkout.stripe.com denies screenshots; `find`, `get_page_text` and the
  JavaScript tool still work there. Vercel CLI is authorized on the Mac (`npx vercel@latest`).

---

## 8. Status update — 2026-09-04

- **Live to subscribers** (`NOON_SEND_MODE=subscribers`); the timer fires at **11:59 ET** so the
  edition lands at noon. Pricing **$49/mo, $490/yr** (Stripe prices price_1UBhF1GXFv3s1ifAbkWIfy8l /
  price_1UBhF2GXFv3s1ifAwh6oWsMg; old ones archived). Pages: /noon (signup), /noon/premium (the
  deliberate upgrade page, linked from the email's Upgrade box), /noon/upgrade (the wall for walled
  links). Teams/bespoke copy on both, pointing at /inquire. Telegram alert on every free signup;
  paid alerts come from the Stripe webhook.
- **Editor additions** (another session, 2026-09-04): per-link "keep in free edition" flag, source
  pill under the caret, a social/free PDF (`latest-free.pdf` at /latest.pdf; premium at
  /latest-premium.pdf behind login), four 1080×1350 PNG cards per edition (cards.py), Oracle and
  Gelasio fonts on the droplet for PDFs.
- **Links** (`pulse/editor/links.py`, run at draft build and re-runnable on a stored draft):
  tracking redirects resolved (awstrack, Google Alerts wrapper, newsletter click-trackers), utm
  stripped; source pills rebuilt from the resolved URLs — never "gmail"/"Newsletter", never our
  own domains; Mailchimp-hosted newsletters named from the account (MAILCHIMP_ACCOUNTS). The
  renderer's `_entry_pills` prefers `entry["_pills"]` when a cleaned draft carries it.
- **Cards**: text goes through the email's `_body_links` (so every link in the shown paragraphs is
  underlined, cover standfirst included); up to three paragraphs, shrinking type before dropping
  one. No PDF card deck (owner: underline only, images cannot link).
- **PDFs**: no trailing blank page (wrapper cell bottom padding dropped in print CSS).
- Tooling: Vercel CLI and Stripe CLI (profile `--project-name he4` = the live account
  acct_1TbkTOGXFv3s1ifA; the owner has four identically named Stripe accounts) are authorized on
  the Mac. Test promo code NOONTEST (100% off, 2 redemptions left).

---

## 9. Status update — 2026-09-04 evening: Monday-to-Friday only, Monday covers the weekend

- **Weekday schedule** (owner decision): both droplet timers are `Mon..Fri` (repo units under
  `pulse/editor/systemd/`, installed and verified: next fire Mon 2026-09-07). `cli.py send` and
  `cli.py ingest` skip a Saturday or Sunday date unless `--force` — this covers the send timer's
  Persistent catch-up after downtime (without it a missed Friday send would fire on Saturday and
  send Saturday's brief). `pulse-synth.yml` crons are `1-5`; `pulse-daily.yml` scrapers still run
  every day so the weekend corpus exists for Monday.
- **Monday lookback 72h**: `config.corpus_lookback_hours()` returns 72 on a Monday (US Eastern),
  24 otherwise; `PULSE_LOOKBACK_HOURS` overrides. Used by `generate_daily_briefing` (today's pool,
  convergence, organic conversations, stats, collection errors; the past-6-days block is unchanged
  and still excludes today's pool by id) and by `v4b_runner.py --lookback-hours` (default). The
  workflow computes the same number in a "Corpus window" step, passes it as `PULSE_LOOKBACK_HOURS`
  to the synthesis and v4b steps, and scales the enrichment steps (`enrich_articles --hours` =
  window + 12, `enrich_tweet_links --hours` = window).
- **Monday prompt**: when the window exceeds 24h each item in the today's-pool block carries a
  `[Fri]`/`[Sat]`/`[Sun]`/`[Mon]` stamp (US Eastern) and a WINDOW NOTE tells the model only items
  stamped with today's weekday are "today"; the rest are cited by day. First real run: Mon 7 Sep.
  Check that edition for "today" applied to Friday news and for the corpus size (three days of
  items compete for the same 280 slots).
- Verified today's (Fri 4 Sep) edition: held at 11:59 ET by the owner, sent manually at 12:00:53 ET
  to 8/8 subscribers (2 premium, 6 free). Six draft versions were saved after the send (17:27–18:47
  UTC); those edits are in the stored draft and PDF path only, not in what subscribers received.
- Workspace for this product on the Dropbox root: `NewsAtNoon/` (CLAUDE.md, scripts of each
  session's patches and checks).

---

## 10. Status update — 2026-09-05: substack.com feeds mirrored from the droplet

- **Finding**: the 17 competitor newsletters hosted on `*.substack.com` (Calculated Risk, Kevin
  Erdmann, Cameron Murray, Nominal News, Maximum New York, Derek Thompson, Goldsmith-Pinkham, Ryan
  Avent, Robin Brooks, L.A. Reported, Krugman, Kustov, Clancy, Zvi, Richardson, Import AI, Hugh
  Clarke) answered HTTP 403 to GitHub Actions and to Browserbase on every run since at least
  2026-06-18 and had never produced an item. The 28 feeds on custom domains collect normally. The
  health email's "Substack RSS degraded" warning is those 17 errors per run. In the two stored
  editions (3–4 Sep), 5 of 132 theme links came from competitor newsletters, one from a
  substack.com host (via Gmail).
- **Cause**: Cloudflare in front of substack.com. From the droplet it challenges httpx (403,
  `cf-mitigated: challenge`) but serves urllib and curl; from Actions and Browserbase everything is
  blocked.
- **Fix (live)**: `pulse/editor/mirror_feeds.py` fetches the substack.com feeds with urllib every
  hour at :15 UTC (`noon-feeds.timer`, log `~/work/noon/logs/feeds.log`) into
  `~/work/noon/feeds/<slug>.xml` plus `index.json`; Caddy serves them at
  `https://noon.homeeconomics.us/feeds/` (route in `pulse/editor/caddy/noon.caddy`, installed);
  `collectors/rss_substacks._fetch_via_mirror` reads the mirror first when `SUBSTACK_MIRROR_BASE`
  is set (`pulse-daily.yml` env), and only the misses go to Browserbase/httpx. A failed mirror
  fetch keeps the previous file, so the collector sees a stale feed rather than nothing.
- Follow-up to consider: the collected newsletters are underused in the synthesis (routed into
  themes, no dedicated section); revisit once a week of the full corpus exists.
- **Extended to every OPML feed (same day)**: the mirror now fetches all 161 feeds (17 substack.com
  + 144 from `pulse/data/Feeds.opml`) as `f_<sha1(xmlUrl)[:12]>.xml`; `collectors/rss_feeds.py`
  fetches directly first and reads the mirror (`FEED_MIRROR_BASE`) only when a feed fails or
  answers non-200, so nothing changes for working feeds. Rescued: the Taylor & Francis housing
  journals (Housing Studies, Housing Policy Debate, Journal of Housing Research, Journal of Real
  Estate Research, JAPA, Journal of Urban Affairs, Spatial Economic Analysis, Annals of the AAG),
  Inman, FeedBurner's Calculated Risk, and our own feed. Still dark, blocked for the droplet too:
  Century 21, Wiley's Real Estate Economics and Journal of Regional Science (403), Seattle Times
  (202 bot check). Check: `~/work/noon/feeds/index.json` and `~/work/noon/logs/feeds.log`.

---

## 11. Status update — 2026-09-08

- **Inline images** (owner: illustrate a theme with a Pro Map PNG). `POST /api/upload` in the editor
  (cookie auth, 8 MB cap, any image Pillow can decode; AVIF/HEIC/WebP/GIF/TIFF converted to PNG,
  served at `/images/YYYY/MM/<sha1>.png`); Image button beside Link/Unlink on entries, the standfirst
  and the paper; the figure round-trips as `![caption](url)` on its own line. Renderer:
  `_split_images` lifts image lines out before any link pass; block `<img width=600>` with a 13px
  caption; captions link bare URLs themselves (iOS Mail auto-linked them and swallowed the space
  before the address); no pill from an image host; `links.py` and `cards.py` skip image lines;
  `thepromap.com` is an own domain in the free edition. Regression scripts in `NewsAtNoon/scripts/`
  (37_–45_, 64_, 72_); baseline diffs must ignore the hourly `?v=` on front-page images.
- **Editor on phones**: headline and URL fields are self-sizing textareas; grouped move buttons;
  44px tap targets; focus bar 12px into the gutter; Sign out styled as a button.
- **Front pages**: `_front_page_clip` keeps the masthead half when Freedom Forum ships a two-page
  spread (NYT 8 Sep); `pulse-frontpages.yml` re-captures and uploads on demand.
- **Feeds**: `brave_sections.py` (Brave Search) is a backstop for the NYT real estate section only —
  Brave does not index wsj.com. The Journal's real estate section (no Dow Jones feed) comes from the
  droplet mirror: a Google News RSS search decoded to wsj.com URLs (`gn_wsj-real-estate.xml`,
  `GNEWS_SEARCHES` in `mirror_feeds.py`, needs `googlenewsdecoder` and `pillow-heif` in the venv);
  it is an ordinary OPML entry. Dead `Alert: Nicole Friedman` removed. Eight duplicate NYT rows
  (Brave's www-less URLs, fixed) remain in `items` from 8 Sep.
- **Reply-To**: `sender.REPLY_TO` (NOON_REPLY_TO, default the owner) on test and subscriber sends;
  `pulse@home-economics.us` is not a mailbox (probe never arrived) — a Workspace alias was
  attempted and dropped by the owner.
- Web edition and `/latest.pdf` show the last *sent* edition by design; the editor's Preview/PDF
  buttons show the current draft.

---

## 12. Status update — 2026-09-08 evening: signup attribution and the morning report

- **Source tag (portal, commit 2ff0382).** Links the owner posts carry `?src=<channel>-<post>` on
  any of /noon, /noon/premium, /noon/upgrade (e.g. `homeeconomics.us/noon?src=x-0908`,
  `?src=li-rents`). `NoonSourceTag` (mounted on all three pages) stores the tag in
  localStorage `noon_src` for 30 days (a newer tag replaces it); with no tag and nothing stored
  it stores the referrer as `ref:<host>`. `NoonSignupForm` posts it as `source`;
  `api/pulse/subscribe` validates it (≤64 chars, `[A-Za-z0-9._:-]`) and `setPulseSubscribed`
  writes `pulseNewsletter.source` (an existing source is replaced only by an explicit tag on a
  resubscribe; `ref:` never overwrites). `NoonUpgrade` passes it to `api/checkout`, which sets
  `noon_source` on the Checkout Session and subscription metadata; the webhook's
  `recordPulsePremium` stamps `tools.pulseSince` (first activation only), fills
  `pulseNewsletter.source` if unset, and creates a subscribed `pulseNewsletter` record for a
  buyer who never did the free signup (before this, such a buyer was not on the send list at
  all, because `subscribers.py` requires `pulseNewsletter.subscribed`). Both Telegram alerts
  (free signup, paid) now carry the source. The free edition's walled links already arrive at
  /noon/upgrade with `?src=email`, so "email" is a source in its own right.
- **Morning report (pipeline).** `pulse/editor/signup_report.py`, run by `noon-report.timer`
  at 07:00 ET daily (`systemd/noon-report.service|timer`, log `report.log`), emails the owner
  "News at Noon signups: <day>": signups, premium, unsubscribes in the last 24 h, totals, and
  signups by source (7 days and since launch). Details in `pulse/editor/README.md`.
  Premium subscriptions made before 2026-09-08 have no `pulseSince` and appear only in totals;
  the Stripe key is not on the droplet, so there is no customer-creation-time fallback.
- End-to-end test 2026-09-08: `/noon?src=test-0908` → form → Clerk `source == "test-0908"` →
  signed unsubscribe → listed in a `--hours 1` report. Test address removed from the list.

---

## 13. Status update — 2026-09-09: four chronic health-report warnings

- **Twitter "1 Apify error in last 24h"**: the only error was the 19:36 budget-exhausted run
  of 8 Sep, superseded by two successful runs. `pipeline_health_report._last_successful_run`
  now floors the error window at the source's latest successful `collection_runs` row;
  older errors appear as a note ("N earlier errors, cleared by the run at HH:MM UTC") and do
  not degrade. `analysis/pipeline_health.py` stage 2 downgrades such errors to WARNING, which
  is logged but not emailed (the "Pulse collection broken" alert was sent three times for the
  one stale failure).
- **RSS news "48 collector errors"** was six feeds times eight runs. Causes and fixes:
  Springer's two search feeds (Annals of Regional Science, J. of Real Estate Finance and
  Economics) answer httpx with **HTTP 200 and a "Client Challenge" HTML page** while serving
  urllib the feed; the collector only consulted the mirror on a non-200 status, so it parsed
  the HTML and logged SAXParseException 15:4. `rss_feeds.py` now treats a 200 HTML body as a
  failed fetch (mirror first, else "HTTP 200 but HTML, not a feed"), tries the mirror when the
  direct bytes do not parse, and the sanitizer strips characters XML forbids. Century 21 (403
  everywhere) and Seattle Times real estate (202 bot check everywhere) removed from the OPML.
  Wiley's Real Estate Economics and Journal of Regional Science (403 everywhere) are now built
  on the droplet from the Crossref API (`CROSSREF_JOURNALS` in `mirror_feeds.py`, files
  `cr_real-estate-economics.xml` and `cr_journal-of-regional-science.xml`, DOI links, abstract
  as description); the OPML points at the mirror URLs under the unchanged titles. The mirror
  skips its own `noon.homeeconomics.us/feeds/` URLs in the OPML pass.
- **Substack "14 of 45 feeds silent 14d+"**: silence no longer degrades `_probe_rss_subset`;
  only collector errors in the latest run or zero items do, and the silent list is a 30-day
  informational note. Both RSS stages now show errors from the latest run and the 24h total.
  `COMPETITOR_SUBSTACKS`: Apricitas (no post since 3 May, same feed on both hosts), Ezra Klein
  (NYT feeds cover him) and David Pierce (consumer tech) removed; Conor Sen's Bloomberg feed
  ("Former Bloomberg Opinion Columnist") replaced by his Substack "The Housing Frame";
  Mike DelPrete (valid feed, last post 22 Jan 2026) and Miller Samuel (valid feed, last post
  19 Aug 2026, URL set to the redirect target) kept.
- **Journal abstracts "1 of 5 picks missing"**: the pick was "Editorial Board".
  `config.is_non_paper_title` (Editorial Board, Issue Information, Table of Contents, Erratum,
  Corrigendum, Retraction, Correction, Announcement, Call for Papers, Front Matter, Masthead...;
  at the start of a title, or anywhere in a title under four words) is applied in both journal
  pools in `run_pipeline.py`, in `fetch_journal_abstracts._pick_todays_5`, in the Crossref
  mirror, and in the abstract probe.
- Local render (read-only DB) after the change: Twitter ok, Substack ok, journal abstracts ok;
  RSS news still degraded on the six errors of the 09:05 run, which clears at the next run.
  Scripts 111–125 in `NewsAtNoon/scripts/`; backups in `NewsAtNoon/data/backups/`.

## 14. Status update — 2026-09-16: LinkedIn sources page

- New owner-only page at https://noon.homeeconomics.us/sources (linked from the editor's
  bottom buttons as "LinkedIn sources"). Code: `pulse/editor/sources.py`,
  `static/sources.html`, `static/sources.js`; routes listed in `app.py`'s docstring.
- It lists the 31 current LinkedIn targets plus the 482 housing contacts from the
  2026-09-03 interactions list (CSV in Dropbox, `2026_09_03_LinkedIn_HousingVoices/outputs/
  housing_contacts.csv`; kept out of this public repo). Same person under a public slug and
  an internal ID is merged by name.
- Recent activity: posts already collected in `pulse.db` (last 30 days, free); for other
  accounts a "Load recent posts" button runs Apify (month window, 5 posts, about $0.0075 per
  account at most; batches of 25). Previews cache to `~/work/noon/linkedin_previews.json`.
  Internal-ID URLs (`/in/ACoAA…`) work with the actor (tested on Ian Kennedy).
- Add/Remove rewrites `linkedin_targets.json` in this clone at once; Publish commits only
  that file and pushes to main, so the next `pulse-daily` run reads it. The collector cap is
  80 accounts (`LINKEDIN_MAX_TARGETS`); the page refuses to go past it.
- Also on 2026-09-16: `noon-report.timer` had not been enabled on OVH after the migration;
  enabled it. The old droplet's timers could not be verified as stopped.

## 15. Status update — 2026-09-17: Paper of the Day is never left empty

- On 2026-09-17 the synthesis model returned `paper_of_the_day: null` (none of the 6 journal
  items in the 24h window was about housing) and the section was omitted, although the 30-day
  pool held 24 unused housing papers. Owner's rule: every edition has a Paper of the Day.
- `run_pipeline.py`: the post-synthesis repeat check now also fires when the pick is empty and
  fills the slot from the 30-day pool (first candidate by relevance whose RSS body works as a
  summary, else an OpenAlex/Semantic Scholar/Crossref abstract, else title only). If the pool
  is empty the section is still omitted. Fallback picks carry an empty `key_finding`.
- Repeat matching now also catches titles the model shortened (`_paper_recently_used`, first
  40 letters/digits). Before this, "Frozen Markets, Falling Prices…" (run 2026-09-15) was
  still in the pool because the stored title was a shortened form.
- The 2026-09-17 edition had the Amsterdam low-income rental housing paper (Journal of Housing
  Economics, doi:10.1016/j.jhe.2026.102176) inserted by hand; summary written from the
  OpenAlex abstract. Helper: `NewsAtNoon/scripts/insert_paper_2026_09_17.py` in Dropbox.

## Status update — 19 Sep 2026: front pages moved off Bluehost

- The owner set a site-wide 301 on home-economics.us (Bluehost) to homeeconomics.us on 18 Sep and
  will shut Bluehost down. That broke `home-economics.us/pulse-screenshots/*`, which the email's
  "On the Front Pages" images and the health probe used. Images in editions sent before 19 Sep
  no longer load; this was accepted rather than exempting the path on Bluehost.
- Front pages are now captured on the noon server: `noon-frontpages.timer` (Mon–Fri 11:05 and
  15:00 UTC) runs `capture_frontpages.py --publish-dir /home/aziz/work/noon/frontpages`; Caddy
  serves that folder at `https://noon.homeeconomics.us/frontpages/`. Log: `~/work/noon/logs/frontpages.log`.
  Re-capture by hand: `systemctl --user start noon-frontpages.service` (replaces the removed
  `pulse-frontpages.yml` workflow). The synth workflow still captures locally for its own
  headlines, with `--no-upload`.
- Health report: the paywall login probe retries once before reporting "unknown"; "Foreword" and
  "Preface" are treated as non-paper journal titles.
- Other workflows in this repo still upload to Bluehost over SFTP (not Housing at Noon):
  monthly-ces-update, deploy-metro-explorer, update_price_maps, upload-social-charts,
  weekly-social-charts, weekly-rankings, weekly-charts.

## Status update — 24 Sep 2026: Goldman Sachs Research feed and server browser logins

Owner's rule (Aziz, 24 Sep 2026): "build it, series list is right, and make sure the health email
tells me if this, or any others, need me to re-login".

- **Goldman Sachs Research feed.** `pulse/editor/gs_feed.py` opens one new tab in the server's live
  Chrome (CDP 9223, raw CDP through the new `pulse/editor/cdp_tab.py`; never Playwright on 9223; the
  tab is closed at the end) and runs Goldman's own search URL filter for US Economics Analyst, Global
  Views, US Weekly Kickstart, Europe Weekly Kickstart, Housing and Mortgage Monitor, Global Strategy
  Views (title must start with the series name) and for housing research ("homebuilders" equity
  results; "housing" and "mortgage" results with a housing title). Reports from the last 3 days are
  read once (body up to 8000 characters), cached in `~/work/noon/feeds/gs_cache.json` (200 max) and
  published at https://noon.homeeconomics.us/feeds/gs_research.xml (newest 40). Loads are 3-5 s
  apart, 30 per run at most. The OPML lists it in HighPriority as "Goldman Sachs Research".
  A login/SSO redirect, password form or empty report stops the run without retry, writes
  `~/work/noon/gs_status.json` with `logged_out` and exits 2. Units `noon-gsfeed.service/.timer`
  (Mon-Fri 10:00 UTC), log `~/work/noon/logs/gsfeed.log`. First run: 2 items (Global Views: Lower
  Inflation, Limited Hikes; US Economics Analyst: A Broader Energy Shock).
  Note: `collectors/rss_feeds.py` keeps only the first 2000 characters of a feed description, so the
  synthesis sees 2000 of the 8000 characters. Not changed.
- **Login status.** `pulse/editor/login_status.py` (units `noon-loginstatus.service/.timer`, daily
  10:30 UTC, log `loginstatus.log`) writes https://noon.homeeconomics.us/feeds/login_status.json:
  statuses only, no cookies. Each site is tagged with the mechanism that reads it:
  - server Chrome (CDP 9223): Goldman Sachs Research (gs_feed.py), and WSJ, NYT, FT, Bloomberg,
    Economist, Substack for ad hoc reads (live_tab_fetch.py, paywall_fetch.py). Checked live, one
    tab, one page per site, 3-5 s apart. The Goldman row also carries gs_feed.py's last result.
  - Browserbase: WSJ, NYT, FT, as checked by enrich_articles.py each run (pulse.db `paywall_auth`).
    Bloomberg and Economist bodies also go through Browserbase but are not checked there.
- **Health email.** New stage "2.1 — Server browser logins" in `pipeline_health_report.py`, next to
  2.0 (kept). Former 2.1-2.3 are now 2.2-2.4. Server Chrome rows come from login_status.json;
  Browserbase rows come from the report run's own `paywall_auth` table, so 2.0 and 2.1 agree.
  BROKEN when any site is logged out (headline names the sites and where to log in), WARN when the
  JSON is older than 36 hours or any site is unknown, OK otherwise.
- **Re-login procedure.** Server Chrome sites: open https://browser.homeeconomics.us, log in on the
  site in that browser, then `systemctl --user start noon-loginstatus.service` (and
  `noon-gsfeed.service` for Goldman) to refresh the status. Browserbase sites: ask Claude to open
  the site's login page in a Browserbase live-view session on the persistent context; the next
  enrichment run records the new status.
- On 24 Sep Bloomberg answered the server Chrome with a bot check ("access denied") after two test
  loads, so its status reads unknown; do not retry Bloomberg in bursts.

## Status update — 24 Sep 2026: article enrichment moves to the server Chrome (step 1)

Owner's rule (Aziz, 24 Sep 2026): "consolidate enrichment on the server Chrome; Browserbase stays as
fallback for a week, then is cancelled if the health report shows no blocks".

- **What runs where.**
  - Noon server: `pulse/editor/enrich_server.py`, units `noon-enrich.service/.timer` (Mon-Fri 10:15
    UTC, Persistent), log `~/work/noon/logs/enrich.log`. It selects candidates from the read-only
    Dropbox mirror of pulse.db with `enrich_articles._get_items_to_enrich` and `SKIP_DOMAINS` (sources
    rss/gmail/substack/hackernews, last 24 hours, rss/hackernews bodies under 500 characters, no
    relevance threshold, highest relevance first, cap 300). It also skips rows already enriched
    (`enrich_mode` set) and fetches each URL once. It loads each URL in one new tab of the live Chrome
    (CDP 9223, `cdp_tab.py`; tab closed at the end), 2-4 s between loads and at least 6 s between
    loads to the same host (including the host a tracking link lands on). The first bot-check page from
    a host stops that host for the run, with no retry. Bodies are extracted with
    `enrich_articles._extract_article_text` on the page HTML, so the text matches what Browserbase
    stores; 200+ characters counts as ok. No archive.ph fallback on the server. The run stops loading
    after 40 minutes so the file is ready before the 11:00 synthesis. It never writes to pulse.db.
  - GitHub Actions (`pulse-synth.yml`): new step "Apply server-Chrome enrichment" after the re-collect
    and classify steps and before "Enrich articles via Browserbase". It curls the file (fail-soft) and
    runs `pulse/scripts/apply_server_enrichment.py`, which sets `body` and `enrich_mode='server_chrome'`
    on rows whose body is shorter than the file's body, and prints the count.
- **File contract.** https://noon.homeeconomics.us/feeds/enriched_bodies.json
  (`~/work/noon/feeds/enriched_bodies.json`): `{"generated_at", "run": {started_at, finished_at,
  hours, limit, candidates, attempted, ok, empty, blocked, errors, skipped_blocked_host,
  left_for_time, error}, "hosts": {host: {ok, blocked, empty}}, "blocked": {host: page wording},
  "items": {url: {body (<= 8000 chars), title, fetched_at, mode: "server_chrome"}}}`. Items from the
  last 3 days are kept; `hosts` and `blocked` describe the latest run only. Run summary also in
  `~/work/noon/enrich_status.json` (not yet read by login_status.py).
- **Fallback.** The Browserbase step is unchanged. It still runs after the apply step, and it
  re-selects rss/hackernews rows under 500 characters and every gmail/substack row in the window
  (its query does not look at body length or enrich_mode for those two sources), so for gmail and
  substack it still refetches and overwrites `enrich_mode` with `direct`/`archive`.
- **Health email.** New stage "2.2b — Article body enrichment (server Chrome)" next to 2.2: counts of
  bodies by `enrich_mode` for the last 24 hours (server_chrome, direct, archive), the latest server
  run's totals and per-host ok/blocked/empty, the file's age, and the wording of any block. WARN when
  the file is older than 30 hours or any host blocked; OK otherwise.
- **One-week review (through 1 Oct 2026).** Cancel Browserbase only if stage 2.2b shows no blocked
  host on every weekday run and the server bodies cover the paywalled hosts (WSJ, FT, NYT, Economist,
  Bloomberg). Before cancelling, the paywall-auth probe (stage 2.0), archive.ph fallback and the
  Browserbase step itself need a replacement or a decision to drop them.
- **First run (24 Sep 2026, 15:36-15:50 UTC, by hand).** 162 candidates, 125 loads: 106 ok, 18 empty,
  1 blocked; 37 Bloomberg URLs skipped after the block. Per host: ft.com 33 ok / 2 empty, theverge.com
  12, latimes.com 11, washingtonpost.com 9, economist.com 8, fortune.com 7, wsj.com 5 ok; bloomberg.com
  1 ok then blocked on the second load ("Bloomberg - Are you a robot? ... We've detected unusual
  activity from your computer network. To continue, please click the box below to let us know you're
  not a robot."); urban.org 0 ok / 4 empty. Applied to a scratch copy of pulse.db: 102 rows updated.
  Bloomberg is the largest single source of candidates (39 of 162), so as things stand it would stay on
  Browserbase.

## Status update — 24 Sep 2026: licensed feeds moved off the public /feeds/ path

`gs_research.xml`, `gs_cache.json` (Goldman Sachs Research text) and `enriched_bodies.json` (article
bodies read in the server Chrome) hold licensed, paywalled text and were readable by anyone at
https://noon.homeeconomics.us/feeds/. They now sit under a secret path.

- **Folder.** `~/work/noon/feeds_private/` (owner aziz, group caddy, mode 2750; the setgid bit makes new
  files group caddy so Caddy can read them). `gs_feed.py` and `enrich_server.py` write there (env
  override `NOON_PRIVATE_FEEDS_DIR`; file names unchanged). The three files were moved from
  `~/work/noon/feeds/`, and nothing of them is left there.
- **Token.** A 32-hex path token, `NOON_PRIVATE_TOKEN`, kept only in `~/.noon_env` and in the GitHub
  Actions secret of the same name (`gh secret set NOON_PRIVATE_TOKEN -R azizsunderji/HomeEconomics`,
  piped from the env file). It is not in the repo, this file, or any log. To rotate: replace the line
  in `~/.noon_env`, set the secret again the same way, and rerun the render script below.
- **Caddy.** `pulse/editor/caddy/noon.caddy` has `handle_path /private/{$NOON_PRIVATE_TOKEN}/*`
  serving the private folder with `Cache-Control: no-store` and `log_skip` (so the token does not
  reach `/var/log/caddy/noon.log`). The `/feeds/*` block answers 404 for the three file names even if
  a stale copy reappears. Install with `pulse/editor/caddy/render_noon_caddy.sh`: it substitutes the
  token from `~/.noon_env`, writes `/etc/caddy/conf.d/noon.caddy` as root:caddy 640, validates and
  reloads Caddy. A render step was chosen over loading `~/.noon_env` into caddy.service because that
  unit runs `caddy run --environ`, which prints its whole environment to the journal. Never copy the
  repo file to /etc by hand: the placeholder would become empty.
- **GitHub Actions.** `pulse-synth.yml` "Apply server-Chrome enrichment" curls
  `https://noon.homeeconomics.us/private/${{ secrets.NOON_PRIVATE_TOKEN }}/enriched_bodies.json`.
  `FEED_PRIVATE_BASE` (same base URL) is set on the collection step of `pulse-daily.yml` and the
  re-collect step of `pulse-synth.yml`; `NOON_PRIVATE_BASE` is set on the health-report step of
  `pulse-synth.yml`. GitHub masks secret values in logs; no step echoes the URL.
- **Collector.** `rss_feeds.py`: the OPML still lists the public
  `https://noon.homeeconomics.us/feeds/gs_research.xml` (now 404), so no secret is committed. When
  `FEED_PRIVATE_BASE` is set, a feed whose file name is in `PRIVATE_FEEDS` (`{"gs_research.xml"}`) is
  fetched from `FEED_PRIVATE_BASE/<file>` directly; any other noon `/feeds/<file>` feed that answers
  non-200 is retried there. Fetch errors on the private URL are reworded so the URL is not recorded.
- **Health email.** Stage 2.2b reads `NOON_PRIVATE_BASE/enriched_bodies.json`; if the variable is
  missing it shows WARN "private feed URL not configured". `login_status.json` holds no licensed text
  and stays public at `/feeds/login_status.json`.
- **Checked from the server, 24 Sep 2026.** Public `/feeds/` for all three files: 404. Private path
  with the token: 200 for all three, `Cache-Control: no-store`. Wrong or empty token: 404. Other
  public feeds and login_status.json: 200. The collector read the GS feed through the private path
  (2 items in a 30-day window) and got 404 without `FEED_PRIVATE_BASE`.

## Status update — 27 Sep 2026: X carousel cards and "post to X after approval"

Owner's rule (Aziz, 27 Sep 2026: "Yes with approval pls"): each edition can go to X as one post with
the four cards as a carousel, and only after he clicks "Approve and post". Nothing posts on a timer.

- **Cards (`pulse/editor/cards.py`).** Still four 1080x1350 PNGs with the same file names, the same
  `publish_cards` (after every send via pdf.py, and on the /cards page) and the same Dropbox mirror.
  New layout:
  - card 1 (hook): "Housing at Noon · <date>" small, then the first sentence of the standfirst set as
    large as fits (92 px down to 48 px; trimmed only if 48 px still overflows); the first theme's
    title is used when there is no standfirst. Below it, the first theme's image (the first
    `![caption](url)` in its summary, read from disk when it was uploaded through the editor) with its
    caption; otherwise the large Home Economics logo (`static/he-large-black.png`, copied from Brand
    assets, force-added because `.gitignore` has `*.png`). No table of contents any more.
  - cards 2-4: the first three free themes in edition order, as before (theme 1 is skipped when the
    hook is its title). Card 4 puts the theme in the upper three quarters and, in the bottom quarter,
    "Housing at Noon. / Free edition every weekday at noon ET. / homeeconomics.us/noon" (no query
    string on the image). Card 4 has no source pills.
  - Nothing is below 28 px (date, footer, pills, captions raised from 22-24 px; body 33 -> 28 px).
    Every card is checked for vertical and horizontal overflow; a theme that does not fit drops
    paragraphs, then trims its opening paragraph (sentence boundary where possible, otherwise a word
    boundary with an ellipsis) at 1000 down to 80 characters. The small logo in the card header is an
    image and its lettering is smaller than 28 px.
  - Assumption to confirm with the owner: "cards 2 and 3 = the next two themes, card 4 = theme 4" was
    read as the three themes that follow the hook (free themes 1-3), not edition themes 2-4, so theme 1
    is not left out and premium themes stay off X.
- **Posting (`pulse/editor/xpost.py`).** One new tab in the live Chrome (CDP 9223 through
  `cdp_tab.CdpTab`, never Playwright), `https://x.com/compose/post`, the text typed with
  Input.insertText (Enter key events for line breaks), the four PNGs attached with
  DOM.setFileInputFiles on `input[data-testid="fileInput"]`, wait for four thumbnails, then:
  `preview(date)` screenshots the composer to `~/work/noon/xposts/<date>_preview.png`, closes the
  composer and clicks Discard in X's "Save post?" dialog; `post_carousel(date)` clicks
  `tweetButton`, waits for X's toast, takes the post URL from its "View" link (else from the profile
  page, a second load), screenshots `xposts/<date>.png`. Actions 1-3 s apart, at most 2 loads. A
  login page stops the run with "not logged in: re-login at https://browser.homeeconomics.us".
  State `~/work/noon/xposts/state.json` `{date: {status, url, posted_at, text, error, preview_at,
  preview_note}}`; status draft | error (safe to retry) | posting | posted | unconfirmed. The last
  three block any further post for that date. If a run dies after the click, the status becomes
  `unconfirmed`; check X by hand, and edit state.json only if the post did not go out.
  Default text: first two sentences of the standfirst (one sentence, then a word cut with "…", if
  needed to stay under 280 plain characters), a blank line, then "Free edition daily at noon ET:
  https://www.homeeconomics.us/noon?utm_source=x&utm_medium=social". The server refuses text over 280
  as X counts it (links 23). CLI: `python xpost.py text DATE`, `python xpost.py preview DATE`.
- **Editor.** `/cards/{date}` has a "Post to X" panel: editable text with both counts, the four
  thumbnails, "Preview composer" (shows the screenshot inline) and "Approve and post" (confirm()
  dialog, then posts; shows the URL and disables itself afterwards). Routes: `GET /api/xpost/{date}`,
  `POST /api/xpost/{date}/preview` `{text}`, `POST /api/xpost/{date}/post` `{text}`,
  `GET /api/xpost/{date}/preview.png|shot.png`; all owner-cookie only. The main editor shows a
  "Cards and X post" button under the banner once the edition is sent. Script: `static/cards.js`.
- **Tested 27 Sep 2026 on the 25 Sep edition.** Cards rendered (image hook card, since theme 1 has
  the owner's hand-drawn diagram); a stress render with no image, a 330-character hook and a
  1,500-character paragraph fitted. `preview` for real: X logged in as @AzizSunderji, four thumbnails,
  text with the blank line intact, no warning from X; closing gave "Save post? … Save / Discard",
  Discard was clicked, and X's drafts list afterwards showed only the owner's older drafts. Post was
  not clicked. `noon_verify.py`: ALL PASS (renderer untouched).

## Status update — 28 Sep 2026: card 1 carries the whole standfirst; LinkedIn carousel PDF; X posting removed

Owner's rule (Aziz, 28 Sep 2026): "Card 1 doesn't have enough info. We can have the lead but it should
have the whole intro para, since it's short. I think we don't need the elaborate posting mechanism, just
the cards formatted right, for both platforms [X and LinkedIn], with the CTA."

- **Card 1 (`cards.py`).** "Housing at Noon · <date>" small, then the whole standfirst (plain text,
  images and link markup removed, paragraph breaks kept), first sentence in ABC Oracle Edu Medium, the
  rest Regular, set as large as fits from 56 px down to 34 px in 2 px steps. Below it the first theme's
  image with caption (image area at least 380 px tall), otherwise the large logo and tagline. Footer
  "Free edition daily at noon ET / homeeconomics.us/noon" kept. Fitting order: text + image at 56-34 px;
  then text + logo at 56-34 px; then the standfirst cut at a sentence end with " …" (logo, 34 px),
  dropping one sentence at a time. With no standfirst the old behaviour holds (first theme's title,
  bold, and that theme left out of cards 2-4). Note: recent standfirsts are 540-640 characters, not
  about 300; all of 22-28 Sep fit whole at 34-44 px, and 24/25 Sep kept their images. A 1,935-character
  stress test was cut at a sentence end and fitted.
- **Cards 2-4 unchanged.** The four PNGs keep their names and layout (for X: attach all four to one post).
- **LinkedIn carousel PDF.** `render_cards` also writes `Housing at Noon <date> carousel.pdf`: four
  pages, each 810 x 1012.5 pt (1080 x 1350 px at 96 dpi), no margins, the card PNG filling the page
  and embedded losslessly (PyMuPDF, `make_carousel_pdf`). Chromium's `page.pdf` was tried first but
  rounds the page to 810 x 1013.04 pt, so it was not used. `publish_cards` mirrors it to the same
  Dropbox `editions/cards` folder. Checked on 25 and 28 Sep: pdfinfo 4 pages, 810 x 1012.5 pt; each
  page's embedded image is pixel-identical to its PNG; a pdftoppm render at 1080x1350 differs from the
  PNG by a mean of 3-4.5 of 255 (poppler smoothing), no layout difference.
- **Editor.** `/cards/{date}` shows one line ("X: attach the four PNGs to one post (they display as a
  carousel). LinkedIn: upload the PDF as a document post."), a download link for the PDF
  (`/cards/{date}/carousel.pdf`), and the four cards. The "Post to X" panel, `static/cards.js` and all
  `/api/xpost/*` routes are gone. The main page's post-send button is now "Cards".
- **`xpost.py`** stays in the repo, unused, with a docstring saying so. `~/work/noon/xposts/` was
  deleted after checking `state.json`: one entry (25 Sep) with status `draft`, nothing posted.

## Status update — 28 Sep 2026 (later): one card per free theme, no intro card, up to 10 cards

Owner's rule (Aziz, 28 Sep 2026): no opening/intro card; one card per FREE theme with the theme's
entire text; a format that works for Instagram and X carousels (1080x1350). This replaces the hook
card and cards 2-4 described in the two sections above.

- **Which themes.** Exactly the free edition's: `cards.free_themes` sorts entries by rank and calls
  `email_lunch._split_entries(entries, "free")`, so the set, order, numbers (rank), title casing,
  sentence-start fixes, link narrowing ("On X," before handles) and pills (`_entry_pills`) match the
  free email. Images in a summary are left out. Links appear as plain text (the anchor words, no
  underline).
- **Layout (`cards.py`).** 64 px margins. Thin header: blue number and bold 46 px title on a theme's
  first card; "Title (continued)" at 30 px on continuation cards. Body 40 px, stepping down in 2 px
  steps to 34 px; paragraphs kept. Pills (28 px) on the theme's last card only. Footer (28 px):
  "Housing at Noon · <date>", and "2/2"-style markers on continuation cards (not on a theme's first
  card). The last card of the set has the sign-up band ("Housing at Noon. / Free edition every
  weekday at noon ET. / homeeconomics.us/noon") at the bottom, below the footer.
- **Fitting.** All measuring happens in one Playwright page (the body is swapped in place). A theme
  first tries one card at 40, 38, 36, 34 px. If it does not fit at 34 px it is split at sentence
  ends (greedy fill, measured card by card; a sentence taller than a card would be split at a word):
  the card count is the one needed at 34 px, and the font is the largest size that keeps that count.
  Every final card is measured again before the screenshot; nothing is clipped.
- **10-card cap.** If the set needs more than 10 cards, the last theme is cut at a sentence end
  with " …" to fit the cards left (at least one card per theme); if that is not enough, the theme
  before it is cut too, and so on. No theme is dropped. Each cut is logged as a WARNING
  ("theme N cut to 1 card(s) at 34 px ... k of m sentences kept").
- **Files.** `Housing at Noon <date> card1.png` … `cardK.png` (K varies) and `... carousel.pdf`
  (all cards, PyMuPDF, 810 x 1012.5 pt pages). Stale `card(K+1).png` and higher from an earlier
  render of the same date are deleted from the cards folder and from the Dropbox mirror.
- **Editor.** `/cards/{date}` shows all cards, the PDF link, and "Instagram: post all cards as one
  carousel (max 10). X: post the first four cards. LinkedIn: upload the PDF as a document post."
- **Tested.** 25 Sep (7 free themes): themes 1, 2, 3 two cards each (40, 34, 40 px); themes 5, 6, 7,
  11 needed at least 14 cards in total, so all four were cut to one card at 34 px (theme 5 kept 5 of
  6 sentences, 6 kept 4 of 8, 7 kept 3 of 4, 11 kept 4 of 7). 28 Sep (6 free themes): 10 cards with
  no cuts (themes 2, 3, 4 two cards at 40 px; 10 one card at 36 px; 16 one card at 38 px; 17 two
  cards at 40 px). `noon_verify.py`: ALL PASS. `xpost.py` (unused) still imports.
- **Open point for the owner.** Editions with seven or more long free themes (like 25 Sep) will
  lose text from the later themes to stay within 10 cards.

## Status update — 28 Sep 2026 (cards v4): every free theme on ONE card at 36 px, condensed to fit; closing sign-up card

Owner's rules (Aziz, 28 Sep 2026): every free theme fits on one card, no continuation cards, at a fixed
text size; the builder condenses the theme text to fit, more aggressively when the theme is long. "It
should look really good, that's the key for social." Eyebrow "Theme One" ... by position in the free
edition (not the entry's rank). Source pills on every card. Small Home Economics logo in every footer. The
last card is a standalone call-to-action card. This replaces the continuation cards and 10-card cutting in
the section above.

- **Layout (`cards.py`).** 80 px side margins. Eyebrow "Theme One" (blue, Medium 30 px); title Medium
  54 px, -0.03em, balanced wrapping; body 36 px, line height 1.35, at most two paragraphs; each paragraph's
  last two words are bound so no line holds a single word (CSS `text-wrap: pretty` was tried and dropped:
  it made short paragraphs ragged). The pills (cream #DADFCE, ink, 28 px, as in the email) sit just above
  the footer, so they are in the same place on every slide. Footer: HE wordmark (52 px,
  `static/he-large-black.svg`, copied from Brand assets) left, "Housing at Noon · <date>" right. CTA card:
  brand blue background, large logo, "Housing at Noon" 104 px, "A daily brief on the U.S. housing market,
  free every weekday at noon ET", and homeeconomics.us/noon in a cream box. Up to 9 themes plus the CTA
  (Instagram's 10); beyond 9, the last themes are left off and logged, and the CTA card stays.
- **Fitting.** Budget = the most characters of a two-paragraph filler (the theme's own words) that fit at
  36 px with the theme's own title and pills, found by rendering. Recent budgets are 727-898 characters.
  If the visible text is within the budget and fits as it is, it is used unchanged. Otherwise
  `claude-sonnet-5` condenses it. The pipeline has no Sonnet step, since v4b uses Opus and Haiku, so the
  owner's default model applies (`NOON_CARDS_MODEL` overrides). The prompt states N = 95% of the budget
  and asks the model to keep the meaning, every number and every attribution, and the author's
  first-person commentary as the last paragraph. It also asks for at most 2 paragraphs, no links,
  headings or bullets, and a measured register. The first run dropped every one of Aziz's first-person
  paragraphs until the commentary rule was added. A reply over N gets one retry: the reply's sentences
  are shown with their lengths and the target is 0.9N. A second reply that is still over N but fits on
  the card at 36 px is kept, because the 5% margin exists only to make it fit. This departs from the
  brief's "cut", and the owner can reverse it. Otherwise whole sentences are dropped from the end until
  it fits. Overflow at 36 px after that steps to 34 px, then cuts. Nothing is clipped.
- **Cache and cost.** `~/work/noon/cards_cache.json`, keyed by sha1(theme markdown + budget). A second
  render of 25 and 28 Sep through `/cards/<date>` was byte-identical and made no API calls. Cost per
  edition, logged with token counts: about $0.09 for 25 Sep (14 calls, 21.8k input + 4.5k output tokens)
  and about $0.065 for 28 Sep (11 calls). `ANTHROPIC_API_KEY` was added to `~/.noon_env` (it was not
  there; the same key as `~/.pulse_dev_env`); backup at `~/.noon_env.bak_20260928`. Without a key the
  builder cuts at sentence ends instead and logs a warning.
- **Limitation, measured.** Sonnet 5 with thinking off does not hold a character limit. First replies
  ran 5-50% over N. Adaptive thinking at low, medium and high effort did not help: it barely thought, and
  the replies were 6-53% over. Across repeated runs, 1 to 5 of the 13 themes still ended in a sentence
  cut. At this compression (budgets are 35-85% of the theme length) numbers and attributions are
  lost. In the committed cache: 25 Sep Theme Three lost Lance Lambert's LinkedIn point (5.99% before
  Khamenei's death) and @JonKutsmeda's $5,912 per $100K. 25 Sep Theme Two (2,160 visible characters)
  lost Kevin Erdmann, Census/HUD as the source, HousingWire as the source of the median-price figure, the 6% average price cut, and all of Aziz's home-size commentary (the final sentence cut removed it). 28 Sep Theme Four lost
  its second paragraph (the FT post; 30-year rates above 7%). 28 Sep Theme Two lost "near-7.5% rates" and
  the 37% BMO figure. Options for the owner: a third request, a smaller body size, or accepting these
  losses.
- **Editor.** `/cards/{date}` line: "Instagram: post all cards as one carousel. X: post the first four
  cards. LinkedIn: upload the PDF as a document post." The first render of a date takes about 30-60 s
  (Claude calls).
- **Tested.** 25 Sep: 7 themes + CTA = 8 cards. 28 Sep: 6 + CTA = 7. All at 36 px. Stale card9/card10
  from v3 were deleted. `noon_verify.py`: ALL PASS. Previews: `OVH/NewsAtNoon/outputs/cards_v4_<date>_*`.

## Status update — 28 Sep 2026 (cards v5): condensed card texts keep every number and attribution; pill names

Owner's rule (Aziz, 28 Sep 2026): cards must keep every number and attribution; fix pill names.
Commit ebd315a. This replaces the "condense, retry once, else cut at a sentence end" step in the
cards v4 section above.

- **Facts to keep (`cards.py`).** Before condensing, the facts are taken from the visible text less its
  pointer sentences ("My X post on this is here", "I wrote about this on Substack here", "the map below").
  They are numbers, percentages, dollar figures and dates (regex, each with a few words of context),
  @handles and "On X,"/"On LinkedIn,"-style lead-ins (regex), every source pill the text names, and
  other named sources and people (`claude-haiku-4-5`; names not found in the text are dropped, possessive
  pairs are split, and a surname is dropped only when the full name is listed). The author's commentary
  is a fact as well: first-person sentences (pointer sentences aside), plus a last paragraph that names no
  source. The check for it uses its distinctive words (at least two must survive), plus first-person
  wording only if the original view was in the first person. That last condition matters. With a plain
  first-person check, the model invented "I don't have a clean answer" for 25 Sep Theme One, whose only
  first-person words were in pointer sentences. With names drawn from pointer sentences, it invented
  "Via Substack."
- **Condense, verify, repair.** `claude-sonnet-5` gets the facts list, a target of 85% of the card budget,
  the budget as a hard ceiling, and an instruction to keep the commentary and add nothing. A reply passes
  when every fact is present (numbers and handles verbatim, names case-insensitive on word boundaries),
  it is within the budget, and it fits at 36 px. Otherwise up to 3 repair requests follow, each naming the
  missing facts and the exact excess. When a draft is within the budget but still does not fit, the excess
  is measured on the card: the filler-based budget overstates what real two-paragraph text can use by
  about 2-10%. Only the latest draft is sent back with each repair, to keep the cost down.
- **Never sentence-cut a condensed text.** If no draft passes, each draft is judged by what the card would
  show (whole at 36, 34 or 32 px, else the last-guard cut). The builder picks the one that loses the
  fewest facts at the largest size, and a WARNING names the theme and anything missing (visible in
  `~/work/noon/logs/editor.log`). The render-time overflow cut at 32 px stays as the last guard.
- **Cache.** Key = sha1(`PROMPT_VERSION` + theme markdown + budget); `PROMPT_VERSION =
  "cards-v5-2026-09-28-facts"`. The old `~/work/noon/cards_cache.json` was deleted. Fact lists are cached
  per theme text. A result made while the Haiku call failed is not cached. Note: `anthropic` 1.3.0 in
  `pulse-venv` rejects `temperature`, so Haiku runs at its default. That is why the pill names are added
  deterministically.
- **Cost.** 25 Sep: Haiku 7 calls (4.1k in / 0.4k out), Sonnet 20 calls (49.6k in / 7.1k out), about
  $0.18. 28 Sep: Haiku 6 calls, Sonnet 13 calls (27.1k in / 4.1k out), about $0.10. Across five test runs
  of 25 Sep, the cost was $0.165-0.229; that edition is the heaviest case (seven long themes).
- **Pills.** `free_themes` now rebuilds a cleaned draft's pills with `links.outlets_for` (the ingest rule),
  so new display names reach cards of drafts ingested earlier. Stored `_pills` in `noon_drafts.db` were not
  changed, so a re-render of an already-sent email keeps its old pill names; new ingests get the new
  names. Added to `source_names.json` (file re-sorted by host): kevinerdmann.substack.com Kevin Erdmann;
  theargumentmag.com The Argument; gs.com Goldman Sachs Research (covers idfs.gs.com, publishing.gs.com);
  evansoltas.com Evan Soltas; theovershoot.co The Overshoot; robinjbrooks.substack.com Robin Brooks;
  live-aia-web.pantheonsite.io AIA; aia.org AIA; brownstoner.com Brownstoner; thecityreporter.nyc The
  City Reporter; fastcompany.com Fast Company; flsenate.gov Florida Senate; theverge.com The Verge;
  gov.uk UK Government; supremecourt.gov Supreme Court; wnyc.org WNYC; worksinprogress.news Works in
  Progress; buildingabundance.ca Building Abundance; ourworldindata.org Our World in Data.
- **Result (committed cache).** Theme: original chars / budget / final chars / px / facts kept of total /
  repairs.
  25 Sep: 1: 1228/745/860/32/9 of 9/3. 2: 2160/727/747/32/31 of 32/3 (last-guard cut). 3: 1548/879/1024/32/36 of 36/3.
  4: 1153/898/864/36/19 of 19/0. 5: 1455/836/822/36/12 of 12/2. 6: 1148/837/821/36/17 of 17/0. 7: 1321/831/809/36/10 of 10/2.
  28 Sep: 1: 1361/809/783/36/22 of 22/1. 2: 1184/829/840/34/20 of 20/3. 3: 1475/850/885/34/13 of 13/3.
  4: 979/828/776/36/5 of 5/0. 5: 770/827/678/36/8 of 8/0. 6: 871/819/757/36/18 of 18/0.
  Every earlier loss named in the cards v4 section is now kept: Kevin Erdmann, Census/HUD, HousingWire,
  6%, 5.99%, $5,912 per $100K, 28 Sep Theme Four's second paragraph (FT, 7%), 7.5% and 37%.
- **Open for the owner.** (1) 25 Sep Theme Two (2,160 chars, 32 facts) does not reliably fit even at 32 px.
  It fitted whole in two of five test runs. In the committed run the last guard removed Aziz's home-size
  commentary. The per-figure HousingWire attribution for the median price is also gone, although
  HousingWire is still named. Options: allow 30 px, allow more repair rounds, or accept the loss for
  themes this dense. (2) The fact check cannot catch a changed meaning. On review, 28 Sep Theme Two said
  that 37% of parents with young children "expect to give it this year" (the original says they expect
  help from their own parents). That text was corrected by hand in the cache (entry marked
  `hand_corrected`; script `OVH/NewsAtNoon/scripts/fix_cache_28sep_theme2.py`). A second-pass meaning
  check, for example a Haiku comparison of each claim, would be the systematic fix. (3) Cards show at most
  six pills (`card_html` `[:6]`), so 25 Sep Theme Three shows no MarketWatch pill, although the text names
  MarketWatch.
- **Checks.** py_compile; `noon_verify.py` ALL PASS; editor restarted, active; `/cards/2026-09-25` and
  `/cards/2026-09-28` return 200 from the cache (no API calls). Previews:
  `OVH/NewsAtNoon/outputs/cards_v5_<date>_card<N>.png` and `cards_v5_<date>_carousel.pdf`; fidelity data
  with original and final texts: `OVH/NewsAtNoon/data/cards_v5_fidelity_<date>.json`.

## Status update — 29 Sep 2026 (cards v7): constant 36 px body, one-line titles, new top row, card text editor

Owner's rules (Aziz, 29 Sep 2026): "text size must be constant"; "I want a way to edit the text, some
kind of editor, like the main one"; the logo moves to the top right, beside the numeral; the title sits
on one line. Commit cfea8c2. This replaces the 34/32 px fallback and the footer logo described in the
cards v4 and v5 sections.

- **Layout (`cards.py`).** The top row has the 96 px bold blue numeral at the left (it replaced "Theme One"
  in an uncommitted edit, now committed) and the small HE logo (52 px) at the right, centred on the numeral.
  Top padding is 48 px. The footer holds only "Housing at Noon · <date>", left-aligned, 40 px from the
  bottom. The title is on one line: 54 px, stepping down 2 px to 40 px. If a generated title does not fit
  at 40 px, `claude-sonnet-5` shortens it to the characters that fit (cached, logged). If an owner's title
  does not fit, it wraps and the panel flags it. Budgets rose by about 100 characters (28 Sep: 912-982,
  against 809-850 before).
- **Constant 36 px.** `FALLBACK_PXS` is removed. A draft passes when every fact is present and the whole
  text fits at 36 px. After 3 repair rounds, if the latest draft still does not fit, up to 2 more rounds
  follow (5 in all) with a target of 75% of the budget. If no draft fits at all, the model is asked once to
  drop its least important sentence(s) and keep every listed fact (WARNING "last resort"). The draft that
  fits with the fewest missing facts is used. Only if none fits does the last guard cut at a sentence end
  at 36 px. `PROMPT_VERSION = "cards-v7-2026-09-29-36px"`. `FACTS_VERSION` stays at the v5 value, so the
  Haiku fact lists are reused.
- **Card text editor.** `/cards/{date}` (`static/cards.html`, `cards.js`, styles at the end of `style.css`)
  renders the cards and then shows each image with a text panel. The panel has the title (one line; a
  live check with the card font says what size it will be set at, and turns red if it cannot fit at
  40 px), the body (contenteditable, one paragraph per block, plain text only), a counter against the
  card's budget (red when over), Re-render, and Reset to generated. The CTA card's description line can
  be edited the same way (pos 0). Edits save 1.2 s after typing stops (PUT) into the new table
  `card_overrides` (date, pos, title, body, updated_at) in `noon_drafts.db`. Every render uses them,
  including the one after the 11:59 send, and never calls Claude for an edited card. If an owner's body is
  too long, the image leaves off the last sentence(s) and the panel says so. The API has
  GET `/api/cards/{date}`, PUT/DELETE `/api/cards/{date}/{pos}` and POST `/api/cards/{date}/render`;
  `/cards-font/medium.otf` (owner only) serves the title font for the live check. The render writes
  `Housing at Noon <date> cards.json` (not mirrored) for the panel. Renders run one at a time behind a
  lock. A re-render from the cache takes about 1 s.
- **Tested (28 Sep).** First render under v7: 44 s, 16 Sonnet calls, about $0.09. Theme: original /
  budget / final chars / facts kept / repairs: 1: 1361/936/890/22 of 22/3. 2: 1184/932/890/20 of 20/1.
  3: 1475/982/946/13 of 13/2. 4: 979/920/815/5 of 5/0. 5: 770/912/770 unchanged. 6: 871/928/871
  unchanged. All at 36 px. No theme needed the last-resort sentence drop. Four titles were shortened
  by the model: 3 "UK housebuilder stocks surge on Burnham Help-to-Buy" -> "... on Help-to-Buy revival"
  (Burnham dropped, "revival" added); 4 "becomes" -> "is"; 5 dropped "'s North Shore"; 6 dropped
  "Mortgage". The API test saved an override for card 1 and re-rendered: the PNG and PDF page 1 changed.
  The override was then deleted and the card re-rendered: both came back byte-identical. An overlong owner
  body and title (card 2) were cut and wrapped, and flagged. No overrides are stored now.
- **Open.** (1) 28 Sep Theme Two again says that 37% of parents with young children "expect to give it
  this year". The source says they expect help from their own parents. The v5 hand correction lived in
  the retired v5 cache entry. Fix it in the card panel (the override persists). (2) Overrides are keyed by
  position. If the themes are reordered after an edit, the text stays with the position.
- Previews: `OVH/NewsAtNoon/outputs/cards_v7_2026-09-28_card<N>.png`, `cards_v7_2026-09-28_carousel.pdf`,
  `cards_v7_2026-09-28_card1_override_test.png`, `cards_editor_phone.png`, `cards_editor_phone_v2.png`
  (a whole card row at 390 px) and `cards_editor_desktop.png`.

## Status update — 29 Sep 2026: no draft, OpenAI credits exhausted, send timer paused

- The 11:00 UTC synthesis produced no v4b brief: the embeddings step got "You have no credits
  remaining" from OpenAI (text-embedding-3-small is the pipeline's only OpenAI use). The V4b
  workflow step is written `|| echo "V4b run failed — continuing without"`, so the run reported
  success and the health email said "0 broken". Aziz is adding credits; the script
  `NewsAtNoon/scripts/223_wait_for_openai_then_synth.sh` re-triggers pulse-synth.yml once they are live.
- `noon-send.timer` was STOPPED at 15:28 UTC on Aziz's instruction so nothing goes out with no
  draft. Re-enable with `systemctl --user start noon-send.timer` once today's edition is handled.
- Follow-ups: fail the run when V4b fails; a BROKEN health stage when the day's brief is missing;
  an OpenAI credit-balance probe in the health email.

## Status update — 29 Sep 2026: server-Chrome article enrichment removed; Browserbase is the only enrichment

Owner's rule (Aziz, 29 Sep 2026): "Chrome is a poor way of doing enrichment, move all of it to
Browserbase." This reverses the 24 Sep section "article enrichment moves to the server Chrome (step 1)"
before its one-week review ended.

- **Removed.**
  - Units `noon-enrich.service/.timer` (disabled, unit files deleted from `~/.config/systemd/user/`,
    daemon reloaded).
  - `pulse/editor/enrich_server.py` and `pulse/scripts/apply_server_enrichment.py` (git rm).
  - The `pulse-synth.yml` step "Apply server-Chrome enrichment". The step "Enrich articles via
    Browserbase" is unchanged and is now the only article-body enrichment.
  - `~/work/noon/feeds_private/enriched_bodies.json` and `~/work/noon/enrich_status.json` (paywalled
    text, no longer produced). `~/work/noon/logs/enrich.log` is left as a record of past runs.
  - Health email stage "2.2b — Article body enrichment (server Chrome)" and its WARN "private feed URL
    not configured". Stage 2.2 (Browserbase enrichment) is unchanged.
- **Health email stage 2.1.** Server Chrome rows for WSJ, NYT, FT, Bloomberg, Economist and Substack
  now read "used by ad hoc reads only (not article enrichment)"; Browserbase rows read "used by
  enrich_articles.py". The Goldman row is unchanged. The report sets these labels itself; the
  `used_by` text in login_status.json is not changed.
- **Stays on the server Chrome.** The Goldman Sachs Research feed (`gs_feed.py`, `noon-gsfeed.timer`),
  the login check (`login_status.py`, `noon-loginstatus.timer`), X posting (`xpost.py`), and ad hoc
  reads (`live_tab_fetch.py`, `paywall_fetch.py`). `cdp_tab.py` stays because those scripts use it.
- **Kept as is.** The private path (`~/work/noon/feeds_private/`, `/private/<NOON_PRIVATE_TOKEN>/` in
  Caddy, `gs_research.xml` and `gs_cache.json`), `FEED_PRIVATE_BASE` on the collection and re-collect
  steps (needed for the Goldman feed), and `NOON_PRIVATE_BASE` on the health-report step (no stage
  reads it now; left in place, harmless). Caddy's 404 rule for a stray public `enriched_bodies.json`
  is also left in place. Rows already marked `enrich_mode='server_chrome'` in pulse.db stay as they are.
- **Checked.** YAML parses; `pipeline_health_report.py` compiles; a `--dry-run` against a copy of
  pulse.db rendered 30 stages with no 2.2b and 2.1 OK ("all 10 site logins OK").

## Status update — 29 Sep 2026: cards on Sonnet 5.5; Opus 5.5 shadow test; price table

Owner's rules (Aziz, 29 Sep 2026, "do all three"): cards to Sonnet 5.5; a three-edition Opus 5.5
shadow test of the brief with no subscriber impact; current prices in the spend tracker.
Commits d9f2c16, 55cb30f, f5367bf.

- **Cards (`editor/cards.py`).** `CONDENSE_MODEL` defaults to `claude-sonnet-5-5`. Sonnet 5.5
  rejects thinking "disabled" with a 400, so it is sent `thinking: {"type": "between_tools"}` (its
  lowest setting: no up-front thinking; accepted only at effort low/medium/high) with
  `output_config.effort: "high"`. `PROMPT_VERSION = "cards-v8-2026-09-29-sonnet55"`;
  `FACTS_VERSION` unchanged, so Haiku fact lists are reused. `NOON_CARDS_MODEL=claude-sonnet-5` in
  `~/.noon_env` switches back (then bump `PROMPT_VERSION` again). Editor restarted.
- **Cards comparison** (re-render into `OVH/NewsAtNoon/data/cards_s55/`; published cards and the
  Dropbox mirror were not replaced). Condensed themes, facts kept / repair rounds, S5 -> S5.5:
  28 Sep: 1 22/22, 3 -> 1; 2 20/20, 1 -> 1; 3 13/13, 2 -> 1; 4 5/5, 0 -> 0 (5 and 6 fit unchanged).
  29 Sep: 1 8/8, 0 -> 0; 2 35/35, 5 + last-resort drop -> 1; 3 25/25, 1 -> 0; 4 17/17, 5 -> 0;
  5 15/15, 4 -> 2; 6 14/14, 0 -> 0; 7 9/9, 1 -> 0; 8 14/14, 0 -> 1; 9 22/22, 1 -> 0.
  Repair rounds 23 -> 7 (commit d9f2c16 says 30; 23 is correct). Cost and time: 28 Sep $0.09 /
  44 s -> $0.058 / 33 s; 29 Sep $0.255 / about 119 s -> $0.111 / 56 s (the S5 29 Sep figure also
  includes 9 Haiku fact calls, about $0.007). Every card at 36 px. Reading every condensed text
  against its original found no reversed meaning. 28 Sep Theme Two now says "37% of parents with
  young children expect help" (Sonnet 5 wrote "expect to give it"); "from their own parents" is
  dropped, so it is vague but not wrong. 29 Sep Theme Two drops that Mohtashami rejects 9% as a
  base case (it keeps his conditions) and Hepp's "not her baseline". 29 Sep Theme Four's title still
  cannot be shortened to one line and wraps, as before. No card overrides existed; none were touched.
  Previews: `OVH/NewsAtNoon/outputs/cards_s55_<date>_card<N>.png`; side-by-side texts:
  `OVH/NewsAtNoon/data/cards_s55_compare.json`.
- **Writer model override.** `PULSE_WRITER_MODEL` (or `--model` on `run_pipeline.py synthesize` and
  `v4b_runner.py`) sets the writer for the v1 synthesis (`analysis/synthesize.py`) and the
  v3.1/v4/v4b writers. Default `claude-opus-4-8`, whose requests are byte-for-byte as before (no
  thinking field, default effort). For `claude-opus-5-5`, `synthesize.writer_request_kwargs` sends
  thinking adaptive (5.5 cannot disable it), `output_config.effort: "high"` (5.5 defaults to
  medium; high matches today), and doubles `max_tokens` (thinking counts toward it: 32768 -> 65536,
  4096 -> 16384; all calls stream). `check_writer_refusal` raises on `stop_reason == "refusal"`.
  No writer call uses tools, so the 5.5 ban on forced `tool_choice` does not apply.
- **Shadow step (`pulse-synth.yml`, last step, after "Push DB back to Dropbox").** Runs on the
  first production run of 30 Sep, 1 Oct and 2 Oct 2026 (UTC), once per day. It is gated on the
  date, not on `github.event_name == 'schedule'`: the brief is actually written by the 11:00 UTC
  workflow_dispatch run (actor azizsunderji); GitHub's cron fires hours late and those runs are
  skipped by the guard, so a schedule-only gate would never fire. It runs `v4b_runner.py --model
  claude-opus-5-5 --rewrite-v1-themes --briefing-type daily_v4b_shadow_opus55 --to
  aziz@home-economics.us --subject-prefix "[SHADOW Opus 5.5] "`: the v1 synthesis again on 5.5
  (not stored; "recently led themes" read from before today so the production brief is not
  counted), its themes replace the stored scaffold's (paper, headlines and the injected lists stay
  production's), then the full v4b chain on 5.5, stored as `daily_v4b_shadow_opus55` and emailed to
  Aziz only; then pulse.db is pushed again. `editor/ingest.py` reads only `daily_v4b_attach`, so the
  noon server never sees it. A non-production type without `--to`/`--no-send` exits, so a shadow
  cannot reach `send_lunch_to_subscribers`. Non-fatal: `continue-on-error`, `timeout-minutes: 35`,
  `|| echo`. Job timeout 60 -> 100 min (the production run takes about 50). Embeddings are not
  shared with the production run (that would change the production step); they cost under 1 cent.
  The trigger classifier's cache in pulse.db is reused, so it adds almost nothing.
- **Estimated shadow cost (ESTIMATED): about $3 per day, $2.5-4.5, so about $10 for the three
  days.** Basis: v1 synthesis on Opus 4.8 about $1.9/day (anthropic_spend minus the v4b cost),
  v4b Opus $0.9-1.9 and Haiku $0.04-0.15 (last five `_v4b_meta.cost`); Opus 5.5 is 20% cheaper per
  token but adds thinking tokens at effort high. The shadow's spend is recorded in the
  `anthropic_spend` table under `claude-opus-5-5`, so that day's total there includes it.
- **Local proof.** Against a copy of pulse.db (`OVH/NewsAtNoon/data/opus55_proof/`), two single
  theme-rewrite calls on `claude-opus-5-5` (`scripts/models55_08_proof_opus55.py`): both returned
  200 and parsed. The model returned keep_original both times, with correct reasons (the attached
  items did not bear on the theme). The second call read 29,377 cached tokens. Total $0.19. The
  full shadow chain (`--rewrite-v1-themes`) was not run end to end locally; its first real run is
  30 Sep.
- **How to read the shadow results.** Each test day Aziz gets an email "[SHADOW Opus 5.5] <usual
  subject> | N entries ..." shortly after the production run. Compare it with that day's draft in the
  editor. In pulse.db, `SELECT id, created_at FROM briefings WHERE briefing_type =
  'daily_v4b_shadow_opus55'`; `content_json -> _v4b_meta` has `writer_model`, `cost`,
  `timings_seconds`, `rewrite_log`, and the production themes it replaced are in
  `_production_v1_theme_titles`. `preview_lunch.py` can re-render a stored id. The Actions log of the
  step prints the counts, cost and each rewrite. After 2 Oct, delete the step.
- **How to switch production to Opus 5.5 later.** Change `WRITER_MODEL_DEFAULT` in
  `pulse/scripts/analysis/synthesize.py` and `OPUS_MODEL`'s default in `pulse/scripts/v3_1_runner.py`
  to `claude-opus-5-5` (two constants), or set `PULSE_WRITER_MODEL: claude-opus-5-5` once in the
  job-level `env:` of `pulse-synth.yml` (one line; covers both). Also consider the job timeout.
- **Price table (`analysis/anthropic_spend.py`).** Added claude-opus-5-5 $4/$20 (cache read $0.20,
  Anthropic's rate, not 10%), claude-opus-5 $5/$25, claude-sonnet-5-5 and claude-sonnet-5 $2/$10,
  and the `claude-haiku-4-5` alias $1/$5; cache write 1.25x input and cache read 10% of input, as in
  the existing entries. Old entries kept.
- **Open.** (1) Shadow rows begin with "daily", so `run_pipeline._recent_paper_picks` (`LIKE
  'daily%'`) sees them; their paper is production's, so the exclusion list does not change.
  `dashboard/build_digest.py` lists all briefing types and will show them. (2) If the 30 Sep run
  fails before the V4b step (as on 29 Sep), no shadow runs that day.
