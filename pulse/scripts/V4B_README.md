# Pulse v4b — v1 themes as backbone, clusters attached

`v4b_runner.py` is the second revision of the unified-list pipeline. It
keeps v4's single ranked list but changes where the entries come from.

## Why

The v4 cluster-first run showed that embedding clusters group by
wording, not by event. A multi-outlet news story (CNBC + CoStar +
HousingWire on the mortgage-rate spike) fragmented across entries; three
single-outlet v1 themes (Compass/NWMLS, LA County v. State Farm,
data-center backlash) fell out because no cluster formed around them;
single-item rescue entries padded the list. Meanwhile v1's wide-context
call consolidates events well but attaches social reaction weakly, and
the v3.1 cluster chain is the better finder of organic conversations.

v4b keeps both strengths: **v1 `conversation_themes` are the news
backbone. Coherent social/mixed clusters attach to the nearest theme and
the theme is rewritten with them integrated. Clusters that attach to
nothing become standalone entries. One ranked list. No rescue pass.**

The runner is additive. It imports from `v4_runner.py`, `v3_1_runner.py`,
`analysis/roundup_clustering.py`, `analysis/synthesize.py` and
`delivery/*` and modifies none of them. It writes its own rows
(`briefing_type = "daily_v4b_attach"`).

## Stages

| # | Stage | Reused from | Notes |
|---|-------|-------------|-------|
| 1 | Scaffold | `v3_1_runner.load_v1_scaffold` | Latest v1 `daily` briefing. Everything kept; `conversation_themes` (T themes) become the backbone; v1's `conversation_roundups` are dropped. |
| 2 | Corpus, embed, HDBSCAN, US-housing check, sub-cluster, same-author merge, coherence gate | `v3_1_runner`, `roundup_clustering` | Identical call sequence to v4 → C coherent clusters. Embeddings and the `item_id -> row` map are kept. No rescue pass. |
| 3 | Attachment (new) | — | Rules below. Every cluster is logged with its best theme, cosine, URL overlap and decision (`_v4b_meta.attach_log`). |
| 3b | Per-item relevance gate (new, run 3) | Haiku via the same `TrackedAnthropic` client the v3.1 helpers use | One Haiku YES/NO call per item of every attached cluster. NO items are dropped from the attachment; a cluster with no YES item is detached and returned to the standalone pool. Every drop is logged (`_v4b_meta.gate_log`). Rules below. |
| 4 | Rewrite attached themes (new) | `_format_items_for_sonnet`, `historical_context_for_cluster` | One Opus call per theme with ≥1 surviving attachment; `V4B_REWRITE_PREFIX` + v1 `SYSTEM_PROMPT`. The model returns a `paragraphs` list. Length / paragraph / bridge / original-numbers checks; at most ONE retry call; otherwise the original is kept. Themes with no attachment pass through unchanged, `anchor_type = "news"`, no call. |
| 5 | Standalone clusters | `v4_runner.write_entry_for_cluster` (`V4_ENTRY_WRITER_PREFIX`) | Unattached coherent clusters plus clusters detached by the gate. Skips logged with reasons (`_v4b_meta.skips`). |
| 6 | Dedup | `embed_corpus` | Same pairing as v4 (`title + summary` cosine ≥ `--dedup-threshold`, default 0.80). Loser chosen by `(is_theme, size, n_news_outlets, earlier)`: a theme always beats a standalone entry. |
| 7 | Rank | `v4_runner.rank_entries` | v4 formula, cap `--max-entries` (18). |
| 8 | Assemble, post-process | `v4_runner.entry_to_theme`, `postprocess_entries`, `build_v4_briefing` | As v4, plus a local paragraph-break restoration pass (`restore_paragraph_breaks`) after `postprocess_entries`: `synthesize._validate_briefing_urls` rejoins a summary with single spaces whenever it strips a sentence, which is what flattened the data-center entry into one block in runs 1 and 2. The pass maps each surviving sentence back to its pre-post-processing paragraph and re-inserts the breaks; restorations are listed in `_v4b_meta.postprocess.paragraph_breaks_restored`. Theme-based entries keep v1's platform badges, `heat_level` and `topics`, plus one badge per new outlet/platform among attached items. Meta key is `_v4b_meta`. |
| 9 | Store | — | `briefing_type = "daily_v4b_attach"`. Skipped with `--no-store`. |
| 10 | Send | `_render_lunch_variants`, `_lunch_footer`, `_lunch_subject`, `send_lunch_to_subscribers`, `_post_resend` | Email product is **News at Noon** (`PRODUCT_NAME`, `EMAIL_FROM = "News at Noon <pulse@home-economics.us>"`; template in `delivery/email_lunch.py`, previews via `preview_lunch.py`). `--to` sends one email with subject prefix `[V4B SHADOW] `. Without `--to`/`--no-send` it sends premium (working links) and free (walled, top-N entries) variants to subscribers. |

