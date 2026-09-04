# Step 5 — dbt Project and Staging Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the dbt project (the first dbt work in this repo) and one
`stg_<source>__jobs` model per connector, each mapping that source's raw
JSONB payload into an identical column contract — so a source's quirks are
isolated in exactly one place. `int_jobs__unioned` unions them and
collapses to the latest version per `(source_name, source_job_id)`.

**Architecture:** Five staging models (one per connector currently writing
to `bronze.raw_jobs`), each a thin, contract-enforced `SELECT` extracting
fields from `payload` (JSONB) with `->`/`->>` — no joins, no aggregation.
`int_jobs__unioned` is the only model that combines them, via `UNION ALL`
plus a `ROW_NUMBER() OVER (PARTITION BY source_name, source_job_id ORDER
BY fetched_at DESC)` window to collapse bronze's intentional
version-history rows (a changed payload creates a new row, per
`bronze.py`'s own merge-key design) down to current state.

**Tech Stack:** dbt-core 1.11 + dbt-postgres, `dbt_utils` (for the
surrogate key, per `.claude/rules/sql-style.md`'s explicit rule against
hand-rolled `MD5(CONCAT(...))`), the same Postgres instance every other
part of this project uses.

**Spec:** `PLAN.md`'s "Step 5 — dbt project and staging models" section
and `plan/backlog.yml`'s `STEP-05` entry (`jira_key: JOB-85`).

## Scope note — five sources, not four; no per-user grain; salary stays raw

- `STEP-05`'s backlog subtasks were written before Jooble existed as a
  connector (JOB-440, added after JOB-68). Bronze already has real Jooble
  rows. This plan builds `stg_jooble__jobs` too — leaving a real source
  with real data unstaged would be a worse gap than the backlog text
  being slightly stale. `int_jobs__unioned`'s acceptance line ("rows from
  all four sources") is satisfied a fortiori by five.
- Every model here is SHARED-zone (job posting content, no `user_id`) —
  `dbt/README.md`'s Step 1a convention ("Shared marts build once, with no
  per-user grain at all") already settled this; nothing in this plan
  needs to relitigate it.
- Salary stays as raw text (`salary_raw`) in every staging model.
  `PLAN.md` explicitly scopes `parse_salary` — day-rate/annual
  disambiguation, currency conversion, normalised bands — to Step 6, a
  separate, later step. Building that logic here would be scope creep
  into a step whose own "testing rule" (write tests from real bronze
  rows, not invented examples) this plan hasn't done the work for.
- This branch is based on `main` as currently merged (through JOB-68/
  JOB-440, migration `0005`) — JOB-76's `collection_channel` column
  (migration `0006`) is still on an unmerged, separate PR. These staging
  models therefore do not reference `collection_channel`. If/when that PR
  merges, surfacing it here is a small, separate follow-up, not part of
  this plan.
- `title`/`company`/`location`/`description`/`salary_raw`/`posted_at` are
  in the contract but **not** `not_null`-tested. Manual entries can have
  every one of these genuinely null (best-effort LLM extraction, `core.
  ingestion.manual_connector`'s own documented behavior when extraction
  fails) — coercing that to a fake non-null value would be the exact
  `unknown`-as-a-value violation `PLAN.md` warns against in Step 5a.
  `source_name`/`source_job_id`/`job_url`/`job_url_canonical`/
  `entry_method`/`fetched_at`/`run_id`/`payload_sha256` — populated by
  `run_connector` for every row regardless of source — are the columns
  actually tested `not_null`, matching what "required column" can
  honestly mean given real data.

## Real payload shapes below are from this session's own live API probes

Every JSONB field path used in this plan's SQL was confirmed against real
responses captured earlier in this same session (Adzuna/Reed/Greenhouse/
Jooble's real API calls during Step 4/4a/4b's planning) or read directly
from `ManualConnector`'s source code (`core/ingestion/manual_connector.py`)
for the manual-entry payload shape — not reconstructed from dbt-adapter
memory or generic assumptions about what a "jobs API" looks like.

## Global Constraints

- SQL style per `.claude/rules/sql-style.md`: uppercase keywords/functions,
  lowercase snake_case identifiers, 4-space indent, trailing commas, CTEs
  preferred over nested subqueries, header comment on every model stating
  what it produces and its grain, `{{ ref(...) }}`/`{{ source(...) }}`
  never hardcoded schema.table, `{{ dbt_utils.generate_surrogate_key([...])
  }}` for surrogate keys.
- SQL testing per `.claude/rules/sql-testing.md`: every model gets a
  `schema.yml` entry; staging models get `unique` + `not_null` on PK only;
  source freshness defined in `sources.yml`.
- Do not touch anything under `packages/core/` or `apps/` — this plan is
  entirely new files under `job_search/dbt/`, plus one line in
  `requirements.txt`.
- `docker compose up -d postgres` must be running for every task's
  verification (`dbt debug`/`dbt run`/`dbt test` all need a live
  connection — dbt has no meaningful mock-database mode, so every task's
  own verification is inherently a live run, not a unit test).

---

### Task 1: dbt project scaffolding

**Files:**
- Create: `dbt/dbt_project.yml`
- Create: `dbt/profiles.yml`
- Create: `dbt/packages.yml`
- Create: `dbt/models/staging/_sources.yml`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `source('bronze', 'raw_jobs')` — consumed by every staging
  model in Tasks 2-3.

- [ ] **Step 1: Add the dbt dependency**

Add to `requirements.txt` (alongside the existing pinned versions):

```
dbt-postgres==1.8.1
```

- [ ] **Step 2: Write `dbt_project.yml`**

`dbt/dbt_project.yml`:

```yaml
name: 'job_search'
version: '1.0.0'
config-version: 2

profile: 'job_search'

model-paths: ["models"]
test-paths: ["tests"]
macro-paths: ["macros"]
seed-paths: ["seeds"]

target-path: "target"
clean-targets:
  - "target"
  - "dbt_packages"

models:
  job_search:
    staging:
      +materialized: view
      +schema: staging
    intermediate:
      +materialized: table
      +schema: intermediate
```

- [ ] **Step 3: Write `profiles.yml`**

`dbt/profiles.yml` — reads connection details from the same `.env`-managed
environment variables every other part of this project uses (via `{{
env_var(...) }}`), so it's safe to commit (no literal secrets) and never
drifts from `core.settings.Settings`'s own DSN construction:

```yaml
job_search:
  target: local
  outputs:
    local:
      type: postgres
      host: "{{ env_var('DBT_PG_HOST', 'localhost') }}"
      port: "{{ env_var('DBT_PG_PORT', '5432') | as_number }}"
      user: "{{ env_var('POSTGRES_USER') }}"
      pass: "{{ env_var('POSTGRES_PASSWORD') }}"
      dbname: "{{ env_var('POSTGRES_DB') }}"
      schema: public
      threads: 4

    gcp:
      type: postgres
      # Placeholder target, structurally present but not yet connectable —
      # no GCP Cloud SQL instance exists yet (PLAN.md's GCP deploy is a
      # later phase). Kept here now, per the plan's "two targets from the
      # start" requirement, so Step 5 doesn't have to be revisited to add
      # it — not tested live in this plan's verification.
      host: "{{ env_var('DBT_GCP_PG_HOST', '') }}"
      port: "{{ env_var('DBT_GCP_PG_PORT', '5432') | as_number }}"
      user: "{{ env_var('DBT_GCP_PG_USER', '') }}"
      pass: "{{ env_var('DBT_GCP_PG_PASSWORD', '') }}"
      dbname: "{{ env_var('DBT_GCP_PG_DB', '') }}"
      schema: public
      threads: 4
```

Note `DBT_PG_HOST` defaults to `"localhost"` — dbt runs from the host
(outside Docker) throughout this plan's verification, so it needs
`localhost:5432`, not the Docker-internal `postgres` hostname `.env`'s
`DATABASE_URL` uses. `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` are
already in `.env` (the migration/owner role — dbt needs schema-creation
rights, the same trust level Alembic already runs at, so the owner role is
correct here, not the RLS-scoped app role).

- [ ] **Step 4: Write `packages.yml` and install it**

`dbt/packages.yml`:

```yaml
packages:
  - package: dbt-labs/dbt_utils
    version: [">=1.1.0", "<2.0.0"]
```

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt deps
```

Expected: `dbt_utils` installs into `dbt/dbt_packages/` with no errors.

- [ ] **Step 5: Write the source definition with freshness**

`dbt/models/staging/_sources.yml`:

```yaml
version: 2

sources:
  - name: bronze
    schema: bronze
    tables:
      - name: raw_jobs
        description: >
          Append-only landing table for every connector's fetched job
          postings (PLAN.md Step 2/3/4). A changed payload for the same
          (source_name, source_job_id) creates a new version row rather
          than overwriting — int_jobs__unioned (Task 5) is where that
          collapses to current state.
        loaded_at_field: fetched_at
        freshness:
          warn_after: { count: 7, period: day }
          error_after: { count: 30, period: day }
        columns:
          - name: source_name
            description: "Which connector produced this row, e.g. 'adzuna'."
          - name: source_job_id
            description: "The source's own identifier for this posting."
          - name: job_url
            description: "The original (uncanonicalised) job URL."
          - name: job_url_canonical
            description: "The canonicalised job URL."
          - name: entry_method
            description: "'api', 'manual', or 'scraped'."
          - name: fetched_at
            description: "When this record was captured."
          - name: run_id
            description: "The ULID identifying the ingestion run."
          - name: payload
            description: "The full connector-specific JSONB payload."
          - name: payload_sha256
            description: "SHA-256 of the payload's dedup-relevant content."
```

`warn_after`/`error_after` are deliberately generous (7/30 days, not
hours) — this project has no scheduler yet (Step 4a's own final review
already documented this same boundary for discovery-channel cadence), so
"freshness" here means "has anyone run an ingest at all recently," not a
production SLA.

- [ ] **Step 6: Verify the connection**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt debug
```

Expected: `All checks passed!` — confirms the Postgres connection, the
`profile.yml`/`dbt_project.yml` are both valid, and dbt can reach
`bronze.raw_jobs`.

```bash
DBT_PROFILES_DIR=. python3.11 -m dbt source freshness
```

Expected: runs without error (a `WARN` on `bronze.raw_jobs` is fine and
expected if no ingest has run recently in this environment — that's the
freshness check doing its job, not a failure).

- [ ] **Step 7: Commit**

```bash
git add dbt/dbt_project.yml dbt/profiles.yml dbt/packages.yml \
  dbt/models/staging/_sources.yml requirements.txt
git commit -m "feat(job_search): scaffold the dbt project"
```

`.gitignore` already excludes `dbt/dbt_packages/`, `dbt/target/`, and
`dbt/logs/` (added during planning) — Step 4's `dbt deps` output and any
later `dbt run`/`dbt test` artifacts won't land in git. No action needed
here beyond the `git add` above.

---

### Task 2: `stg_adzuna__jobs` and `stg_reed__jobs`

**Files:**
- Create: `dbt/models/staging/stg_adzuna__jobs.sql`
- Create: `dbt/models/staging/stg_reed__jobs.sql`
- Modify: `dbt/models/staging/_staging.yml` (created here, extended by
  Task 3)

**Interfaces:**
- Consumes: `source('bronze', 'raw_jobs')` (Task 1).
- Produces: `ref('stg_adzuna__jobs')`, `ref('stg_reed__jobs')` — each
  returning the shared 14-column contract (`source_name, source_job_id,
  job_url, job_url_canonical, entry_method, title, company, location,
  description, salary_raw, posted_at, fetched_at, run_id,
  payload_sha256`) — consumed by Task 5's `int_jobs__unioned`.

Adzuna's real payload fields (live-verified): `id`, `title`, `description`,
`redirect_url`, `company.display_name`, `location.display_name`,
`salary_min`, `salary_max`, `created` (ISO-8601 with `Z` offset).

Reed's real payload fields (live-verified): `jobId`, `jobTitle`,
`employerName`, `locationName`, `jobDescription`, `jobUrl`,
`minimumSalary`, `maximumSalary`, `currency`, `date` (`"DD/MM/YYYY"`,
day-first — the same format `ReedConnector`'s `_parse_reed_date` already
parses this way).

- [ ] **Step 1: Write `stg_adzuna__jobs.sql`**

`dbt/models/staging/stg_adzuna__jobs.sql`:

```sql
-- stg_adzuna__jobs: one row per Adzuna posting, mapped to the shared
-- staging contract. Grain: source_job_id (unique within source_name).

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'title' AS title,
    payload -> 'company' ->> 'display_name' AS company,
    payload -> 'location' ->> 'display_name' AS location,
    payload ->> 'description' AS description,
    CASE
        WHEN payload ->> 'salary_min' IS NOT NULL
            OR payload ->> 'salary_max' IS NOT NULL
        THEN CONCAT_WS('-', payload ->> 'salary_min', payload ->> 'salary_max')
        ELSE NULL
    END AS salary_raw,
    NULLIF(payload ->> 'created', '')::timestamptz AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'adzuna'
```

- [ ] **Step 2: Write `stg_reed__jobs.sql`**

`dbt/models/staging/stg_reed__jobs.sql`:

```sql
-- stg_reed__jobs: one row per Reed posting, mapped to the shared staging
-- contract. Grain: source_job_id (unique within source_name).

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'jobTitle' AS title,
    payload ->> 'employerName' AS company,
    payload ->> 'locationName' AS location,
    payload ->> 'jobDescription' AS description,
    CASE
        WHEN payload ->> 'minimumSalary' IS NOT NULL
            OR payload ->> 'maximumSalary' IS NOT NULL
        THEN CONCAT_WS(
            ' ',
            CONCAT_WS(
                '-', payload ->> 'minimumSalary', payload ->> 'maximumSalary'
            ),
            payload ->> 'currency'
        )
        ELSE NULL
    END AS salary_raw,
    -- Reed's `date` is "DD/MM/YYYY" (day-first) — same format
    -- ReedConnector's own _parse_reed_date already assumes.
    NULLIF(payload ->> 'date', '')::timestamptz AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'reed'
```

Note the `posted_at` cast here relies on Postgres's `to_timestamp`-style
implicit parsing of `DD/MM/YYYY` via a plain `::timestamptz` cast, which
does NOT reliably assume day-first — **this is flagged for Step 3's
verification**, not assumed correct. If a live test row's `date` value
casts to the wrong month/day (e.g. `"03/09/2026"` becoming March 9 instead
of September 3), replace the cast with an explicit
`TO_TIMESTAMP(payload ->> 'date', 'DD/MM/YYYY')` call instead — do not
leave a silently-wrong date parse in a committed model.

- [ ] **Step 3: Write the schema.yml entries**

`dbt/models/staging/_staging.yml`:

```yaml
version: 2

models:
  - name: stg_adzuna__jobs
    description: "One row per Adzuna posting, mapped to the shared staging contract."
    columns:
      - name: source_job_id
        description: "Primary key within source_name."
        tests:
          - not_null
      - name: source_name
        tests:
          - not_null
      - name: job_url
        tests:
          - not_null
      - name: job_url_canonical
        tests:
          - not_null
      - name: entry_method
        tests:
          - not_null
      - name: fetched_at
        tests:
          - not_null
      - name: run_id
        tests:
          - not_null
      - name: payload_sha256
        tests:
          - not_null

  - name: stg_reed__jobs
    description: "One row per Reed posting, mapped to the shared staging contract."
    columns:
      - name: source_job_id
        description: "Primary key within source_name."
        tests:
          - not_null
      - name: source_name
        tests:
          - not_null
      - name: job_url
        tests:
          - not_null
      - name: job_url_canonical
        tests:
          - not_null
      - name: entry_method
        tests:
          - not_null
      - name: fetched_at
        tests:
          - not_null
      - name: run_id
        tests:
          - not_null
      - name: payload_sha256
        tests:
          - not_null
```

- [ ] **Step 4: Run and test both models live**

```bash
cd job_search
docker compose up -d postgres
cd dbt
DBT_PROFILES_DIR=. python3.11 -m dbt run --select stg_adzuna__jobs stg_reed__jobs
DBT_PROFILES_DIR=. python3.11 -m dbt test --select stg_adzuna__jobs stg_reed__jobs
```

Expected: both models build (`view` materialization — should be near
instant), all `not_null` tests PASS. If either model has zero rows
(no Adzuna/Reed data has ever landed in bronze in this environment), the
`not_null` tests trivially pass on an empty set — that's expected here,
not a bug; Task 6's full verification is where real-row coverage is
confirmed. Manually inspect a few real rows if any exist:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT source_job_id, title, company, posted_at FROM staging.stg_reed__jobs LIMIT 5;"
```

If `posted_at` values look wrong for any real Reed row (see Step 2's
note), fix the cast before moving on.

- [ ] **Step 5: Commit**

```bash
git add dbt/models/staging/stg_adzuna__jobs.sql \
  dbt/models/staging/stg_reed__jobs.sql \
  dbt/models/staging/_staging.yml
git commit -m "feat(job_search): add stg_adzuna__jobs and stg_reed__jobs"
```

---

### Task 3: `stg_greenhouse__jobs`, `stg_jooble__jobs`, `stg_manual__jobs`

**Files:**
- Create: `dbt/models/staging/stg_greenhouse__jobs.sql`
- Create: `dbt/models/staging/stg_jooble__jobs.sql`
- Create: `dbt/models/staging/stg_manual__jobs.sql`
- Modify: `dbt/models/staging/_staging.yml`

**Interfaces:**
- Consumes: `source('bronze', 'raw_jobs')` (Task 1).
- Produces: `ref('stg_greenhouse__jobs')`, `ref('stg_jooble__jobs')`,
  `ref('stg_manual__jobs')` — same 14-column contract as Task 2's two
  models — consumed by Task 5.

Greenhouse's real payload fields (live-verified): `title`, `company_name`,
`location.name`, `content` (HTML description), `absolute_url`,
`updated_at`, `first_published` (both ISO-8601 with explicit UTC offset,
e.g. `"2026-09-03T13:32:53-04:00"` — no day-first ambiguity, safe to cast
directly).

Jooble's real payload fields (live-verified): `title`, `company`,
`location`, `snippet` (truncated description), `salary` (free text,
sometimes empty string), `updated` (offset-less ISO-8601, e.g.
`"2026-08-05T07:54:35.6100000"` — `JoobleConnector`'s own
`_parse_jooble_updated` already treats this as needing a UTC assumption).

Manual's real payload shape (from `core/ingestion/manual_connector.py`'s
source, not an external API): `raw_text` (the full pasted posting),
`posted_date` (user-supplied, ISO date string or null), `notes`,
`overrides` (user-typed company/title/location), `parsed` (LLM-extracted
`ExtractedJobFields`, already merged with any `overrides` at ingestion
time — `title`/`company`/`location`/`contract`/`salary`/`seniority`, any
or all of which can be null if extraction failed), `field_source`.

- [ ] **Step 1: Write `stg_greenhouse__jobs.sql`**

`dbt/models/staging/stg_greenhouse__jobs.sql`:

```sql
-- stg_greenhouse__jobs: one row per Greenhouse posting, mapped to the
-- shared staging contract. Grain: source_job_id (unique within source_name).

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'title' AS title,
    payload ->> 'company_name' AS company,
    payload -> 'location' ->> 'name' AS location,
    payload ->> 'content' AS description,
    -- Greenhouse doesn't expose salary data anywhere in its board API.
    NULL AS salary_raw,
    COALESCE(
        NULLIF(payload ->> 'first_published', '')::timestamptz,
        NULLIF(payload ->> 'updated_at', '')::timestamptz
    ) AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'greenhouse'
```

- [ ] **Step 2: Write `stg_jooble__jobs.sql`**

`dbt/models/staging/stg_jooble__jobs.sql`:

```sql
-- stg_jooble__jobs: one row per Jooble posting, mapped to the shared
-- staging contract. Grain: source_job_id (unique within source_name).

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload ->> 'title' AS title,
    payload ->> 'company' AS company,
    payload ->> 'location' AS location,
    payload ->> 'snippet' AS description,
    NULLIF(payload ->> 'salary', '') AS salary_raw,
    NULLIF(payload ->> 'updated', '')::timestamptz AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE source_name = 'jooble'
```

- [ ] **Step 3: Write `stg_manual__jobs.sql`**

`dbt/models/staging/stg_manual__jobs.sql`:

```sql
-- stg_manual__jobs: one row per manually-pasted posting, mapped to the
-- shared staging contract. Grain: source_job_id (unique within
-- source_name). Unlike the four API-sourced models, title/company/
-- location here come from best-effort LLM extraction (already merged
-- with any user override at ingestion time) and can be genuinely NULL
-- when extraction failed — never coerced to a placeholder, per PLAN.md's
-- "never coerce unknown to a default" rule.

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload -> 'parsed' ->> 'title' AS title,
    payload -> 'parsed' ->> 'company' AS company,
    payload -> 'parsed' ->> 'location' AS location,
    payload ->> 'raw_text' AS description,
    payload -> 'parsed' ->> 'salary' AS salary_raw,
    NULLIF(payload ->> 'posted_date', '')::timestamptz AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE entry_method = 'manual'
```

Note the filter is on `entry_method = 'manual'`, NOT `source_name =
'manual'` — confirmed directly against `core/ingestion/manual_connector.
py`: `source_name` on a manual entry's `RawJob` is whatever free-text
label the user typed (e.g. `"linkedin_manual"`), while `entry_method`
is the fixed `"manual"` constant `apps/pipeline/app/cli.py`'s
`_cmd_ingest` passes to `run_connector` (`entry_method="manual" if
args.source == "manual" else "api"`). A `source_name = 'manual'` filter
would have silently matched zero rows against real data.

- [ ] **Step 4: Extend the schema.yml entries**

Append to `dbt/models/staging/_staging.yml`'s `models:` list, matching
Task 2's exact per-model pattern (description + the same 8 `not_null`
tests on `source_job_id`/`source_name`/`job_url`/`job_url_canonical`/
`entry_method`/`fetched_at`/`run_id`/`payload_sha256`) for
`stg_greenhouse__jobs`, `stg_jooble__jobs`, and `stg_manual__jobs`.

- [ ] **Step 5: Run and test all three models live**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt run --select stg_greenhouse__jobs stg_jooble__jobs stg_manual__jobs
DBT_PROFILES_DIR=. python3.11 -m dbt test --select stg_greenhouse__jobs stg_jooble__jobs stg_manual__jobs
```

Expected: all three build, all tests PASS. Inspect real rows for each,
same as Task 2 Step 4 — this environment has real Greenhouse/Jooble data
from Step 4/4a's live acceptance runs, so these two specifically should
have non-trivial row counts to actually check:

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT source_job_id, title, company, posted_at FROM staging.stg_greenhouse__jobs LIMIT 5;"
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT source_job_id, title, company, posted_at FROM staging.stg_jooble__jobs LIMIT 5;"
```

If `source_name = 'manual'`'s WHERE clause (Step 3's note) turns out
wrong, fix it and re-run before moving on — don't commit a staging model
that silently returns zero rows for a real source.

- [ ] **Step 6: Commit**

```bash
git add dbt/models/staging/stg_greenhouse__jobs.sql \
  dbt/models/staging/stg_jooble__jobs.sql \
  dbt/models/staging/stg_manual__jobs.sql \
  dbt/models/staging/_staging.yml
git commit -m "feat(job_search): add stg_greenhouse__jobs, stg_jooble__jobs, stg_manual__jobs"
```

---

### Task 4: Enforce the shared column contract

**Files:**
- Modify: `dbt/models/staging/_staging.yml`

**Interfaces:** None new — this task only adds `config: {contract:
{enforced: true}}` and explicit `data_type` declarations to the 5
existing model entries from Tasks 2-3.

dbt model contracts require every column dbt validates to have an
explicit `data_type` in `schema.yml`, and the model's actual `SELECT`
output must match that contract exactly (right columns, right order,
right types) or the build fails loudly — this is the enforcement
mechanism `PLAN.md` asks for ("A source that drifts should fail the
build, not silently produce nulls three models later").

- [ ] **Step 1: Add `contract: {enforced: true}` and column types to
  each of the 5 models**

For each of the 5 model entries in `dbt/models/staging/_staging.yml`, add
`config: {contract: {enforced: true}}` at the model level, and add
`data_type` to every column entry (including the 6 columns that don't
currently have a `tests:` block — `title`, `company`, `location`,
`description`, `salary_raw`, `posted_at` — contracts require every
selected column to be declared, not just the tested ones). Use this exact
type mapping for all 5 models (identical contract, per the plan's whole
point):

```yaml
      - name: source_name
        data_type: text
      - name: source_job_id
        data_type: text
      - name: job_url
        data_type: text
      - name: job_url_canonical
        data_type: text
      - name: entry_method
        data_type: text
      - name: title
        data_type: text
      - name: company
        data_type: text
      - name: location
        data_type: text
      - name: description
        data_type: text
      - name: salary_raw
        data_type: text
      - name: posted_at
        data_type: timestamptz
      - name: fetched_at
        data_type: timestamptz
      - name: run_id
        data_type: text
      - name: payload_sha256
        data_type: text
```

Merge this with each model's existing `tests:` entries (don't duplicate
the column list — add `data_type:` to the existing column blocks, and add
new column blocks for the 6 previously-untested columns).

Add `config: {contract: {enforced: true}}` as a top-level key on each of
the 5 model entries (a sibling of `description:`/`columns:`, not nested
inside `columns:`).

- [ ] **Step 2: Run all 5 models with contracts enforced**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt run --select stg_adzuna__jobs stg_reed__jobs stg_greenhouse__jobs stg_jooble__jobs stg_manual__jobs
```

Expected: all 5 build successfully. If any model's `SELECT` output
doesn't match its declared contract exactly (a type mismatch, a missing
column, wrong column order), dbt fails that model's build with a clear
contract-violation error — fix the model's `SELECT` (not the contract) to
match, since the contract is what Task 1-3 already decided the shared
shape should be.

