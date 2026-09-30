# Step 16 — Calibrate the scoring — design

## Purpose

Step 15 built a four-component scoring funnel (vector similarity, cross-
encoder rerank, skill coverage, LLM fit) and blends the present components
for every hard-filter-passing job. Until today the blend weighs every
component equally — a placeholder, not a calibration. PLAN.md's Step 16 is
explicit that an uncalibrated score "produces a confident-looking ordering
that's roughly random, and you won't notice because the numbers look
plausible." This spec builds the mechanism PLAN.md names verbatim:

1. Hand-label 30 jobs as strong / maybe / no.
2. Fit component weights against the labels.
3. Hold out 10 to validate rather than fit.
4. Record the agreement figure in the repo.

This mirrors Step 9's dedup calibration (hand-label → curve/fit → choose →
record a measured figure in PLAN.md) closely enough that Step 9's Streamlit
page (`3_Dedup_Calibration.py`) and its "nothing saved until you click
Save" pattern are the direct precedent for this page.

## Already decided (DECISIONS.md / PLAN.md, applied here, not reopened)

- Every calibration in the plan is provider-specific (DECISIONS.md): a
  future LLM-provider swap invalidates the LLM-fit component's calibration.
  Out of scope for this spec — noted for future-you, not solved here.
- `scoring.weight` (PK `user_id, component`, `CHECK component IN
  ('vector_similarity', 'reranker', 'skill_coverage', 'llm_fit')`) already
  exists (migration 0028) — this spec writes to it, never redefines it.
- `blend.py`'s fallback (equal weight when no `scoring.weight` row exists)
  stays exactly as-is — it's the correct pre-calibration behavior and
  remains correct as the fallback for any component a future recalibration
  drops a row for.
- `scoring.job_label` was deliberately NOT created in migration 0028 —
  reserved for this spec's own migration (0028's docstring says so
  explicitly).
- "Re-check calibration after any change to the embedding model" (PLAN.md)
  — addressed below via a stored `embedding_model` on every calibration
  run and a UI staleness warning, not by blocking anything mechanically.

## New decisions this spec makes

**Candidate pool for hand-labeling.** Only jobs where all four components
are present (`hard_filter_passed = true` AND `vector_similarity_score`,
`reranker_score`, `skill_coverage_score`, `llm_fit_score` all non-null) are
eligible — a job missing a component can't inform that component's weight.
Since `score-llm-rerank` caps at the top 50 pre-LLM-scored jobs per user
(Step 15), this pool is naturally small (≤50) regardless of pool size.
Candidates are drawn via stratified random sampling across score bands
(deciles of the *existing* equal-weight `final_score`) so labels span the
range rather than clustering at the top — the same "sample across the
score range, not just the obvious ends" principle Step 9 states explicitly
for pair labeling.

**Label scale and numeric encoding.** `strong` / `maybe` / `no`, stored as
text (matches PLAN.md's own words exactly, and mirrors `dedup.pair_labels`
storing `match`/`not_match` as text rather than an int enum). For fitting
and agreement math only, mapped to `strong=1.0, maybe=0.5, no=0.0` — an
ordinal scale, never persisted as the number.

**Fit/holdout split.** Once ≥30 jobs are labeled, a "Fit weights" action
draws a reproducible sample: `random.Random(0).sample(labeled_job_ids, 30)`
(if more than 30 are labeled — e.g. a relabel happened — the extra rows are
simply not used *this* run; they remain available for the next
recalibration). The 30 are split `sample[:20]` (fit) / `sample[20:]`
(holdout) — this order is what `random.Random(0).sample` returns, not a
second shuffle, so the split is fully reproducible from the label set
alone. No held-out job is ever used for fitting.