## Attachment rules

- Theme vector: embedding of `theme + "\n" + related_news_trigger + "\n" + summary`
  (same `text-embedding-3-small` model as the corpus).
- Cluster vector: L2-normalized centroid of its item embeddings.
- `cosine` = cluster vector · theme vector, for every theme.
- `url_overlap` = number of cluster item URLs (item URL plus thread-merged
  URLs, normalized: scheme, `www.`, query, fragment and trailing slash
  stripped) that appear in the theme's `platforms[].url` or in any
  markdown link inside the theme summary.
- Candidate themes are ordered by `(url_overlap desc, cosine desc)`; the
  first is the cluster's **best theme**. A shared URL therefore outranks
  wording similarity, which is the failure mode this revision is
  addressing. The log records the top-cosine theme too when it differs.
- Attach to the best theme if `cosine >= --attach-threshold` (0.55) OR
  `url_overlap >= --attach-min-url-overlap` (1). Each cluster attaches to
  at most one theme.

## Per-item relevance gate (stage 3b)

Added after run 2, in which the same-author merge of clusters 8800 +
8801 welded Calculated Risk's Freddie HPI post to Lawler's GSE
MBS-holdings post; the whole cluster attached to the Freddie theme on
cosine 0.75 and the rewrite bolted the Lawler material on as
"Separately on the GSEs, ...".

- For every item in every attached cluster: one Haiku call
  (`GATE_MODEL = v3_1_runner.HAIKU_MODEL`, currently
  `claude-haiku-4-5-20251001`, so `analysis.anthropic_spend` prices it)
  with the theme title, trigger and first 600 chars of the summary, and
  the item's title plus first 800 chars of body. Question: "Does this
  item report on, or react to, the same event or argument as this
  theme? Answer YES or NO." `max_tokens = 4`.
- NO items are dropped from the attachment. A cluster that loses every
  item is detached and returned to the standalone pool (written by the
  standalone writer as before). A cluster that keeps some items is
  replaced by a new `Cluster` with the same `cluster_id` holding the YES
  items only, so the rewrite, the allowed-link set, the historical
  context and the entry's `attached_item_ids` / `cluster_size` all see
  the gated set.
- A failed Haiku call keeps the item (fail-open, logged).
- Every drop is logged (`_v4b_meta.gate_log`: theme index/title,
  cluster id, item id, source, author, title, raw answer). `attach_log`
  records gain `gate_kept_items` / `gate_dropped_items`, and a detached
  cluster's record is flipped to `attached: false` with
  `attach_reason` suffixed `; detached_by_gate` and `gate_detached: true`.
- Counts: `gate_items_checked`, `gate_items_dropped`,
  `gate_clusters_detached`, `gate_haiku_calls`,
  `clusters_attached_after_gate`, `themes_with_attachments_after_gate`,
  `clusters_standalone_after_gate`. Cost: `_v4b_meta.cost.haiku_gate`
  (`calls`, `cents`). Run 3: 15 items checked, 4 dropped, 0.79c.

