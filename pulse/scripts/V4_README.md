# Pulse v4 — unified entries

`v4_runner.py` replaces the two-section layout (v1 "News Themes" +
v3.1 "Conversations") with one ranked list of entries. Each entry is
written from one pre-clustered set of items and integrates the news
reporting and the social reaction in a single summary. A dated news
anchor is optional; when one exists it is recorded as `trigger`.

The runner is additive. It imports from `v3_1_runner.py`,
`analysis/roundup_clustering.py`, `analysis/synthesize.py` and
`delivery/*` and modifies none of them. It writes its own rows
(`briefing_type = "daily_v4_unified"`) and never touches v1 or v3.1 rows.

## Stages

| # | Stage | Reused from | Notes |
|---|-------|-------------|-------|
| 1 | Scaffold | `v3_1_runner.load_v1_scaffold` | Latest v1 `daily` briefing. Everything is kept except `conversation_themes` and `conversation_roundups`. |
| 2 | Corpus, embed, HDBSCAN | `load_corpus_v3_1`, `embed_corpus`, `cluster_items` | Same calls and parameters as v3.1. Embeddings and the `item_id -> row` map are kept for later stages. |
| 2b | US-housing check, sub-cluster by shared story, same-author merge, coherence gate | `is_us_housing_relevant`, `subcluster_by_shared_story`, `merge_adjacent_clusters`, `coherence_check` | Identical to v3.1. |
| 3 | Rescue pass (new) | — | News items (`rss`, `google_news`, `substack`, `gmail`) with `relevance_score >= --rescue-relevance` (default 60) that ended in HDBSCAN noise or were dropped before the coherence gate. Near-duplicates are grouped transitively at embedding cosine >= 0.85; each group is a rescue cluster (size may be 1). Cap 8, highest relevance first. `origin = "rescue"`. Items in clusters that reached the gate and failed it are not rescued. |
| 4 | Write | `_format_cluster_for_sonnet`, `_format_items_for_sonnet`, `load_historical_pool`, `historical_context_for_cluster` | One Opus call per cluster (real and rescue), same model, token limit, streaming, prompt caching and past-6-day context as v3.1's `write_roundup_for_cluster`. System prompt is `V4_ENTRY_WRITER_PREFIX` + v1 `SYSTEM_PROMPT`. No v1-theme dedup list is passed. Every skip is logged with its reason and stored in `_v4_meta.skips`. |
| 5 | Programmatic dedup (new) | `embed_corpus` | Embed `title + summary` of each entry. Any pair with cosine >= `--dedup-threshold` (default 0.80) loses the smaller-cluster member (tie: fewer news sources; second tie: the later one). Both titles and the score are logged and stored in `_v4_meta.dedup_drops`. |
| 6 | Rank | — | Formula below. Keep `--max-entries` (default 18). |
| 7 | Assemble | — | `v4["entries"]` is canonical. `v4["conversation_themes"]` is the same list mapped to the shape `render_briefing_html` already renders; `v4["conversation_roundups"] = []`. |
| 8 | Post-process | `_strip_refusal_meta`, `_strip_forbidden_bridges`, `_enforce_paragraph_breaks`, `_autolink_bare_handles`, `_validate_briefing_urls`, `_compute_cited_sources` | Run on the v4 content only (a temporary dict holding the mapped themes), then cleaned summaries are copied back onto `entries`. An entry whose summary is emptied by URL validation is dropped and ranks are renumbered. `_reject_social_anchored_themes`, `_enforce_housing_focused_themes` and `_dedup_cross_theme_citations` are deliberately not run: the first two enforce the theme/roundup distinction that v4 retires (the topic filter would drop every entry because v4 sets `topics = []`); the third strips any sentence that re-cites a handle already cited in an earlier entry, which v3.1 also does not apply. |
| 9 | Store | — | `INSERT INTO briefings (briefing_type, content_json, created_at, email_sent, email_sent_at)` with `briefing_type = "daily_v4_unified"`. Skipped with `--no-store`. |
| 10 | Send | `_render_variants`, `_with_unsub_footer`, `_subscriber_subject`, `_post_resend`, `send_v3_email_to_subscribers` | `--to` sends one premium-variant email with the subject prefixed `[V4 SHADOW] `. Without `--to` and without `--no-send` it calls v3.1's subscriber send. |

## Ranking formula

```
score = cluster_size
      + 2 * n_distinct_news_sources
      + (2 if anchor_type == "news" else 1 if anchor_type == "mixed" else 0)
```

`n_distinct_news_sources` counts distinct outlet names among cluster
items whose `source` is in `{rss, google_news, substack, gmail}`. The
outlet name is `feed_name`; when that is empty (gmail and substack rows)
it falls back to the email sender's display name, then to the URL
domain's publication name (`synthesize._classify_url_only`).

Ties break on larger `cluster_size`, then more news sources, then lower
`cluster_id`. The list is sorted descending and cut to `--max-entries`.