**Weight-fitting algorithm.** A grid search over the 4-weight simplex —
every 4-tuple of multiples of 0.05 in [0, 1] that sums to 1.0 (1,771
combinations; trivial to compute) — evaluated on the 20 fit-set jobs.
For each candidate weight vector, blend each fit-set job's four components
(reusing `blend.py`'s existing weighted-mean formula) and compute the
**Spearman rank correlation** (`scipy.stats.spearmanr`) between the 20
blended scores and the 20 numeric labels. The weight vector with the
highest correlation wins; ties are broken toward the smoothest distribution
(lowest max single weight, i.e. prefer not concentrating everything on one
component), then by iteration order for full determinism.

Rank correlation, not least-squares regression, because PLAN.md's own
"Done when" criterion is about ranking agreement ("top 10 by computed score
substantially matches top 10 by hand ranking"), not about predicting the
numeric label's exact value. `scipy` is already an installed transitive
dependency of `sentence-transformers` (verified live: 1.17.1) — this spec
adds it to `requirements.txt` explicitly rather than relying on an
undeclared transitive import.

**The recorded "agreement figure."** After fitting, the same fitted
weights are applied to the 10 **holdout** jobs (never seen during fitting),
and the Spearman correlation between their blended scores and their
numeric labels is the agreement figure — this is the honest generalization
measure "hold out 10 to validate rather than fit" calls for. With exactly
10 holdout jobs, ranking "the 10" *is* ranking PLAN.md's literal "top 10",
so this single number is a direct, unweasely answer to the "Done when"
question.

**Where it's recorded.** A new `scoring.calibration_run` table (per-user,
RLS, append-only history — same shape as `dedup.calibration_thresholds`
but scoped to a user since scoring weights are a personal preference, not
a shared fact). Columns: `id`, `user_id`, `fit_count`, `holdout_count`, one
numeric column per component weight, `holdout_agreement`, `embedding_model`
(copied from the fit-set job_score rows — see staleness check below),
`calibrated_by`, `calibrated_at`. Saving a calibration writes this table
**and** upserts `scoring.weight`'s four rows in one transaction — nothing
is written until the human clicks "Save weights" in the UI, exactly
mirroring Step 9's "nothing is saved until you click Save thresholds."

Once real jobs are labeled and a real run completes (this spec's plan
includes actually running it against the real pool, not just building the
mechanism), the measured agreement figure is additionally recorded as a
"**Measured (date):** ..." note under PLAN.md's Step 16 section, mirroring
Step 9's own recorded note.

**Embedding-model staleness check.** `scoring.calibration_run.embedding_model`
lets the UI compare the last saved run's embedding model against the
current `scoring.job_score.embedding_model` for hard-filter-passing jobs.
A mismatch means "the embedding model changed since you calibrated" —
PLAN.md's own words ("different vectors, different distances, invalid
weights") — surfaced as a warning banner, not a block; recalibrating is
always the human's call.

**No pipeline CLI subcommand.** Like Step 9's dedup calibration, this is a
human-in-the-loop, on-demand exercise driven entirely through the UI/API,
not a scheduled pipeline stage. Saving new weights takes effect the next
time `score-blend` runs for that user (documented in the UI's success
message) — no new coupling between the UI and the pipeline process.

## Data model (schema: `scoring`, migration 0029)

```sql
CREATE TABLE scoring.job_label (
    user_id     UUID NOT NULL REFERENCES app_user(id),
    job_group_id TEXT NOT NULL,
    label       TEXT NOT NULL CHECK (label IN ('strong', 'maybe', 'no')),
    labeled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, job_group_id)
);
-- RLS: user_id = current_setting('app.current_user_id', true)::uuid
-- GRANT SELECT, INSERT, UPDATE, DELETE TO job_search_app
--   (DELETE allowed: a human may want to un-label a mistaken entry)

CREATE TABLE scoring.calibration_run (
    id                        SERIAL PRIMARY KEY,
    user_id                   UUID NOT NULL REFERENCES app_user(id),
    fit_count                 INTEGER NOT NULL,
    holdout_count             INTEGER NOT NULL,
    vector_similarity_weight  NUMERIC NOT NULL,
    reranker_weight           NUMERIC NOT NULL,
    skill_coverage_weight     NUMERIC NOT NULL,
    llm_fit_weight            NUMERIC NOT NULL,
    holdout_agreement         NUMERIC NOT NULL,
    embedding_model           TEXT NOT NULL,
    calibrated_by             TEXT,
    calibrated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- RLS: user_id = current_setting('app.current_user_id', true)::uuid
-- GRANT SELECT, INSERT TO job_search_app (append-only, like calibration_thresholds)
-- Sequence grant for calibration_run_id_seq, same pattern as 0010
```

## Core module: `packages/core/core/scoring/calibration.py`

- `read_labels(engine, user_id) -> list[JobLabel]` — every label this user
  has recorded, newest first.
- `write_label(engine, user_id, job_group_id, label) -> None` — upsert
  (INSERT ... ON CONFLICT DO UPDATE), so relabeling a job overwrites rather
  than duplicating.
- `delete_label(engine, user_id, job_group_id) -> None` — supports
  un-labeling a mistake from the UI.
- `pick_labeling_candidate(engine, user_id) -> LabelCandidate | None` —
  the eligible pool (all four components present, `hard_filter_passed`)
  minus already-labeled `job_group_id`s, stratified by `final_score`
  decile, one random pick from a random non-empty decile. Returns `None`
  when nothing eligible remains unlabeled. `LabelCandidate` carries enough
  of `gold.dim_job` (title, company, location, engagement_type,
  description) and `scoring.job_score` (all four component scores,
  `llm_rationale`, `llm_missing_skills`, `llm_stretch_flag`) for the UI to
  show a human enough context to label confidently.
- `split_and_fit(engine, user_id, seed=0) -> CalibrationPreview` — raises
  `ValueError` if fewer than 30 labels exist; otherwise samples 30
  (`random.Random(seed)`), splits 20/10, grid-searches the fit set,
  computes holdout agreement. Writes nothing — a pure preview, same as
  `compute_precision_recall_curve` does for dedup.
- `save_calibration(engine, user_id, preview, calibrated_by=None) -> None`
  — persists `preview`'s weights to `scoring.weight` (4 upserts) and
  inserts one `scoring.calibration_run` row, in one `session_scope`
  transaction.

`_grid_search_weights(fit_rows, fit_labels) -> dict[str, float]` and
`_spearman_agreement(rows, labels, weights) -> float` are the two testable
units underneath `split_and_fit` — small, pure functions over in-memory
data (no DB), so their tests don't need `live_owner_engine()` at all.

## API: `apps/api/app/routers/scoring.py` (extended)

- `GET /scoring/labeling-candidate` → the next candidate from
  `pick_labeling_candidate`, or `204 No Content` when none remain.
- `GET /scoring/labels` → every label this user has recorded (for the
  progress counter and an "already labeled" list in the UI).
- `PUT /scoring/labels/{job_group_id}` → body `{"label": "strong"}`, upserts.
- `DELETE /scoring/labels/{job_group_id}` → un-labels.
- `POST /scoring/calibrate` → runs `split_and_fit`, returns the preview
  (weights, fit/holdout counts, holdout agreement) — no writes. `400` if
  fewer than 30 labels exist.
- `POST /scoring/calibration-runs` → body carries the previewed weights
  plus `calibrated_by`; calls `save_calibration`. Returns the saved run.
- `GET /scoring/calibration-runs` → history, newest first, for the page's
  "past runs" table and the staleness check.

Every endpoint uses `get_current_user_id` exactly like the rest of the
`scoring` router (Step 22a's local-dev override makes these testable and
usable today without waiting on Step 22).

## UI: `apps/ui/app/pages/9_Scoring_Calibration.py`

Two sections, following `3_Dedup_Calibration.py`'s structure:

1. **Hand-label jobs** — a user-manual expander explaining the label scale
   and why holdout matters; a progress line ("`N` / 30 labeled"); the
   current candidate's title/company/location/engagement type/description
   and (when present) the LLM rerank's rationale/missing-skills/stretch
   flag as labeling context; three buttons (Strong / Maybe / No) that
   write the label and immediately fetch the next candidate. An
   already-labeled list (job title + label + a small "un-label" control)
   below, for correcting mistakes.
2. **Fit & validate** — disabled with an explanatory caption until ≥30
   labels exist. A "Preview calibration" button calls `POST
   /scoring/calibrate` and displays the four fitted weights, fit/holdout
   counts, and the holdout agreement figure — nothing saved yet, exactly
   like the dedup page's live preview. A "Save weights" button then calls
   `POST /scoring/calibration-runs`. A staleness warning banner appears
   when the current `job_score.embedding_model` differs from the most
   recent saved run's. A history table below shows every past
   `calibration_run`.

## Testing plan

- `calibration.py`'s pure functions (`_grid_search_weights`,
  `_spearman_agreement`) get unit tests with hand-constructed component
  rows and labels — including a case where the correct weighting is
  obvious from the fixture (e.g. only `skill_coverage_score` correlates
  with the label) so the grid search's winner is asserted exactly, not
  just "some weight."
