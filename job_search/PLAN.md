# Job Search Platform — Implementation Plan

**36 steps · 7 phases · 396 subtasks · 209 points**
**Plus 25 unscheduled improvements, ordered — see "Nice to have" at the end.**

**Multi-user, two trusted users, personal use.** The governing rule is the
two-zone split in Step 1a: job data is shared, anything derived from a person
is per-user. Statutory data-protection machinery is deliberately **out of
scope** under the household/personal-use assumption — see `DECISIONS.md` §7
for the reasoning and the trigger that would change it.

This document is the reasoning behind `plan/backlog.yml`. The YAML is what
Jira consumes; this is what you read when you've forgotten why a decision was
made. Keep them in the same commit.

**Stack:** Python · Postgres (pgvector) · dlt · dbt · Streamlit · FastAPI ·
Ollama (local embeddings) · Claude API (generation) · Docker · GCP Cloud Run ·
n8n (webhooks only) · Jira Free.

**The three steps that decide whether this works:** Step 6 (normalisation),
Step 9 (dedup calibration), Step 16 (scoring calibration). Each is a place
where doing it by feel produces something that looks like it works and
doesn't. Everything else is code you've written variants of before.

**The two steps with a deadline attached:** Step 5a (IR35 and rate basis) and
Step 4a (discovery corpus). Both capture data that cannot be added
retroactively — skip them and six months of collection is permanently missing
those fields. Step 12a (eval harness) is a soft third: technically
retrofittable, realistically never done if it isn't done first.

---

# Phase 0 — Skeleton and vertical slice

The goal is one job flowing end to end, not breadth of sources. A working
spine you can extend beats four connectors feeding nothing.

---

## Step 0 — Backlog and Jira sync · 3 pts

### Why this is first

You will drop this project for three weeks when client work gets busy. A
written backlog with acceptance criteria is what lets you pick it back up
without re-deriving the plan. There's also a second-order benefit worth
naming: you hold a CSM credential and you're job-hunting — a properly run
Jira project on your own build is an artefact you can walk an interviewer
through.

### Tool choice

Jira Free, not Monday.com. Monday's free plan caps at 200 items account-wide
across 3 boards, and subitems count toward it. This backlog is ~238 items
before you log a single bug. The GraphQL API is fine on Monday free; the item
ceiling is not. Jira Free gives 10 users, full REST API v3, and native
epic/story/subtask/bug hierarchy — which also means one tool covers both
project tracking and bug tracking rather than two.

GitHub Issues + Projects is the honest alternative if you want less friction:
issues link to commits automatically, `gh` CLI means no API client to write.
Jira wins only on the portfolio argument.

### The integration contract

This is the part that matters. One-way, one-shot generation — **not** sync.

| The script writes | The script never touches |
|---|---|
| summary, description | status |
| acceptance criteria | assignee |
| labels, story points | comments, worklog |
| issue links, subtasks | sprint assignment |

Disjoint field ownership is what removes the entire class of sync conflicts.
There is no reconciliation job, no webhook receiver, no state drift, because
the two systems never write to the same field. Bidirectional sync on a solo
project is where the project goes to die — you'd spend more time on
conflict resolution than on the dedup engine.

Idempotency: on first creation the script writes the returned key back into
the YAML as `jira_key: JOB-42`. Re-runs update in place. Never edit
`jira_key` by hand.

Bugs are raised directly in Jira, never generated from the plan file. Don't
auto-file bugs from application logs either — log to Postgres, review, raise
the ones that matter. Auto-filed bugs on a solo project become a backlog of
noise you eventually bulk-close.

### Done when

Running `python plan/sync_jira.py` twice creates the full backlog on the
first run and produces zero duplicates on the second.

---

## Step 1 — Repository and container scaffold · 5 pts

### Why containerise from day one

You want this running locally and on GCP. If it only ever runs on your host
Python, the GCP move becomes a rewrite rather than a deployment. Write the
Dockerfiles before any application code — the constraint shapes the code
correctly from the start.

### Layout

```
apps/
  api/        Dockerfile   FastAPI — all business logic
  ui/         Dockerfile   Streamlit
  pipeline/   Dockerfile   dlt + dbt + dedup (batch, no server)
dbt/
infra/        Terraform
plan/         backlog.yml, sync_jira.py
data/landing/ bind-mounted locally, GCS in cloud
```

Three images. The pipeline image is separate from the API image because in
GCP it runs as a **Cloud Run Job**, not a service — batch semantics, up to
24h timeout, no request lifecycle to fight. This split is what keeps the
cloud story simple later.

### Compose services

| Service | Image | Note |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | Named volume, pgvector preinstalled |
| `api` | `apps/api` | :8000, source mounted for hot reload |
| `ui` | `apps/ui` | :8501 |
| `pipeline` | `apps/pipeline` | `profiles: [cli]` — run on demand |
| `ollama` | `ollama/ollama` | Embeddings, cheap classification |
| `n8n` | `n8nio/n8n` | `profiles: [orchestration]`, own schema |

### The rule that makes GCP cheap

**Every environment difference is an environment variable, never a code path
or a separate image.** One `settings.py` via pydantic-settings:

```
ENV                  local | gcp
DATABASE_URL / DB_CONNECTION_MODE
LANDING_URI          file:///data/landing | gs://bucket/landing
EMBEDDING_PROVIDER   ollama | vertex
EMBEDDING_MODEL
LLM_PROVIDER         anthropic
ANTHROPIC_API_KEY
ADZUNA_APP_ID / ADZUNA_APP_KEY / REED_API_KEY / JOOBLE_KEY
API_BASE_URL
```

Use `fsspec` for the landing zone so `file://` and `gs://` are the same code.
Commit `.env.example` with every variable; `.env` stays ignored.

### The LLM gateway — build it before any LLM code exists

```python
llm.complete(task="skill_extraction", ...)
```

Model resolves **per task** from config. Not a single global `LLM_PROVIDER`
switch — that's precisely what forces an all-or-nothing provider migration
later. Per-task routing means the local/target boundary can move one task at
a time, which is the whole point.

Two adapters (`ollama`, `anthropic`), identical signature and response shape.
Log provider, model, `prompt_version`, tokens and cost on every call from day
one — that log is what nice-to-have #9 needs and it's free to add now.

See `DECISIONS.md` §1 for why this matters more than it looks.

### Done when

`docker compose up` yields a hello-world FastAPI on :8000, a Streamlit page
on :8501, and a psql-reachable Postgres with pgvector available.

---

## Step 1a — Tenancy model and row-level isolation · 8 pts

### Why this is Phase 0 and not later

Multi-user is not a feature you add; it is a property of every table you
create. Retrofitting `user_id` and isolation across thirty-odd tables, their
indexes, their dbt models and every query is a rewrite, not a migration. The
convention has to exist before the first table does.

### The two-zone rule — the whole design

| Zone | Contents | Grain |
|---|---|---|
| **Shared** | Job postings, dedup identity map, `dim_job`, `dim_company`, company intel, all market marts, taxonomy, emergent detection, question *text* | collected once, identical for everyone |
| **Per-user** | Truth base, scores, artefacts, applications, review progress, offers, preferences, alerts | scoped to `user_id` |

This split is what makes multi-user cheap rather than linear. Job collection,
dedup, skill extraction, categorisation and the entire market intelligence
layer are **collected once and amortised across every user** — the expensive,
high-volume work doesn't multiply. Only the genuinely personal work does.

Phases 0–2 are therefore almost unchanged by this decision. Phases 3–5 are
where tenancy bites.

### Isolation, in depth

- Every per-user table: `user_id NOT NULL`, FK to `app_user`, index leading
  on `user_id`
- **Postgres row-level security on every per-user table.** With two trusted
  users this is primarily a **correctness** guard, not a privacy one. The
  realistic failure isn't one person reading the other's CV — it's a query
  missing its `WHERE user_id` and silently blending two people's scores into
  one dashboard, or an `evidence_ref` from the wrong truth base landing in a
  tailored CV. Those are wrong-output bugs, and they're hard to spot
- Session user context set per request in FastAPI, from a **verified token
  only**. Never from a client-supplied field
- Separate DB roles: the app role runs under RLS, the migration role bypasses
  it. The application never connects as owner
- **A negative test per per-user table**: run a query with no `WHERE` clause
  as user A, assert zero rows belonging to user B

Shared tables are marked as such in the model layer, so nobody adds `user_id`
by reflex and quietly fragments the shared pool.

### Quotas, because users share finite resources

Two things are contended: **LLM spend** and **the Adzuna call allowance**. At
two users this is mild, but the guard is a few lines and prevents one heavy
application month from starving the other user's ingestion. Per-user caps on
monthly LLM spend, artefact generations and alerts, plus a fair-use split on
the shared API quota.

### Done when

A query run as user A cannot return user B's truth base, scores, artefacts or
applications even with the `WHERE` clause omitted, proven by test.

---

## Step 2 — Manual job entry, end to end · 8 pts

### Why the manual connector comes first

It has no API keys, no rate limits, and no external dependency. That isolates
the pipeline mechanics from network flakiness — when something breaks in
Step 4, you'll know it's the connector and not the loader, because the loader
already works.

It's also a real requirement in its own right: LinkedIn has no usable jobs
API, so anything you find by browsing gets pasted in by hand.

### The form

