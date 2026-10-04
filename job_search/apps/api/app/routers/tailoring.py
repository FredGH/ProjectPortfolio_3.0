"""Tailored-CV endpoints (PLAN.md Step 17).

Per-user via `get_current_user_id`, exactly like the scoring and CV routers
(501 until Step 22a's identity middleware, except with the local-dev
`DEV_USER_ID` override). A run is created synchronously and executed as a
background task; poll `GET /tailoring/runs/{run_id}`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.dependencies import get_app_db_engine, get_llm_adapters
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import Engine

from core.cv.store import read_truth_base_version
from core.db.session import get_current_user_id
from core.llm.types import LLMAdapter
from core.tailoring.checks import line_location
from core.tailoring.context import list_candidates, load_job_context
from core.tailoring.decisions import DecisionError, apply_decision
from core.tailoring.loop import (
    CriticUnavailableError,
    NoCvError,
    NoTargetTitleError,
    UnknownJobError,
    ensure_critic_available,
    execute_tailoring,
    start_tailoring,
)
from core.tailoring.store import (
    StaleDecisionError,
    StoredOrphan,
    StoredRun,
    latest_run_id,
    read_orphan,
    read_run,
    save_decision,
)

router = APIRouter()


class CandidateModel(BaseModel):
    """One job the user could tailor a CV for."""

    job_group_id: str
    title_for_display: str | None
    company: str | None
    final_score: float
    latest_run_id: uuid.UUID | None
    latest_status: str | None


class RunRequest(BaseModel):
    """Request body for starting a run."""

    job_group_id: str


class RunAccepted(BaseModel):
    """Response to starting a run."""

    run_id: uuid.UUID


class LatestRun(BaseModel):
    """Response for a job's latest run."""

    run_id: uuid.UUID


class SourceBullet(BaseModel):
    """One truth-base bullet, offered as a link target for an orphan."""

    bullet_id: str
    text: str
    experience_index: int
    role: str


class OrphanModel(BaseModel):
    """One line awaiting (or past) a decision."""

    id: uuid.UUID
    kind: str
    section: str
    experience_index: int | None
    bullet_index: int | None
    text: str
    claimed_refs: list[str]
    issue: str | None
    status: str
    evidence_ref: str | None


class RunModel(BaseModel):
    """A tailoring run with its document, orphans and link targets.

    `progress` is the live progress of a `generating` run (phase, message,
    finished-attempt history), else None; `started_at`/`updated_at` let the
    page show how long it has been running and how recent the last update is.
    """

    run_id: uuid.UUID
    job_group_id: str
    target_title: str
    status: str
    attempts: int
    error_message: str | None
    document: dict | None
    orphans: list[OrphanModel]
    sources: list[SourceBullet]
    progress: dict | None
    started_at: datetime
    updated_at: datetime


class DecisionRequest(BaseModel):
    """Request body for deciding an orphan."""

    action: Literal["link", "reject"]
    evidence_ref: str | None = None

    @model_validator(mode="after")
    def _link_needs_a_ref(self) -> DecisionRequest:
        """Reject a `link` that names no bullet.

        Returns:
            The validated request.

        Raises:
            ValueError: If `action` is `link` and `evidence_ref` is empty.
        """
        if self.action == "link" and not self.evidence_ref:
            raise ValueError("a link needs an evidence_ref")
        return self


def _run_model(engine: Engine, user_id: uuid.UUID, run: StoredRun) -> RunModel:
    """Build the response model for a stored run.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run: The stored run.

    Returns:
        The `RunModel`, with the truth-base bullets of the version the run
        used as link targets.
    """
    stored = read_truth_base_version(engine, user_id, run.truth_base_version)
    sources: list[SourceBullet] = []
    if stored is not None:
        for index, role in enumerate(stored.truth_base.experience):
            label = f"{role.title} at {role.company}"
            sources.extend(
                SourceBullet(
                    bullet_id=bullet.bullet_id,
                    text=bullet.text,
                    experience_index=index,
                    role=label,
                )
                for bullet in role.bullets
            )
    return RunModel(
        run_id=run.id,
        job_group_id=run.job_group_id,
        target_title=run.target_title,
        status=run.status,
        attempts=run.attempts,
        error_message=run.error_message,
        document=run.document.model_dump() if run.document else None,
        orphans=[OrphanModel(**o.__dict__) for o in run.orphans],
        sources=sources,
        progress=run.progress if run.status == "generating" else None,
        started_at=run.created_at,
        updated_at=run.updated_at,
    )


def _other_pending_locations(
    run: StoredRun, decided: StoredOrphan, removed: tuple[int, int] | None
) -> frozenset[str]:
    """Locate the run's other pending lines in the post-decision document.

    Mirrors `save_decision`'s re-indexing: when a line is removed, later
    lines in the same role move up by one.

    Args:
        run: The run, as read before the decision.
        decided: The orphan being decided (excluded).
        removed: `(experience_index, bullet_index)` of a removed line.

    Returns:
        `summary` / `e{role}b{bullet}` locations.
    """
    locations: set[str] = set()
    for other in run.orphans:
        if other.id == decided.id or other.status != "pending":
            continue
        bullet = other.bullet_index
        if (
            removed is not None
            and other.section == "experience"
            and other.experience_index == removed[0]
            and bullet is not None
            and bullet > removed[1]
        ):
            bullet -= 1
        locations.add(line_location(other.section, other.experience_index, bullet))
    return frozenset(locations)