- [ ] **Step 3: Run the quality gate**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt parse
```

Expected: `Success` — confirms `_staging.yml`'s YAML is valid and every
contract is internally consistent (no orphaned column declarations, no
duplicate model names).

- [ ] **Step 4: Commit**

```bash
git add dbt/models/staging/_staging.yml
git commit -m "feat(job_search): enforce the shared staging column contract"
```

---

### Task 5: `int_jobs__unioned`

**Files:**
- Create: `dbt/models/intermediate/int_jobs__unioned.sql`
- Create: `dbt/models/intermediate/_intermediate.yml`

**Interfaces:**
- Consumes: `ref('stg_adzuna__jobs')`, `ref('stg_reed__jobs')`,
  `ref('stg_greenhouse__jobs')`, `ref('stg_jooble__jobs')`,
  `ref('stg_manual__jobs')` (Tasks 2-3).
- Produces: `ref('int_jobs__unioned')` — one row per `(source_name,
  source_job_id)`, current version only.

- [ ] **Step 1: Write `int_jobs__unioned.sql`**

`dbt/models/intermediate/int_jobs__unioned.sql`:

```sql
-- int_jobs__unioned: one row per (source_name, source_job_id), current
-- version only — bronze is append-only with version rows on payload
-- change (bronze.py's own merge-key design), so this is where that
-- collapses to current state. Grain: job_key (source_name, source_job_id).

WITH unioned AS (

    SELECT * FROM {{ ref('stg_adzuna__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_reed__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_greenhouse__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_jooble__jobs') }}
    UNION ALL
    SELECT * FROM {{ ref('stg_manual__jobs') }}

),

-- Rank every version of the same posting by recency, so only the latest
-- fetch of a changed payload survives.
ranked AS (

    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY source_name, source_job_id
            ORDER BY fetched_at DESC
        ) AS version_rank
    FROM unioned

)