| Field | Required | Note |
|---|---|---|
| Source name | yes | Dropdown of prior values + free text. `linkedin_manual`, `otta`, `recruiter_email`. Autocomplete beats a fixed enum — you'll hit sources you didn't anticipate |
| Job URL | yes | Validated, canonicalised on submit |
| Job spec | yes | Textarea, full posting |
| Posted date | no | Defaults today; matters for the ±45-day dedup veto |
| Company / title / location | no | Overrides when the parser gets it wrong |
| Notes | no | "via recruiter X", "referral through Y" — travels with the job |

### Two rules that prevent data loss

1. **Raw pasted text is stored verbatim in `payload.raw_text` and never
   overwritten.** The parse is a derived field. If extraction is wrong, you
   re-run it from landing without asking the user to paste again.
2. **User overrides win over the parser**, flagged `field_source = 'user'`.

### URL canonicalisation

Lowercase host, strip `utm_*`, `ref`, `src`, `trk`, `aff`, session IDs,
trailing slash, resolve one redirect hop. Two sources pointing at the same
canonical URL is an instant dedup match and costs nothing.

`source_job_id`: extract from known URL patterns where possible — LinkedIn
canonicalises to `/jobs/view/{numeric_id}`, and that ID is stable and
reliable. Paste the same job twice and it dedupes on the ID alone. Fall back
to `sha256(canonical_url)` where no ID is extractable.

### The landing zone

```
landing/source=manual/dt=2026-08-23/run_id=01J.../part-0001.jsonl.gz
```

One JSONL line per record, wrapping the untouched payload with `_source_name`,
`_source_job_id`, `_job_url`, `_fetched_at`, `_run_id`, `_request_params`,
`_payload_sha256`.

Immutable and replayable. **This is why the extraction zone exists**: if your
parsing logic changes in six months, you rebuild bronze from landing without
re-hitting a single API. Worth the extra hop.

### Bronze

```sql
bronze.raw_jobs (
  _dlt_load_id, _dlt_id,
  source_name, source_job_id, job_url, job_url_canonical,
  entry_method,          -- api | manual | scraped
  fetched_at, run_id, request_params jsonb,
  payload jsonb, payload_sha256
)
```

Append-only. Unique on `(source_name, source_job_id, payload_sha256)` — a
re-fetch with unchanged payload is a no-op; a changed payload is a new
version row. **That gives you posting change history and `last_seen_at` for
free**, which Step 21a needs for the lifecycle metric.

`entry_method` matters more than it looks — see the selection-bias trap in
Step 21a.

### Done when

Pasting a real LinkedIn posting produces a bronze row with correct
`source_name`, `job_url_canonical`, `payload.raw_text`, `payload_sha256`.

---

## Step 3 — Connector contract and shared runner · 5 pts

### The contract

```python
fetch(query, since) -> Iterator[RawJob]

RawJob = {
  source_name, source_job_id, job_url, job_url_canonical,
  payload,            # raw dict, untouched
  fetched_at, run_id, request_params, payload_sha256
}
```

`source_name` and `job_url` are captured at extraction time, **before any
parsing**, so provenance survives even when the payload shape changes or
parsing fails.

### What the runner owns, not the connectors

Rate limiting (per-source token bucket from `sources.yml`), retry with
exponential backoff and jitter, run_id generation, landing writes via
`fsspec`, run metadata emission. Centralising these is the difference between
adding a connector in an hour and adding one in a day.

`sources.yml` drives everything:

```yaml
adzuna:
  enabled: true
  auth: {app_id: ${ADZUNA_APP_ID}, app_key: ${ADZUNA_APP_KEY}}
  calls_per_hour: 40
  concurrency: 2
  backoff: {base: 2, max_retries: 5}
  regions: [gb, ie, fr, de, us]
```

Retrofit manual entry to implement the protocol, so there's exactly one code
path into landing.

### Done when

Adding a connector requires one new file and one `sources.yml` block — no
changes to the runner.

---

## Step 4 — First three API connectors · 8 pts

### Why these three

Deliberately different shapes: keyed REST with pagination (Adzuna), keyed
REST with different pagination (Reed), keyless per-company JSON
(Greenhouse). If the contract survives all three, it'll survive the rest.

### The full source roster (for later)

| Source | Auth | Coverage | Notes |
|---|---|---|---|
| **Adzuna** | app_id + key | UK/EU/US, 19+ countries | Primary. 1,000 free calls/month. Also exposes salary histograms, historical salary, vacancies by region — feeds Step 21a directly |
| **Reed.co.uk** | free key | UK | Deepest UK coverage |
| **Jooble** | free key | 60+ countries | EU breadth, POST JSON |
| **Greenhouse / Lever / Ashby** | none | Global | Free, per-company JSON. Highest signal — direct from employer, no aggregator lag |
| **Workable / SmartRecruiters** | none | Global | Same pattern, verify current endpoints |
| **Arbeitnow / Remotive / RemoteOK** | none | EU + remote | Free single-board feeds |
| **The Muse / Findwork** | free key | Mixed | Verify tier |
| **USAJOBS** | free key | US federal only | Narrow but clean |
| **EURES** | portal/feed | EU | Official EU mobility portal |
| **JobSpy** | none | LinkedIn/Indeed/Glassdoor | Actively maintained, but LinkedIn rate-limits around page 10 per IP — proxies mandatory. Keep pluggable, low volume, personal use |

### On LinkedIn specifically

There is no LinkedIn jobs search API. Their only jobs API is partner-gated
for *publishing* jobs and closed to new partners. The unofficial Voyager API
gets accounts banned within days. Every "LinkedIn Jobs API" on RapidAPI or
Apify is a reseller of scraped data. Plan aggregator-first; treat scraping as
an optional supplement you accept the maintenance burden for.

### Target company registry

The ATS sources need a table of companies with their board slug:

```sql
target_company (name, ats_provider, board_slug, active, added_at)
```

Seed 20 by hand. Step 21a's `fct_employer_activity` will auto-suggest more
based on who actually appears in your results — so it becomes
self-populating.

### Important for Step 21a

**Pull Adzuna's historical salary endpoint now**, and store it as a separate
reference fact. Your own data starts at zero, so it's the only way to have a
meaningful salary trend in month one.

### Done when

`docker compose run --rm pipeline ingest --source adzuna --query "data engineer" --region gb`
lands files and loads bronze, for all three sources.

---

## Step 4a — Discovery corpus · 5 pts

### The structural problem this solves

Step 21a freezes the keyword × region matrix, for good reasons — an unstable
query set makes every trend chart a lie. But a frozen keyword matrix has a
consequence nobody notices until they try to do something like emergent-role
detection:

**You cannot discover a job title you did not think to search for.**

If your matrix contains "data engineer", "data scientist", "ML engineer" and
"software engineer", then Forward Deployed Engineer, Context Engineer, AI
Solutions Architect, Evaluation Engineer and every other role the market
invents next year are invisible to you. Not under-represented — structurally
absent. The targeted corpus can only ever confirm what you already suspected.

So Step 21b needs a corpus the targeted channel cannot provide.

### The design

A second collection channel, `collection_channel = 'discovery'`, deliberately
wide and shallow:

- **Full ATS board dumps.** This is the richest source by a wide margin.
  Greenhouse, Lever and Ashby endpoints return the *entire* board for a
  company — every open role, whatever it's called. For your target-company
  registry, that's the complete title distribution at employers you care
  about, free, with no keyword filter in the way. If a company has invented a
  new function, it is on their board under its real name.
- **Category-level sweeps.** Adzuna's category endpoints ("IT Jobs",
  "Engineering Jobs") rather than keyword queries.
- **Seniority-agnostic broad queries** across your regions.

Run weekly rather than daily — this is about breadth and history, not
freshness, and it has to stay inside free tiers.

### The separation rule

`collection_channel` sits alongside `entry_method` and does the same job for
a different axis. Discovery records are **excluded from targeted-channel
trend metrics by default**. Two corpora, two purposes:

| | Targeted | Discovery |
|---|---|---|
| Query set | frozen, versioned | broad, evolving |
| Cadence | daily | weekly |
| Used for | volume, salary, skill demand trends | emergent role detection |
| Bias | known and controlled | wide but uneven |

Mixing them would put the exact discontinuity problem Step 21a guards
against straight back into your volume charts.

### The registry becomes strategic

Step 4 seeds `target_company` with 20 entries mainly to test the connector.
Here it becomes a genuine asset — every company added is a full board's worth
of title vocabulary. Grow it aggressively. `fct_employer_activity` from
Step 21a tells you which employers actually matter in your market, and those
are the boards worth dumping.

### Done when

Discovery runs return titles that appear nowhere in the targeted keyword
matrix, and every record carries `collection_channel`.


# Phase 1 — Silver

Conform every source into a single column contract, and build the
normalisation functions that dedup depends on.

---

## Step 5 — dbt project and staging models · 5 pts

One `stg_<source>__jobs` model per connector, each mapping that source's JSON
into an **identical column contract**. All source quirks are isolated here
and nowhere else — this is the layer that means adding a source never touches
downstream models.

Enforce it with dbt model contracts in `schema.yml`, not convention. A source
that drifts should fail the build, not silently produce nulls three models
later.

`int_jobs__unioned` unions the staging models and takes the latest version
per `(source_name, source_job_id)` — bronze is append-only with version rows,
so this is where you collapse to current state.

Two dbt targets from the start: `local` and `gcp`. Same models, same tests.

### Done when