The gate is strict with roundup-style items: in run 3 it dropped Slow
Boring's "The many ways to build more housing" from the California
theme even though the v1 theme already cites it. The original sentence
survives (the rewrite may not delete original sentences); only the
attachment is lost.

## Rewrite rules (`V4B_REWRITE_PREFIX`)

Input: the original theme JSON (`theme, summary, related_news_trigger,
platforms, heat_level, topics`), the attached clusters' items packaged
exactly as `write_roundup_for_cluster` packages them (id, source, url,
title, body to 1,500 chars, author, published_at, feed_name,
enrich_links), and the same past-6-day context block, chosen against the
centroid of the attached items.

The user message also states the original's character count, its
paragraph count and the exact length ceiling in characters.

The prefix requires: keep the news anchor and return the original
trigger unchanged; preserve the original's numbers, sources and
attributions and do not delete any original sentence; return the
summary as a JSON list `paragraphs` (one string per paragraph, every
original paragraph boundary preserved as a boundary between elements,
new elements for new voices, no element over 900 chars); a hard length
ceiling of `max(1.35 × original chars, original + 500 chars)`, with
every added sentence adding a fact, quote, number or named reaction
not in the original and no scene-setting; never start a paragraph with
"Separately", "In other news", "On a related note" or "Meanwhile,"
(such content is a different story and is left out); put every new
citation in its own sentence (a new link welded into an original
sentence would take the original fact down with it when
`_validate_briefing_urls` strips an unverifiable URL, which is what
removed the Abbott and AB 1903 sentences in the first test run);
integrate attached reporting and reaction into the argument rather than
appending a reaction paragraph; leave out attached items that belong to
a different sub-story; cite only URLs already in the original theme,
the attached items, their `enrich_links`, or time-stamped past-6-day
items; first sentence restates the anchor event; 2-4 short paragraphs
with all v1 paragraph, citation and attribution rules; return
`{"title","paragraphs":[...],"trigger","anchor_type":"news"|"mixed"}`
or `{"keep_original": true, "reason": ...}`. A legacy `summary` string
is accepted and split on blank lines; the list is joined with `\n\n`.

### Programmatic checks and the single retry

Constants: `REWRITE_SOFT_RATIO = 1.35`, `REWRITE_SOFT_SLACK = 500`
(the ceiling stated in the prompt), `REWRITE_HARD_RATIO = 1.5`,
`REWRITE_MAX_PARA_CHARS = 900`, `_REWRITE_BRIDGE_RE`.

After the first call, `_check_rewrite` flags any of:

- joined length > 1.5 × original chars;
- original had ≥ 2 paragraphs and the rewrite has fewer than 2;
- any paragraph over 900 chars;
- a paragraph starting with `Separately`, `In other news`,
  `On a related note` or `Meanwhile,` (local check; synthesize.py's
  bridge list is not edited);
- any numeric token of the original (`$450,000`, `6.87%`, `2021`, ...)
  absent from the rewrite. Added after run 3, in which the tighten
  retry of the Freddie theme dropped the Colorado / South Dakota /
  South Carolina state declines from an original sentence.

If anything is flagged, exactly ONE retry call is made as a second
turn of the same conversation (assistant reply appended, then a user
message listing the violations and the matching instructions: "tighten
to under N chars, remove anything that does not add a specific fact,
cut only added sentences"; "return ≥ k paragraphs as separate list
elements, none over 900 chars"; "remove the bridged sub-story
entirely"; "restore the missing original figures"; and always the
ceiling reminder). If the retry still fails any check, or fails to
parse, the original v1 theme is kept with reason
`retry_still_violates: ...`.

Other guardrails, unchanged: unparseable JSON, an empty paragraph list,
or any markdown link whose normalized URL is outside the allowed set
(original theme URLs + gated attached item URLs + their enrich_links +
offered historical items) keeps the original; an empty returned trigger
falls back to the original; an invalid `anchor_type` becomes `mixed` if
any attached item is social, else `news`.