SELECT
    {{ dbt_utils.generate_surrogate_key(['source_name', 'source_job_id']) }}
        AS job_key,
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    title,
    company,
    location,
    description,
    salary_raw,
    posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM ranked
WHERE version_rank = 1
```

- [ ] **Step 2: Write the schema.yml entry**

`dbt/models/intermediate/_intermediate.yml`:

```yaml
version: 2

models:
  - name: int_jobs__unioned
    description: >
      One row per (source_name, source_job_id), current version only —
      unions every stg_<source>__jobs model and collapses bronze's
      version-history rows to the latest fetch. This is the model every
      downstream dedup/normalisation step (PLAN.md Step 6+) reads from,
      never the staging models directly.
    columns:
      - name: job_key
        description: "Surrogate key: hash of (source_name, source_job_id)."
        tests:
          - unique
          - not_null
      - name: source_name
        tests:
          - not_null
      - name: source_job_id
        tests:
          - not_null
      - name: job_url
        tests:
          - not_null
      - name: job_url_canonical
        tests:
          - not_null
      - name: entry_method
        tests:
          - not_null
          - accepted_values:
              values: ['api', 'manual', 'scraped']
      - name: fetched_at
        tests:
          - not_null
      - name: run_id
        tests:
          - not_null
      - name: payload_sha256
        tests:
          - not_null