`anchor_type` comes from the writer but is normalised: an entry claiming
`news` or `mixed` with a null `trigger` is downgraded to `social` (no
bonus without a stated dated event); `social` with a trigger becomes
`mixed`.

## Entry schema (`v4["entries"][i]`)

```
title          str   headline-style, 5-10 words
summary        str   markdown prose with inline [text](url) links
trigger        str | null   one sentence naming the dated news event, or null
anchor_type    "news" | "social" | "mixed"
item_ids       [int]  cluster item ids, including thread-merged ids
sources        {source: count}   e.g. {"rss": 2, "twitter": 5}
news_outlets   [str]  distinct outlet names among news items
n_news_sources int
cluster_id     int    HDBSCAN/sub-cluster id, or 900000+k for rescue clusters
cluster_size   int
origin         "cluster" | "rescue"
score          int
rank           int    1-based, after dedup, ranking and post-processing
```

The rendered `conversation_themes[i]` mirror is
`{theme, summary, related_news_trigger (trigger or ""), platforms,
heat_level: "medium", topics: [], _v4_rank, _v4_cluster_id}` with one
`{name, reply_count: 0, sentiment: "neutral", url}` platform badge per
distinct news outlet and per social platform present in the cluster.

`v4["_v4_meta"]` holds `source_v1_briefing_id`, stage `counts`,
`timings_seconds`, `skips` (with reasons), `dedup_drops` (with scores),
`coherence_gate_failures`, `merge_log`, `postprocess` stats (bridge
strips, paragraph breaks, autolinks, `url_audit`) and `cost`.

## Cost accounting

Every Anthropic call still goes through `analysis.anthropic_spend.record_usage`,
so the daily total in the email header is unchanged. In addition the
runner keeps an in-process tally for this run only (Anthropic by model,
priced with the same rate table; OpenAI embedding tokens observed from
the API response, priced at 2 cents per million) and stores it in
`_v4_meta.cost`. The historical-pool embedding inside
`load_historical_pool` cannot be observed without modifying v3.1, so its
tokens are estimated (chars/4) and reported separately as
`openai_embed_cents_estimated_unobserved`. `anthropic_spend_table_delta_cents`
is the before/after difference in the shared table, as a cross-check
(it includes anything else that ran concurrently).

## Running

```
source ~/.pulse_dev_env            # venv + ANTHROPIC/OPENAI keys + PULSE_DB (dev copy)
cd ~/work/HomeEconomics/pulse/scripts

python v4_runner.py --no-send --no-store                       # dry run, prints everything
python v4_runner.py --no-send --no-store --dump-json out.json  # also write the v4 dict
python v4_runner.py --to aziz@home-economics.us --no-store     # shadow email (needs RESEND_API_KEY)
python v4_runner.py                                            # subscriber send + store (not yet used)
```

Flags: `--db`, `--lookback-hours` (24), `--min-cluster-size` (3),
`--max-entries` (18), `--rescue-relevance` (60), `--dedup-threshold`
(0.80), `--to`, `--no-send`, `--no-store`, `--dump-json`.

## Rollback

- The runner is a new file; `v3_1_runner.py`, `synthesize.py`,
  `roundup_clustering.py` and the delivery modules are untouched.
- v4 rows are stored under `briefing_type = "daily_v4_unified"`. v1
  (`daily`) and v3.1 (`daily_v3_1_hybrid`) rows are never read for
  anything but the scaffold and never written.
- Cutover is intended to be a `PULSE_PIPELINE` repository variable
  read by `.github/workflows/pulse-synth.yml` (`v3_1` runs
  `v3_1_runner.py`, `v4` runs `v4_runner.py`). That variable is not
  wired yet; today the workflow still runs v3.1 unconditionally.
  Rolling back after cutover is flipping the variable.

## Known risks

- HDBSCAN with `min_cluster_size = 3` drops singletons and pairs to
  noise. A single high-relevance article with no echo would never be
  written. The rescue pass mitigates this for news sources at
  `relevance_score >= 60`, capped at 8 clusters per run; social
  singletons are still dropped by design.
- Cost: one Opus call per cluster written, including rescue clusters
  and entries later removed by dedup or the rank cap. v3.1 capped its
  writes at 15; v4 writes every coherent cluster plus up to 8 rescue
  clusters, so expect roughly one extra Opus call per extra entry. The
  per-run figure is in `_v4_meta.cost`.
- The programmatic dedup uses a fixed cosine threshold (0.80) on
  title+summary embeddings. Two genuinely distinct entries about the
  same subject (for example two different California housing bills)
  can score above it; check `_v4_meta.dedup_drops` when an expected
  entry is missing.
- The ranking formula is simple and favours large clusters with many
  news outlets. A single-item rescue entry with a news anchor scores
  1 + 2 + 2 = 5, which will usually sit below multi-source clusters.
- `_validate_briefing_urls` HEAD-probes any cited URL not collected in
  the last 48 hours, so re-running against an old scaffold is slower
  and may strip sentences.
