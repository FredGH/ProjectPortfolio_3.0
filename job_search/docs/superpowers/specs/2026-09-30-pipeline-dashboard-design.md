# Pipeline dashboard: unified status/control center for the whole workflow — design

No PLAN.md step id — this is tooling on top of Steps 2 through 16, not a
new capability of its own. No backlog id yet (Jira integration disabled,
see `docs/jira-integration-disabled.md`).

## Problem

The pipeline from raw job ingestion through calibrated scoring is ~20
CLI subcommands plus 4 human-review UI pages, wired together by
implicit knowledge of run order (PLAN.md's step numbers, or just
having built it). Concretely, during Step 16's own end-to-end smoke
test:

- Getting from an empty `scoring.job_score` to real all-four-present
  data meant hand-chaining 7 CLI commands in the right order, with no
  UI feedback on what had already run or what each one produced.
- Diagnosing why "no eligible jobs to label" required directly
  querying Postgres — there was no way to see, from the UI, that
  `score-similarity`'s top-200 cap and `score-skill-coverage`'s pool
  barely overlapped.
- Only one of ~20 automatable stages (`extract-job-skills`, via
  [7_Skill_Extraction_Runner.py](../../../apps/ui/app/pages/7_Skill_Extraction_Runner.py))
  has a UI trigger at all; the rest require a shell.

There is also no single place that shows "here is the whole workflow,
here is what state it's in, here is what needs your attention" —
whether that attention is a Run button or a human review queue.

## Goals

- One new UI page giving full visibility into every stage of the
  pipeline — automated (CLI-triggerable) and human-review alike — its
  last-run time, current status, and whether it's stale relative to
  its dependencies.
- Let every automatable stage be triggered from that page, with live
  progress and cancellation, the same safety properties
  [2026-09-23-skill-extraction-batch-runner-design.md](2026-09-23-skill-extraction-batch-runner-design.md)
  already established (Postgres-durable status, `BackgroundTasks`, no
  concurrent runs).
- Staleness is dependency-based: the dashboard knows the stage graph
  and flags a stage as stale when a declared upstream stage has
  completed more recently than it has — not merely "N days since last
  run."
- A user-picker for the per-user scoring-funnel stages (no real
  multi-user auth yet — Step 22a — so this is a plain dropdown over
  `app_user`, not an authenticated identity).
- The dashboard sits alongside the existing numbered pages, not in
  place of them: it shows state and links out; the actual review UIs
  (dedup review, categorisation review, skill review, CV editor,
  scoring calibration) are unchanged.
- The stage catalog this is built on becomes the enforced source of
  truth for "what the pipeline consists of": a test fails if a CLI
  subcommand exists with no matching catalog entry, so a newly added
  pipeline step can't be forgotten here. The README documents the full
  graph and this requirement.

## Non-goals

- No true content-aware staleness (row counts, hashes of what a stage
  actually processed). Confirmed explicitly: timestamp-order
  comparison against the declared dependency graph is enough for now.
  Documented in the README as an approximation, not hidden.
- No general-purpose job queue (Celery/RQ). Same non-goal as the
  skill-extraction runner, same reasoning: `BackgroundTasks` is enough
  at this scale.
- No orchestration/auto-chaining — the dashboard shows staleness, it
  does not automatically re-run a stale stage's whole upstream chain.
  The user presses Run per stage they choose.
- No replacement of any existing page's own UI. Manual Job Entry, all
  four review pages, CV Editor, and Scoring Calibration are untouched.
- No real authentication. The user-picker is a plain dropdown; per-user
  stages still ultimately answer to whichever `user_id` was picked,
  with no session/identity enforcement (matches this app's current
  pre-Step-22a state everywhere else).

## Stage catalog & dependency graph

Every stage is one of three kinds:

- **automated** — has a Run button, progress, and a "last completed"
  timestamp that feeds staleness for its downstream stages.
- **review** — a human queue (dedup review, categorisation review,
  skill review, CV editor). Shows a pending count and a link to its
  existing page. Does **not** carry a "last run" timestamp of its own
  for staleness purposes (a human queue has no single completion
  event) — see **Staleness computation** below for how it's skipped.
