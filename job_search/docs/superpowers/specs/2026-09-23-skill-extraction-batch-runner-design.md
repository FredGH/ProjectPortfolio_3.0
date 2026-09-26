# Skill-extraction batch runner: UI-triggered, Docker-safe, resumable — design

Step 14 (ESCO skill normalisation) follow-up. No backlog id yet — to be
assigned via `jira-log` when implementation starts.

## Problem

Running `extract-job-skills` at any real scale currently means a human
babysitting a shell script:
[scripts/extract_in_batches.sh](../../../scripts/extract_in_batches.sh)
wraps the CLI in bounded sub-batches, calling `ollama stop` and pausing
between each, because one unbroken ~50-job run grew Ollama's resident
`llama-server` process from ~5.0 GB to 9.31 GB and froze the host
machine solid (confirmed via macOS's jetsam memory-pressure report).
The script:

- requires a native `venv/` and the `ollama` CLI on the host — it does
  not run inside the `pipeline` container (no venv, no `ollama` CLI
  there), so it offers no protection when Ollama is Docker-hosted;
- has no UI — starting a scoped run (by source, by country) means
  editing a command line;
- has no persisted status — progress is whatever scrolled past in the
  terminal.

## Goals

- Trigger a scoped extraction run (by source, by country, multi-select
  of each) from the Streamlit UI, mirroring how
  [6_Skill_Review.py](../../../apps/ui/app/pages/6_Skill_Review.py)
  already talks to the API.
- Keep the same bounded-sub-batch-plus-Ollama-unload protection that
  makes the shell script safe, but make it work identically whether
  Ollama is native or Docker-hosted — replacing the `ollama stop` CLI
  call with Ollama's own HTTP unload (`POST /api/generate` with
  `"keep_alive": 0`, no `prompt`), which only needs network reach to
  `ollama_base_url` and nothing host-specific.
- Show live progress and let the user cancel a run in flight.
- Survive an API process restart without losing track of a run's
  status (see **Why Postgres, not in-memory** below — this is the one
  place this design deliberately diverges from the closest precedent
  in this codebase).

## Non-goals

- No general-purpose background-job framework (Celery/RQ, a queue).
  FastAPI's `BackgroundTasks` is enough, matching the precedent set by
  [2026-09-14-cv-extraction-progress-design.md](2026-09-14-cv-extraction-progress-design.md).
- No user-configurable batch size or pause duration — fixed at the
  values already proven safe (30 jobs, 10s pause — the
  `scripts/extract_in_batches.sh` defaults), so the UI can't be used to
  accidentally recreate the original freeze.
- No concurrent runs. One active run globally, enforced at the database
  level.
- `scripts/extract_in_batches.sh` is not deleted — it still works for
  a native-Ollama, CLI-only workflow, and needs no changes.

## Why Postgres, not in-memory

The closest precedent, JOB-202's CV-extraction-progress work
([2026-09-14-cv-extraction-progress-design.md](2026-09-14-cv-extraction-progress-design.md)),
explicitly chose in-memory job state (a module-level dict) and named
"no durability across API restarts" as a non-goal — reasonable there,
since a CV extraction job takes minutes. A skill-extraction run over an
unfiltered scope can take hours; losing status on an API restart mid-run
(this API container has already needed a rebuild once this week, for
the httpx/anthropic/docling fix) would leave the user unable to tell
whether a multi-hour run needs restarting from scratch. A small
Postgres table costs little and makes that failure mode a non-issue —
the extraction data itself was never at risk either way, since
`write_job_skills` commits each job independently, but *knowing* where
a run stands is worth persisting here in a way it wasn't there.

## Architecture

### Data model — `silver.skill_extraction_run` (new, migration `0025`)

| column | type | notes |
|---|---|---|
| `run_id` | uuid, pk | |
| `status` | text | `running` \| `completed` \| `cancelled` \| `failed` |
| `sources` | text[], nullable | `NULL` = every source |
| `countries` | text[], nullable | `NULL` = every country |
| `total_pending` | int | snapshot of `count_pending_jobs(...)` at start |
| `extracted_count` | int | running total, updated after each sub-batch |
| `failed_count` | int | running total, updated after each sub-batch |
| `cancel_requested` | bool | default `false` |
| `error_message` | text, nullable | set only on `status = failed` |
| `started_at` | timestamptz | |
| `updated_at` | timestamptz | bumped after each sub-batch |
| `finished_at` | timestamptz, nullable | |