`int_jobs__unioned` returns rows from all four sources with no nulls in any
required column, and `dbt test` passes.

---

## Step 5a — IR35, contract terms and day-rate modelling · 5 pts

### Why this cannot wait

Two of the improvements in this plan are **not retrofittable**, and this is
one of them. Engagement terms have to be in the staging contract from the
start, because six months of collected postings that never captured IR35
status cannot have it added later — the source text is in bronze, but you'd
be reprocessing everything, and for aggregator sources the description was
truncated anyway.

For UK contract work this is also the single most decisive filter. An outside-IR35
day rate and an inside-IR35 day rate are not comparable numbers, and a
system that treats them as equivalent will rank badly and advise worse.

### The fields

| Field | Values |
|---|---|
| `engagement_type` | permanent / contract / ftc / interim / unknown |
| `ir35_status` | inside / outside / not_applicable / undetermined / unknown |
| `engagement_vehicle` | umbrella / limited / paye / agency_paye / unknown |
| `rate_basis` | annual / daily / hourly |
| `contract_length_months` | where stated |

Extraction is phrase rules first (job specs use fairly consistent language:
"outside IR35", "inside scope", "umbrella only"), LLM for the residual.

### The rule that matters

**Never coerce `unknown` to a default.** `unknown` is a value. Defaulting
undetermined IR35 status to "outside" because that's what you want will
produce a system that confidently recommends roles you'd take a large net pay
cut on. Defaulting to "inside" hides the good ones. Carry the uncertainty
through to the UI and let yourself decide.

### The comparability fix

This is where the salary marts in Step 21a are quietly rescued. Comparing
£600/day against £75,000 as if they were the same number wrecks every
percentile, every median and every skill premium. Normalise to **both** an
annualised figure and a day-rate equivalent, with the assumed working-days
constant documented and visible — because the assumption is contestable and
you'll want to change it.

### Done when

Every `silver__job_posting` row carries `engagement_type`, `ir35_status` and
a rate normalised to a comparable basis, with explicit `unknown` rather than
null-as-guess.

---

## Step 6 — Normalisation functions · 5 pts · **critical**

### Why this is one of the three risky steps

Unglamorous and decisive. Every dedup signal in Steps 7–9 is computed over
these outputs. If `strip_title` leaves req IDs in, your blocking key
fragments and identical jobs never get compared. If `normalise_company`
doesn't handle `Ltd` vs `Limited`, you get duplicate employers in
`fct_employer_activity`. Nothing downstream can recover from a bad
normaliser.

### The functions

- **`normalise_company`** — strip `Ltd`, `Limited`, `Inc`, `GmbH`, `PLC`,
  `SA`, punctuation, casing. Backed by an **alias table** for the cases
  normalisation can't reach: Meta/Facebook, Google/Alphabet, and the dozens
  of recruitment agencies trading under multiple names.
- **`strip_title`** — remove seniority prefixes, `(m/f/d)`, req IDs,
  `- Remote`, location suffixes, `| Company Name`. **For matching only.**
- **`title_raw`** — the source title, verbatim, always preserved.
- **`title_for_display`** — strips decoration only (req IDs, `(m/f/d)`,
  `| Company`, `- Remote`, location suffixes) and **keeps seniority and
  qualifiers**. This is the string that goes on generated documents.

Three fields, three purposes. The trap is using the matching title for
display: `strip_title` deliberately removes "Senior", so a CV headline built
from it would say "Data Engineer" when the posting said "Senior Data Engineer
(Remote)". Same normaliser, wrong output — see `DECISIONS.md` §5.
- **`normalise_location`** — to ISO country + NUTS region. Needed for the
  regional grain in every mart.
- **`canonicalise_url`** — already built in Step 2, formalised here.
- **`parse_salary`** — to annualised GBP band, with currency conversion and
  day-rate vs annual disambiguation. Day rates are the norm for UK contract
  work and comparing £600/day against £75,000 as if they're the same number
  will wreck your salary marts.

### The testing rule

**Write the tests from actual rows in your own bronze data, never invented
examples.** Invented examples test the cases you already thought of. Real
data contains the ones you didn't — that's the entire value of doing this
after Step 4 rather than before.

### Done when

Pytest suite covers ~40 real examples pulled from bronze and passes.

---

# Phase 2 — Deduplication engine

The phase that determines whether every downstream count is correct. **Do not
defer it.** Every market metric, every skill frequency, every "how many data
engineer roles in London" number is wrong if the same job appears three
times. Also includes the first GCP deploy, placed here deliberately.

---

## Step 7 — Exact match and blocking · 5 pts

### Cheap matches first

- `job_url_canonical` equality
- `content_sha256` = hash of
  `normalise(company) || normalise(title) || normalise(location) || left(normalise(description), 1000)`

These cost nothing and catch a meaningful share.

### Blocking

Comparing every pair is O(n²) and won't survive 50,000 postings. Block key:

```
normalise_company || left(strip_title, 12) || country
```

Only candidates sharing a block get compared. Add a **soft block on company
alone** for cases where titles diverge badly ("Senior Data Engineer" vs
"Data Platform Engineer" at the same company, same posting).

**Measure block size distribution.** If any block exceeds a few hundred
records, your key is too coarse and you've reintroduced the quadratic
problem in miniature. Large employers with many similar roles are where this
shows up.

### Done when

Candidate pair generation across full bronze completes in seconds, and no
block exceeds a few hundred records.

---

## Step 8 — Similarity scoring · 8 pts

Weighted multi-signal score across candidate pairs within a block.

| Signal | Method | Weight |
|---|---|---|
| Company name | `pg_trgm` similarity + alias table | high |
| Title | token-set ratio | high |
| Description | SimHash 64-bit, Hamming ≤ 3 | high |
| Description embedding | `pgvector` cosine ≥ 0.95 | medium |
| Location | after geo-normalisation to NUTS/ISO | medium |
| Posted date | within ±14 days | low — but **hard veto beyond ±45** |
| Salary band | overlap | low |

The date veto is worth calling out: employers repost the same role months
apart, and those are genuinely different opportunities with different
outcomes. Merging them loses the second one.

### Persist every component, not just the blend

You will retune the weights in Step 9. If only the blend is stored, retuning
means recomputing everything from scratch. Store the components and retuning
is a SQL query.

### Done when

Scores computed and persisted for all candidate pairs, with each component
signal stored separately.

---

## Step 9 — Calibration and review queue · 5 pts · **critical**

### Why precision matters more than recall here

The two failure modes are not symmetric:

- **False split** (missed match): the same job appears twice in your list.
  Mildly annoying. You notice and move on.
- **False merge**: two genuinely different jobs collapse into one, and one of
  them **silently disappears from your pipeline forever**. You never know it
  existed.

So bias hard toward precision. Aim above 0.95 at the auto-match threshold.

### The method

1. Hand-label 50 candidate pairs as match / not-match. Sample across the
   score range, not just the obvious ends.
2. Plot precision-recall across the threshold range.
3. Set auto-match and auto-reject cutoffs **from the curve**, not from
   intuition. Intuition will pick 0.8 because it feels right; the curve will
   tell you the knee is at 0.87.
4. Everything between the cutoffs goes to a **review queue** in Streamlit.
   You clear it in a few minutes a week, and each decision is training data
   for the next recalibration.

Record the measured precision figure in the repo. Future-you will want to
know whether 0.95 was achieved or aspired to.

### Done when

Measured precision at the chosen auto-match threshold exceeds 0.95, and the
review queue is usable.

---

## Step 10 — Clustering, identity map and survivorship · 8 pts

### Why this is Python, not dbt

Union-find over pairwise matches produces transitive groups (A~B, B~C ⇒ one
cluster). dbt doesn't do connected-component clustering gracefully, and dbt
Python models don't run on Postgres. Orchestrate as:

```
dbt run --select staging  →  dedup.py  →  dbt run --select silver+
```

### The stability guarantee

```sql
silver.job_identity_map (
  source_name, source_job_id, job_group_id,
  match_method, confidence, matched_at, is_manual_override
)
```

Two non-negotiables:

- **Incremental matching.** New postings are matched against existing cluster
  *representatives*, not by re-clustering the whole table. Full re-clustering
  reshuffles group membership as data arrives.
- **`job_group_id` never changes once assigned.** Every artefact, every
  application record, every note you've written references it. A shifting key
  orphans all of it.

Manual overrides set `is_manual_override` and are never recomputed.

### Survivorship — field-level, not record-level

Source rank: direct ATS (Greenhouse/Lever/Ashby) > Reed/Adzuna > other
aggregator > scraped/manual.

But resolve `description` and `apply_url` by **separate rules**:

- `description` → longest non-empty wins. Manual entries usually have the
  fullest text because aggregators truncate.
- `apply_url` → source rank wins. You want to apply via the Greenhouse link,
  not the LinkedIn one, even when LinkedIn had the better description.

- `title_for_display` → survives from **the same source as `apply_url`**.
  Boards phrase the same role differently, and you must mirror the posting
  you're actually applying to, not whichever source had the longest
  description.

Picking one winning record wholesale gets this wrong in both directions.
Keep every source URL in `dim_job.sources[]` regardless.

### Done when

Running the full pipeline twice produces byte-identical `job_group_id`
assignments. Write this as a regression test.

---

## Step 11 — Gold layer · 5 pts

