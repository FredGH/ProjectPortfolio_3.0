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
    """One extraction step's lifecycle state.

    Attributes:
        PENDING: Step has not yet started.
        RUNNING: Step is currently executing.
        DONE: Step completed successfully.
        FAILED: Step raised an exception.
    """

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
        steps=[StepRecord(name=name, status=StepStatus.PENDING) for name in STEP_NAMES],
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


def _set_status(job_id: uuid.UUID, status: str) -> None:
    """Set a job's overall status.

    Args:
        job_id: The job whose status to update.
        status: The new status value.
    """
    with _LOCK:
        _JOBS[job_id].status = status


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
    _set_status(job_id, "running")
    job_started_at = time.monotonic()

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
    except Exception as exc:  # noqa: BLE001
        _fail_job(job_id, "parsing_document", str(exc))
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
    except Exception as exc:  # noqa: BLE001
        _fail_job(job_id, "extracting_fields", str(exc))
        return

    # Measured here, not inside the "saving" step below: this is the
    # end-to-end time to *extract* the CV (parsing + the LLM call) that
    # gets persisted alongside the row it describes, not including the
    # DB write's own (much smaller) duration.
    extraction_seconds = time.monotonic() - job_started_at

    try:
        version = _run_step(
            job_id,
            "saving",
            lambda: write_truth_base(
                engine,
                user_id,
                markdown,
                truth_base,
                extraction_seconds=extraction_seconds,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _fail_job(job_id, "saving", str(exc))
        return

    _set_status(job_id, "succeeded")
    with _LOCK:
        _JOBS[job_id].result_version = version
