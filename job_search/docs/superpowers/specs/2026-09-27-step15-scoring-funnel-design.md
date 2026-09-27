# Step 15 — Scoring funnel — design

Status: design approved in conversation 2026-09-27 (one open scope question
resolved: preferences get a DB table + settings page, not a config file).
Everything else in "Already decided" below comes from PLAN.md Step 15 and
DECISIONS.md §4 (Retrieval design), which this spec applies rather than
re-litigates. This spec awaits written review.

## Purpose

Rank the shared job pool for each user by *fit*, not by topic similarity.
Four cost-controlled stages: hard filters, vector similarity + cross-encoder
rerank, skill coverage, LLM re-rank of the top 50 only. Every component score
is persisted separately so Step 16 can fit blend weights against real
hand-labels instead of guessed constants.

Success criteria (PLAN.md's "Done when"): every job has a score with every
component stored separately, and the LLM stage never sees more than 50 jobs
per run.

Out of scope for this spec: Step 16 (calibration — its own spec, after this
is built and real component names/ranges exist to calibrate against); a
jobs-browsing UI (not in Step 15's acceptance criteria — a natural follow-up,
not built here); French-market support (open question in DECISIONS.md §8,
unrelated to this step).

## Already decided (DECISIONS.md §4, applied here, not reopened)

- Structural chunking of job specs by section, never fixed-size windows.
- LlamaIndex's node-parser utilities as a **library** only (no framework
  adoption — no `VectorStoreIndex`, no LlamaIndex-managed storage).
- Compare like-for-like sections only (experience↔responsibilities,
  skills↔requirements), not whole-document similarity.
- Cross-encoder rerank between vector search and the LLM stage.
- pgvector, not a separate vector store (must join against `dim_job`).
- Embedding dimension and pgvector index type fixed now, not deferred.
- `fct_job_score` grain is `(user_id, job_group_id)`: the shared job pool,
  scored independently per user.
- CV embeddings are per-user and never shared; job embeddings are shared and
  computed once (PLAN.md Step 15, backlog subtasks).
- Blend weights are config-driven, not hardcoded, and are fitted **per user**
  by Step 16 — so they live in a per-user store, not a single shared file.

## New decisions this spec makes

| Decision | Choice | Why |
|---|---|---|
| Embedding model | Reuse `nomic-embed-text` (768-dim, already running locally via Ollama, already used for ESCO in Step 14) | No new model to pull; it supports asymmetric `search_query:`/`search_document:` prefixes, which fits CV-vs-JD retrieval; DECISIONS §2.8 already commits to "embed locally with Ollama, always" |
| pgvector index type | HNSW | Behaves well without retuning as the corpus grows (no `lists` parameter to size against current row count, unlike IVFFlat); default `m=16, ef_construction=64` is adequate at this scale (low thousands of jobs, not millions) |
| Cross-encoder | `sentence-transformers`' `CrossEncoder` with a `BAAI/bge-reranker-base` checkpoint, run in the **pipeline** image only | Matches the "bge-reranker class, local" instruction; `sentence-transformers` reuses the torch install `docling` already pulls into the pipeline image, so the marginal size cost is the checkpoint (~1.1 GB) and the library itself, not a second torch |
| User scoring preferences | New table `scoring.user_preference` (RLS) + a Streamlit settings page | User's explicit choice this session (over a config file) |
| Calibration weights | New table `scoring.weight` (RLS), one row per `(user_id, component)` | Written by Step 16's fitting process, not hand-edited — a table fits the tenancy pattern already used for `cv_truth_base`/`user_quota` better than YAML |
| Hand-labels for Step 16 | New table `scoring.job_label` (RLS) | Same shape as the existing `dedup.pair_labels`/`3_Dedup_Calibration.py` pattern — reused, not reinvented (built in the Step 16 spec, listed here only so the schema below is complete) |
| Schema | New `scoring` schema | Matches the one-schema-per-domain convention (`silver`, `dedup`, `esco`) |

## Data model (schema: `scoring`)

All per-user tables use the existing `session_scope(engine, user_id=...)` /
`app.current_user_id` GUC RLS pattern (see `core/db/session.py`, `app_user`
0001). Job-pool tables (shared, no `user_id`) follow the `dedup.*` pattern:
owner-role writes via a pipeline CLI subcommand, `job_search_app` gets
SELECT only, dbt reads them as plain sources.

