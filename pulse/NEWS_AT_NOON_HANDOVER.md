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
5. Send time: decided 12:15 ET, not yet implemented (see feature 1 — it falls out of the editor design).

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
- Workflow: draft arrives ~7am ET, owner edits, **send goes out at 12:15 ET whether or not it was
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
- **Send at 12:15 ET from the droplet**: a cron (or the same FastAPI app's scheduler) at 16:15
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
  the edit link once), `noon-send.timer` (12:15 America/New_York; sends unless Held or already
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
