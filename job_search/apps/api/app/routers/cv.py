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
from datetime import datetime

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import Engine

from core.cv.jobs import ExtractionJob, create_job, get_job, run_extraction_job
from core.cv.schema import CVTruthBase
from core.cv.store import (
    list_truth_base_history,
    read_truth_base,
    read_truth_base_version,
    write_truth_base,
)
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter

router = APIRouter(prefix="/cv")


class TruthBaseResponse(BaseModel):
    """One user's current CV truth base, over the wire.

    Attributes:
        version: The current version number.
        extracted_markdown: The markdown this version was parsed from.
        truth_base: The structured truth base.
        label: The name given to this version at save time, if any.
    """

    version: int
    extracted_markdown: str
    truth_base: CVTruthBase
    label: str | None = None


class TruthBaseWriteRequest(BaseModel):
    """A correction-pass save, or any other direct truth-base replace.

    Attributes:
        extracted_markdown: The markdown to store alongside this version.
        truth_base: The truth base to store as the new current version.
        label: An optional name for this version (e.g. "Before I added
            the AI section"), shown when browsing history.
    """

    extracted_markdown: str
    truth_base: CVTruthBase
    label: str | None = None


class HistoryEntryResponse(BaseModel):
    """One version's history-listing entry, over the wire.

    Attributes:
        version: This version's number.
        label: The name given to this version at save time, if any.
        created_at: When this version was written.
    """

    version: int
    label: str | None
    created_at: datetime


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
        label=stored.label,
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
        engine,
        user_id,
        request.extracted_markdown,
        request.truth_base,
        label=request.label,
    )
    return WriteResult(version=version)


@router.get("/truth-base/versions", response_model=list[HistoryEntryResponse])
def list_versions(
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[HistoryEntryResponse]:
    """List every version of the caller's CV, newest first.

    Args:
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every `HistoryEntryResponse`, ordered by version descending.
    """
    return [
        HistoryEntryResponse(
            version=entry.version, label=entry.label, created_at=entry.created_at
        )
        for entry in list_truth_base_history(engine, user_id)
    ]


@router.post("/truth-base/versions/{version}/restore", response_model=WriteResult)
def restore_version(
    version: int,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> WriteResult:
    """Restore an old version of the caller's CV as the new current one.

    Copies that version's content forward as a brand-new version —
    history is never rewritten in place, so this is itself a new,
    traceable entry rather than a rollback.

    Args:
        version: The historical version number to restore.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new version number.

    Raises:
        fastapi.HTTPException: 404, if `version` doesn't exist for
            this user.
    """
    old = read_truth_base_version(engine, user_id, version)
    if old is None:
        raise HTTPException(status_code=404, detail="unknown CV version")
    new_version = write_truth_base(
        engine, user_id, old.extracted_markdown, old.truth_base, label=old.label
    )
    return WriteResult(version=new_version)


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
def get_extract_job(
    job_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> ExtractionJobResponse:
    """Return one extraction job's current progress and result.

    Args:
        job_id: The job id returned by `POST /cv/extract`.
        user_id: Injected by `get_current_user_id` — gates this route
            behind auth like every other `/cv` route. The job registry
            itself is not user-scoped (job_id alone is the poll key by
            design), so this value isn't otherwise used here.

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