- **`dim_job`** — one row per deduplicated job. `job_group_id` PK, surviving
  record's fields, `sources[]` as an array of
  `{source_name, job_url, first_seen_at}` so the UI can show "also on Reed,
  Adzuna".
- **`dim_company`**
- **`fct_market_demand`** — the base demand fact, filtered to
  `entry_method = 'api'`

### The selection-bias filter

Manual entries are jobs *you chose to paste*. If they flow into demand
metrics, your chart measures your own browsing habits, not the market.
Filter them out of anything market-facing; let them flow freely into scoring
and the application pipeline, where the bias is harmless and actually
desirable.

Document *why* in the model, not just the `where` clause — this is exactly
the kind of filter that gets "fixed" by a future you who's forgotten.

### Done when

`dim_job` has a unique `job_group_id`, and `fct_market_demand` excludes
manual entries.

---

## Step 11a — Job categorisation and seniority banding · 3 pts

### Why this is its own step

Categories are the grain of `fct_market_volume`, `fct_skill_demand`, the
salary marts, the skill gap analysis, and the Q&A bank. Until now they've
been assumed everywhere and specified nowhere. Small step, load-bearing.

### Hybrid classifier — cheap first

1. **Rules on normalised title** — catches ~70% deterministically and for
   free. "Senior Data Engineer" is not ambiguous.
2. **Embedding nearest-centroid** — build centroids from labelled examples,
   assign the remainder by cosine distance.
3. **LLM** — residual only, with a confidence score returned.

Running the LLM over everything would work and would also cost real money for
no accuracy gain on the 70% that rules handle perfectly.

### Two taxonomies, deliberately

**Classification taxonomy (7)** — the analytical grain for every mart, chart
and gap analysis: software engineer, data engineer, data scientist, AI/ML
engineer, analytics engineer, platform/DevOps, `other`.

**`qa_category` (4 active)** — the grain for the interview question bank:
software engineer, data engineer, data scientist, AI engineer.

Mapped:

| Classification | → `qa_category` |
|---|---|
| analytics engineer | data engineer |
| platform/DevOps | software engineer |
| AI/ML engineer | AI engineer |

The rationale differs by taxonomy. Market analysis wants **fine grain** —
analytics engineer and data engineer have genuinely different salary curves
and skill signatures, and collapsing them loses real signal. The question
bank wants **coarse grain**, because generating and reviewing 30 questions
per topic is expensive and the behavioural half transfers completely between
adjacent categories while the technical half overlaps substantially.

**The mapping lives in `category_map.yml`, not in code.** That's the whole
point — it has to be extendable without a code change, because Step 21b will
propose extending it.

`qa_category` is persisted on `dim_job` so the question-bank lookup is a
join, not a lookup table embedded in the application.

Plus `seniority_band` — junior / mid / senior / lead / principal — from the
same pass.

Persist `category`, `category_confidence`, `category_method`. Route
low-confidence classifications to a review list rather than guessing; a
misfiled job is a wrong number in six charts.

### Done when

Hand-checking 100 classifications yields >90% agreement.

---

## Step 12 — First GCP deployment · 8 pts

### Why here and not at the end

Prove the path while there's little to break. Deploying a five-model dbt
project and one connector surfaces the same IAM, networking and secret
problems as deploying the finished system — with a fraction of the debugging
surface. Deploy once, confirm, **then go back to working locally.**

### Target architecture

| Component | Service | Why |
|---|---|---|
| API | Cloud Run service | Scale to zero, min-instances 0 |
| Streamlit | Cloud Run service, behind IAP | This is your CV data — don't put it on the open internet |
| Pipeline | **Cloud Run Jobs** | Right primitive for batch, 24h timeout, no request lifecycle |
| Schedule | Cloud Scheduler → Cloud Run Jobs | Step 22 |
| Landing | GCS bucket | Identical path convention to local |
| Database | Neon (free, pgvector) → Cloud SQL if outgrown | See cost note |
| Secrets | Secret Manager, mounted as env vars | |
| Images | Artifact Registry, `europe-west2` | |

Cloud SQL connection via `cloud-sql-python-connector` with IAM auth rather
than the sidecar proxy — Cloud Run has native integration and no password
lands in the DSN. Locally the same code falls back to a plain DSN based on
`DB_CONNECTION_MODE`.

### Three things that will bite you

**1. Ollama doesn't come to GCP.** Running it on Cloud Run needs GPU
allocation and a warm instance — expensive and pointless at personal volume.
Worse: **different embedding models produce incompatible vectors.** Embed
locally and query in cloud and your similarity scores are garbage.

Recommendation: **embed locally with Ollama, always.** The GCP pipeline reads
precomputed vectors rather than generating them. Slightly awkward, but it
keeps CV embeddings under your control and sidesteps the mismatch entirely.
Store `embedding_model` alongside every vector regardless, so a future switch
is detectable rather than silent.

Generation (tailoring, letters, Q&A) is Claude API in both environments — no
drift there.

**2. n8n and Cloud Run don't mix.** n8n's cron triggers need a persistently
running process; Cloud Run scaling to zero kills them silently. Resolved in
Step 23 by dropping n8n's scheduler entirely.

**3. Cloud SQL is your only real cost.** Smallest sensible instance runs
roughly £20–30/month, always on, even idle. Everything else scales to zero.
Start on **Neon's free tier** — real Postgres, pgvector, scales to zero, dbt
and dlt don't care. Move to Cloud SQL only if you outgrow it.

Service accounts: one per workload, least privilege. The pipeline job needs
`storage.objectAdmin` on the landing bucket and `cloudsql.client`. The UI
needs `cloudsql.client` and nothing else. Don't share.

### Done when

The same git commit runs locally and in GCP with only env vars differing, and
an ingestion run in GCP lands rows in the managed database.

---

# Phase 3 — CV truth base and relevance scoring

---

## Step 12a — LLM eval harness and prompt versioning · 8 pts · **critical**

### The largest robustness gap in the original plan

Every dbt model in this project has tests. Every LLM component has none.
That asymmetry is indefensible — you'd never ship a transformation without a
test, yet the CV extraction, the scoring rationale, the tailoring and the
fabrication guard are all currently unverifiable. Each prompt edit is a blind
change to a system with no regression signal.

**Build this before Step 13, not after.** If it comes after, you have five
LLM components with no evals and you'll never retrofit them. If it comes
first, every subsequent LLM step ships with evals as a matter of course,
because the harness already exists and adding a case is trivial.

### The golden set

Hand-curated `(input, expected output)` cases per component:

| Component | What's checked |
|---|---|
| CV extraction | Field-level F1 against a hand-corrected truth base |
| JD skill extraction | Precision/recall against hand-labelled specs |
| Categorisation | Exact match against your Step 11a hand-checks |
| Scoring rationale | LLM-as-judge with a rubric |
| Tailoring | Fabrication guard must fire on planted exaggerations |
| Cover letter | Rubric: evidence-led, no invented claims, correct company |
| Q&A generation | Technical correctness via judge, grounding via evidence_ref |

Exact match works for extraction and categorisation. For generation it's the
wrong instrument — use LLM-as-judge against an explicit rubric, and accept
that it's noisier.

For the tailoring evals specifically, use **RAGAS's faithfulness metric**:
decompose the generated output into atomic claims and verify each against the
truth base. It's a more principled form of the same check the Step 17 critic
performs, and it exists as a library. Don't reinvent it.

### Prompt versioning — keyed on task AND model family

**Prompts are code.** Versioned files in the repo, never inline in Python.

Critically, the registry is keyed on `(task, model_family)`:

```
prompts/skill_extraction/claude.v3.md
prompts/skill_extraction/local.v7.md
```

**Never convert a prompt between families.** A prompt tuned for a 20B local
model — verbose scaffolding, explicit decomposition, heavy few-shot — often
*degrades* output on a stronger model by constraining it into the weaker
model's shape. Write the target variant; keep both.

### Provider-aware evals

The eval runner takes a provider argument, so the same golden set runs
against local and target. Two consequences:

- **Definition of done for every LLM step is "passes evals on the target
  provider"**, even though daily iteration runs local. You never accumulate
  untested distance from production.
- Calibration values — thresholds, weights, confidence cutoffs — are recorded
  **per provider**, not once globally. They don't transfer.

A full target-provider eval run costs roughly $1. Weekly in CI is cheap
insurance against discovering the gap at the worst possible moment.
Stamp `prompt_version` and `model_id` on every generated artefact row, so
when you notice cover letters got worse three weeks ago you can trace it to a
change rather than to vibes. Fix temperature and seed where supported.

### The habit that makes it pay off

**Every bug found in an LLM component becomes an eval case.** The golden set
grows from real failures rather than imagined ones, which is the same
principle as writing Step 6's fixtures from real bronze data. Within a few
months the suite encodes everything that has actually gone wrong.

Fail the run on regression beyond a per-component threshold. Document the
minimum golden-set size below which results aren't meaningful — a suite of
five cases will pass everything.

### Done when

Changing any prompt runs the golden set and reports pass/fail per case, and
every generated artefact carries the `prompt_version` that produced it.

---

## Step 13 — CV truth base · 5 pts

### Why not a resume parser

Skip spaCy/pyresparser-style parsers. LLM structured extraction against a
Pydantic schema is far more robust to layout, and layout is the whole
problem.

### The schema

```
identity, headline, locations, work_auth
skills[]      -> name, canonical_id (ESCO), years, last_used, evidence_refs
experience[]  -> company, title, start, end, bullets[], tech[], metrics[]
education[], certifications[], publications[]
```