A partial unique index —
`CREATE UNIQUE INDEX ... ON silver.skill_extraction_run ((1)) WHERE status = 'running'`
— enforces "at most one active run" at the database level: a second
`INSERT ... status='running'` fails with a unique-violation, which
`start_run()` turns into a typed error the router maps to `409`.

### Core module — `packages/core/core/skills/extraction_run.py` (new)

```python
def start_run(
    engine: Engine,
    *,
    sources: list[str] | None,
    countries: list[str] | None,
) -> uuid.UUID:
    """Insert a `running` row; raises RunAlreadyActive on the unique-index
    violation. Returns the new run_id."""

def run_loop(
    run_id: uuid.UUID,
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    ollama_base_url: str,
    ollama_model: str,
    http_client: httpx.Client,
) -> None:
    """The background task body. Repeats:
      1. write_job_skills(engine, adapters=adapters, limit=30,
         sources=..., countries=...) -> WriteSummary
      2. update the run row: extracted_count += ..., failed_count += ...,
         updated_at = now()
      3. POST {ollama_base_url}/api/generate {"model": ollama_model,
         "keep_alive": 0} to unload the model
      4. sleep 10s
      5. re-read cancel_requested; if set, mark `cancelled` and stop
      6. if count_pending_jobs(...) == 0, mark `completed` and stop
    Any exception escaping a sub-batch (not an individual job — those
    are already caught inside write_job_skills) marks the run `failed`
    with error_message = str(exc) and stops; never leaves a row stuck
    `running` on an unhandled error.
    """

def get_active_run(engine: Engine) -> RunStatus | None: ...
def request_cancel(engine: Engine, run_id: uuid.UUID) -> None: ...
def list_filter_options(engine: Engine) -> FilterOptions:
    """Distinct apply_source_name / country_iso among pending jobs,
    for the UI's multi-selects."""
```

Kept independent of FastAPI so `run_loop` can be integration-tested
directly, the same separation
[2026-09-14-cv-extraction-progress-design.md](2026-09-14-cv-extraction-progress-design.md)
used for `run_extraction_job` in `core/cv/jobs.py`.

### API — new router `apps/api/app/routers/extraction_runs.py`

- `GET /skills/extraction-runs/filters` → `{"sources": [...], "countries": [...]}`
- `GET /skills/extraction-runs/pending-count?sources=&countries=` → live
  count for the current selection (lets the UI show "N jobs match"
  before starting)
- `POST /skills/extraction-runs` (body: `sources`, `countries`) →
  `start_run`, then `background_tasks.add_task(run_loop, ...)`; `202`
  with `run_id`, or `409` if one is already active
- `GET /skills/extraction-runs/active` → the active run's snapshot, or
  `null`
- `POST /skills/extraction-runs/{run_id}/cancel` → `request_cancel`;
  `404` if `run_id` isn't the active run

### UI — `apps/ui/app/pages/7_Skill_Extraction_Runner.py` (new)

