"""Pipeline dashboard endpoints — status/control-center across every
automated CLI stage and human-review step (docs/superpowers/specs/
2026-09-30-pipeline-dashboard-design.md).
"""

from __future__ import annotations

import uuid

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.pipeline.registry import REVIEW_STAGES, STAGES
from core.pipeline.runner import (
    RunAlreadyActive,
    RunNotFound,
    get_active_run,
    request_cancel,
    run_stage,
    start_run,
)
from core.pipeline.staleness import compute_stage_states

router = APIRouter()


class StageStatusModel(BaseModel):
    """Response entry for one automated stage."""

    kind: str = "automated"
    name: str
    depends_on: list[str]
    per_user: bool
    has_run_button: bool
    last_completed_at: str | None
    last_status: str | None
    is_blocked: bool
    is_stale: bool
    stale_because: str | None


class ReviewStageStatusModel(BaseModel):
    """Response entry for one human-review stage."""

    kind: str = "review"
    name: str
    page_path: str
    pending_count: int


class RunRequestBody(BaseModel):
    """Request body for POST /pipeline/stages/{stage}/run."""

    user_id: uuid.UUID | None = None
    params: dict = {}


class RunResponseModel(BaseModel):
    """Response body for a started run."""

    run_id: uuid.UUID


class ActiveRunModel(BaseModel):
    """Response body for GET /pipeline/stages/{stage}/active."""

    run_id: uuid.UUID
    status: str
    progress_current: int | None
    progress_total: int | None
    error_message: str | None
    updated_at: str
    """ISO timestamp of the last progress heartbeat -- the UI (Task 10)
    compares this against now() to detect a run whose API process
    restarted mid-run (same "possibly stalled" signal the original
    skill-extraction runner surfaced, generalized to every stage)."""


class UserModel(BaseModel):
    """Response entry for GET /pipeline/users."""

    id: uuid.UUID
    email: str
    display_name: str


@router.get(
    "/pipeline/stages", response_model=list[StageStatusModel | ReviewStageStatusModel]
)
def get_stages(
    user_id: uuid.UUID,
    engine: Engine = Depends(get_app_db_engine),
) -> list[StageStatusModel | ReviewStageStatusModel]:
    """Every automated and review stage, merged with live state.

    Args:
        user_id: Required — per-user stages' state depends on which
            user is selected; global stages' state ignores it. The
            dashboard always has a user selected.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every `STAGES`/`REVIEW_STAGES` entry as its matching model.
    """
    states = compute_stage_states(engine, user_id=user_id)
    result: list[StageStatusModel | ReviewStageStatusModel] = []
    for name, spec in STAGES.items():
        state = states[name]
        result.append(
            StageStatusModel(
                name=name,
                depends_on=list(spec.depends_on),
                per_user=spec.per_user,
                has_run_button=spec.has_run_button,
                last_completed_at=(
                    state.last_completed_at.isoformat()
                    if state.last_completed_at
                    else None
                ),
                last_status=state.last_status,
                is_blocked=state.is_blocked,
                is_stale=state.is_stale,
                stale_because=state.stale_because,
            )
        )
    for name, spec in REVIEW_STAGES.items():
        result.append(
            ReviewStageStatusModel(
                name=name,
                page_path=spec.page_path,
                pending_count=spec.pending_count(engine, user_id),
            )
        )
    return result


@router.get("/pipeline/users", response_model=list[UserModel])
def get_users(engine: Engine = Depends(get_app_db_engine)) -> list[UserModel]:
    """List every app_user, for the dashboard's user-picker.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        Every user, ordered by email.
    """
    # app_user is RLS-isolated: under the app role with no user context
    # (nil-UUID GUC) this returns zero rows -- see the task-9 report.
    with session_scope(engine) as conn:
        rows = conn.execute(
            text("SELECT id, email, display_name FROM app_user ORDER BY email")
        ).all()
    return [
        UserModel(id=row.id, email=row.email, display_name=row.display_name)
        for row in rows
    ]


@router.post(
    "/pipeline/stages/{stage}/run", response_model=RunResponseModel, status_code=202
)
def run_pipeline_stage(
    stage: str,
    body: RunRequestBody,
    background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_app_db_engine),
) -> RunResponseModel:
    """Start a stage's run in the background.

    Args:
        stage: The stage name, must be a key of `STAGES`.
        body: `user_id` (required for a per-user stage, must be omitted
            for a global one) and `params`.
        background_tasks: FastAPI's background-task scheduler.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new run's id, `202` (FastAPI's default for a POST that
        returns 200 by model — Task 10's UI treats any 2xx as started).

    Raises:
        fastapi.HTTPException: `404` if `stage` is unknown; `400` if
            `user_id` is missing/present incorrectly for this stage's
            `per_user` flag; `409` if any pipeline run is already
            active.
    """
    spec = STAGES.get(stage)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown stage {stage!r}")
    if not spec.has_run_button:
        raise HTTPException(
            status_code=400,
            detail=f"{stage} has no dashboard-triggerable run (CLI only)",
        )
    if spec.per_user and body.user_id is None:
        raise HTTPException(status_code=400, detail=f"{stage} requires user_id")
    if not spec.per_user and body.user_id is not None:
        raise HTTPException(status_code=400, detail=f"{stage} is not a per-user stage")
    params = dict(body.params)
    if spec.per_user:
        params["user_id"] = body.user_id
    try:
        run_id = start_run(engine, stage=stage, user_id=body.user_id, params=params)
    except RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(run_stage, run_id, engine, spec, params)
    return RunResponseModel(run_id=run_id)


@router.get("/pipeline/stages/{stage}/active", response_model=ActiveRunModel | None)
def get_active_stage_run(
    stage: str,
    engine: Engine = Depends(get_app_db_engine),
) -> ActiveRunModel | None:
    """Return the active run for `stage`, if the currently active
    pipeline run (system-wide, at most one) happens to be for it.

    Args:
        stage: The stage name.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The active run's snapshot, or None.
    """
    snapshot = get_active_run(engine)
    if snapshot is None or snapshot.stage != stage:
        return None
    return ActiveRunModel(
        run_id=snapshot.run_id,
        status=snapshot.status,
        progress_current=snapshot.progress_current,
        progress_total=snapshot.progress_total,
        error_message=snapshot.error_message,
        updated_at=snapshot.updated_at.isoformat(),
    )


@router.post("/pipeline/stages/{stage}/cancel", status_code=204, response_model=None)
def cancel_stage_run(stage: str, engine: Engine = Depends(get_app_db_engine)) -> None:
    """Cancel `stage`'s active run.

    Args:
        stage: The stage name (used only to look up the active run and
            confirm it matches — the actual cancel target is whatever
            run is currently active).
        engine: Injected via `get_app_db_engine`.

    Raises:
        fastapi.HTTPException: `404` if no run is active, or the active
            run is for a different stage.
    """
    snapshot = get_active_run(engine)
    if snapshot is None or snapshot.stage != stage:
        raise HTTPException(status_code=404, detail=f"no active run for {stage!r}")
    try:
        request_cancel(engine, snapshot.run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