- **input** — Manual Job Entry. Listed for completeness, no status to
  track (it's a form, not a pipeline stage with a done/not-done state).

```
ingest ─┬─→ enrich-engagement-terms ─→ compute-blocking-keys ─→ compute-similarity-features
        │                                                              │
        │                                                              ▼
        │                                          compute-title-similarity-scores
        │                                                              │
        │                                                              ▼
        │                                          [Dedup Review — review]  (page 2)
        │                                                              │
        │                                                              ▼
        │                                        [Dedup Calibration — review]  (page 3)
        │                                                              │
        │                                                              ▼
        │                                                        cluster-jobs
        │                                                              │
        │                                                              ▼
        │                                                  compute-survivorship
        │                              (verified live: write_job_category selects
        │                               FROM silver.job_survivorship — a real, not
        │                               inferred, dependency)
        │                                                              │
        │                                                              ▼
        │                                                       classify-jobs
        │                                                              │
        │                                                              ▼
        │                                      [Categorisation Review — review]  (page 4)
        │
        ├─→ [CV Editor — review, input]  (page 5) ─→ map-cv-skills
        │
        ├─→ load-esco ─→ embed-esco ────────────────────────┐
        │                                                     ▼
        └─→ extract-job-skills ─→ map-skills ─→ llm-map-skills ─→ [Skill Review — review]  (page 6)
             (own runner already — page 7, migrated onto this
              design's generic table, see below)

── everything above feeds Step 15, per real user ──────────────────────

score-filter-jobs ─→ chunk-embed-jobs (shared, global) ─┬─→ score-similarity ─→ score-skill-coverage
map-cv-skills ─→ chunk-embed-cv (per-user) ─────────────┘         │                    │
                                                                    └─────────┬─────────┘
                                                                              ▼
                                                                       score-llm-rerank
                                                                              │
                                                                              ▼
                                                                        score-blend
                                                                              │
                                                                              ▼
                                                    [Scoring Calibration — review]  (page 9)
```

`load-esco`/`embed-esco` and `extract-job-skills` are independent of
each other until `map-skills` (confirmed with the project owner, not
inferred).

## Data model

### `pipeline.stage_run` (new, migration `0030`)

One generic table for every stage's run history — automated stages
only (review stages have no runs to track).

| column | type | notes |
|---|---|---|
| `run_id` | uuid, pk | |
| `stage` | text, not null | matches the CLI subcommand name exactly (`"classify-jobs"`, `"score-blend"`, ...) |
| `user_id` | uuid, nullable | set only for per-user stages (the Step 15 funnel) |
| `status` | text, not null | `running` \| `completed` \| `cancelled` \| `failed` |
| `params` | jsonb, not null, default `{}` | stage-specific input args (`top_n`, `sources`, `countries`, ...) |
| `progress_current` | int, nullable | null → UI shows a spinner, not a bar (not every stage can report fine-grained progress) |
| `progress_total` | int, nullable | |
| `cancel_requested` | bool, not null, default `false` | |
| `result` | jsonb, nullable | stage-specific outcome detail set on completion (e.g. `failed_count`, `mapping_summary` — what today's `skill_extraction_run` keeps in dedicated columns) |
| `error_message` | text, nullable | set only on `status = 'failed'` |
| `started_at` | timestamptz, not null, default `now()` | |
| `updated_at` | timestamptz, not null, default `now()` | bumped on every progress tick |
| `finished_at` | timestamptz, nullable | |

**Global lock, confirmed explicitly** (not per-stage, not per-user — a
single lock across the entire pipeline, same reasoning as the original
skill-extraction runner: running two Ollama-heavy stages at once has
already frozen the host once):

```sql
CREATE UNIQUE INDEX ux_stage_run_one_active
    ON pipeline.stage_run ((1))
    WHERE status = 'running';
```

A second `INSERT ... status='running'` — for *any* stage, not just the
same one — fails with a unique violation, which `start_run` turns into
`RunAlreadyActive`, which the router maps to `409`.

### Migrating `skill_extraction_run` onto this table

A true global lock requires exactly one lock mechanism. Leaving
`silver.skill_extraction_run` as a second, independent table with its
own partial-unique-index lock would mean two stages *could* run
concurrently — a dashboard-triggered stage and a skill-extraction
run — each satisfying its own table's lock while violating the
system-wide one. So this design retires `silver.skill_extraction_run`
and moves `extract-job-skills` onto `pipeline.stage_run`:

- `sources`/`countries` move into `params`.
- `extracted_count`/`total_pending` become `progress_current`/`progress_total`.
- `failed_count` and `mapping_summary` move into `result`.
- `cancel_requested`, `error_message`, the three timestamps, and the
  overall state machine are unchanged in spirit — same columns, new
  table.
- `packages/core/core/skills/extraction_run.py`'s actual batching
  logic (30-job sub-batches, the Ollama `keep_alive: 0` unload between
  batches, per-job progress/cancel checks) is **not** rewritten — only
  its persistence layer changes, from talking to
  `silver.skill_extraction_run` directly to going through the new
  generic `pipeline.core` read/write helpers (see below).
- `7_Skill_Extraction_Runner.py` is repointed at the new generic
  `/pipeline/stages/extract-job-skills/*` endpoints instead of
  `/skills/extraction-runs/*`; its own UI (source/country multi-select,
  progress display) is otherwise unchanged.
- `test_extraction_run.py` / `test_extraction_runs_router.py` are
  updated for the new table, not deleted — the behavior they pin
  (batching, unload, cancel-between-jobs) still needs proving.

This is real migration work, not a footnote — expect it to be one of
the implementation plan's larger tasks.

## Core module

### `packages/core/core/pipeline/registry.py` (new)

The stage catalog — the single source of truth this design keeps
promising. Two parallel catalogs:

```python
@dataclass(frozen=True)
class StageSpec:
    name: str                      # matches the CLI subcommand exactly
    depends_on: tuple[str, ...]    # other automated stage names (never review stages — see staleness)
    per_user: bool
    run: Callable[..., Any]        # the existing function this stage already calls
                                    # (write_job_category, compute_final_scores, ...)
    supports_progress: bool        # False -> progress_current/total stay null, UI shows a spinner

STAGES: dict[str, StageSpec] = {
    "ingest": StageSpec(depends_on=(), per_user=False, ...),
    ...
    "classify-jobs": StageSpec(depends_on=("compute-survivorship",), per_user=False, ...),
    ...
    "score-blend": StageSpec(
        depends_on=("score-similarity", "score-skill-coverage", "score-llm-rerank"),
        per_user=True, ...
    ),
}

@dataclass(frozen=True)
class ReviewStageSpec:
    name: str                       # display name, not a CLI subcommand
    depends_on: tuple[str, ...]     # automated stage names only
    page_path: str                  # e.g. "Dedup_Review_Queue" for the UI link
    pending_count: Callable[[Engine], int]

REVIEW_STAGES: dict[str, ReviewStageSpec] = {
    "dedup-review": ReviewStageSpec(depends_on=("compute-title-similarity-scores",), ...),
    ...
}
```

**Enforcement test** (`test_pipeline_registry.py`): every
`subparsers.add_parser(...)` name found in `apps/pipeline/app/cli.py`
has a matching `STAGES` entry, except a short, explicit,
named-in-the-test exclusion list (`run-evals` — developer tooling, not
a pipeline stage). `STAGES`/`REVIEW_STAGES` also get a no-cycles check
and a check that every `depends_on` name actually exists in `STAGES`.

### `packages/core/core/pipeline/runner.py` (new)

Generalizes `extraction_run.py`'s `start_run`/`run_loop`/
`get_active_run`/`request_cancel` to work against any `StageSpec`:

```python
def start_run(engine, *, stage: str, user_id: uuid.UUID | None, params: dict) -> uuid.UUID:
    """Inserts a `running` row; RunAlreadyActive on the global-lock violation."""

def run_loop(run_id: uuid.UUID, engine: Engine, spec: StageSpec, params: dict) -> None:
    """Calls spec.run(...) with progress/cancel hooks wired to this run_id's
    row, the same on_job_done/should_stop pattern extraction_run.py already
    proved. Marks completed/cancelled/failed exactly like today's run_loop."""

def get_active_run(engine: Engine) -> RunStatus | None: ...
def request_cancel(engine: Engine, run_id: uuid.UUID) -> None: ...
```

Stages whose underlying function has no natural per-item progress hook
(most of them — `compute_final_scores`, `write_job_category`, etc. run
to completion in one call) simply don't get `on_job_done` wired up;
`run_loop` marks them `completed` on return with `progress_current`/
`progress_total` left null. Only `extract-job-skills` (today) and
`score-llm-rerank` (each of its up-to-50 LLM calls is a natural
progress tick) get real progress bars at launch; everything else shows
a spinner. This is a property of each stage's own implementation, not
a limitation of the runner — a future stage can opt into fine-grained
progress by wiring a callback, same as these two.

### Staleness computation

```python
def compute_staleness(engine: Engine) -> dict[str, StageStatus]:
    """For every automated stage: its own last-completed-run timestamp
    (None if never run), and whether it's stale (any of its STAGES-graph
    dependencies has a more recent last-completed timestamp, or a
    dependency has never run at all -> 'blocked', a distinct state from
    'stale'). Review-stage edges in a dependency chain are skipped by
    walking to the nearest upstream *automated* stage -- e.g.
    cluster-jobs's declared dependency for staleness purposes is
    compute-title-similarity-scores, not "Dedup Review" (which has no
    completion timestamp to compare)."""
```

## API

New router, `apps/api/app/routers/pipeline.py`:

- `GET /pipeline/stages?user_id=` → every `StageSpec`/`ReviewStageSpec`
  merged with live state: automated stages get last-run time, status,
  staleness (and why — which dependency triggered it); review stages
  get their pending count. `user_id` is required — per-user stages'
  state (last run, staleness) depends on which user is selected;
  global stages' state ignores it. The dashboard always has a user
  selected (the picker's default), so this is never optional in
  practice.
- `POST /pipeline/stages/{stage}/run` (body: `user_id` if per-user,
  plus stage params) → `start_run` + `background_tasks.add_task`.
  `202` with `run_id`, `409` if anything is already running
  (system-wide), `400` if `user_id` is missing for a per-user stage or
  given for a global one.
- `GET /pipeline/stages/{stage}/active?user_id=` → progress snapshot or
  `null`.
- `POST /pipeline/stages/{stage}/cancel` → `request_cancel`; `404` if
  not the active run.
- `GET /pipeline/users` → `[{id, email, display_name}]` from `app_user`,
  for the picker.

## UI

### `apps/ui/app/pages/0_Pipeline_Dashboard.py` (new)

Numbered `0` so it's the first thing in the sidebar. Layout:

1. A user-picker (selectbox, `GET /pipeline/users`), kept in
   `session_state` so it survives reruns. Defaults to the row matching
   `DEV_USER_ID` if present.
2. Every stage from `GET /pipeline/stages`, grouped under phase headers
   matching PLAN.md's own step numbers (Ingestion & Dedup / Categorisation
   / CV & Skills / Scoring) — ~20 stages flat would be a wall of text.
3. Each automated-stage row: name, "last run: `<relative time>`" (or
   "never"), a status badge, a staleness badge with a caption naming
   the specific upstream stage responsible when stale, and a Run
   button — disabled with a tooltip ("needs `compute-survivorship` to
   run first") if a hard dependency has never completed. While
   running: a progress bar (or spinner if `progress_total` is null)
   and a Cancel button, replacing the Run button. Global one-run-at-a-
   time lock means every *other* stage's Run button is also disabled
   while anything runs, with a "pipeline busy: `<other stage>` is
   running" tooltip.
4. Each review-stage row: name, "`<N>` pending," a button/link to its
   existing page.
5. While any stage is `running`, the page polls (`time.sleep(5);
   st.rerun()`) — same interval and pattern as
   `7_Skill_Extraction_Runner.py`.

## Error handling

Identical shape to the skill-extraction runner, generalized:

- Starting while something else runs anywhere in the pipeline → `409`,
  UI shows that other run's progress instead of a form.
- A stale `running` row (API restarted mid-run) surfaced past a
  threshold as "possibly stalled — Cancel to clear it," not
  auto-cleared.
- A failed run shows `error_message`, dismissible, no auto-retry.
- A stage whose hard dependency has never run: Run button disabled
  with an explanatory tooltip, not merely a staleness badge (staleness
  implies "was once valid, now isn't"; "never run" is a stronger,
  distinct state).

## Testing

- `test_pipeline_registry.py`: catalog-completeness (every CLI
  subcommand has an entry or is on the named exclusion list), no
  cycles, every `depends_on` resolves.
- `test_pipeline_runner.py`: generic run mechanics against one cheap
  fake `StageSpec` (global lock via `RunAlreadyActive`, progress ticks,
  cancel-between-ticks, a forced failure) — not repeated per real
  stage, since each stage's own correctness is already covered by its
  existing tests; this only proves the generic harness.
- `test_pipeline_staleness.py`: fixture rows with controlled
  `finished_at` timestamps prove the dependency-order comparison and
  the review-stage-skipping behavior.
- `test_pipeline_router.py`: `409` on double-start (any stage),
  `202` → poll → `completed`, `404` on bad cancel, `400` on a
  missing/extra `user_id`.
- `test_extraction_run.py`/`test_extraction_runs_router.py`: updated
  for the new table (see migration section), not deleted.
- No automated UI test — matches this repo's convention for every
  Streamlit page.

## README

A new "Pipeline Dashboard" section: what it is, the full stage graph
(this doc's diagram, cleaned up), exactly how staleness is computed
(timestamp-order against the declared graph — explicitly *not*
content-aware, so a re-run that touches zero new rows still clears a
staleness flag), the review-stage-skip rule, and this requirement,
stated plainly: **every new pipeline CLI subcommand must be added to
`STAGES` (or the exclusion list) and, if it represents new
human-review work, `REVIEW_STAGES`, before it ships** — enforced by
`test_pipeline_registry.py`, not just documented.
