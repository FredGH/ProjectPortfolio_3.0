"""Pipeline dashboard endpoints — status/control-center across every
automated CLI stage and human-review step (docs/superpowers/specs/
2026-09-30-pipeline-dashboard-design.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from app.dependencies import (
    NATIVE_OLLAMA_BASE_URL,
    get_app_db_engine,
    get_http_client,
    get_llm_adapters,
    get_native_ollama_adapter,
    get_ollama_http_client,
    get_owner_db_engine,
    get_skill_mapping_hook_factory,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine, text

from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
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
from core.settings import get_settings
from core.skills.extraction_run import RunAlreadyActive as ExtractionRunAlreadyActive
from core.skills.extraction_run import run_loop
from core.skills.extraction_run import start_run as start_extraction_run

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
    last_error_message: str | None = None


class ReviewStageStatusModel(BaseModel):
    """Response entry for one human-review stage."""

    kind: str = "review"
    key: str
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
                last_error_message=state.last_error_message,
            )
        )
    for key, spec in REVIEW_STAGES.items():
        result.append(
            ReviewStageStatusModel(
                key=key,
                name=spec.name,
                page_path=spec.page_path,
                pending_count=spec.pending_count(engine, user_id),
            )
        )
    return result


@router.get("/pipeline/users", response_model=list[UserModel])
def get_users(engine: Engine = Depends(get_owner_db_engine)) -> list[UserModel]:
    """List every app_user, for the dashboard's user-picker.

    Args:
        engine: Injected via `get_owner_db_engine`.

    Returns:
        Every user, ordered by email.
    """
    # TODO(Step 22a): require auth -- this lists every user via the owner role.
    # app_user is RLS-isolated, so the app role would see zero rows here;
    # the owner-role engine (injected above) lists every user. Only the
    # three picker columns are selected.
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, email, display_name FROM app_user ORDER BY email")
        ).all()
    return [
        UserModel(id=row.id, email=row.email, display_name=row.display_name)
        for row in rows
    ]


def _start_extraction(
    params: dict,
    background_tasks: BackgroundTasks,
    engine: Engine,
    adapters: dict[str, LLMAdapter],
    native_ollama_adapter: LLMAdapter,
    http_client: httpx.Client,
    ollama_http_client: httpx.Client,
    mapping_hook_factory: Callable[..., Callable[[], str]],
) -> RunResponseModel:
    """Start an extract-job-skills run and schedule its `run_loop`.

    Args:
        params: Request `params`: optional `sources`, `countries`,
            `ollama_location` ("docker" or "native").
        background_tasks: FastAPI's background-task scheduler.
        engine: The app-role engine.
        adapters: LLM adapters (Docker Ollama by default).
        native_ollama_adapter: Adapter for Ollama on the host machine.
        http_client: Used for the Ollama unload call between sub-batches.
        ollama_http_client: Used for the post-run mapping's embeddings.
        mapping_hook_factory: Builds the post-completion skill mapping.

    Returns:
        The new run's id.

    Raises:
        fastapi.HTTPException: `400` for an invalid `ollama_location`;
            `409` if a run is already active.
    """
    sources = params.get("sources")
    countries = params.get("countries")
    location = params.get("ollama_location", "docker")
    if location not in ("docker", "native"):
        raise HTTPException(
            status_code=400, detail="ollama_location must be 'docker' or 'native'"
        )
    try:
        run_id, total_pending = start_extraction_run(
            engine, sources=sources, countries=countries
        )
    except ExtractionRunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if total_pending > 0:
        task_config = load_task_config("skill_extraction")
        if location == "native":
            ollama_base_url = NATIVE_OLLAMA_BASE_URL
            adapters = {**adapters, "ollama": native_ollama_adapter}
        else:
            ollama_base_url = get_settings().ollama_base_url
        background_tasks.add_task(
            run_loop,
            run_id,
            engine,
            adapters=adapters,
            http_client=http_client,
            ollama_base_url=ollama_base_url,
            model=task_config.model,
            provider=task_config.provider,
            sources=sources,
            countries=countries,
            map_skills=mapping_hook_factory(
                engine,
                ollama_base_url=ollama_base_url,
                embedding_model=get_settings().embedding_model,
                http_client=ollama_http_client,
                llm_adapters=adapters,
            ),
        )
    return RunResponseModel(run_id=run_id)


@router.post(
    "/pipeline/stages/{stage}/run", response_model=RunResponseModel, status_code=202
)
def run_pipeline_stage(
    stage: str,
    body: RunRequestBody,
    background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
    native_ollama_adapter: LLMAdapter = Depends(get_native_ollama_adapter),
    http_client: httpx.Client = Depends(get_http_client),
    ollama_http_client: httpx.Client = Depends(get_ollama_http_client),
    mapping_hook_factory: Callable[..., Callable[[], str]] = Depends(
        get_skill_mapping_hook_factory
    ),
) -> RunResponseModel:
    """Start a stage's run in the background.

    `extract-job-skills` is special-cased: it runs through
    `core.skills.extraction_run` (its own `run_loop`, with Ollama
    location selection and post-run skill mapping), not `run_stage`.
    Its `params` may carry `sources`, `countries` (lists or omitted) and
    `ollama_location` ("docker" default, or "native"). The extra
    Ollama/mapping dependencies are used only by that branch.

    Args:
        stage: The stage name, must be a key of `STAGES`.
        body: `user_id` (required for a per-user stage, must be omitted
            for a global one) and `params`.
        background_tasks: FastAPI's background-task scheduler.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The new run's id, with status `202` (set on the route decorator).

    Raises:
        fastapi.HTTPException: `404` if `stage` is unknown; `400` if
            `user_id` is missing/present incorrectly for this stage's
            `per_user` flag; `409` if any pipeline run is already
            active.
    """
    # TODO(Step 22a): require auth -- this endpoint triggers stages whose
    # wrappers use the owner-role DSN, with no caller identity check.
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
    if stage == "extract-job-skills":
        return _start_extraction(
            body.params,
            background_tasks,
            engine,
            adapters,
            native_ollama_adapter,
            http_client,
            ollama_http_client,
            mapping_hook_factory,
        )
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