```
scoring.user_preference        -- per-user, RLS
  user_id                uuid PK, FK app_user
  preferred_locations    text[] NOT NULL DEFAULT '{}'   -- empty = no filter
  remote_ok              text NOT NULL DEFAULT 'no_preference'
                          CHECK (remote_ok IN ('required','preferred',
                                                'no_preference','excluded'))
  contract_types         text[] NOT NULL DEFAULT '{}'   -- subset of
                          -- permanent/contract/ftc/interim; empty = no filter
  excluded_ir35_statuses text[] NOT NULL DEFAULT '{}'   -- subset of
                          -- inside/outside/not_applicable/undetermined/
                          -- unknown; empty = exclude nothing (never default
                          -- an exclusion onto the user — DECISIONS §2.13)
  min_seniority_band     text NULL CHECK (min_seniority_band IN
                          ('junior','mid','senior','lead','principal'))
  max_seniority_band     text NULL CHECK (max_seniority_band IN
                          ('junior','mid','senior','lead','principal'))
                          -- min <= max in ordinal band order is validated at
                          -- the write layer (settings page/API), not a DB
                          -- CHECK — expressing that ordinal comparison over
                          -- two text columns is not worth a CHECK expression
  min_salary_annual      numeric NULL
  min_rate_daily         numeric NULL
  max_posting_age_days   integer NULL                   -- NULL = no filter
  updated_at             timestamptz NOT NULL DEFAULT now()

scoring.job_chunk_embedding     -- SHARED, no RLS, owner-role writes
  job_group_id   text NOT NULL
  section        text NOT NULL CHECK (section IN
                  ('company_blurb','responsibilities','requirements',
                   'nice_to_have','benefits','other'))
  chunk_index    integer NOT NULL      -- 0-based, order within the section
  chunk_text     text NOT NULL
  embedding      vector(768) NOT NULL
  embedding_model text NOT NULL
  computed_at    timestamptz NOT NULL DEFAULT now()
  PK (job_group_id, section, chunk_index)
  INDEX (HNSW) ON embedding

scoring.cv_chunk_embedding      -- per-user, RLS
  user_id        uuid NOT NULL FK app_user
  cv_version     integer NOT NULL      -- ties to cv_truth_base.version
  section        text NOT NULL CHECK (section IN
                  ('summary','experience','skills','education',
                   'certifications','projects'))
  chunk_index    integer NOT NULL
  source_ref     text NULL             -- e.g. an Experience's bullet_id,
                                        -- for traceability back to the CV
  chunk_text     text NOT NULL
  embedding      vector(768) NOT NULL
  embedding_model text NOT NULL
  computed_at    timestamptz NOT NULL DEFAULT now()
  PK (user_id, cv_version, section, chunk_index)
  INDEX (HNSW) ON embedding

scoring.job_score               -- per-user, RLS. Silver table; dbt's
                                 -- fct_job_score (gold) is a thin pass-
                                 -- through joined with dim_job for display.
  user_id                uuid NOT NULL FK app_user
  job_group_id           text NOT NULL
  hard_filter_passed     boolean NOT NULL
  vector_similarity_score numeric NULL   -- NULL if hard-filtered out
  reranker_score         numeric NULL    -- NULL unless in the top ~200
  skill_coverage_score   numeric NULL    -- NULL if hard-filtered out
  llm_fit_score          numeric NULL    -- NULL unless in the top 50
  llm_rationale          text NULL
  llm_missing_skills     text[] NULL
  llm_stretch_flag       boolean NULL
  final_score            numeric NULL    -- the config-weighted blend
  embedding_model        text NULL       -- recorded so a model change is
                                          -- auditable (PLAN.md: store
                                          -- embedding_model with every vector)
  scored_at              timestamptz NOT NULL DEFAULT now()
  PK (user_id, job_group_id)

scoring.weight                  -- per-user, RLS. Written by Step 16.
  user_id      uuid NOT NULL FK app_user
  component    text NOT NULL CHECK (component IN
               ('vector_similarity','reranker','skill_coverage','llm_fit'))
  weight       numeric NOT NULL
  fitted_at    timestamptz NOT NULL DEFAULT now()
  PK (user_id, component)

scoring.job_label                -- per-user, RLS. Built by the Step 16 spec;
                                  -- listed here only for a complete schema.
  user_id      uuid NOT NULL FK app_user
  job_group_id text NOT NULL
  label        text NOT NULL CHECK (label IN ('strong','maybe','no'))
  is_holdout   boolean NOT NULL DEFAULT false
  labeled_at   timestamptz NOT NULL DEFAULT now()
  PK (user_id, job_group_id)
```