Provenance (every rewrite attempt, success or not) in
`_v4b_meta.rewrite_log[i]`: `orig_len`, `rewrite_len`, `ratio`,
`length_ceiling`, `retried`, `retry_reasons`, `retry_still_violates`,
`first_attempt` (`rewrite_len`, `ratio`, `paragraphs` of the rejected
first answer), `opus_calls`, `original_paragraphs`,
`rewritten_paragraphs` (plus `original_len` / `rewritten_len` aliases
kept for readers of the run 1/2 JSON). Successful rewrites also carry
the same fields on the entry as `entries[i].rewrite_provenance`.
Counts: `rewrite_calls` (themes sent), `rewrite_opus_calls` (calls
including retries), `rewrite_retries`; `cost.opus_calls` has
`rewrites` (calls), `rewrite_themes`, `rewrite_retries`.

Note on short originals: for an original under 1,000 chars the hard
1.5× threshold is *below* the stated soft ceiling (`original + 500`).
In run 4 the 727-char Freddie theme came back at 1,360 then 1,133
chars (ceiling 1,227; hard limit 1,090) and was kept original.

## Ranking

```
score = size + 2 * n_distinct_news_outlets
      + (2 if anchor_type == "news" else 1 if "mixed" else 0)
```

For standalone cluster entries `size` and outlets are as in v4 (cluster
item count; distinct outlet names among `rss/google_news/substack/gmail`
items via `news_outlet_name`).

For theme-based entries:

- `size = n_distinct_cited_sources_in_v1_theme + total_attached_items`,
  where the first term is the number of distinct normalized URLs the v1
  theme cites (markdown links in the summary plus `platforms[].url`).
  Distinct URLs are the closest analogue to cluster items.
- `n_distinct_news_outlets` = distinct non-social `platforms[].name`
  values in the v1 theme (social names: twitter, x, bluesky, hackernews,
  reddit, threads, mastodon, linkedin, youtube, tiktok) plus distinct
  outlets among attached news items, case-insensitive.
- `anchor_type` is `news` for untouched themes and whatever the rewrite
  returned (`news` | `mixed`) for rewritten ones.

Ties break on larger size, then more outlets, then lower `cluster_id`.
Theme entries carry a synthetic `cluster_id = 700000 + v1_theme_index`.

## Entry schema (`v4b["entries"][i]`)

```
title, summary, trigger, anchor_type      as v4
origin               "theme" | "theme+attached" | "cluster"
v1_theme_index       int | null
v1_theme_title       str (theme entries only)
attached_cluster_ids [int]
attach_log           [ {cluster_id, size, best_theme, cosine, url_overlap,
                        attached, attach_reason, top_cosine_theme, ...} ]
                     (theme entries: one record per attached cluster;
                      cluster entries: the cluster's own record)
rewrite_status       "no_attachments" | "rewritten" | "kept_original (reason)" | null
rewrite_provenance   {orig_len, rewrite_len, ratio, retried, retry_reasons,
                      length_ceiling, original_paragraphs,
                      rewritten_paragraphs, opus_calls}
                     (rewritten theme entries only)
item_ids             theme-cited URLs mapped to items.id (corpus first,
                     then a 7-day raw DB lookup) followed by attached
                     cluster item ids (thread-merged ids included)
theme_item_ids, attached_item_ids, v1_cited_urls, unmatched_urls
sources              {source: count} over attached items
news_outlets, n_news_sources
cluster_size         "size" as defined above
cluster_id           real cluster id, or 700000 + theme index
score, rank
```

`v4b["conversation_themes"]` mirrors the entries in the shape
`render_briefing_html` renders (`_v4b_origin` added);
`v4b["conversation_roundups"] = []`. `v4b["_v4b_meta"]` holds
`source_v1_briefing_id`, `v1_theme_titles`, stage `counts`,
`timings_seconds` (includes `relevance_gate`), `attach_log`, `gate_log`,
`rewrite_log`, `skips`, `dedup_drops`, `coherence_gate_failures`,
`merge_log`, `postprocess` (includes `paragraph_breaks_restored`) and
`cost` (v4's tracker plus `opus_calls`, `opus_cents_by_phase` split
into rewrites vs standalone writes, and `haiku_gate`).

