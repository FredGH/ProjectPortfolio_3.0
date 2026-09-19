# Step 14 — Skill Normalisation via ESCO (JOB-215) — Design

## Context

PLAN.md Step 14 gives the CV and job specs a shared skill vocabulary.
Without it, Step 15's coverage score and Step 21's gap ranking are
arithmetic on incompatible strings ("GCP" vs "Google Cloud Platform").

Step 13 left `Skill.canonical_id` in the CV truth-base schema, always
`None`, for this step to populate. Step 15 (scoring funnel) and Step 21
(skill-gap marts) depend on the output.

Per PLAN.md: use **ESCO** (UK/EU), map both directions (truth-base skills
and JD skills to ESCO IDs), classify each JD skill as must-have or
nice-to-have, and route unmapped skills to a review list, never a silent
drop.

## Decisions made while designing

| # | Decision | Rejected | Why |
|---|---|---|---|
| 1 | ESCO loaded from the bulk CSV release, dropped by hand into gitignored `data/esco/` | Live ESCO web API; loader that downloads it | Offline and reproducible; the portal download is form/licence-gated; PLAN.md's subtask says "load into Postgres" |
| 2 | Mapping cascade: alias/label match, then pgvector nearest neighbour, then review. No LLM in the mapping path | Adding an LLM pick over top-k; exact-match only | Deterministic and cheap. Exact-only would make the review list huge |
| 3 | JD extraction runs over dedup survivors only | Every raw posting; only jobs passing Step 15 hard filters | Duplicates would repeat identical LLM work and can disagree; Step 15 doesn't exist yet |
| 4 | Review list = table + Streamlit resolve page; resolutions persist to a DB alias table | CLI-only; table-only | Fix once, applies to every future CV and JD; matches the Categorisation Review and CV Editor pattern |
| 5 | ESCO label embeddings persisted as a rebuildable cache, no ANN index | Ephemeral file cache; dropping the embedding stage | DECISIONS.md fixes embedding dimension and index type at Step 15. This is a derived cache (re-run the embed step to change model or dimension), and an exact scan over ~14k rows needs no index, so Step 15's index-type choice for CV/JD chunks is untouched |

## Scope

**In scope:** ESCO loader (skills, labels, occupations, occupation-skill
relations); label embeddings; alias table and curated seed file; the
mapper; JD skill extraction with must-have/nice-to-have classification;
CV skill mapping; `silver__bridge_job_skill`; review table, API and
Streamlit page; wiring `skill_extraction` into Step 12a's eval harness.

**Out of scope:**
- O\*NET / US taxonomy.
- French-language ESCO (English only; French-market scope is still an
  open item in DECISIONS.md).
- Using ESCO occupations to cross-check Step 11a categorisation. The
  occupation data is loaded so that can be done later, but not built.
- Coverage scoring (Step 15) and gap ranking (Step 21).
- Showing canonical labels in the CV Editor page.
- Any ANN index on `esco.skill_embedding`.

## Tenancy

Everything here is the **shared zone** (`docs/tenancy.md`: "taxonomy" is
shared). No `user_id`, no RLS. Each migration says so in its docstring.

Consequence, accepted: a review resolution made by one user changes the
mapping for all users. `skill_mapping` and `skill_alias` therefore hold
skill strings only, never user data. `skill_mapping.seen_in_cv` is a
boolean, not a reference to a CV.

## Data model

### `esco` schema (loader-owned, read-only to the app)

```
esco.skill(skill_id TEXT PK,            -- trailing UUID of the ESCO concept URI
           concept_uri TEXT UNIQUE NOT NULL,
           preferred_label TEXT NOT NULL,
           skill_type TEXT, reuse_level TEXT, description TEXT)
esco.skill_label(skill_id → esco.skill, label TEXT, label_norm TEXT,
                 is_preferred BOOL,  PK (label_norm, skill_id))
esco.occupation(occupation_id TEXT PK, concept_uri, preferred_label, description)
esco.occupation_skill(occupation_id, skill_id, relation_type)  -- essential|optional
esco.skill_embedding(skill_id PK → esco.skill, embedding_model TEXT NOT NULL,
                     embedding vector(768) NOT NULL)
```

`skill_label` holds preferred, alt and hidden labels, normalised (below),
so exact matching covers ESCO's own synonyms. `skill_embedding` holds one
vector per skill, of its preferred label only (about 14k embedding calls;
embedding every alt label would be about 10x that for little gain, since
alt labels are already matched exactly). `embedding_model` is recorded per
row; the mapper refuses to run if it differs from
`Settings.embedding_model` (DECISIONS.md §2.8: mixed models corrupt
similarity silently).