Before `scoring.weight` has a row for a user (before Step 16 runs), stage 4's
blend uses a documented **default** weight set (equal weights across the
components that exist for that job), so `final_score` is never NULL for a
job that passed hard filters — "looks-plausible-but-uncalibrated" is exactly
what Step 16 exists to fix, not something Step 15 should hide by leaving
`final_score` NULL.

## Pipeline (four stages, one CLI subcommand each so a stage can be re-run
alone — matching the `map-skills`/`embed-esco`/`classify-jobs` precedent)

### Stage 1 — Hard filters (`score-filter-jobs`)

Pure SQL over `dim_job` joined with the user's `scoring.user_preference` row:
location/remote, `engagement_type`, `ir35_status` (never coerce `unknown` —
only excluded if the user explicitly lists it), `seniority_band` between the
user's min/max, salary/rate floor (`rate_annualised`/`rate_daily_equivalent`
already computed by Step 5a), `posted_at` within `max_posting_age_days`. A
NULL preference field means "no filter on this dimension." Writes one
`scoring.job_score` row per `(user_id, job_group_id)` with
`hard_filter_passed` and nothing else set. No embeddings touched here — this
is the cheap ~80%-kill stage the plan calls for.

### Stage 2 — Chunking + embeddings (`chunk-embed-jobs`, `chunk-embed-cv`)

Two separate commands, since job chunks are shared/computed-once and CV
chunks are per-user:

- `chunk-embed-jobs [--limit N]`: for each `dim_job` row with no
  `scoring.job_chunk_embedding` rows yet, split `description` into sections
  by a **heading-detection heuristic**: a line under 60 characters that
  matches one of a fixed set of canonical heading variants per section
  (e.g. responsibilities: "responsibilities", "what you'll do", "the role";
  requirements: "requirements", "about you", "what we're looking for",
  "essential"; nice_to_have: "nice to have", "desirable", "bonus",
  "preferred"; benefits: "benefits", "what we offer", "perks") starts a new
  section, case-insensitive, same normalisation as
  `core.skills.normalise.normalise_skill`. Text before the first detected
  heading is `company_blurb`; any text that never matches a heading (a
  postings with no discernible structure, common from aggregators) becomes
  one `other` section rather than being silently dropped — a known, accepted
  quality boundary: heading phrasing this diverse cannot be caught
  perfectly by a fixed list, and getting it wrong here costs a slightly
  worse chunk grouping, not a crash or missing content. Within each detected
  section, split further into passage-sized chunks with LlamaIndex's
  `SentenceSplitter` (library call, not the framework's storage/index
  layer). Embed each chunk via `embed_text(..., model="nomic-embed-text")`
  with the `search_document:` prefix, insert.
- `chunk-embed-cv --user-id <id> [--refresh]`: same chunking applied to the
  CV truth base's own structure directly (no header-detection needed — the
  truth base is already structured: `summary`, each `Experience`'s bullets
  joined into one "experience" section per role, `skills` as one section,
  etc.), embedded with the `search_query:` prefix (the CV is the query side
  of retrieval), one CV version's chunks replacing the previous version's
  under `--refresh` (default: skip if a `cv_chunk_embedding` for the current
  `cv_version` already exists).

### Stage 2 continued — Vector similarity + rerank (`score-similarity`)