## Running

```
source ~/.pulse_dev_env
cd ~/work/HomeEconomics/pulse/scripts

python -u v4b_runner.py --no-send --no-store                       # dry run
python -u v4b_runner.py --no-send --no-store --dump-json out.json  # + dict
python -u v4b_runner.py --no-send --no-store \
    --dump-json ~/work/v4_scratch/v4b_runN.json \
    2>&1 | tee ~/work/v4_scratch/v4b_runN.log                       # test-run pattern
python -u v4b_runner.py --to aziz@home-economics.us --no-store     # shadow email
python -u v4b_runner.py                                            # subscriber send + store (not yet used)
```

`main()` calls `sys.stdout.reconfigure(line_buffering=True)` so the
progress lines stream when stdout is redirected through `tee` or to a
file; `-u` is no longer required but harmless.

Flags: `--db`, `--lookback-hours` (24), `--min-cluster-size` (3),
`--max-entries` (18), `--dedup-threshold` (0.80), `--attach-threshold`
(0.55), `--attach-min-url-overlap` (1), `--to`, `--no-send`,
`--no-store`, `--dump-json`.

## Rollback

- New file only. `v3_1_runner.py`, `v4_runner.py`, `synthesize.py`,
  `roundup_clustering.py` and `delivery/*` are untouched.
- Rows are stored under `briefing_type = "daily_v4b_attach"`; v1
  (`daily`), v3.1 (`daily_v3_1_hybrid`) and v4 (`daily_v4_unified`)
  rows are never written.
- Cutover is intended to be the `PULSE_PIPELINE` repository variable
  read by `.github/workflows/pulse-synth.yml` (`v3_1`, `v4`, `v4b`).
  It is not wired yet; the workflow still runs v3.1 unconditionally.

## Known risks

- The attachment threshold (0.55 cosine) and the URL-overlap rule are
  untuned. Too low and unrelated clusters pollute themes; too high and
  the social reaction stays standalone and duplicates the theme (the
  dedup pass then drops the standalone, losing the reaction entirely).
  Read `_v4b_meta.attach_log` after each run.
- A wrong attachment pollutes a good theme. The per-item gate (stage
  3b) now removes items that Haiku judges to be about a different
  event, the rewrite prompt lets the model return `keep_original`, and
  the foreign-link guardrail catches links from outside the inputs,
  but a plausible-sounding but wrong integration of a YES item is
  still not caught programmatically. The gate can also be too strict
  (see the Slow Boring case above); a dropped item that the v1 theme
  already cites costs nothing, a dropped fresh item is lost from that
  theme (it stays in its cluster only if the whole cluster detaches).
- Two Opus passes per attached story: v1 already wrote the theme, v4b
  rewrites it, and a rule violation adds one retry call. Cost is one
  or two Opus calls per attached theme plus one per standalone
  cluster; see `_v4b_meta.cost.opus_calls`. The gate adds one Haiku
  call per attached item (run 3: 15 calls, 0.79c).
- The hard 1.5× length threshold is stricter than the stated soft
  ceiling for originals under 1,000 chars, so short themes with a rich
  attachment tend to end as `kept_original` after the retry (Freddie
  theme, run 4). Raising the hard threshold to
  `max(1.5 × original, original + 500)` would align the two; not done.
- The paragraph-restoration pass keys sentences on their link-stripped
  text; a sentence that `_validate_briefing_urls` rewrote (corpus URL
  correction) still matches, but a sentence whose wording changed
  would stay attached to the preceding paragraph.
- The theme `size` term counts distinct cited URLs, which favours
  themes that cite many links over themes that cite one outlet deeply.
- `_validate_briefing_urls` HEAD-probes any cited URL not collected in
  the last 48 hours, so re-running against an old scaffold is slower and
  may strip sentences.
