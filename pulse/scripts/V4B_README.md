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
| 4 | Rewrite attached themes (new) | `_format_items_for_sonnet`, `historical_context_for_cluster` | One Opus call per theme with ≥1 attachment; `V4B_REWRITE_PREFIX` + v1 `SYSTEM_PROMPT`. Themes with no attachment pass through unchanged, `anchor_type = "news"`, no call. |
| 5 | Standalone clusters | `v4_runner.write_entry_for_cluster` (`V4_ENTRY_WRITER_PREFIX`) | Unattached coherent clusters. Skips logged with reasons (`_v4b_meta.skips`). |
| 6 | Dedup | `embed_corpus` | Same pairing as v4 (`title + summary` cosine ≥ `--dedup-threshold`, default 0.80). Loser chosen by `(is_theme, size, n_news_outlets, earlier)`: a theme always beats a standalone entry. |
| 7 | Rank | `v4_runner.rank_entries` | v4 formula, cap `--max-entries` (18). |
| 8 | Assemble, post-process | `v4_runner.entry_to_theme`, `postprocess_entries`, `build_v4_briefing` | As v4. Theme-based entries keep v1's platform badges, `heat_level` and `topics`, plus one badge per new outlet/platform among attached items. Meta key is `_v4b_meta`. |
| 9 | Store | — | `briefing_type = "daily_v4b_attach"`. Skipped with `--no-store`. |
| 10 | Send | `_render_variants`, `_with_unsub_footer`, `_subscriber_subject`, `_post_resend` | `--to` sends one premium-variant email with subject prefix `[V4B SHADOW] `. Without `--to`/`--no-send` it calls v3.1's subscriber send. |

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

## Rewrite rules (`V4B_REWRITE_PREFIX`)

Input: the original theme JSON (`theme, summary, related_news_trigger,
platforms, heat_level, topics`), the attached clusters' items packaged
exactly as `write_roundup_for_cluster` packages them (id, source, url,
title, body to 1,500 chars, author, published_at, feed_name,
enrich_links), and the same past-6-day context block, chosen against the
centroid of the attached items.

The prefix requires: keep the news anchor and return the original
trigger unchanged; preserve the original's numbers, sources and
attributions and do not delete any original sentence; keep the
original's paragraph breaks and add breaks for new voices; put every
new citation in its own sentence (a new link welded into an original
sentence would take the original fact down with it when
`_validate_briefing_urls` strips an unverifiable URL, which is what
removed the Abbott and AB 1903 sentences in the first test run);
integrate attached reporting and reaction into the argument rather than
appending a reaction paragraph; leave out attached items that belong to
a different sub-story (same-author merges can weld two stories into one
cluster); cite only URLs already in the original theme, the attached
items, their `enrich_links`, or time-stamped past-6-day items; first
sentence restates the anchor event; 2-4 short paragraphs with all v1
paragraph, citation and attribution rules; return
`{"title","summary","trigger","anchor_type":"news"|"mixed"}` or
`{"keep_original": true, "reason": ...}`.

Programmatic guardrails after the call: an empty summary, unparseable
JSON, or any markdown link whose normalized URL is outside the allowed
set (original theme URLs + attached item URLs + their enrich_links +
offered historical items) causes the original v1 theme to be kept, with
the reason logged in `_v4b_meta.rewrite_log`. An empty returned trigger
falls back to the original; an invalid `anchor_type` becomes `mixed` if
any attached item is social, else `news`. A rewrite that collapses a
multi-paragraph original into one paragraph is kept but logged
(`original_paragraphs` / `rewritten_paragraphs` in `rewrite_log`).

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
`timings_seconds`, `attach_log`, `rewrite_log`, `skips`, `dedup_drops`,
`coherence_gate_failures`, `merge_log`, `postprocess` and `cost`
(v4's tracker plus `opus_calls` and `opus_cents_by_phase` split into
rewrites vs standalone writes).

## Running

```
source ~/.pulse_dev_env
cd ~/work/HomeEconomics/pulse/scripts

python -u v4b_runner.py --no-send --no-store                       # dry run
python -u v4b_runner.py --no-send --no-store --dump-json out.json  # + dict
python -u v4b_runner.py --to aziz@home-economics.us --no-store     # shadow email
python -u v4b_runner.py                                            # subscriber send + store (not yet used)
```

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
- A wrong attachment pollutes a good theme. The rewrite prompt lets the
  model return `keep_original`, and the foreign-link guardrail catches
  links from outside the inputs, but a plausible-sounding but wrong
  integration is not caught programmatically.
- Two Opus passes per attached story: v1 already wrote the theme, v4b
  rewrites it. Cost is one Opus call per attached theme plus one per
  standalone cluster; see `_v4b_meta.cost.opus_calls`.
- The theme `size` term counts distinct cited URLs, which favours
  themes that cite many links over themes that cite one outlet deeply.
- `_validate_briefing_urls` HEAD-probes any cited URL not collected in
  the last 48 hours, so re-running against an old scaffold is slower and
  may strip sentences.