Guard first: refuse to compare a CV chunk and a job chunk embedded with
different `embedding_model` values (mirrors
`core.skills.mapper.EmbeddingModelMismatch` from Step 14) — a stale
`nomic-embed-text` job vector compared against a CV re-embedded under a new
model would produce a confident-looking, meaningless number, exactly the
failure mode PLAN.md's "re-check calibration after any change to the
embedding model" warns about. A mismatch skips that job's vector score for
that user (surfaced as a count in the command's output) rather than either
crashing the whole run or silently comparing incompatible vectors.

For each user, each job that passed hard filters: cosine similarity between
every CV section-embedding and every JD chunk-embedding of the *paired*
section (experience↔responsibilities, skills↔requirements, summary↔
company_blurb; unpaired sections — nice_to_have, benefits, projects,
education — are not scored, per "compare like sections only"), take the max
per section pair, average across paired sections into
`vector_similarity_score`. Rank jobs by that score, take the top ~200, run
the `sentence-transformers` `CrossEncoder` over (CV summary + top experience
text, full JD text) pairs for those 200, store `reranker_score`. Jobs outside
the top ~200 keep `reranker_score` NULL.

### Stage 3 — Skill coverage (`score-skill-coverage`)

Independent of stages 2/2b — only needs Step 14's `silver__bridge_job_skill`
(job_group_id, skill_id, requirement_level) and the CV truth base's `skills`
(canonical_id, years, last_used). For each job: for every
`must_have`/`nice_to_have` skill_id the job asks for, check whether the
user's CV has that `canonical_id`; a hit is weighted by requirement level
(must-have counts more) and decayed by recency (`last_used` older than 5
years decays toward zero, per PLAN.md — decay curve is a config constant so
Step 16 can retune it, not fit weights, since it changes the score's meaning,
not its blend). `skill_coverage_score` = weighted-hit sum / weighted-total
sum, so it is naturally in [0, 1] regardless of how many skills a job lists.

### Stage 4 — LLM re-rank (`score-llm-rerank`)

Pre-LLM score = equal-weighted blend of whatever of
`vector_similarity_score`/`reranker_score`/`skill_coverage_score` is non-NULL
for that job (this is a selection heuristic for who reaches the LLM, not
`final_score`). Top 50 per user go to a new `job_scoring` task in
`config/llm_tasks.yml` (anthropic, `claude-sonnet-5`, prompt family
`claude`), which returns `fit_score` (0-100), `rationale`, `missing_skills[]`,
`stretch_flag`. Persisted onto the same `scoring.job_score` row. Everyone
else keeps `llm_fit_score` NULL. Finally, `final_score` is computed for every
hard-filter-passing job as the weighted blend of whichever components are
non-NULL for it, using `scoring.weight` if a row exists for that user and
component, else the documented equal-weight default.

## Testing

Real Postgres, no DB mocking; only the Ollama/Anthropic/cross-encoder calls
are faked via injected functions/adapters, following this repo's existing
pattern. Fixture rows prefixed `zzfixture`/`fixture-` as elsewhere.

- `scoring.user_preference` CRUD + RLS: a user cannot read another's row.
- Hard filters: each dimension in isolation (location, remote, contract
  type, IR35 exclusion, seniority band, salary floor, posting age) plus the
  "no preference set = no filter" case and the "`unknown` is never
  auto-excluded" case explicitly.
- Chunking: a JD with all five sections splits into the right number of
  chunks in the right sections; a JD missing a section produces no chunk for
  it (not an empty/error chunk); re-running `chunk-embed-jobs` does not
  duplicate rows for an already-chunked job.
- Vector similarity: two near-identical CV/JD section pairs score near 1.0
  (via a fake `embed` returning controlled vectors, same technique as
  `test_skills_mapper.py`'s `axis_vector` fixtures); unpaired sections are
  never compared (assert no cross-section comparison happens); a CV chunk
  and a job chunk with different `embedding_model` values are skipped, not
  compared, and the skip is counted.
- Chunking heading detection: a JD with clear canonical headings splits
  correctly; a JD with no recognisable heading becomes one `other` section
  rather than raising or dropping content; a heading-like line over 60
  characters (clearly body text, not a heading) is not treated as one.
- Reranker: only the top ~200 by vector score get a non-NULL
  `reranker_score`; a fake `CrossEncoder` stand-in for the test.
- Skill coverage: a must-have hit scores higher than an equivalent
  nice-to-have hit; a skill last used 6+ years ago scores lower than an
  identical skill used last year; a job with no bridge rows scores using an
  empty total (no divide-by-zero — 0 total means the score is undefined, not
  0, and the caller must handle that case, e.g. by excluding it from the
  blend for that job rather than treating "no data" as "no fit").
- LLM re-rank: exactly 50 (or fewer, if fewer passed) get sent regardless of
  pool size; a fake Anthropic adapter confirms the prompt never includes a
  job outside the top 50.
- `final_score`: uses `scoring.weight` when present, the documented default
  otherwise; a job missing a component (e.g. never reached stage 4) blends
  only the components it has, not a missing-as-zero.

## Cost and infra

Embeddings: local Ollama, no marginal cost beyond compute already paid for
in Step 14. Reranker: local, `sentence-transformers` + a `bge-reranker-base`
checkpoint added to the **pipeline** image only (not api/ui) — a genuine
image-size increase (roughly 1-2 GB beyond what docling's torch already
adds); build it in isolation and check the disk guard used earlier this
project (`~/Documents/GitHub/.../job_search`'s Docker build history) before
building api/ui/pipeline in parallel. LLM re-rank: Claude Sonnet 5, capped at
50 jobs per run per user — bounded, predictable cost, same shape as
`job_categorisation`'s existing use.