- `read_labels`/`write_label`/`delete_label` — integration tests against
  `live_owner_engine()`, `zzfixture`-scoped, mirroring
  `test_scoring_hard_filters.py`'s fixture-insert/cleanup pattern.
- `pick_labeling_candidate` — tests that it (a) never returns an already
  labeled job, (b) never returns a job missing any of the four components,
  (c) returns `None` once every eligible job is labeled.
- `split_and_fit` — tests the `ValueError` below 30 labels; tests that,
  given a fixed `seed`, the same label set always produces the same
  fit/holdout split (reproducibility is a documented guarantee); tests
  that no `job_group_id` appears in both the fit and holdout sets.
- `save_calibration` — tests that it writes exactly one `scoring.weight`
  row per component (upsert, not duplicate on a second save) and exactly
  one new `scoring.calibration_run` row per call (append-only history).
- Router tests (mirroring `test_scoring_hard_filters.py`'s owner-role
  fixture style, via `TestClient`) for each new endpoint's happy path and
  its `204`/`400` edge cases.
- End-to-end dogfood: after the real Step 15 pipeline is run for the real
  user (prerequisite to this spec, not part of it), actually hand-label 30
  real jobs, run a real calibration, and record the real agreement figure
  in PLAN.md — the same "Measured (date): ..." discipline Step 9 used.