**Every bullet gets a stable ID.** This is what Step 17's fabrication guard
checks against — without stable IDs there's no way to verify a generated
bullet traces to something real.

### Extraction tooling matters more than the model here

Use **Docling** rather than naive PDF text extraction. CVs are multi-column,
table-heavy and layout-dependent — exactly the documents that plain text
extraction scrambles. A CV mangled at this stage produces a corrupted truth
base, and since every generated artefact draws from it, the corruption
propagates silently into every tailored CV you send.

Store the extracted markdown alongside the parsed JSON. Re-parsing then costs
an LLM call rather than a re-extraction, and you can diff extractors when
confidence is low.

### One base CV per user — enforced by the database

`cv_truth_base` carries `user_id` with a **UNIQUE constraint on it**. Exactly
one base CV per user, guaranteed by the schema rather than by application
logic that might be bypassed by an import path or a fixture.

Replacing the base CV is an explicit versioned update, never a second row.
That keeps "which CV was this artefact generated from?" answerable.

### The correction pass is not optional

Extraction gets ~85% right. The remaining 15% — a date, a job title, a
metric — will propagate into every tailored CV you generate. Build the
Streamlit correction UI and fix them once, permanently. Version the truth
base so CV edits are traceable.

### Done when

The CV round-trips to JSON and back, and every bullet carries a stable ID
that survives re-extraction.

---

## Step 14 — Skill normalisation via ESCO · 5 pts

Without a shared vocabulary between your CV and job specs, gap analysis is
arithmetic on incompatible strings.

**ESCO** for UK/EU — free, official, multilingual, and maps skills to
occupations, which the categorisation in 11a can cross-check against.
**O\*NET** for US if you want US-specific granularity.

Map both directions: truth-base skills to ESCO IDs, and JD-extracted skills
to ESCO IDs. Classify each JD skill as **must-have vs nice-to-have** — the
distinction drives both the coverage score in Step 15 and the gap ranking in
Step 21.

Handle unmapped skills with a review list rather than silent drops. Emerging
tools won't be in ESCO yet, and those are often exactly the gaps worth
knowing about.

### Done when

"GCP", "Google Cloud" and "Google Cloud Platform" all resolve to one ID.

---

## Step 15 — Scoring funnel · 8 pts

Pure embedding similarity ranks by *topic*, not by *fit*. Four stages, each
cheaper than the one it feeds:

1. **Hard filters** — location/remote, contract type, seniority band, salary
   floor, posting age. Cheap, kills ~80%.
2. **Vector similarity** — embed CV sections and JD chunks separately, cosine
   over section pairs. Local Ollama into pgvector. Store `embedding_model`
   with every vector.
2b. **Cross-encoder rerank** — local `bge-reranker`-class model over the top
   ~200 before anything reaches the LLM.
3. **Skill coverage** — weighted by must-have vs nice-to-have, with recency
   decay on skills unused for 5+ years. A skill you last touched in 2015 is
   not the same asset as one you used last quarter.
4. **LLM re-rank, top 50 only** — returns `fit_score`, `rationale`,
   `missing_skills[]`, `stretch_flag`. Cost-controlled because it never sees
   the other 950.

### Chunking is the decision that dominates retrieval quality

More than model choice. Job specs have consistent internal structure —
company blurb, responsibilities, requirements, nice-to-haves, benefits — so
chunk **structurally by section, never by fixed-size window**. Then compare
like with like: your experience against their responsibilities, your skills
against their requirements. A 512-token window would blend a benefits
paragraph into a requirements comparison and quietly degrade every score.

Use LlamaIndex's node-parser utilities as a **library** for this. Not the
framework — see `DECISIONS.md` §3.

### Why a cross-encoder belongs between stages 2 and 3

Bi-encoders embed the CV and the JD independently and compare the vectors.
Cross-encoders read both together, which makes them substantially more
accurate at exactly this pairwise-relevance judgement — and pairwise
relevance is the whole task here.

Running one locally over the top ~200 costs nothing, takes seconds, and means
the 50 you spend LLM tokens on are better chosen. It directly improves the
metric Step 16 calibrates against, for no marginal spend.

Persist the reranker score as its own component.

### Two decisions that must happen here, not later

**Embedding dimension** is baked in at first embedding; changing it means
re-embedding everything. **pgvector index type** (HNSW vs IVFFlat) should be
chosen at the same time. Both currently sit in the nice-to-have list — pull
them forward into this step, because retrofitting either is expensive.

Final score is a config-driven weighted blend with **all component scores
persisted**. Same reasoning as Step 8: you're going to retune in Step 16.

### Done when

Every job has a score with components stored separately, and the LLM stage
never sees more than 50 jobs per run.

---

## Step 16 — Calibrate the scoring · 5 pts · **critical**

The most commonly skipped step and the one that decides whether the ranking
is useful or decorative. An uncalibrated score produces a confident-looking
ordering that's roughly random, and you won't notice because the numbers look
plausible.

1. Hand-label 30 jobs as strong / maybe / no.
2. Fit component weights against the labels.
3. **Hold out 10 to validate rather than fit** — otherwise you've measured
   your ability to memorise, not to rank.
4. Record the agreement figure in the repo.

Re-check calibration after any change to the embedding model. Different
vectors, different distances, invalid weights.

### Done when

Top 10 by computed score substantially matches top 10 by hand ranking on the
held-out set.

---

# Phase 4 — Generated artefacts

---

## Step 17 — Generation with the fabrication guard · 8 pts

### The design decision that matters

**Generation is constrained to the truth base.** The model may reorder,
reframe, re-weight and re-word existing bullets, and may surface skills you
have but didn't emphasise. It may not add anything without an
`evidence_ref`.

This is the difference between a tailoring tool and a fabrication engine.
Without it you will, eventually, send a CV containing something you can't
defend in an interview.

### The critic loop

Tailor generates. Critic checks three things:

- ATS rule compliance
- Keyword coverage against the job spec
- **Every generated bullet maps to a source `evidence_ref`**

Loop back to Tailor at most twice, then stop. Orphan bullets — ones with no
traceable source — **surface in the UI for an explicit decision**. They don't
silently pass and they don't silently vanish; sometimes the model has
correctly inferred something true that your CV states obliquely, and you want
to see that case.

### Title mirroring: a template field, not a generation instruction

The target title on every generated document is `dim_job.title_for_display`,
**injected as a template field**. It is never generated, never paraphrased,
never "adapted". Same principle as the ATS rules in Step 18: enforce
mechanically, because a prompt asking for the exact title works most of the
time and a template field works every time.

### The line that must not be crossed

**The headline mirrors the target title. The employment history never
changes.**

Putting "Senior Analytics Engineer" in your CV headline when applying for
that role is normal positioning — it states what you're applying for.
Changing "Senior Data Engineer at Credit Suisse" in the experience section to
match the spec is fabricating your employment history, and it's the kind of
thing that ends an offer at reference stage.

The critic enforces both halves: the exact target string is present in the
headline, **and** no experience-section title differs from the truth base.
The second assertion is the one that matters.

Where the target title implies seniority or scope the truth base doesn't
evidence — a "Head of Data" posting against an individual-contributor history
— flag it as a **stretch**, not a fabrication. The headline is still honest
about what you're applying for; you just want to know before you send it.

**The critic runs on the target provider from day one**, even while the
Tailor runs local. A safety guard validated on a weaker model than the one
actually running it is worse than no guard — it produces false confidence. At
40 runs a month this is under $1. Assert the critic's configured provider in
tests so a config drift can't silently downgrade it.

This critic loop is the single highest-value agentic pattern in the whole
build. Everything else that looks like it wants agents is really a
deterministic pipeline with LLM calls in it.

All artefacts key on `job_group_id`, not on a source posting. Apply once per
real job, not once per board.

### Done when

A deliberate prompt toward exaggeration is caught by the critic and surfaced
rather than emitted. Write this as an adversarial test.

---

## Step 18 — ATS-compliant docx output · 5 pts

**Enforce ATS rules mechanically in a locked template, not by prompting.** A
prompt that says "don't use tables" works most of the time; a template
without table support works every time.

- Single column, no tables, no text boxes, no headers/footers, no images or
  icons
- Standard section headings — `Experience`, `Education`, `Skills`, not "My
  Journey"
- Dates as `MM/YYYY – MM/YYYY`
- Acronym plus expansion on first use: `ELT (Extract, Load, Transform)`,
  `GCP (Google Cloud Platform)`
- Job title mirroring — `title_for_display` injected as a template field at
  the top of the document. Asserted against the **rendered** docx text, not
  just the source data, so a template change can't silently drop it
- Filename: `<surname>_<title_for_display>_<company>.docx`. Recruiters sort
  and search by filename, and a generic `CV.docx` in a folder of forty is
  invisible

Emit `.txt` alongside the `.docx` and diff them. ATS parsers read the
document in XML order, which is not always the visual order — the diff is how
you catch a template change that silently reorders sections.

### Done when

Text extracted from the generated docx reads in the correct order. Automated
test.

---

## Step 19 — Cover letter and elevator pitch · 5 pts

Same constrained pipeline, same fabrication guard, plus a **Researcher agent**
retrieving company context: company site, recent news, Companies House for UK
entities.