```

- [ ] **Step 3: Run and test live**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt run --select int_jobs__unioned
DBT_PROFILES_DIR=. python3.11 -m dbt test --select int_jobs__unioned
```

Expected: model builds (`table` materialization — will take a moment
longer than the staging views, still fast at this data volume), all
tests PASS, including `unique` on `job_key` — if that test fails, it
means the same `(source_name, source_job_id)` pair produced more than one
`version_rank = 1` row, which would mean `fetched_at` has exact
duplicate timestamps for two real version rows of the same posting; if
that happens, add `payload_sha256` as a tiebreaker to the `ORDER BY` in
Step 1 rather than leaving a real non-unique key in a committed model.

- [ ] **Step 4: Commit**

```bash
git add dbt/models/intermediate/int_jobs__unioned.sql \
  dbt/models/intermediate/_intermediate.yml
git commit -m "feat(job_search): add int_jobs__unioned"
```

---

### Task 6: Full verification (controller-run, not dispatched)

- [ ] **Step 1: Confirm Postgres is up**

```bash
cd job_search
docker compose up -d postgres
docker compose exec -T postgres pg_isready
```

- [ ] **Step 2: Full `dbt build`**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt build
```

Expected: every model builds, every test passes, in one command — the
project's own definition of "green."

- [ ] **Step 3: Confirm the acceptance criterion directly**

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT source_name, count(*) FROM intermediate.int_jobs__unioned GROUP BY source_name ORDER BY source_name;"
```