## Review Focus

- **Fewer than 30 eligible jobs exist.** `pick_labeling_candidate` must
  return `None` cleanly (not raise) once the eligible pool is exhausted,
  and the UI must say plainly "no more jobs available to label" rather
  than erroring — the real pool being scored has a hard 50-job LLM-rerank
  cap, so exhausting it is a realistic, not a pathological, case.
- **Fewer than 30 total labels when "Fit weights" is clicked.** The API
  returns `400` with a clear message ("need at least 30 labels, have N");
  the UI disables the button rather than letting a click fail silently.
- **A job's component scores change between labeling and fitting** (e.g. a
  re-run of `score-blend` after new `score-llm-rerank` data). `split_and_fit`
  reads current `scoring.job_score` values at fit time, not a snapshot
  taken when the label was written — labels are about the *job*, weights
  are fit against *current* scores, which is the only way recalibration
  after a re-score stays meaningful.
- **All labels are the same value** (e.g. every job labeled "strong").
  Spearman correlation against a constant array is undefined (zero
  variance) — `_spearman_agreement` and the grid search must handle this
  without a crash (scipy returns `nan`; treat as "no signal," report
  clearly in the UI rather than showing a misleading number).
- **A job_group_id disappears from `gold.dim_job`** between being labeled
  and a later fit run (a dbt rebuild changed job identity). `split_and_fit`
  must skip labels whose job no longer has a `scoring.job_score` row rather
  than crashing the whole calibration — this shrinks the effective label
  count, which should surface as a lower `fit_count`/`holdout_count`, not
  a 500.

---