### `silver` schema

```
silver.custom_skill(skill_id TEXT PK,   -- 'custom:<slug>'
                    canonical_label TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now())
silver.skill_alias(alias_norm TEXT PK,
                   skill_id TEXT NOT NULL,     -- ESCO id or 'custom:<slug>'
                   source TEXT NOT NULL,       -- 'seed' | 'review'
                   created_at TIMESTAMPTZ)
silver.skill_mapping(raw_norm TEXT PK,
                     raw_example TEXT NOT NULL,   -- one original spelling, for display
                     skill_id TEXT NULL,
                     method TEXT NOT NULL,        -- 'alias'|'label'|'embedding'|'none'
                     score NUMERIC NULL,          -- cosine, embedding method only
                     candidate_skill_id TEXT NULL, -- best below-threshold neighbour
                     candidate_score NUMERIC NULL,
                     review_status TEXT NULL,     -- NULL|'open'|'rejected'|'resolved'|'dismissed'
                     seen_in_cv BOOL NOT NULL DEFAULT false,
                     mapped_at TIMESTAMPTZ NOT NULL DEFAULT now())
silver.job_skill_extraction(job_group_id TEXT, prompt_version TEXT,
                            model TEXT, extracted_at TIMESTAMPTZ,
                            PK (job_group_id, prompt_version))
silver.job_skill_raw(job_group_id TEXT, prompt_version TEXT, raw_skill TEXT,
                     raw_norm TEXT, requirement_level TEXT,
                     PK (job_group_id, prompt_version, raw_norm),
                     FK → job_skill_extraction)
```

Invariants (CHECK constraints):
- `method = 'none'` if and only if `skill_id IS NULL`.
- `review_status` of `open`, `rejected` or `dismissed` only while `skill_id IS NULL`; `resolved` only when it is set.
- `requirement_level IN ('must_have', 'nice_to_have')`.

`job_skill_extraction` exists so a job that yields zero skills is still
recorded as done, and is not re-sent to the LLM on every run.

`skill_mapping` is keyed on the normalised string, not the job. CV and JD
skills share one cache; fixing a string fixes it everywhere, and
re-mapping after an alias fix needs no LLM call.

Grants: `job_search_app` gets `INSERT, UPDATE` on `silver.skill_alias`,
`silver.skill_mapping` and `silver.custom_skill` (the review page writes
them via the API). Everything else is SELECT via migration 0010's default
privileges. Migration numbers are 0022 (esco schema) and 0023 (silver
tables); check the Alembic head again at implementation time, since a
parallel branch could land first (0017 collided once already).

## String normalisation

`normalise_skill(raw) -> str`: Unicode NFKC, lowercase, `&` → `and`,
strip surrounding punctuation, collapse whitespace. Deliberately no
stemming or stop-word removal: "Google Cloud" vs "Google Cloud Platform"
is handled by alias entries, not by fuzzy string rules that would also
merge "Java" and "JavaScript".

## The mapper — `core/skills/mapper.py`

`map_skill(conn, raw, *, embed, embedding_model) -> SkillMatch`
(`skill_id | None`, `method`, `score`), a cascade:

1. **Alias**: `skill_alias.alias_norm = raw_norm` (method `alias`).
2. **ESCO label**: `esco.skill_label.label_norm = raw_norm` (method
   `label`). If several skills share a label, prefer a skill whose
   *preferred* label is the match, then the lexicographically smallest
   `skill_id`; ambiguity is resolved by an alias entry.
3. **Embedding**: `embed(raw)` (the existing Ollama helper), then
   `ORDER BY embedding <=> :q LIMIT 1` over `esco.skill_embedding`.
   Accepted if cosine similarity ≥ the accept threshold (method
   `embedding`, score stored).
4. Otherwise `skill_id = NULL`, method `none`, `review_status = 'open'`.
   The nearest below-threshold ESCO neighbour, if any, is stored as
   `candidate_skill_id` / `candidate_score` for the review page.

The string normaliser, `normalise_skill`, lives separately in
`core/skills/normalise.py`.

Aliases outrank ESCO labels so a curated correction can override ESCO.

The accept threshold is a starting value, **not empirically tuned**, the
same stance as Step 11a's cutoffs. The plan sets it after inspecting real
ESCO neighbours, and the acceptance activity is hand-checking about 100
embedding matches through the review page. The knob is a named constant
with a docstring saying so.