The same title rule applies to every artefact, not just the CV: the cover
letter names the exact `title_for_display` in its opening line and its
reference line, and the pitch opens with it. The critic asserts exact title
presence across all generated documents, and the immutability rule on past
titles holds everywhere.

Pitch in 30s / 60s / 90s variants — the 30s is the one you'll actually use,
but generating all three forces the model to identify what's genuinely
essential.

Stored as `artefact_cover_letter` and `artefact_pitch`, keyed on
`job_group_id`.

---

# Phase 5 — Market intelligence and interface

---

## Step 19a — Persisted company intelligence · 5 pts

Step 19's Researcher agent gathers company context for the cover letter and
then throws it away. That's a waste twice over: you pay for the research
again on the next application to the same employer, and — more importantly —
it's exactly the material you want three weeks later when you're prepping for
an interview there.

**`dim_company_intel`**, keyed on `dim_company`:

- Business model, funding stage, size trajectory
- Recent news and announcements
- Tech stack signals from engineering blogs and public repos
- Companies House data for UK entities: incorporation, accounts, officers,
  filing history — genuinely useful for judging a small consultancy's
  stability before taking a contract with them
- Sentiment signals where available

Use **Crawl4AI or Firecrawl** for the fetching rather than hand-rolled
`requests` + BeautifulSoup. The output shape is the point — LLM-ready
markdown from JS-rendered pages — not the HTTP call. Cache by URL and date so
re-research doesn't re-crawl.

Two details that matter:

**Staleness, not immutability.** Add `researched_at` and a refresh threshold.
Re-research when stale rather than rebuilding from scratch, and **version the
intel** so a cover letter you sent in March remains explicable against what
you knew in March.

**Attach intel to the end client, not the agency.** Half your UK contract
postings come through agencies, and researching Hays tells you nothing about
the bank you'd actually be working at. Where the end client is identifiable,
that's the key.

### Done when

A second application to the same company reuses stored intel rather than
re-researching, and the interview prep view surfaces it.

---

## Step 20 — Interview question bank · 8 pts

**A batch job, not a runtime feature.** Generate once per category, refresh
quarterly.

```sql
question_bank (
  id, category, topic, difficulty,
  question, model_answer, personal_answer,
  tags[], last_reviewed, ease_factor, next_review
)
```

Four active `qa_category` values × ~4 topics × 30 ≈ 480 Q&As. Generate in
batches with a critic pass for technical correctness.

Read the active list from `category_map.yml`; never hardcode it. Generation
is **idempotent per category** — promoting a new one generates only that
bank, and never regenerates the existing four. That property is what makes
the Step 21b promotion workflow cheap enough to actually use.

### Two refinements that make it worth doing

- **Behavioural answers must be generated from your truth base.** A generic
  STAR answer about "a time you handled conflict" is worthless. One built
  from your actual Credit Suisse or PrescientAI work is something you can
  deliver convincingly, because it happened.
- **Score technical questions against your CV** so the 30 you're most likely
  to fumble surface first. The point isn't coverage; it's finding the gaps.

### The bank splits across the two zones

**Shared:** question text and model answers, per `qa_category`. The same
technical questions serve every user in that category, so generation cost
amortises — never regenerate a bank per user.

**Per-user:** `personal_answer` and SM-2 state, in
`user_question_progress(user_id, question_id, ease_factor, next_review,
personal_answer)`.

That split matters because the personal answers are the valuable part and
they're built from each user's own truth base — a shared model answer is a
scaffold; the personal answer is the thing you'd actually say.

SM-2 spaced repetition on `ease_factor` / `next_review`, plus Anki export so
you can drill on a phone.

---

## Step 20a — Interview pipeline beyond "applied" · 5 pts

`fct_application` currently stops at submission, which means the system knows
nothing about the part of the process that actually determines outcomes.

Extend to a stage model — applied, screen, tech, panel, final, offer,
rejected, withdrawn — with stage date, format, duration, and interviewer
names and roles. Per-stage prep artefacts draw on `dim_company_intel` and the
question bank. Track rejection stage and stated reason. Surface time-in-stage
so stalled processes become visible rather than quietly forgotten.

### The part that's worth more than the rest combined

**Capture the questions you were actually asked.**

Step 20 generates ~480 questions from a model's guess at what an interviewer
might ask. A question you were genuinely asked, at a company in your target
market, for a role in your category, is worth more than fifty generated ones
— and there is currently nowhere in the system to put it.

Write them back into `question_bank` with source tagging, and weight the SM-2
review scheduler toward real-world-asked questions over generated ones. After
a handful of interviews the bank stops being synthetic and starts being an
empirical record of what this market actually asks you.

Add a structured post-interview debrief prompt — the capture has to happen
within a day or the detail is gone.

### Done when

A multi-stage process is trackable end to end, and questions captured from a
real interview appear in the question bank tagged to their source.

---

## Step 21a — Market intelligence marts · 8 pts

dbt models, one per analytical grain. The dashboard becomes a thin rendering
layer. Build these *before* the UI — reverse that order and you'll end up
with chart-shaped SQL embedded in Streamlit callbacks.

### The eight marts

**`fct_market_volume`** — category × region × week × seniority × work_model.
Postings, WoW and MoM change, share of category total, new vs reposted.

**`fct_market_salary`** — category × region × month. Median, P25, P75, IQR
spread, min/max advertised, split by contract (day rate) vs permanent
(annual). Critically: **`n_disclosed` and `disclosure_rate` alongside every
figure.** UK salary disclosure runs well under half of postings, so a median
without its denominator is misleading — and the disclosure rate is itself a
signal, rising when employers compete.

**`fct_skill_demand`** — category × region × month × esco_skill_id.
Frequency, share of category postings, rank, **rank delta vs 90 days ago**
(the rising/falling signal), must-have vs nice-to-have ratio.

**`fct_skill_salary_premium`** — median salary of postings requiring the skill
minus median of those that don't, per category. **This is the single most
useful chart you'll build.** It tells you which gap to close first in
economic terms rather than by raw frequency — and those rankings diverge
sharply. The most-mentioned skill is often table stakes; the premium skill is
the differentiator.

**`fct_skill_cooccurrence`** — skill pairs within category, with lift score.
Surfaces the actual stack combinations employers want — Snowflake + dbt +
Airflow as a cluster, not three independent line items.

**`fct_employer_activity`** — company × category × month. Volume, agency vs
direct ratio, new entrants. Doubles as the auto-seeding source for your ATS
target-company registry from Step 4.

**`fct_posting_lifecycle`** — days a posting stays live before disappearing
from all sources. A proxy for time-to-fill: short-lived postings mean either
an easy fill or an already-identified internal candidate. Falls out of your
bronze versioning for free via `last_seen_at`.

**`fct_market_composition`** — remote/hybrid/onsite mix, contract vs
permanent, seniority mix, all as trends.

### The trap that will ruin every trend chart

**Your query matrix, not the market, determines what you see.** Add Reed in
week 3 and Jooble in week 6, and volume jumps each time — it looks exactly
like a hiring boom. Same effect if you widen your keyword list. This is the
single most likely way to fool yourself with this system.

Two mitigations, both here rather than retrofitted:

1. **Stable source panel.** Every trend metric computes only over sources
   active for the *entire* window. Store `source_active_from` per source; the
   mart filters on it. Charts footnote the panel.
2. **Frozen query matrix.** Fix the keyword × region matrix early and version
   it. When you change it, stamp a `matrix_version` and break the trend line
   **visibly** rather than letting a discontinuity masquerade as signal.

Add a dbt test asserting no trend metric bypasses the source panel filter.

### On expectations

**Trends need time depth you won't have.** Volume and skill-frequency charts
are near-useless until 8–12 weeks of collection; expect the trend views to
become genuinely informative around month three. The salary, skill-frequency
and premium views work from day one — those are cross-sectional, not
longitudinal. The Adzuna historical salary data from Step 4 is what fills the
gap in the interim.

### Done when

All eight marts build and pass tests; every trend metric respects the source
panel, and every salary figure carries its `n` and disclosure rate.

---

## Step 21b — Emergent role and macro trend detection · 8 pts

### What this feature is for

Every other analytical step in this project answers "what does the market
want *now*, and how do I match it?" This one answers a different question:
**what is the market becoming, and what should I be able to do in eighteen
months?**

Two levels:

- **Micro** — new job titles and functions emerging from, and *between*, the
  supported categories. The Forward Deployed Engineer is the canonical
  example: not a software engineer, not a solutions architect, not a
  consultant, but a function that formed in the gap between them and now has
  a name, a salary band and a career path.
- **Macro** — the direction the AI transformation is pushing the whole
  market, measured rather than asserted.

By design this reaches **outside the project's own taxonomy**. The `other`
category from Step 11a stops being a dustbin and becomes the primary input.
A taxonomy can only ever classify what it was built to classify; emergent
roles are, definitionally, the things it can't.

### Input: the discovery corpus, not the targeted one

See Step 4a. Novelty detection against a frozen keyword matrix would only
ever rediscover your own assumptions.

---

### Micro: detecting emergent titles

**1. Canonical title extraction**

Beyond `strip_title` from Step 6 — extract title phrase n-grams, strip
modifiers and employer-specific decoration, build `dim_title_canonical` with
`first_seen_at`, `last_seen_at`, `distinct_employer_count`,
`distinct_source_count`.

**2. Novelty index**

```
novelty = f(recency of first_seen) × employer_breadth × growth_velocity
```

