# CV extraction progress + timeout fix — design

JOB-202 follow-up. Continues the CV truth-base correction-pass work on
`feat/JOB-202-cv-truth-base`.

## Problem

`POST /cv/extract` ([apps/api/app/routers/cv.py](../../../apps/api/app/routers/cv.py))
runs Docling PDF→markdown conversion and one Ollama LLM call
synchronously inside a single request/response cycle. The Streamlit
page ([apps/ui/app/pages/5_CV_Correction.py](../../../apps/ui/app/pages/5_CV_Correction.py))
blocks on that one HTTP call with a 120s client timeout — the same
120s the API's own Ollama `httpx.Client` uses
([apps/api/app/dependencies.py](../../../apps/api/app/dependencies.py)).
Because Docling's parse time is added on top of the Ollama call before
the response returns, the outer 120s client timeout is virtually
guaranteed to fire before the Ollama call's own 120s budget would —
producing the generic `Extraction failed: timed out` with no
indication of which step was slow or how close it was to finishing.

This also means there is currently no way to show progress: it is one
opaque blocking round-trip.

## Goals

- Show step-by-step progress (Docling parse → LLM extraction → DB
  save) with per-step elapsed time, live, while extraction runs.
- Stop the UI's own HTTP timeout from being the thing that fails
  extraction — a slow-but-working extraction should finish
  server-side even if the browser gave up waiting on an earlier call.
- On genuine failure, report which step failed and why, not a bare
  "timed out".

## Non-goals

- No durability across API restarts (in-memory job state is
  acceptable — confirmed with the user; this is a single-user local
  app, not a multi-instance production service).
- No general-purpose background-job framework (Celery/RQ). Nothing
  else in this codebase needs one yet — introducing a broker service
  for one two-step pipeline would be pure overhead.
- No change to the actual extraction logic (Docling call, prompt,
  schema) — only how its execution is scheduled, observed, and
  reported.

## Architecture

### Job state — `packages/core/core/cv/jobs.py` (new)

```python
class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

@dataclass
class StepRecord:
    name: str                     # "parsing_document" | "extracting_fields" | "saving"
    status: StepStatus
    started_at: float | None = None    # time.monotonic()
    finished_at: float | None = None

@dataclass
class ExtractionJob:
    job_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    steps: list[StepRecord]
    result_version: int | None = None
    error: str | None = None
    failed_step: str | None = None
```

A module-level `dict[uuid.UUID, ExtractionJob]` plus a `threading.Lock`
holds all in-flight and recently-finished jobs — the same
process-lifetime-singleton pattern `extract.py` already uses for its
`DocumentConverter` (`_converter()`), just mutable. All reads and
mutations of a job happen while holding the lock; the status endpoint
returns a snapshot (steps with `duration_seconds` computed from
`time.monotonic()` deltas, `None`/elapsed-so-far for a still-running
step).

`run_extraction_job(job_id, file_bytes, filename, adapters, engine,
user_id)` is the pipeline itself, now living in `jobs.py` rather than
inline in the router: it steps through `parsing_document` (calls
`docling_to_markdown`), `extracting_fields` (calls
`extract_truth_base`), and `saving` (calls `write_truth_base`),
marking each `StepRecord` `running` → `done` as it goes. Any exception
(the same `DoclingError`/`ValueError` cases the router already
special-cases, plus a catch-all for anything unexpected) marks the job
`failed`, records which step it happened in, and stores the message —
no exception escapes to crash the background execution silently.

Jobs are not user-scoped in the registry (job_id is the only key
needed to poll); `write_truth_base` inside the runner still goes
through the normal `user_id`-scoped write path.

### API — `apps/api/app/routers/cv.py`

- `POST /cv/extract` now: reads the upload, creates an `ExtractionJob`
  in `queued` status, schedules `run_extraction_job` via FastAPI's
  `BackgroundTasks` (runs in the thread pool, off the event loop —
  the built-in mechanism for exactly this, no new dependency), and
  returns `202 {"job_id": ...}` immediately. The upload's bytes are
  read before returning so the request body isn't needed after the
  response is sent.
- `GET /cv/extract/jobs/{job_id}` — returns the job's current
  snapshot: `status`, `steps` (name/status/duration_seconds), and on
  `succeeded` a `version`, or on `failed` an `error` + `failed_step`.
  404 if `job_id` is unknown (never created, or lost to an API
  restart).

### UI — `apps/ui/app/pages/5_CV_Correction.py`

On "Extract": POST (short ~10s timeout — it now only waits for the
job to be created, not to finish) stores `job_id` in
`st.session_state`. On every rerun, if a `job_id` is present the page
polls `GET /cv/extract/jobs/{job_id}` (short timeout too) instead of
rendering the upload form, and shows an `st.status` block listing each
step: done steps get a checkmark and their duration, the running step
shows elapsed-so-far, pending steps are greyed out. While
`status in {"queued", "running"}`: `time.sleep(1)` then `st.rerun()`.
On `succeeded`: clear the session job_id, show the success message,
`st.rerun()` into the normal truth-base view. On `failed`: show the
error and failed step, clear the session job_id, leave the upload form
so the user can retry. A `404` from the status endpoint is treated the
same as `failed` with a "job lost, please retry" message, not a crash.

## Data flow

```
Upload → POST /cv/extract (<1s, returns job_id)
                │
                ▼
      BackgroundTasks runs run_extraction_job
      ┌─────────────────────────────────────┐
      │ parsing_document  (Docling)          │
      │ extracting_fields (Ollama LLM call)  │
      │ saving            (DB write)         │
      └─────────────────────────────────────┘
                │
   UI polls GET /cv/extract/jobs/{job_id} every ~1s
                │
      done → show result / error, stop polling
```

## Error handling

- `DoclingError` during `parsing_document` → job `failed`,
  `failed_step="parsing_document"`, same message the router formats
  today.
- `ValueError` during `extracting_fields` (LLM response doesn't parse
  into `CVTruthBase`) → job `failed`,
  `failed_step="extracting_fields"`.
- Any other exception → caught, job `failed` with the step it occurred
  in and `str(exc)` — prevents a silent background-task crash from
  leaving a job stuck `running` forever.
- The Ollama `httpx.Client`'s existing 120s timeout is unchanged. If
  it's genuinely exceeded, that now surfaces as a normal `failed` job
  on the `extracting_fields` step with the `httpx.TimeoutException`
  message, not an opaque top-level "timed out" from a client that was
  never the one actually waiting on the LLM.
- Unknown/expired `job_id` on the status endpoint → `404`, UI shows a
  retry prompt.

## Testing

- `packages/core/tests/test_cv_jobs.py` (new): step transitions
  through a happy path; failure attribution for a Docling error and
  for an LLM parse `ValueError`; duration is recorded and monotonic
  non-negative.
- `packages/core/tests/integration/test_cv_router.py`: extend for the
  new `202`/`job_id` response from `POST /cv/extract`, and the new
  `GET /cv/extract/jobs/{job_id}` — queued/running/succeeded/failed
  transitions, 404 for an unknown id.
- No automated test for the Streamlit polling UI (no existing
  precedent for UI tests in this repo). Verified manually via the
  `run` skill: upload a CV, confirm the step checklist updates with
  plausible per-step durations, confirm a forced Docling failure (bad
  file) and a forced LLM failure surface the right step + message.