`map_pending(engine, *, embed, embedding_model)` maps every distinct
`raw_norm` in `job_skill_raw` that has no `skill_mapping` row. CV strings
are mapped and flagged through `map_strings(..., seen_in_cv=True)`, called
from `map-cv-skills`. Existing rows are never re-mapped implicitly, so a
human resolution is never overwritten.

`map_strings` works in chunks (100 strings by default). Per chunk it reads
the existing rows, runs the cascade, including the embedding calls, on a
read-only connection, then commits one short write transaction. No write
transaction is held open across Ollama calls, and a failed run (for
example an Ollama timeout) keeps every chunk already committed, so
re-running `map-skills` resumes where it stopped.

`--remap-unresolved` deletes rows with method `embedding`, or method
`none` with `review_status = 'open'` (never `rejected`, `resolved` or
`dismissed`), then maps again (used after re-embedding or changing the
threshold).

## Seed aliases — `config/skill_aliases.yml`

Committed, small, loaded into `skill_alias` (source `seed`) by the same
command that maps. Maps alias → ESCO `skill_id`, or to a `custom:<slug>`
entry with a canonical label for tools ESCO lacks. It contains the GCP
variants (this is what satisfies the "done when") plus a handful of
obvious cases (AWS, Kubernetes/k8s, PostgreSQL/Postgres). It grows via the
review page (source `review`), not by hand-editing.

Seed sync is an upsert that never overwrites a `review`-sourced row.

## JD skill extraction — `core/skills/jd_extract.py`