Expected: a row for every source that has real bronze data in this
environment (at minimum `adzuna`, `greenhouse`, `jooble` — real live rows
exist from Step 4/4a's own acceptance runs; `reed`/`manual` if any were
ingested in this environment too), each with `count > 0`.

```bash
docker compose exec -T postgres psql -U job_search_owner -d job_search \
  -c "SELECT count(*) FROM intermediate.int_jobs__unioned WHERE source_name IS NULL OR source_job_id IS NULL OR job_url IS NULL OR job_url_canonical IS NULL OR entry_method IS NULL OR fetched_at IS NULL OR run_id IS NULL OR payload_sha256 IS NULL;"
```

Expected: `0` — confirms "no nulls in any required column" directly, not
just via `dbt test`'s own bookkeeping.

- [ ] **Step 4: Run the dbt-level quality checks one more time**

```bash
cd job_search/dbt
DBT_PROFILES_DIR=. python3.11 -m dbt source freshness
DBT_PROFILES_DIR=. python3.11 -m dbt test
```

Expected: both clean (freshness `WARN` is acceptable per Task 1 Step 6's
note; any `ERROR` is not).

- [ ] **Step 5: Confirm the Python-side quality gate and test suite are
  unaffected**

This plan touches no Python files. Confirm that's really true and nothing
regressed:

