# CV Extraction Progress + Timeout Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `POST /cv/extract`'s single blocking call with a background job the UI polls, showing a live per-step progress checklist (Docling parse → LLM extraction → DB save) with per-step timing, and eliminating the premature client-timeout that currently produces the opaque "Extraction failed: timed out".

**Architecture:** `POST /cv/extract` creates an in-memory `ExtractionJob`, schedules the real pipeline via FastAPI `BackgroundTasks`, and returns the job id immediately. `GET /cv/extract/jobs/{job_id}` reports the job's live state. The Streamlit page polls that endpoint with a `time.sleep(1)` + `st.rerun()` loop and renders an `st.status` step checklist.

**Tech Stack:** Python 3.11, FastAPI (`BackgroundTasks`), Streamlit 1.39, `unittest`/`coverage`, existing Docling + Ollama pipeline (unchanged).

**Spec:** [docs/superpowers/specs/2026-09-14-cv-extraction-progress-design.md](../specs/2026-09-14-cv-extraction-progress-design.md)

## Global Constraints

- No durability across API restarts — an in-memory job registry is acceptable (single-user local app; confirmed with the user).
- No general-purpose background-job framework (no Celery/RQ/broker service) — one process, one `BackgroundTasks` call.
- No change to the extraction logic itself (`docling_to_markdown`, `extract_truth_base`, their prompt/schema) — only how execution is scheduled and observed.
- Python 3.11. Black line length 88. `isort` (black profile). `ruff` clean.
- Google-style docstrings (`Args`/`Returns`/`Raises`) required on every function and class, public and private.
- Type hints required on all public signatures; `from __future__ import annotations` at the top of every module.
- No bare `except:` — always name the exception type (a broad `except Exception as exc:  # noqa: BLE001` is the codebase's existing convention for "must not silently crash a background task").
- Tests: `unittest` + `coverage`, run from `packages/core/`: `coverage run -m unittest discover && coverage report -m`. Integration tests (anything touching Postgres) go in `packages/core/tests/integration/` and must use a real connection — never mock the database.

---

## Task 1: Extraction job state (`core.cv.jobs`)

**Files:**
- Create: `packages/core/core/cv/jobs.py`
- Test: `packages/core/tests/test_cv_jobs.py`

**Interfaces:**
- Consumes: `core.cv.extract.docling_to_markdown(file_bytes: bytes, filename: str) -> str`, `core.cv.extract.extract_truth_base(markdown: str, *, adapters: dict[str, LLMAdapter]) -> CVTruthBase`, `core.cv.store.write_truth_base(engine: Engine, user_id: uuid.UUID, extracted_markdown: str, truth_base: CVTruthBase) -> int`, `core.llm.types.LLMAdapter`, `docling.exceptions.BaseError`.
- Produces (used by Task 2): `STEP_NAMES: tuple[str, ...]` = `("parsing_document", "extracting_fields", "saving")`; `class StepStatus(str, Enum)` with members `PENDING`, `RUNNING`, `DONE`, `FAILED`; `class StepRecord` (fields `name: str`, `status: StepStatus`, `started_at: float | None`, `finished_at: float | None`, plus a `duration_seconds: float | None` property); `class ExtractionJob` (fields `job_id: uuid.UUID`, `status: Literal["queued","running","succeeded","failed"]`, `steps: list[StepRecord]`, `result_version: int | None`, `error: str | None`, `failed_step: str | None`); `create_job() -> uuid.UUID`; `get_job(job_id: uuid.UUID) -> ExtractionJob | None`; `run_extraction_job(job_id: uuid.UUID, file_bytes: bytes, filename: str, *, adapters: dict[str, LLMAdapter], engine: Engine, user_id: uuid.UUID) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `packages/core/tests/test_cv_jobs.py`:

```python
"""Tests for core.cv.jobs — the in-memory extraction job registry and
the pipeline runner that drives it. Docling/LLM/DB calls are patched
at the module level (`core.cv.jobs.docling_to_markdown`, etc.) rather
than run for real: this module tests orchestration (step ordering,
timing, failure attribution), not Docling/Ollama/Postgres themselves —
those are covered by test_cv_extract.py and the router's integration
tests.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from docling.exceptions import BaseError as DoclingError

from core.cv.jobs import STEP_NAMES, StepStatus, create_job, get_job, run_extraction_job
from core.cv.schema import CVTruthBase

_ADAPTERS: dict = {}
_ENGINE = object()
_USER_ID = uuid.uuid4()


class TestCreateJob(unittest.TestCase):
    def test_new_job_starts_queued_with_pending_steps_in_order(self) -> None:
        job_id = create_job()
        job = get_job(job_id)
        self.assertEqual(job.status, "queued")
        self.assertEqual([step.name for step in job.steps], list(STEP_NAMES))
        for step in job.steps:
            self.assertEqual(step.status, StepStatus.PENDING)
            self.assertIsNone(step.started_at)
            self.assertIsNone(step.duration_seconds)

    def test_unknown_job_id_returns_none(self) -> None:
        self.assertIsNone(get_job(uuid.uuid4()))


class TestRunExtractionJob(unittest.TestCase):
    @patch("core.cv.jobs.write_truth_base")
    @patch("core.cv.jobs.extract_truth_base")
    @patch("core.cv.jobs.docling_to_markdown")
    def test_successful_run_marks_all_steps_done_and_records_durations(
        self, mock_docling, mock_extract, mock_write
    ) -> None:
        mock_docling.return_value = "# Jane Doe"
        mock_extract.return_value = CVTruthBase(identity="Jane Doe", headline="Engineer")
        mock_write.return_value = 3

        job_id = create_job()
        run_extraction_job(
            job_id, b"bytes", "cv.pdf", adapters=_ADAPTERS, engine=_ENGINE, user_id=_USER_ID
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.result_version, 3)
        for step in job.steps:
            self.assertEqual(step.status, StepStatus.DONE)
            self.assertIsNotNone(step.duration_seconds)
            self.assertGreaterEqual(step.duration_seconds, 0.0)
        mock_write.assert_called_once_with(_ENGINE, _USER_ID, "# Jane Doe", mock_extract.return_value)

    @patch("core.cv.jobs.docling_to_markdown")
    def test_docling_failure_marks_job_failed_at_parsing_document(self, mock_docling) -> None:
        mock_docling.side_effect = DoclingError("boom")

        job_id = create_job()
        run_extraction_job(
            job_id, b"bytes", "cv.pdf", adapters=_ADAPTERS, engine=_ENGINE, user_id=_USER_ID
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.failed_step, "parsing_document")
        self.assertIn("boom", job.error)
        steps_by_name = {step.name: step for step in job.steps}
        self.assertEqual(steps_by_name["parsing_document"].status, StepStatus.FAILED)
        self.assertEqual(steps_by_name["extracting_fields"].status, StepStatus.PENDING)
        self.assertEqual(steps_by_name["saving"].status, StepStatus.PENDING)

    @patch("core.cv.jobs.extract_truth_base")
    @patch("core.cv.jobs.docling_to_markdown")
    def test_llm_parse_failure_marks_job_failed_at_extracting_fields(
        self, mock_docling, mock_extract
    ) -> None:
        mock_docling.return_value = "# Jane Doe"
        mock_extract.side_effect = ValueError("bad json")

        job_id = create_job()
        run_extraction_job(
            job_id, b"bytes", "cv.pdf", adapters=_ADAPTERS, engine=_ENGINE, user_id=_USER_ID
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.failed_step, "extracting_fields")
        self.assertIn("bad json", job.error)
        steps_by_name = {step.name: step for step in job.steps}
        self.assertEqual(steps_by_name["parsing_document"].status, StepStatus.DONE)
        self.assertEqual(steps_by_name["extracting_fields"].status, StepStatus.FAILED)
        self.assertEqual(steps_by_name["saving"].status, StepStatus.PENDING)

    @patch("core.cv.jobs.write_truth_base")
    @patch("core.cv.jobs.extract_truth_base")
    @patch("core.cv.jobs.docling_to_markdown")
    def test_saving_failure_marks_job_failed_at_saving(
        self, mock_docling, mock_extract, mock_write
    ) -> None:
        mock_docling.return_value = "# Jane Doe"
        mock_extract.return_value = CVTruthBase(identity="Jane Doe", headline="Engineer")
        mock_write.side_effect = RuntimeError("db down")

        job_id = create_job()
        run_extraction_job(
            job_id, b"bytes", "cv.pdf", adapters=_ADAPTERS, engine=_ENGINE, user_id=_USER_ID
        )

        job = get_job(job_id)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.failed_step, "saving")
        self.assertEqual(job.error, "db down")
        steps_by_name = {step.name: step for step in job.steps}
        self.assertEqual(steps_by_name["parsing_document"].status, StepStatus.DONE)
        self.assertEqual(steps_by_name["extracting_fields"].status, StepStatus.DONE)
        self.assertEqual(steps_by_name["saving"].status, StepStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `packages/core/`): `coverage run -m unittest tests.test_cv_jobs -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'core.cv.jobs'`

- [ ] **Step 3: Write the implementation**

Create `packages/core/core/cv/jobs.py`:

```python
"""In-memory extraction job tracking (JOB-202 progress work): lets
`POST /cv/extract` return immediately with a job id while
`run_extraction_job` does the real work — Docling parse, LLM call, DB
write — off the request, recording per-step timing and failure
attribution as it goes. State lives only for the API process's
lifetime; see the design spec's Non-goals for why that's acceptable
here (docs/superpowers/specs/2026-09-14-cv-extraction-progress-design.md).
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, TypeVar

from docling.exceptions import BaseError as DoclingError
from sqlalchemy import Engine

from core.cv.extract import docling_to_markdown, extract_truth_base
from core.cv.schema import CVTruthBase
from core.cv.store import write_truth_base
from core.llm.types import LLMAdapter

STEP_NAMES: tuple[str, ...] = ("parsing_document", "extracting_fields", "saving")

_StepResult = TypeVar("_StepResult")


class StepStatus(str, Enum):
    """One extraction step's lifecycle state."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class StepRecord:
    """Timing and status for one step of an extraction job.

    Attributes:
        name: The step's identifier — one of `STEP_NAMES`.
        status: The step's current `StepStatus`.
        started_at: `time.monotonic()` when the step began, or None if
            it hasn't started yet.
        finished_at: `time.monotonic()` when the step ended (whether it
            succeeded or failed), or None while still running or pending.
    """

    name: str
    status: StepStatus
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def duration_seconds(self) -> float | None:
        """Elapsed time for this step.

        Returns:
            None if the step hasn't started. The running total
            (`time.monotonic()` minus `started_at`) if it's still in
            progress. The final duration once `finished_at` is set.
        """
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


@dataclass
class ExtractionJob:
    """One CV extraction run's full state.

    Attributes:
        job_id: The job's unique id.
        status: "queued" until the background task starts, then
            "running", then "succeeded" or "failed".
        steps: One `StepRecord` per `STEP_NAMES` entry, in order.
        result_version: The new truth-base version, set on success.
        error: The failure message, set on failure.
        failed_step: Which step's name failed, set on failure.
    """

    job_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    steps: list[StepRecord] = field(default_factory=list)
    result_version: int | None = None
    error: str | None = None
    failed_step: str | None = None


_JOBS: dict[uuid.UUID, ExtractionJob] = {}
_LOCK = threading.Lock()


def create_job() -> uuid.UUID:
    """Register a new queued extraction job.

    Returns:
        The new job's id, with one pending `StepRecord` per `STEP_NAMES`
        entry already in place.
    """
    job_id = uuid.uuid4()
    job = ExtractionJob(
        job_id=job_id,
        status="queued",
        steps=[
            StepRecord(name=name, status=StepStatus.PENDING) for name in STEP_NAMES
        ],
    )
    with _LOCK:
        _JOBS[job_id] = job
    return job_id


def get_job(job_id: uuid.UUID) -> ExtractionJob | None:
    """Return a point-in-time snapshot of a job's state.

    Args:
        job_id: The job to look up.

    Returns:
        A deep copy of the job's current state, or None if `job_id` is
        unknown (never created, or lost to an API restart).
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        return copy.deepcopy(job) if job is not None else None


def _update_step(job_id: uuid.UUID, step_name: str, **fields: object) -> None:
    """Mutate one step's fields on the job stored in the registry.

    Args:
        job_id: The job whose step to update.
        step_name: Which step — must match a `STEP_NAMES` entry.
        **fields: `StepRecord` attribute names/values to set.
    """
    with _LOCK:
        job = _JOBS[job_id]
        for step in job.steps:
            if step.name == step_name:
                for key, value in fields.items():
                    setattr(step, key, value)
                return


def _run_step(
    job_id: uuid.UUID, step_name: str, func: Callable[[], _StepResult]
) -> _StepResult:
    """Run one pipeline step, recording its timing and outcome.

    Args:
        job_id: The job this step belongs to.
        step_name: Which step — must match a `STEP_NAMES` entry.
        func: The zero-argument callable that does the step's work.

    Returns:
        `func`'s return value.

    Raises:
        Exception: Whatever `func` raises, after marking the step
            failed — the caller decides how to translate it into the
            job's overall failure state.
    """
    _update_step(
        job_id, step_name, status=StepStatus.RUNNING, started_at=time.monotonic()
    )
    try:
        result = func()
    except Exception:
        _update_step(
            job_id, step_name, status=StepStatus.FAILED, finished_at=time.monotonic()
        )
        raise
    _update_step(
        job_id, step_name, status=StepStatus.DONE, finished_at=time.monotonic()
    )
    return result


def _fail_job(job_id: uuid.UUID, step_name: str, message: str) -> None:
    """Mark a job failed at a specific step.

    Args:
        job_id: The job to mark failed.
        step_name: Which step failed.
        message: The failure message to surface to the caller.
    """
    with _LOCK:
        job = _JOBS[job_id]
        job.status = "failed"
        job.failed_step = step_name
        job.error = message


def run_extraction_job(
    job_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    *,
    adapters: dict[str, LLMAdapter],
    engine: Engine,
    user_id: uuid.UUID,
) -> None:
    """Run the full CV extraction pipeline for one job.

    Scheduled as a FastAPI `BackgroundTasks` callback by `POST
    /cv/extract` — runs off the request/response cycle so the caller
    never blocks on Docling parsing or the LLM call. Updates the job's
    steps and final status as it progresses; never raises (any
    exception from a step ends the job in "failed" status instead).

    Args:
        job_id: The job to run — must already exist via `create_job`.
        file_bytes: The uploaded CV document's raw bytes.
        filename: The original filename (its extension tells Docling
            which format to parse).
        adapters: Every available LLM adapter, keyed by provider.
        engine: The app-role (RLS-enforced) database engine.
        user_id: The user this CV belongs to.
    """
    with _LOCK:
        _JOBS[job_id].status = "running"

    try:
        markdown = _run_step(
            job_id,
            "parsing_document",
            lambda: docling_to_markdown(file_bytes, filename),
        )
    except DoclingError as exc:
        _fail_job(
            job_id, "parsing_document", f"could not read the uploaded document: {exc}"
        )
        return

    try:
        truth_base: CVTruthBase = _run_step(
            job_id,
            "extracting_fields",
            lambda: extract_truth_base(markdown, adapters=adapters),
        )
    except ValueError as exc:
        _fail_job(
            job_id,
            "extracting_fields",
            f"could not extract a CV from the uploaded document: {exc}",
        )
        return

    try:
        version = _run_step(
            job_id,
            "saving",
            lambda: write_truth_base(engine, user_id, markdown, truth_base),
        )
    except Exception as exc:  # noqa: BLE001
        _fail_job(job_id, "saving", str(exc))
        return

    with _LOCK:
        job = _JOBS[job_id]
        job.status = "succeeded"
        job.result_version = version
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `coverage run -m unittest tests.test_cv_jobs -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint and commit**

```bash
cd packages/core
ruff check core/cv/jobs.py tests/test_cv_jobs.py
isort core/cv/jobs.py tests/test_cv_jobs.py
black core/cv/jobs.py tests/test_cv_jobs.py
git add core/cv/jobs.py tests/test_cv_jobs.py
git commit -m "feat(job_search): add in-memory CV extraction job tracking (JOB-202)"
```

---

## Task 2: Wire the job into the API router

**Files:**
- Modify: `apps/api/app/routers/cv.py`
- Modify: `packages/core/tests/integration/test_cv_router.py`

**Interfaces:**
- Consumes (from Task 1): `core.cv.jobs.{ExtractionJob, StepRecord, StepStatus, create_job, get_job, run_extraction_job}`.
- Produces (used by Task 3): `POST /cv/extract` → `202 {"job_id": "<uuid>"}`. `GET /cv/extract/jobs/{job_id}` → `200 {"job_id", "status", "steps": [{"name", "status", "duration_seconds"}, ...], "version", "error", "failed_step"}`, or `404` if unknown.

- [ ] **Step 1: Write the failing tests**

In `packages/core/tests/integration/test_cv_router.py`, add `import json` to the existing import block, add a `_WorkingAdapter` class next to `_UnparseableAdapter`, and replace `test_extract_returns_422_when_llm_response_is_unparseable` (its 422-on-`POST` behavior no longer exists — extraction now always accepts and reports failure via the job) with the three tests below:

```python
import json  # add to the top import block, alongside the existing imports
```

```python
class _WorkingAdapter:
    """A fake `LLMAdapter` that returns a minimal, valid CV extraction
    response — the success-path counterpart to `_UnparseableAdapter`.
    """

    def complete(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResponse:
        """Return a fixed, valid extraction payload regardless of input.

        Args:
            model: The provider-specific model identifier (unused).
            prompt: The prompt text (unused).
            temperature: Sampling temperature (unused by the fake).
            seed: A fixed seed (unused by the fake).

        Returns:
            An `LLMResponse` whose `text` is valid `_RawCVTruthBase` JSON.
        """
        return LLMResponse(
            text=json.dumps({"identity": "Jane Doe", "headline": "Engineer"}),
            provider="ollama",
            model=model,
            input_tokens=1,
            output_tokens=1,
        )
```

Replace the removed test with:

```python
    def test_extract_job_succeeds_and_saves_the_truth_base(self) -> None:
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": _WorkingAdapter()
        }
        html = b"<html><body><h1>Jane Doe</h1><p>Engineer.</p></body></html>"

        accept_response = self.client.post(
            "/cv/extract", files={"file": ("cv.html", html, "text/html")}
        )
        self.assertEqual(accept_response.status_code, 202)
        job_id = accept_response.json()["job_id"]

        status_response = self.client.get(f"/cv/extract/jobs/{job_id}")
        self.assertEqual(status_response.status_code, 200)
        body = status_response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["version"], 1)
        self.assertEqual(
            [step["name"] for step in body["steps"]],
            ["parsing_document", "extracting_fields", "saving"],
        )
        for step in body["steps"]:
            self.assertEqual(step["status"], "done")
            self.assertIsNotNone(step["duration_seconds"])

        get_response = self.client.get("/cv/truth-base")
        self.assertEqual(get_response.json()["truth_base"]["identity"], "Jane Doe")

    def test_extract_job_fails_at_extracting_fields_for_unparseable_llm_response(
        self,
    ) -> None:
        app.dependency_overrides[get_llm_adapters] = lambda: {
            "ollama": _UnparseableAdapter()
        }
        html = b"<html><body><h1>Jane Doe</h1><p>Engineer.</p></body></html>"

        accept_response = self.client.post(
            "/cv/extract", files={"file": ("cv.html", html, "text/html")}
        )
        self.assertEqual(accept_response.status_code, 202)
        job_id = accept_response.json()["job_id"]

        status_response = self.client.get(f"/cv/extract/jobs/{job_id}")
        body = status_response.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["failed_step"], "extracting_fields")
        self.assertIn("could not extract", body["error"])

        # Nothing should have been written on a failed extraction.
        get_response = self.client.get("/cv/truth-base")
        self.assertIsNone(get_response.json())

    def test_extract_job_status_returns_404_for_unknown_job_id(self) -> None:
        response = self.client.get(f"/cv/extract/jobs/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `packages/core/`, needs Postgres — `docker compose up -d postgres` first if not already running): `coverage run -m unittest tests.integration.test_cv_router -v`
Expected: FAIL — `POST /cv/extract` still returns `200`/`422` synchronously, `GET /cv/extract/jobs/...` is a 404 route-not-found (endpoint doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Replace `apps/api/app/routers/cv.py` in full:

```python
"""GET/PUT /cv/truth-base, POST /cv/extract, GET /cv/extract/jobs/{job_id}
— the request-serving layer for PLAN.md Step 13's CV truth base. The
first per-user-tenancy router in this API: every handler resolves
`user_id` via `get_current_user_id` (501s until Step 22a's auth lands,
same seam every other per-user endpoint will use) and reads/writes
exclusively through `core.cv.store`, never raw SQL of its own against
`cv_truth_base`.

`POST /cv/extract` does not run the extraction pipeline inline: it
creates a job via `core.cv.jobs.create_job` and schedules
`core.cv.jobs.run_extraction_job` as a `BackgroundTasks` callback,
returning the job id immediately. `GET /cv/extract/jobs/{job_id}` is
how a caller (the Streamlit correction page) observes that job's
progress and final result — see
docs/superpowers/specs/2026-09-14-cv-extraction-progress-design.md for
why extraction moved off the request/response cycle.
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import Engine

from core.cv.jobs import ExtractionJob, create_job, get_job, run_extraction_job
from core.cv.schema import CVTruthBase
from core.cv.store import read_truth_base, write_truth_base
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter

router = APIRouter(prefix="/cv")


class TruthBaseResponse(BaseModel):
    """One user's current CV truth base, over the wire.

    Attributes:
        version: The current version number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The structured truth base.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase


class TruthBaseWriteRequest(BaseModel):
    """A correction-pass save, or any other direct truth-base replace.

    Attributes:
        extracted_markdown: The markdown to store alongside this version.
        truth_base: The truth base to store as the new current version.
    """

    extracted_markdown: str
    truth_base: CVTruthBase


class WriteResult(BaseModel):
    """The outcome of a truth-base write.

    Attributes:
        version: The new version number.
    """

    version: int


class ExtractAcceptedResponse(BaseModel):
    """The immediate response to `POST /cv/extract`.

    Attributes:
        job_id: The id of the background extraction job that was
            scheduled — poll `GET /cv/extract/jobs/{job_id}` with it.
    """

    job_id: uuid.UUID


class ExtractionStepResponse(BaseModel):
    """One extraction step's status, over the wire.

    Attributes:
        name: The step's identifier (`core.cv.jobs.STEP_NAMES`).
        status: "pending", "running", "done", or "failed".
        duration_seconds: Elapsed time — final once the step is done or
            failed, elapsed-so-far while running, None while pending.
    """

    name: str
    status: str
    duration_seconds: float | None


class ExtractionJobResponse(BaseModel):
    """An extraction job's current state, over the wire.

    Attributes:
        job_id: The job's id.
        status: "queued", "running", "succeeded", or "failed".
        steps: Every step's current status, in pipeline order.
        version: The new truth-base version — set once `status` is
            "succeeded".
        error: The failure message — set once `status` is "failed".
        failed_step: Which step failed — set once `status` is "failed".
    """

    job_id: uuid.UUID
    status: str
    steps: list[ExtractionStepResponse]
    version: int | None = None
    error: str | None = None
    failed_step: str | None = None


def _job_to_response(job: ExtractionJob) -> ExtractionJobResponse:
    """Shape an `ExtractionJob` into its wire response.

    Args:
        job: The job snapshot to shape (from `core.cv.jobs.get_job`).

    Returns:
        The equivalent `ExtractionJobResponse`.
    """
    return ExtractionJobResponse(
        job_id=job.job_id,
        status=job.status,
        steps=[
            ExtractionStepResponse(
                name=step.name,
                status=step.status.value,
                duration_seconds=step.duration_seconds,
            )
            for step in job.steps
        ],
        version=job.result_version,
        error=job.error,
        failed_step=job.failed_step,
    )


@router.get("/truth-base", response_model=TruthBaseResponse | None)
def get_truth_base(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> TruthBaseResponse | None:
    """Return the caller's current CV truth base.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The `TruthBaseResponse`, or None if this user has no CV yet.
    """
    stored = read_truth_base(engine, user_id)
    if stored is None:
        return None
    return TruthBaseResponse(
        version=stored.version,
        extracted_markdown=stored.extracted_markdown,
        truth_base=stored.truth_base,
    )


@router.put("/truth-base", response_model=WriteResult)
def put_truth_base(
    request: TruthBaseWriteRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> WriteResult:
    """Replace the caller's CV truth base with a new version.

    Used by both the correction UI's save action and any direct client
    that already has a `CVTruthBase` to store.

    Args:
        request: The new markdown/truth-base pair to store.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new version number.
    """
    version = write_truth_base(
        engine, user_id, request.extracted_markdown, request.truth_base
    )
    return WriteResult(version=version)


@router.post("/extract", response_model=ExtractAcceptedResponse, status_code=202)
async def post_extract(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> ExtractAcceptedResponse:
    """Schedule a CV PDF extraction as a background job.

    Args:
        file: The uploaded CV document.
        background_tasks: Injected by FastAPI — used to run the actual
            pipeline after this response is sent.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The scheduled job's id — poll `GET /cv/extract/jobs/{job_id}`
        for progress and the eventual result.
    """
    file_bytes = await file.read()
    job_id = create_job()
    background_tasks.add_task(
        run_extraction_job,
        job_id,
        file_bytes,
        file.filename or "cv.pdf",
        adapters=adapters,
        engine=engine,
        user_id=user_id,
    )
    return ExtractAcceptedResponse(job_id=job_id)


@router.get("/extract/jobs/{job_id}", response_model=ExtractionJobResponse)
def get_extract_job(job_id: uuid.UUID) -> ExtractionJobResponse:
    """Return one extraction job's current progress and result.

    Args:
        job_id: The job id returned by `POST /cv/extract`.

    Returns:
        The job's current `ExtractionJobResponse`.

    Raises:
        fastapi.HTTPException: 404, if `job_id` is unknown — never
            created, or lost to an API restart (job state is
            in-memory only; see the design spec's Non-goals).
    """
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown extraction job")
    return _job_to_response(job)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `coverage run -m unittest tests.integration.test_cv_router -v`
Expected: PASS (all tests, including the two round-trip/get tests already there)

- [ ] **Step 5: Lint and commit**

```bash
cd packages/core
ruff check ../../apps/api/app/routers/cv.py tests/integration/test_cv_router.py
isort ../../apps/api/app/routers/cv.py tests/integration/test_cv_router.py
black ../../apps/api/app/routers/cv.py tests/integration/test_cv_router.py
git add ../../apps/api/app/routers/cv.py tests/integration/test_cv_router.py
git commit -m "feat(job_search): serve CV extraction as a pollable background job (JOB-202)"
```

---

## Task 3: Streamlit progress UI

**Files:**
- Modify: `apps/ui/app/pages/5_CV_Correction.py`

**Interfaces:**
- Consumes (from Task 2): `POST /cv/extract` → `202 {"job_id"}`; `GET /cv/extract/jobs/{job_id}` → `{"status", "steps": [{"name", "status", "duration_seconds"}], "version", "error", "failed_step"}`.

No new automated test — this repo has no existing precedent for testing Streamlit pages. Verified manually in Step 3 below via the `run` skill.

- [ ] **Step 1: Replace the upload/extract branch**

In `apps/ui/app/pages/5_CV_Correction.py`, add `import time` to the top import block (alongside `httpx`, `pandas`, `streamlit`), add the two helpers below the `_fetch_truth_base` function, and replace the whole `if current is None:` branch (lines 43-57 of the current file) with the polling version:

```python
import time  # add to the top import block
```

```python
_STEP_LABELS: dict[str, str] = {
    "parsing_document": "Parsing document",
    "extracting_fields": "Extracting fields",
    "saving": "Saving",
}

_STEP_ICONS: dict[str, str] = {
    "pending": "⬜",
    "running": "⏳",
    "done": "✅",
    "failed": "❌",
}


def _poll_extraction_job(job_id: str) -> dict:
    """Fetch one extraction job's current status.

    Args:
        job_id: The job id returned by `POST /cv/extract`.

    Returns:
        The parsed `GET /cv/extract/jobs/{job_id}` response body.

    Raises:
        httpx.HTTPError: If the request fails, including a 404 for an
            unknown/expired job id.
    """
    response = httpx.get(
        f"{_settings.api_base_url}/cv/extract/jobs/{job_id}", timeout=10.0
    )
    response.raise_for_status()
    return response.json()


def _render_job_progress(job: dict) -> None:
    """Render an extraction job's steps as a live checklist.

    Args:
        job: A `GET /cv/extract/jobs/{job_id}` response body.
    """
    state = {
        "queued": "running",
        "running": "running",
        "succeeded": "complete",
        "failed": "error",
    }[job["status"]]
    with st.status(f"Extracting CV — {job['status']}", state=state, expanded=True):
        for step in job["steps"]:
            label = _STEP_LABELS.get(step["name"], step["name"])
            icon = _STEP_ICONS[step["status"]]
            if step["duration_seconds"] is not None:
                st.write(f"{icon} {label} ({step['duration_seconds']:.1f}s)")
            else:
                st.write(f"{icon} {label}")
```

Replace the existing branch:

```python
if current is None:
    st.info("No CV on file yet — upload one to extract a truth base.")
    uploaded = st.file_uploader("Upload CV (PDF)", type=["pdf"])
    if uploaded is not None and st.button("Extract"):
        try:
            response = httpx.post(
                f"{_settings.api_base_url}/cv/extract",
                files={"file": (uploaded.name, uploaded.getvalue())},
                timeout=120.0,
            )
            response.raise_for_status()
            st.success(f"Extracted as version {response.json()['version']}.")
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Extraction failed: {exc}")
```

with:

```python
if current is None:
    st.info("No CV on file yet — upload one to extract a truth base.")

    job_id = st.session_state.get("cv_extraction_job_id")
    if job_id is not None:
        try:
            job = _poll_extraction_job(job_id)
        except httpx.HTTPError as exc:
            st.error(f"Lost track of the extraction job — please retry: {exc}")
            del st.session_state["cv_extraction_job_id"]
        else:
            _render_job_progress(job)
            if job["status"] in {"queued", "running"}:
                time.sleep(1)
                st.rerun()
            elif job["status"] == "succeeded":
                del st.session_state["cv_extraction_job_id"]
                st.success(f"Extracted as version {job['version']}.")
                st.rerun()
            else:
                del st.session_state["cv_extraction_job_id"]
                failed_step = _STEP_LABELS.get(job["failed_step"], job["failed_step"])
                st.error(f"Extraction failed at {failed_step}: {job['error']}")
    else:
        uploaded = st.file_uploader("Upload CV (PDF)", type=["pdf"])
        if uploaded is not None and st.button("Extract"):
            try:
                response = httpx.post(
                    f"{_settings.api_base_url}/cv/extract",
                    files={"file": (uploaded.name, uploaded.getvalue())},
                    timeout=10.0,
                )
                response.raise_for_status()
                st.session_state["cv_extraction_job_id"] = response.json()["job_id"]
                st.rerun()
            except httpx.HTTPError as exc:
                st.error(f"Failed to start extraction: {exc}")
```

- [ ] **Step 2: Lint**

```bash
cd apps/ui
ruff check app/pages/5_CV_Correction.py
isort app/pages/5_CV_Correction.py
black app/pages/5_CV_Correction.py
```

- [ ] **Step 3: Manually verify in the running app**

Use the `run` skill to start the stack (or the existing manual-verification docker-compose override already set up in this worktree), then:
1. Upload a CV PDF and click **Extract**. Confirm the step checklist appears immediately (no more multi-second blank wait) and steps flip ⬜ → ⏳ → ✅ with plausible per-step timings as they complete.
2. Confirm on success it lands on the normal truth-base editing view with the new version number.
3. Force a failure (e.g. temporarily point `OLLAMA_BASE_URL`/adapters at something that returns garbage, or reuse the existing `_UnparseableAdapter`-style condition) and confirm the UI shows which step failed and the error message, then lets you retry.

- [ ] **Step 4: Commit**

```bash
git add apps/ui/app/pages/5_CV_Correction.py
git commit -m "feat(job_search): show live CV extraction progress in the correction UI (JOB-202)"
```