- New task `skill_extraction` in `config/llm_tasks.yml`: provider
  `ollama`, `llama3.1:8b`, prompt family `local`, `eval_metric: field_f1`,
  threshold 0.05. Local per DECISIONS.md §1 ("skill extraction: local,
  never migrates"). Prompt at `prompts/skill_extraction/local.v1.md`,
  loaded via the existing registry and called via `core.llm.gateway`.
- Input: the survivor description from `silver.job_survivorship` for each
  `job_group_id` with no `job_skill_extraction` row at the current prompt
  version. Same "only new work" pattern as `write_job_category`.
- Output: JSON list of `{skill, requirement_level}`, validated with a
  Pydantic model; a malformed response records nothing for that job (so it
  is retried next run) and does not fail the batch.
- **Level rule**: `nice_to_have` only on explicit hedging language
  ("nice to have", "bonus", "preferred", "a plus", "desirable"); a skill
  named in a requirements/responsibilities context without hedging is
  `must_have`. This is stated in the prompt and repeated in the golden set.
  Rationale: over-claiming must-haves widens the gap list (visible and
  correctable); under-claiming hides gaps.
- **No silent truncation.** Descriptions longer than the model's context
  window are split on paragraph boundaries and the per-chunk results
  unioned (a skill's level is `must_have` if any chunk says so). The plan
  pins the chunk size to the configured model.
- Duplicate skills within a job collapse on `raw_norm`, keeping
  `must_have` if either mention is.
- CLI: `pipeline extract-job-skills` then `pipeline map-skills`.

## CV side — `core/skills/cv_map.py`

`pipeline map-cv-skills --user-id <uuid>`: read the current truth base,
run each `skills[].name` through `map_skill`, set `canonical_id` where it
resolved and it is currently `None`. An existing `canonical_id`
(hand-corrected) is never overwritten. If anything changed, write it
through `core.cv.store.write_truth_base` with label
`"ESCO skill normalisation"`, giving a new version through the same
history-then-replace path, so the change is traceable and reversible like
any other edit. If nothing changed, no version is written (idempotent).
Bullet IDs are untouched. Unmapped CV skills stay `None` and appear in
the review list with `seen_in_cv = true`.

## `silver__bridge_job_skill` (dbt)

```
silver__skill:            skill_id, canonical_label, source ('esco'|'custom')
                          -- UNION of esco.skill and silver.custom_skill
silver__bridge_job_skill: grain (job_group_id, skill_id)
                          columns: job_group_id, skill_id, requirement_level,
                                   mention_count
```

Built from `job_skill_raw` (latest completed `prompt_version` per job)
joined to `skill_mapping` where `skill_id IS NOT NULL`. `requirement_level`
is `must_have` if any mapped mention is. Unmapped strings are excluded from
the bridge by design; they live in the review list, not in the mart.

dbt tests, per `sql-testing.md`: `unique` on a
`dbt_utils.unique_combination_of_columns(job_group_id, skill_id)`;
`not_null` on both keys; `relationships` for `skill_id` → `silver__skill`
and `job_group_id` → the `silver.job_survivorship` source;
`accepted_values` on `requirement_level`. `esco.skill`,
`silver.custom_skill`, `silver.skill_mapping`, `silver.job_skill_raw`,
`silver.job_skill_extraction` and `silver.job_survivorship` are
Python-written tables, read as plain `source()`s, following the
`silver.job_category` precedent.

## Review list

- **API** `apps/api/app/routers/skills.py`:
  `GET /skills/review` (open items: `raw_example`, JD frequency, sample
  `job_group_id`s, best-guess candidates from the embedding neighbours),
  `GET /skills/review/embedding-matches` (auto-mapped, lowest score
  first), `GET /skills/search?q=` (ESCO label search, for picking a
  target), and `POST` resolve / dismiss / reject.
- **Resolve** writes a `skill_alias` row (source `review`, plus a
  `custom_skill` row if the user marks it custom) and updates the
  `skill_mapping` row to `resolved` in one transaction. **Dismiss** sets
  `dismissed` and the string is never re-queued. **Reject** (on an
  embedding match) clears `skill_id`, keeps the rejected skill as
  `candidate_skill_id`, sets method `none` and `review_status = 'rejected'`
  (shown in the Unmapped tab, never re-mapped by `--remap-unresolved`), so a
  bad auto-match is correctable rather than
  silently permanent.
- **UI** `apps/ui/app/pages/6_Skill_Review.py`, following the
  Categorisation Review page: an "Unmapped" tab and an "Embedding matches
  — verify" tab. The second tab exists because a wrong embedding match is
  the main correctness risk in this design and would otherwise be
  invisible.

## Eval harness wiring (Step 12a)

- `_predict_skill_extraction` registered in `runner.py`'s `_PREDICTORS`.
  It flattens the extraction to one key per skill, `{"skill:<raw_norm>":
  "<requirement_level>"}`. `field_f1` then scores precision and recall over
  (skill, level) pairs with no new metric.
- `evals/golden/skill_extraction.yml`: at least 20 (`MINIMUM_GOLDEN_SET_SIZE`)
  **synthetic** JD snippets, none copied from real postings, covering
  hedging language, skills in prose vs bullets, abbreviations, and
  no-skills text.

## Loader — `core/skills/esco_load.py`

`pipeline load-esco <dir>` reads `skills_en.csv`, `occupations_en.csv` and
`occupationSkillRelations_en.csv` from the release directory, and fails
with a clear message naming any missing file. It is idempotent
(upsert on the primary keys) and records the row counts it loaded.
`pipeline embed-esco` is separate, so the loader needs no Ollama and can
run in CI on the synthetic fixture. Already-embedded skills with a matching
`embedding_model` are skipped; a changed `embedding_model` re-embeds all.

`data/esco/` is added to `.gitignore`, and the README notes the ESCO
attribution requirement. The real dataset is never committed.

## Testing

Per `python-testing.md`: `unittest`, real Postgres for integration tests,
no mocked DB.

- **Unit**: `normalise_skill` cases; cascade ordering with an in-memory
  fake `embed` (aliases beat labels, labels beat embeddings, threshold
  boundary); level-merging rule; chunk-and-union.
- **Integration** (real Postgres, tiny synthetic ESCO CSV fixture): loader
  idempotency and missing-file error; `map_pending` never overwrites a
  resolved row; embedding-model mismatch refusal; CV mapping writes a new
  truth-base version exactly once and preserves bullet IDs; review
  resolve/dismiss/reject transitions and CHECK constraints; the bridge's
  grain and must-have precedence.
- **The acceptance test**: "GCP", "Google Cloud" and "Google Cloud
  Platform" resolve to one `skill_id` through `map_skill`.
- **dbt**: the tests above, run against the fixture data.
- **Evals**: `run-evals skill_extraction` reports a real score (not
  `insufficient_data`).

## Risks

- **False embedding matches** (e.g. a tool name landing on an unrelated
  ESCO skill) are silent unless surfaced. Mitigations: conservative
  threshold, the verify tab, and `reject`.
- **ESCO coverage of tools is patchy** (the reason for `custom:` skills
  and the seed file). Emerging tools will land in review by design, as
  PLAN.md intends.
- **Local 8B model quality** for the must-have/nice-to-have call is
  unproven; the golden set is the measurement, and the level rule is
  written to fail toward visible gaps.
- **Shared aliases**: one user's resolution affects all users (accepted
  under Tenancy above; revisit if Step 22a introduces real multi-user use).

## Done when

"GCP", "Google Cloud" and "Google Cloud Platform" all resolve to one
ID; every JD skill has a must-have/nice-to-have level; unmapped skills
appear in the review list and never disappear silently;
`silver__bridge_job_skill` builds with its tests green; and
`run-evals skill_extraction` reports a real score.