Three factors, and **the middle one is doing most of the work**. A title that
appeared six weeks ago at one company is noise — some VP invented a job
title. The same title at eleven unrelated companies is a market forming.
Enforce a hard employer-breadth floor before anything is described as a
trend.

**3. Taxonomy-independent clustering**

Embed canonical titles, cluster with HDBSCAN (density-based, so it doesn't
force everything into a cluster and leaves genuine outliers as outliers).
Clusters with no existing category centroid within threshold are **emergent
candidates**.

Note the deliberate asymmetry: Step 11a classifies *into* a fixed taxonomy;
this clusters *without* one, then asks what the taxonomy failed to cover.
Running both and comparing is the whole method.

**4. Cross-category hybridity**

The Forward Deployed Engineer pattern specifically. For each title cluster,
compare distance to the nearest single category centroid against the best
two-centroid blend. A role that sits much closer to `0.6 × software_engineer
+ 0.4 × solutions_consultant` than to either alone is a **hybrid** — a
function forming in the space between two categories.

This is where genuinely new job families come from, and it's invisible to any
system that only asks "which of my six buckets does this go in?"

**5. Naming and corroboration**

An LLM pass names and characterises each cluster, with web search to check
whether the role exists in the wild under that name or another.

**Every LLM-named cluster is a hypothesis, not a finding.** It goes to a
review queue for explicit confirm/dismiss with your rationale, stored in
`emergent_role_review`. The model will confidently name clusters that are
artefacts of your collection. Human judgement is the validation layer and
there isn't a substitute.

---

### Macro: measuring the AI transformation

Titles are the lagging indicator. The market changes what roles *do* long
before it changes what they're *called* — so the more informative signal is
drift inside stable titles.

**`fct_role_drift`** — Jensen-Shannon divergence between a category's skill
distribution now and 6 / 12 months ago. "Data Engineer" postings increasingly
demanding RAG pipelines, vector stores and eval frameworks is the single most
relevant trend to *you*, and it produces no new title at all. A title-only
system misses it entirely.

**`fct_ai_penetration`** — share of postings per category mentioning agents,
RAG, evals, MCP, fine-tuning, vector stores, prompt engineering. Tracked as a
time series per category, the shape tells you which categories are absorbing
AI work and which are being restructured around it.

**Skill migration into non-AI categories** — the macro signal proper. When
AI-adjacent skills stop being concentrated in "AI Engineer" postings and
start appearing across data engineering, analytics and platform roles, that's
the transformation arriving in your own category rather than an adjacent one.

**Displacement language** — postings describing *oversight of automated
work* versus *performing the work*. "Review and validate model outputs"
versus "build the pipeline". A slow-moving but revealing indicator of how a
category's centre of gravity is shifting.

---

### Traps specific to this feature

**Agency reposts inflate everything.** Recruitment agencies repost roles
under varied titles across multiple boards. Without filtering them out of
novelty scoring, agency title-churn reads as market innovation. Filter
agencies before scoring, not after.

**Title inflation is not novelty.** "Ninja", "Rockstar", "Guru", "Wizard",
"Hero" — stoplist them. They're marketing, and they will otherwise cluster
beautifully into a very confident nonsense finding.

**The base-rate problem is severe here.** Volume trends need 8–12 weeks;
emergent-role detection needs considerably more, because you're looking for
low-frequency signals against a short baseline. **This is the feature that
matures last** — likely six months in. Document the minimum history window
below which the output is not trustworthy, and have the UI refuse to display
a novelty index before that threshold rather than showing a bad one.

**Confident nonsense is the default failure mode.** Clustering plus an LLM
naming pass will *always* produce plausible-sounding emergent roles, whether
or not any exist. Three guards: the employer-breadth floor, external
corroboration, and mandatory human confirm/dismiss. Treat unconfirmed output
as a question, never an answer.

### Promotion into the taxonomy

A confirmed emergent role can grow the taxonomy — but only through an
explicit prompt, never automatically.

**Statistical confirmation gate, before the user is asked anything:**

- Employer-breadth floor met (min distinct non-agency employers)
- Sustained across N consecutive periods, not a single spike
- Growth velocity non-negative
- Agency and repost noise filtered

Only candidates clearing all four reach you. This matters because the whole
feature's failure mode is confident nonsense — a prompt is an interruption,
and an interruption you learn to dismiss is worse than no prompt.

**The prompt offers four outcomes:**

| Choice | Effect |
|---|---|
| Add as classification category | New analytical grain from `valid_from` onward |
| Add as `qa_category` | Enqueues on-demand question bank generation for that category only |
| Both | The full promotion — a genuinely new role family |
| Dismiss | Verdict recorded, cluster suppressed from re-prompting for a configured period |

**Never auto-promote.** A taxonomy that changes without consent silently
invalidates every historical chart — last quarter's "data engineer" volume
means something different once part of it reclassifies. Appending to
`category_map.yml` with a `valid_from` stamp keeps historical classification
reproducible, and retro-classification is an explicit backfill decision
rather than a side effect.

This is the mechanism by which a Forward Deployed Engineer cluster, if it
holds up statistically, becomes the eighth category and the fifth question
bank.

### Feeding back into the CV

Confirmed emergent roles map back to the truth base for a **forward-looking
gap view** — proficiency against where the market is heading, not only where
it currently is. Given your position (senior, 25+ years, deliberately
repositioning), that view is arguably worth more than the current-state gap
analysis. It's the difference between optimising for the next application and
optimising for the next three years.

### Done when

The system independently surfaces at least three candidate emergent roles
outside the taxonomy, each with employer breadth, growth velocity and a skill
signature — and each is either corroborated or dismissed by explicit human
review.

---

## Step 21c — Salary and rate negotiation support · 3 pts

The smallest step in the plan with the highest leverage per point, because it
fires at exactly one moment: an offer on the table and a number to respond
to. You'll already have the marts; this points them at that moment.

Given amount, `rate_basis`, `engagement_type`, `ir35_status`, category,
region and seniority, return the offer's **percentile position** within the
matching slice, plus comparable roles with disclosed rates, plus whether that
slice is rising or falling over six months.

Three things that make it honest rather than confident:

- **Never a bare percentile.** `n_disclosed` and `disclosure_rate` are shown
  every time. "72nd percentile" against 9 disclosed observations is not a
  finding, and you need to see that before you use it in a conversation.
- **Warn when the slice is too thin** to be meaningful, rather than
  computing a number anyway.
- **Inside vs outside IR35 net comparison**, so two offers are actually
  comparable. This is the whole reason Step 5a had to happen early — without
  those fields this feature can't exist.

Record offers received against `fct_application`. That's also the seed data
for the outcome loop in the nice-to-have list.

### Done when

Given an offer, the system returns its percentile position within the correct
slice, with the disclosure caveat stated explicitly.

---

## Step 21 — Streamlit dashboard · 8 pts

### Why Streamlit and not Gradio

Gradio is excellent at *function-shaped* interfaces — input in, artefact out.
This is a multi-panel analytical app: filterable ranked tables, editable
application status, cross-filtered charts, a review queue. Gradio's dataframe
and layout primitives fight you on all of it.

Portability comes from **Docker, not the framework**. The same image runs on
Cloud Run, App Runner, Container Apps, or a HF Space with the Docker SDK.
Gradio buys nothing there.

The FastAPI split still stands, but for a different reason than UI-swapping:
n8n needs endpoints to call for approval gates and digests. Streamlit calls
the same endpoints. **One interface, two clients** — not two interfaces.

### Tabs

**Pipeline**
- Ranked jobs with score breakdown and one-click artefact generation
- Application status kanban writing to `fct_application`

**Market** *(filtered to `entry_method='api'` + stable source panel)*
- Volume trend by category, with region and seniority filters
- Salary distribution box plots by category × region — `n` and disclosure
  rate always visible
- Top 30 skills by category sorted by **rank delta**, rising and falling
- Skill salary premium, ranked
- Skill co-occurrence heatmap
- Work model and contract mix trends
- Top hiring employers, agency vs direct
- Posting lifecycle distribution

**Horizon** *(discovery corpus)*
- Emergent title candidates: novelty index, employer breadth, first-seen date
- Cross-category hybridity scatter — where new functions are forming between
  categories
- Role drift: how each category's skill signature has shifted over 6 and 12
  months
- AI penetration by category — the macro transformation signal
- Hypothesis review queue: confirm or dismiss each candidate

**Me** *(no source constraint)*
- Skill quadrant: market demand vs your proficiency
- **The same quadrant weighted by salary premium** rather than frequency —
  this is the one that tells you what to learn
- Course recommendations against the top-right gaps

**Admin**
- Dedup review queue
- Manual job entry form

### Why Market and Me are separate tabs

Three different corpora with three different biases. Market runs on the
targeted channel with the stable source panel; Horizon runs on the discovery
corpus where breadth matters more than comparability; Me has no source
constraint at all. Mixing them in one panel is how the selection bias leaks
back in after you carefully filtered it out in Step 11.

### On course recommendations — the honest caveat

**There is no good free course API.** Coursera and Udemy affiliate APIs are
gated and unreliable. The pragmatic answer is a curated `skill → courses`
YAML you maintain (~100 entries covers your space), augmented by an LLM
suggestion pass with web search for unmapped gaps. Treat this as the least
automatable component of the whole system and don't over-invest in it.

### Done when

