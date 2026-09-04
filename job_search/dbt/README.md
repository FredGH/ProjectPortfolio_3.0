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