```bash
cd job_search
python3.11 -m black --check .
python3.11 -m isort --check-only .
python3.11 -m ruff check .
python3.11 -m mypy packages/core/core
python3.11 -m mypy apps/api/app
python3.11 -m mypy apps/pipeline/app
```

```bash
cd packages/core
DATABASE_URL="postgresql+psycopg://job_search_owner:change-me@localhost:5432/job_search" \
APP_DATABASE_URL="postgresql+psycopg://job_search_app:change-me-too@localhost:5432/job_search" \
LANDING_URI="file:///tmp/job_search_landing_verify" \
coverage run -m unittest discover
```

Expected: all clean, same pass count as before this plan started (this
plan adds zero Python files).

- [ ] **Step 6: Tear down**

```bash
cd job_search
docker compose down
```

---

## Self-Review Notes (completed during authoring, before Task 1 dispatch)

- **Spec coverage:** `STEP-05`'s 9 subtasks map cleanly: dbt project init
  with local+gcp targets → Task 1; 4 named staging models + the 5th
  (Jooble, added since the backlog was written) → Tasks 2-3; shared
  contract enforced via dbt contracts → Task 4; `int_jobs__unioned` with
  latest-version collapse → Task 5; not_null/accepted_values tests →
  folded into Tasks 2-5's own `schema.yml` entries rather than a separate
  task, since a model without its own tests isn't a complete deliverable;
  source freshness check → Task 1 Step 5.
- **Placeholder scan:** none found — every model's SQL is complete,
  grounded in this session's own live-verified payload shapes, not
  generic assumptions.
- **A genuine, flagged uncertainty, not smoothed over:** Reed's
  `"DD/MM/YYYY"` date cast (Task 2) and manual entry's `source_name`
  filter (Task 3) are both explicitly called out as "verify this against
  real data/code, don't trust this plan's prose" rather than asserted as
  definitely correct — both are places where a plan written without
  running the actual query against real rows could plausibly be wrong,
  and the task text says so rather than hiding it.
- **Type consistency:** the 14-column contract (Task 1's design, applied
  uniformly in Tasks 2-3, formalised in Task 4) was checked against the
  literal `SELECT` list in all 5 models before writing this plan — same
  column names, same order, same intended types throughout.
