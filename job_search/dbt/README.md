# dbt project

Transforms `bronze.raw_jobs` (raw JSONB payloads from the ingestion
connectors) into typed, contract-enforced models.

## Layers

- **staging** (`models/staging/`, views, schema `staging`) — one
  `stg_<source>__jobs` model per source (Adzuna, Reed, Greenhouse,
  Jooble, manual entries), each a thin JSONB extraction into the shared
  14-column contract. Not deduped: bronze is append-only, so a posting
  can appear as multiple version-rows here.
- **intermediate** (`models/intermediate/`, tables, schema
  `intermediate`) — `int_jobs__unioned` unions every staging model and
  collapses bronze's version history down to one current row per
  `(source_name, source_job_id)`. Downstream steps read from here, never
  from the staging models directly.
- **silver** (`models/silver/`, tables, schema `silver`) —
  `silver__job_posting` enriches `int_jobs__unioned` with engagement
  type, IR35 status and normalised rate figures. The enrichment itself
  (`core.enrichment.write_engagement_terms`) runs outside dbt, via the
  `enrich-engagement-terms` pipeline CLI subcommand, and lands in
  `silver.job_engagement_terms` — a plain dbt `source()`, not a model.
- **dedup** (`models/dedup/`, tables, schema `dedup`) —
  `dedup__exact_duplicates` and `dedup__candidate_pairs` find same-posting
  duplicates and the candidate pairs worth scoring; `dedup__similarity_scores`
  scores each of those pairs on every component signal. This layer depends on
  three tables written **outside dbt**, by pipeline CLI subcommands, and read
  as plain `source()`s: `dedup.job_blocking_keys`
  (`compute-blocking-keys`), `dedup.job_similarity_features`
  (`compute-similarity-features`) and `dedup.pair_title_scores`
  (`compute-title-similarity-scores`). The last of those is computed per
  *pair*, so it depends on `dedup__candidate_pairs` having been built first —
  which makes the run order load-bearing.

  After any new postings land, run (CLI steps from the `job_search/` root,
  dbt steps from this directory):

  ```bash
  python3.11 -m apps.pipeline.app.cli compute-blocking-keys
  dbt build --select dedup__exact_duplicates dedup__candidate_pairs
  python3.11 -m apps.pipeline.app.cli compute-similarity-features
  python3.11 -m apps.pipeline.app.cli compute-title-similarity-scores
  dbt build --select dedup__similarity_scores
  ```

  A plain `dbt build` on its own is **not** enough: it rebuilds
  `dedup__candidate_pairs` with the new pairs, but `dedup__similarity_scores`
  joins the out-of-band tables with `INNER JOIN`, so every pair without a
  matching row is silently dropped rather than erroring. The singular test
  `assert_similarity_scores_match_candidate_pairs_count` exists specifically
  to catch that.

## Running it

From this directory, with `.env` loaded and `DBT_PROFILES_DIR=.`:

```bash
dbt build
dbt source freshness
```

## Conventions

Fixed in Step 1a so later steps don't have to relitigate it:

- **Shared marts** (job postings, `dim_job`, `dim_company`, market marts,
  taxonomy) build once, with no per-user grain at all.
- **Per-user models** take `user_id` as part of the model's **grain** — a
  column selected and grouped on — never as a dbt `var`. A `var` is a
  build-time constant; a per-user model must return every user's rows in one
  build (Postgres RLS then scopes what each user's session can see), not be
  rebuilt once per user.