Multi-select sources, multi-select countries (options from `/filters`),
a live "`N` jobs match this scope" line (from `/pending-count`), a
Start button. If `/active` returns a run, the page renders its progress
(`extracted_count` / `total_pending`, `failed_count`, elapsed time) and
a Stop button instead of the selection form, and — while
`status == "running"` — does `time.sleep(5); st.rerun()` to poll, the
same pattern `5_CV_Correction.py` uses at a 1s interval (5s here, since
a sub-batch takes minutes, not the CV job's seconds). A short user-guide
blurb matches `6_Skill_Review.py`'s style. A `running` row whose
`updated_at` is more than 2 minutes stale is shown as "possibly
stalled (API may have restarted) — Cancel to clear it" so a genuine
stuck run can't permanently block new ones without requiring
Postgres access to fix.

## Data flow

```
UI: select sources/countries → POST /skills/extraction-runs
                │
                ▼
      start_run() inserts status='running' row (409 if one exists)
      BackgroundTasks schedules run_loop(run_id, ...)
      API returns 202 {run_id} immediately
                │
                ▼
      run_loop, repeating until done/cancelled/failed:
      ┌────────────────────────────────────────────┐
      │ write_job_skills(limit=30, sources, countries)│
      │ update run row (counts, updated_at)          │
      │ POST /api/generate {keep_alive: 0}  (unload) │
      │ sleep 10s                                    │
      └────────────────────────────────────────────┘
                │
   UI polls GET /skills/extraction-runs/active every ~5s
                │
      completed/cancelled/failed → show final state, stop polling
```

## Error handling

- Starting a run while one is active → `409`, UI shows the existing
  run's progress instead of a form.
- A sub-batch's hard failure (DB connection lost, Ollama unreachable
  for the unload call) → run marked `failed`, `error_message` set, UI
  shows it and offers to dismiss (no automatic retry — matches
  `write_job_skills`' own "failed jobs are retried next run" model:
  the *jobs* already extracted are safe, only the run bookkeeping
  stops).
- Cancellation is checked between sub-batches only — a sub-batch of 30
  always finishes before a cancel takes effect, consistent with "safe
  to interrupt, each job commits on its own" already documented for
  `write_job_skills`.
- A stale `running` row (API restarted mid-run) is surfaced, not
  auto-cleared — see the UI section above. Cancelling it just updates
  the row; it does not touch any already-written skill data.

  **Correction (found during live verification, 2026-09-23):** for a
  genuinely orphaned run (the API process that was running it is gone),
  "Cancel"/"Stop" does NOT clear the row — it only sets a flag nothing is
  left to observe. See README.md's "Running a batch from the UI instead
  of the shell script" section for the real behavior and the manual
  recovery step.

## Testing

- `packages/core/tests/integration/test_extraction_run.py` (new, real
  dev DB per this repo's no-mocking rule): `start_run` raises
  `RunAlreadyActive` when one is active; `run_loop` advances counts
  across sub-batches and reaches `completed`; `cancel_requested` stops
  it after the in-flight sub-batch; a forced failure (bad
  `ollama_base_url`) marks the run `failed` with a message. The Ollama
  unload call is asserted via a fake transport, mirroring how
  `jd_extract.py`'s own tests avoid needing a real model.
- `packages/core/tests/integration/test_extraction_runs_router.py`
  (new): `409` on double-start, `202` + polling to `completed` on a
  small real scope, `404` on cancelling an unknown/inactive run.
- No automated UI test (no precedent for one in this repo — same as
  `5_CV_Correction.py`). Verified manually via the `run` skill: start a
  small scoped run, watch progress update, cancel mid-run, confirm a
  second Start is blocked while one is active and re-enabled after.

## Interaction with the Step 14 leftovers

This closes two of the three items tracked in memory
(`job_search_step14_leftovers_2026-09-23`): the Docker-incompatible
batch-restart script, and the missing UI trigger. The third (the
2,634-item skill-review backlog) is unrelated — no change to that from
this work.

## Update 2026-09-26 — per-job progress, cancel and auto-mapping

Live use showed the per-sub-batch design was too coarse: progress sat at 0 for
20-75 minutes and Stop could take as long. Changed since this spec was written:

- `write_job_skills` takes `on_job_done` / `should_stop` hooks; `run_loop`
  commits counts and bumps `updated_at` after **every job** and checks the cancel
  flag **before every job**. Stop now halts within one job (measured: 37 s).
- The stale-run threshold dropped from 2 hours to 30 minutes, since a live run now
  heartbeats per job.
- A completed run maps its new skills to ESCO automatically
  (`core.skills.post_run_mapping`, result in `silver.skill_extraction_run.
  mapping_summary`, migration 0026). The dbt bridge refresh stays manual — dbt
  cannot run inside the API image.
- A run whose only remaining jobs keep failing now **completes** (they stay
  pending) if it had extracted anything; it **fails** only if it never made progress.

See README.md's "Running a batch from the UI" for the current behaviour.
