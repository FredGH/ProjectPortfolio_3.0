"""Skill-extraction batch run endpoints (Step 14 follow-up): scope
selection, starting a run, its live status, and cancellation. See
docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.dependencies import (
    get_app_db_engine,
)
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Engine

from core.skills.extraction_run import (
    FilterOptions,
    RunStatus,
    get_run,
    list_filter_options,
)
from core.skills.write_job_skills import count_pending_jobs

router = APIRouter(prefix="/skills/extraction-runs")


class FilterOptionsModel(BaseModel):
    """Available scope values (see `core.skills.extraction_run.FilterOptions`)."""

    sources: list[str]
    countries: list[str]


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
    mapping_summary: str | None


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
        mapping_summary=status.mapping_summary,
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