All tabs render against real data, the kanban persists status changes, and no
market chart includes manual entries.

---

# Phase 6 — Orchestration and production

---

## Step 22 — Cloud Scheduler fan-out and hardening · 5 pts

Source × keyword × region matrix — the same one versioned in Step 21a —
firing Cloud Run Jobs on a schedule. Cloud Scheduler is free at this volume
and more reliable than n8n cron for the fan-out.

- Streamlit deployed behind IAP. Non-negotiable: this holds your CV, your
  application history, and your salary expectations.
- All secrets in Secret Manager, none in env files
- Alerting on failed pipeline runs
- Decide Neon vs Cloud SQL based on observed volume and cost
- Document the rollback procedure

### Done when

Scheduled ingestion runs unattended for a week without manual intervention,
and the UI is unreachable without authentication.

---

## Step 22a — Sign-in and identity · 3 pts

### Scope, deliberately narrow

Two users, trusted, personal job hunting. Identity exists so the application
knows **whose truth base and whose scores to load** — it is not a compliance
control, and it doesn't need to be.

Statutory data-protection obligations are **out of scope**. Two people using
a tool for their own job search, not published, not commercial, sits within
UK GDPR's household/personal-use exemption. Building export flows, retention
jobs and processor agreements for that would be ceremony without a
counterparty. The full scope is recorded as LATER item #1 with an explicit
trigger — see `DECISIONS.md` §7.

### Implementation

Step 22 already puts Streamlit behind IAP. Allowlist two Google accounts and
read the verified identity IAP provides. **No OIDC implementation needed** —
that's the whole step.

- Identity from the IAP-provided header only, never a client-supplied field
- Map to `app_user.id` in FastAPI; set the DB session user from it
- Local dev: an env-var user override, hard-disabled when `ENV=gcp`
- Authenticate at the API layer. Streamlit session state is not an auth
  boundary

### Three things worth doing anyway

Cheap, and they'd be the expensive part to retrofit if the trigger ever
fires:

- **No personal data in application logs.** Easy to breach accidentally when
  debugging prompt inputs, and painful to purge afterwards
- **Encryption at rest confirmed** — free on managed Postgres, just verify
- **Decide whether recruiter and interviewer names are stored at all.** Those
  people didn't consent to being in your database, and roles plus dates are
  usually sufficient for interview prep. Storing less is simpler than
  governing more

### Done when

Each user signs in, the app resolves their `user_id` from the IAP identity,
and neither can see the other's truth base, scores or artefacts.

---

## Step 23 — n8n approval gates and digest · 5 pts

**Webhook-triggered only, no cron.** Cloud Run's scale-to-zero kills cron
triggers silently — resolved by moving scheduling to Cloud Scheduler in Step
22 and leaving n8n only the event-driven work.

Deploy on a GCE `e2-small` (~£12/month) or keep it local. Not Cloud Run.

What n8n is genuinely good for here:

- **Daily digest** of new jobs scoring 85+, to email or Telegram
- **Approval gate** before artefact generation — "3 new 85+ jobs, approve
  tailoring?" and it waits for your reply. This human-in-the-loop pattern is
  n8n's real strength

**n8n holds zero business logic.** It calls FastAPI endpoints. The moment
logic starts living in n8n nodes you lose version control, testing and
debuggability on the part of the system you most need them for.

---

## Step 23a — Near-real-time alerting on top-percentile jobs · 3 pts

The daily digest from Step 23 is right for volume. But early applicants get
read — on a competitive posting, being in the first twenty CVs matters more
than being in the best twenty. A top-percentile fit should reach you within
minutes.

Design constraints, all of which are about **restraint**:

- **High-frequency tier for the highest-yield sources only.** Not every
  source, not every keyword. Free tiers won't survive it and most sources
  don't merit it.
- **Percentile threshold against your own score distribution**, not a fixed
  number. A fixed 85 means something different after you recalibrate in Step
  16; a percentile doesn't drift.
- **Hard daily cap.** An alert that fires often is not an alert — it's a
  feed, and you'll mute it within a week. If the cap is being hit, the
  threshold is wrong.
- **Suppress already-known jobs.** A posting appearing on a fourth board is
  not new; dedup already knows this, so gate on `job_group_id`.
- **Quiet hours** in your timezone.

Log alert-to-application conversion. If you're not applying to the things it
alerts on, the threshold is miscalibrated and that's measurable rather than a
matter of opinion.

### Done when

A newly ingested top-percentile job produces a notification within minutes,
and the alert rate stays within the configured cap.


---

# Cross-cutting notes

## Where the risk actually sits

Steps 6, 9 and 16 for correctness; Step 12a for everything LLM-shaped;
Step 21b for credibility; Step 1a because tenancy cannot be retrofitted. Normalisation, dedup calibration, scoring calibration.
Each produces something that looks like it works when done by feel. Budget
real time for the hand-labelling in 9 and 16 — it's a few hours total and
it's the difference between a system you trust and a system you check.

Step 21b is a different kind of risk. It won't produce a wrong number — it
will produce a confident, well-presented, entirely fabricated market trend,
and there's no test that catches that. The employer-breadth floor, the
corroboration pass and the mandatory human verdict are the only defences.
Build all three or don't build the feature.

## The dependency chain is linear — deliberately, but not necessarily

`STEP-00 → 01 → 01A → 02 → 03 → 04 → 04A → 05 → 05A → 06 → 07 → 08 → 09 → 10
→ 11 → 11A → 12 → 12A → 13 → 14 → 15 → 16 → 17 → 18 → 19 → 19A → 20 → 20A
→ 21A → 21B → 21C → 21 → 22 → 22A → 23 → 23A`

Honest for a solo project, but it means Jira shows no parallelism. **Phase 3
genuinely doesn't depend on Phases 1–2** — the CV truth base needs no jobs at
all. Break the `STEP-12 → STEP-13` link if you want a second thread to switch
to when dedup calibration gets tedious.

## Story points are calibration bait

They're relative sizings, guessed. Set your own on the first two epics once
you've felt the actual friction, then let velocity tell you whether Phase 3
is a month or a quarter.

## Once the backlog is in Jira, the backlog is canonical

This document and `backlog.yml` live in the repo and change in the same
commit as the code they describe. That's the point — when you decide in month
three that the dedup approach was wrong, the plan changes alongside it.

---

# Nice to have — unscheduled, ordered

25 improvements not in the scheduled plan, in implementation order. They live
in `backlog.yml` under `nice_to_have:` with a `trigger` condition each —
several are cheap now and expensive later, and the trigger states when to
promote one into the plan.

Sync them to Jira as a separate **"Later"** epic with no sprint and no
dependency links, so they stay visible without distorting velocity.

## The ones with a deadline

**#2 — persist rate-limit state in Postgres.** This is a defect, not an
enhancement. The Step 3 token bucket is in-process; the GCP pipeline is an
ephemeral Cloud Run Job. State dies with the container, every run starts with
a full bucket, and you'll collect 429s or a source ban. Fix before the first
unattended scheduled run.

**#4 — job spec snapshot at apply time.** Postings vanish. Cheap now,
unrecoverable later. Do it before the first real application.

**#5 — CI.** Do it as soon as the Step 6 test suite exists, or those tests
stop being run.

## The one that changes the system's nature

**#1 — application outcome loop.** The highest-value item not scheduled. As
written, the plan builds a system that generates artefacts and never learns
whether they worked; scoring stays a static heuristic indefinitely. Record
outcome per application, then recalibrate the Step 16 weights against real
callbacks rather than your hand labels, and A/B the CV variants.

It isn't scheduled because it needs ~30 recorded outcomes to mean anything,
so it can't be built usefully until you've been running for a while. But it
is the difference between a tool that ranks jobs and a tool that learns your
market. Promote it the moment you have the data.

## Full order

| # | Item | Trigger |
|---|---|---|
| 1 | Application outcome loop | ~30 recorded outcomes |
| 2 | Persist rate-limit state | before first unattended GCP run |
| 3 | Data quality / anomaly detection | 4+ sources live |
| 4 | Job spec snapshot at apply time | before first application |
| 5 | CI pipeline | when Step 6 tests exist |
| 6 | Double-submission guard | when applying via agencies |
| 7 | Structured output enforcement | first malformed-output regression |
| 8 | Embedding cache on content hash | bronze > ~10k postings |
| 9 | LLM cost accounting + budget caps | before unattended generation |
| 10 | pgvector index strategy | when vector latency shows |
| 11 | Schema migrations (Alembic/sqitch) | second manual DDL change |
| 12 | Per-source circuit breaker | first source outage |
| 13 | Recorded API fixtures (VCR) | connector count > 5 |
| 14 | Backup and restore, tested | before data becomes irreplaceable |
| 15 | Dead-letter table | first silent parse failure |
| 16 | Advisory locks on dedup | before concurrent scheduling |
| 17 | PII posture and retention policy | before storing third-party contacts |
| 18 | Least-privilege DB roles | first internet-exposed deploy |
| 19 | CV personas (multiple positionings) | applying across >1 category |
| 20 | Referral path detection | anytime |
| 21 | Incremental dbt marts | mart build > few minutes |
| 22 | Reverse search (skill-anchored) | once truth base embeddings exist |
| 23 | Bronze partitioning + cold storage | bronze > few GB |
| 24 | LLM response caching | when regeneration becomes habit |
| 25 | French-market support | if targeting French roles |
