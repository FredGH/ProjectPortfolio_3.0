"""Skill-extraction batch run endpoints (Step 14 follow-up): scope
selection, starting a run, its live status, and cancellation. See
docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

import httpx
from app.dependencies import (
    NATIVE_OLLAMA_BASE_URL,
    get_app_db_engine,
    get_http_client,
    get_llm_adapters,
    get_native_ollama_adapter,
)
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Engine

from core.llm.task_config import load_task_config
from core.llm.types import LLMAdapter
from core.settings import get_settings
from core.skills.extraction_run import (
    FilterOptions,
    RunAlreadyActive,
    RunNotFound,
    RunStatus,
    get_active_run,
    get_run,
    list_filter_options,
    request_cancel,
    run_loop,
    start_run,
)
from core.skills.write_job_skills import count_pending_jobs

router = APIRouter(prefix="/skills/extraction-runs")


class FilterOptionsModel(BaseModel):
    """Available scope values (see `core.skills.extraction_run.FilterOptions`)."""

    sources: list[str]
    countries: list[str]


class StartRunRequest(BaseModel):
    """A new run's requested scope.

    Attributes:
        sources: Restrict to these sources, or None for every source.
        countries: Restrict to these countries, or None for every country.
        ollama_location: Where the run's Ollama calls go — "docker" (the
            `ollama` compose service, always available whenever the stack
            is) or "native" (Ollama running on the host machine, reached
            via `host.docker.internal` — faster per README.md, but only
            works if it's actually running there with the model pulled).
    """

    sources: list[str] | None = None
    countries: list[str] | None = None
    ollama_location: Literal["docker", "native"] = "docker"


class StartRunResponse(BaseModel):
    """The id of a newly started run."""

    run_id: uuid.UUID


class RunStatusModel(BaseModel):
    """One run's status over the wire (see `core.skills.extraction_run.RunStatus`)."""

    run_id: uuid.UUID
    status: str
    sources: list[str] | None
    countries: list[str] | None
    total_pending: int
    extracted_count: int
    failed_count: int
    cancel_requested: bool
    error_message: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None


def _to_model(status: RunStatus) -> RunStatusModel:
    """Convert a `RunStatus` dataclass into its API model.

    Args:
        status: The run status to convert.

    Returns:
        The equivalent `RunStatusModel`.
    """
    return RunStatusModel(
        run_id=status.run_id,
        status=status.status,
        sources=status.sources,
        countries=status.countries,
        total_pending=status.total_pending,
        extracted_count=status.extracted_count,
        failed_count=status.failed_count,
        cancel_requested=status.cancel_requested,
        error_message=status.error_message,
        started_at=status.started_at,
        updated_at=status.updated_at,
        finished_at=status.finished_at,
    )


@router.get("/filters", response_model=FilterOptionsModel)
def get_filters(engine: Engine = Depends(get_app_db_engine)) -> FilterOptionsModel:
    """List the source/country values a new run could be scoped to.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The available scope values.
    """
    options: FilterOptions = list_filter_options(engine)
    return FilterOptionsModel(sources=options.sources, countries=options.countries)


@router.get("/pending-count")
def get_pending_count(
    sources: list[str] | None = Query(default=None),
    countries: list[str] | None = Query(default=None),
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, int]:
    """Count how many jobs a run with this scope would process.

    Args:
        sources: Restrict to these sources, or None for every source.
        countries: Restrict to these countries, or None for every country.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"pending": <count>}`.
    """
    pending = count_pending_jobs(engine, sources=sources, countries=countries)
    return {"pending": pending}


@router.post("", response_model=StartRunResponse, status_code=202)
def post_start_run(
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    engine: Engine = Depends(get_app_db_engine),
    adapters: dict[str, LLMAdapter] = Depends(get_llm_adapters),
    native_ollama_adapter: LLMAdapter = Depends(get_native_ollama_adapter),
    http_client: httpx.Client = Depends(get_http_client),
) -> StartRunResponse:
    """Start a scoped extraction run in the background.

    Args:
        request: The requested scope.
        background_tasks: Injected by FastAPI — schedules `run_loop`
            after this response is sent.
        engine: Injected via `get_app_db_engine`.
        adapters: Injected via `get_llm_adapters` — always points at the
            Docker `ollama` service. Used as-is when `request.
            ollama_location == "docker"`; its "ollama" entry is swapped
            for `native_ollama_adapter` otherwise, so the actual
            extraction calls go to the chosen location, not just the
            unload ping between sub-batches.
        native_ollama_adapter: Injected via `get_native_ollama_adapter`.
        http_client: Injected via `get_http_client`, used for the
            Ollama unload call between sub-batches.

    Returns:
        The new run's id.

    Raises:
        fastapi.HTTPException: 409 if a run is already active.
    """
    try:
        run_id, total_pending = start_run(
            engine, sources=request.sources, countries=request.countries
        )
    except RunAlreadyActive as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if total_pending > 0:
        task_config = load_task_config("skill_extraction")
        if request.ollama_location == "native":
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
            sources=request.sources,
            countries=request.countries,
        )
    return StartRunResponse(run_id=run_id)


@router.get("/active", response_model=RunStatusModel | None)
def get_active(engine: Engine = Depends(get_app_db_engine)) -> RunStatusModel | None:
    """Return the currently active run, if any.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The active run's status, or None.
    """
    active = get_active_run(engine)
    return _to_model(active) if active is not None else None


@router.get("/{run_id}", response_model=RunStatusModel | None)
def get_one(
    run_id: uuid.UUID, engine: Engine = Depends(get_app_db_engine)
) -> RunStatusModel | None:
    """Return one run's status by id, active or finished.

    Declared after `/filters`, `/pending-count`, and `/active` so those
    literal paths are matched before this path-parameter route (FastAPI
    matches in declaration order, and `/{run_id}` would otherwise
    greedily swallow them).

    Args:
        run_id: The run to look up.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The run's status, or None if `run_id` is unknown.
    """
    status = get_run(engine, run_id)
    return _to_model(status) if status is not None else None


@router.post("/{run_id}/cancel")
def post_cancel(
    run_id: uuid.UUID, engine: Engine = Depends(get_app_db_engine)
) -> dict[str, str]:
    """Ask an active run to stop after its current sub-batch.

    Args:
        run_id: The run to cancel.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.

    Raises:
        fastapi.HTTPException: 404 if `run_id` is unknown or not active.
    """
    try:
        request_cancel(engine, run_id)
    except RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}