@router.get("/tailoring/candidates", response_model=list[CandidateModel])
def get_candidates(
    limit: int = Query(25, ge=1, le=100),
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> list[CandidateModel]:
    """List the user's top-scored jobs, best first.

    Args:
        limit: Maximum jobs to return.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The candidates, each with the user's latest run status, if any.
    """
    return [
        CandidateModel(**c.__dict__)
        for c in list_candidates(engine, user_id, limit=limit)
    ]


@router.post("/tailoring/runs", response_model=RunAccepted, status_code=202)
def post_run(
    body: RunRequest,
    background_tasks: BackgroundTasks,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
) -> RunAccepted:
    """Start a tailoring run for one job.

    Args:
        body: The job to tailor for.
        background_tasks: Runs the loop after the response is sent.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters`.

    Returns:
        The run's id; poll `GET /tailoring/runs/{run_id}`.

    Raises:
        HTTPException: 503 if there is no Anthropic API key for the critic
            (checked before any run is created), 409 if the user has no CV,
            404 if the job does not exist, 422 if the job has no title to
            mirror.
    """
    try:
        ensure_critic_available(adapters)
    except CriticUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        run_id = start_tailoring(engine, user_id, body.job_group_id)
    except NoCvError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnknownJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NoTargetTitleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    background_tasks.add_task(
        execute_tailoring, engine, user_id, run_id, adapters=adapters
    )
    return RunAccepted(run_id=run_id)


@router.get("/tailoring/runs/{run_id}", response_model=RunModel)
def get_run(
    run_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> RunModel:
    """Read a run.

    Args:
        run_id: The run.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The run, its document, orphans and link targets.

    Raises:
        HTTPException: 404 if the run does not exist for this user.
    """
    run = read_run(engine, user_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return _run_model(engine, user_id, run)


@router.get("/tailoring/jobs/{job_group_id}/latest-run", response_model=LatestRun)
def get_latest_run(
    job_group_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> LatestRun:
    """Find the user's most recent run for a job.

    Args:
        job_group_id: The job.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The latest run's id.

    Raises:
        HTTPException: 404 if the user has never tailored for this job.
    """
    run_id = latest_run_id(engine, user_id, job_group_id)
    if run_id is not None:
        return LatestRun(run_id=run_id)
    raise HTTPException(status_code=404, detail="no run for this job")


@router.post("/tailoring/orphans/{orphan_id}/decision", response_model=RunModel)
def post_decision(
    orphan_id: uuid.UUID,
    body: DecisionRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    engine: Engine = Depends(get_app_db_engine),
) -> RunModel:
    """Link or reject an orphan line.

    Args:
        orphan_id: The orphan.
        body: The decision.
        user_id: Injected by `get_current_user_id`.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The updated run.

    Raises:
        HTTPException: 404 if the orphan is unknown, 409 if it is already
            decided or the run changed since it was read, 422 if the
            decision breaks a rule (for example linking a bullet from
            another role).
    """
    orphan = read_orphan(engine, user_id, orphan_id)
    if orphan is None:
        raise HTTPException(status_code=404, detail="unknown orphan")
    if orphan.status != "pending":
        raise HTTPException(status_code=409, detail="already decided")
    run = read_run(engine, user_id, orphan.tailored_cv_id)
    stored = read_truth_base_version(
        engine, user_id, run.truth_base_version if run else 0
    )
    if run is None or run.document is None or stored is None:
        raise HTTPException(status_code=409, detail="this run has no document")
    job = load_job_context(engine, run.job_group_id)
    try:
        # First pass: learn whether a line is removed, so the other pending
        # lines' locations can be re-indexed before coverage is recomputed.
        removed = apply_decision(
            run.document,
            orphan,
            action=body.action,
            evidence_ref=body.evidence_ref,
            truth_base=stored.truth_base,
        ).removed_position
        result = apply_decision(
            run.document,
            orphan,
            action=body.action,
            evidence_ref=body.evidence_ref,
            truth_base=stored.truth_base,
            job_skills=job.skills if job is not None else None,
            pending_locations=_other_pending_locations(run, orphan, removed),
        )
    except DecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        save_decision(
            engine,
            user_id,
            orphan=orphan,
            status="linked" if body.action == "link" else "rejected",
            evidence_ref=body.evidence_ref if body.action == "link" else None,
            document=result.document,
            base_document=run.document,
            removed_position=result.removed_position,
        )
    except StaleDecisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = read_run(engine, user_id, run.id)
    if updated is None:
        raise HTTPException(status_code=409, detail="the run disappeared")
    return _run_model(engine, user_id, updated)
