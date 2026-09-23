"""Skill-extraction batch run lifecycle (Step 14 follow-up): starting,
tracking and cancelling a scoped extraction run triggered from the UI.
`run_loop` — the actual sub-batch execution — lives here too but is
implemented and tested separately (see `test_extraction_run_loop.py`)
since it needs a fake LLM adapter and a fake Ollama transport rather
than just live Postgres.

silver.skill_extraction_run has at most one `status = 'running'` row,
enforced by a partial unique index (migration 0025) — `start_run`
relies on that index's IntegrityError rather than an application-level
lock. See docs/superpowers/specs/2026-09-23-skill-extraction-batch-runner-design.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from core.skills.write_job_skills import CURRENT_PROMPT_VERSION, count_pending_jobs


class RunError(ValueError):
    """Base error for an invalid run-lifecycle operation."""


class RunAlreadyActive(RunError):
    """Raised when `start_run` is called while one run is already active."""


class RunNotFound(RunError):
    """Raised when a run id names no run, or names one that already
    finished (for operations that only apply to an active run)."""


@dataclass(frozen=True)
class RunStatus:
    """One extraction run's full state.

    Attributes:
        run_id: The run's unique id.
        status: "running", "completed", "cancelled", or "failed".
        sources: The source scope this run was started with, or None
            for every source.
        countries: The country scope this run was started with, or
            None for every country.
        total_pending: How many jobs matched the scope when the run
            started.
        extracted_count: Jobs successfully extracted so far.
        failed_count: Jobs whose extraction failed so far (retried by
            a future run, same as `write_job_skills`' own semantics).
        cancel_requested: Whether a cancel has been requested.
        error_message: Set only when `status == "failed"`.
        started_at: When the run began.
        updated_at: When progress was last recorded.
        finished_at: When the run reached a terminal status, or None
            while still running.
    """

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


@dataclass(frozen=True)
class FilterOptions:
    """Source/country values available to scope a new run.

    Attributes:
        sources: Distinct `apply_source_name` values among pending jobs.
        countries: Distinct `country_iso` values among pending jobs.
    """

    sources: list[str]
    countries: list[str]


_COLUMNS = (
    "run_id, status, sources, countries, total_pending, extracted_count, "
    "failed_count, cancel_requested, error_message, started_at, updated_at, "
    "finished_at"
)
_SELECT_ACTIVE = text(
    f"SELECT {_COLUMNS} FROM silver.skill_extraction_run WHERE status = 'running'"
)
_SELECT_ONE = text(
    f"SELECT {_COLUMNS} FROM silver.skill_extraction_run WHERE run_id = :run_id"
)
_INSERT_RUNNING = text(
    "INSERT INTO silver.skill_extraction_run "
    "(status, sources, countries, total_pending) "
    "VALUES ('running', CAST(:sources AS text[]), CAST(:countries AS text[]), "
    ":total_pending) RETURNING run_id"
)
_INSERT_COMPLETED = text(
    "INSERT INTO silver.skill_extraction_run "
    "(status, sources, countries, total_pending, finished_at) "
    "VALUES ('completed', CAST(:sources AS text[]), CAST(:countries AS text[]), "
    "0, now()) RETURNING run_id"
)
_REQUEST_CANCEL = text(
    "UPDATE silver.skill_extraction_run SET cancel_requested = TRUE "
    "WHERE run_id = :run_id AND status = 'running'"
)
_SELECT_PENDING_SOURCES = text(
    "SELECT DISTINCT js.apply_source_name AS value "
    "FROM silver.job_survivorship AS js "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "ORDER BY 1"
)
_SELECT_PENDING_COUNTRIES = text(
    "SELECT DISTINCT d.country_iso AS value "
    "FROM silver.job_survivorship AS js "
    "JOIN gold.dim_job AS d ON d.job_group_id = js.job_group_id "
    "LEFT JOIN silver.job_skill_extraction AS e "
    "ON e.job_group_id = js.job_group_id AND e.prompt_version = :prompt_version "
    "WHERE e.job_group_id IS NULL AND js.winning_description IS NOT NULL "
    "AND d.country_iso IS NOT NULL "
    "ORDER BY 1"
)


def _row_to_status(row) -> RunStatus:
    """Convert one `silver.skill_extraction_run` row into a `RunStatus`.

    Args:
        row: A row from `_SELECT_ACTIVE` or `_SELECT_ONE`.

    Returns:
        The row as a `RunStatus`.
    """
    return RunStatus(
        run_id=row.run_id,
        status=row.status,
        sources=row.sources,
        countries=row.countries,
        total_pending=row.total_pending,
        extracted_count=row.extracted_count,
        failed_count=row.failed_count,
        cancel_requested=row.cancel_requested,
        error_message=row.error_message,
        started_at=row.started_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def start_run(
    engine: Engine,
    *,
    sources: list[str] | None,
    countries: list[str] | None,
) -> tuple[uuid.UUID, int]:
    """Start a new extraction run for the given scope.

    If nothing is pending in scope, the run is recorded already
    `completed` (so it shows in history and the caller can tell "ran,
    found nothing to do" apart from "never started") and the caller
    should not schedule `run_loop` for it.

    Args:
        engine: The app-role engine.
        sources: Restrict to these `apply_source_name` values, or None
            for every source.
        countries: Restrict to these `country_iso` values, or None for
            every country.

    Returns:
        `(run_id, total_pending)`.

    Raises:
        RunAlreadyActive: If a run is already `running`.
    """
    total_pending = count_pending_jobs(engine, sources=sources, countries=countries)
    params = {"sources": sources, "countries": countries}
    if total_pending == 0:
        with engine.begin() as conn:
            run_id = conn.execute(_INSERT_COMPLETED, params).scalar_one()
        return run_id, 0
    try:
        with engine.begin() as conn:
            run_id = conn.execute(
                _INSERT_RUNNING, {**params, "total_pending": total_pending}
            ).scalar_one()
    except IntegrityError as exc:
        raise RunAlreadyActive("an extraction run is already active") from exc
    return run_id, total_pending


def get_active_run(engine: Engine) -> RunStatus | None:
    """Return the currently active run, if any.

    Args:
        engine: The app-role engine.

    Returns:
        The active run's status, or None if no run is active.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ACTIVE).first()
    return _row_to_status(row) if row is not None else None


def get_run(engine: Engine, run_id: uuid.UUID) -> RunStatus | None:
    """Return one run's status by id, active or finished.

    Args:
        engine: The app-role engine.
        run_id: The run to look up.

    Returns:
        The run's status, or None if `run_id` is unknown.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ONE, {"run_id": run_id}).first()
    return _row_to_status(row) if row is not None else None


def request_cancel(engine: Engine, run_id: uuid.UUID) -> None:
    """Ask a running run to stop after its current sub-batch.

    Args:
        engine: The app-role engine.
        run_id: The run to cancel.

    Raises:
        RunNotFound: If `run_id` is unknown, or names a run that is
            not currently `running`.
    """
    with engine.begin() as conn:
        result = conn.execute(_REQUEST_CANCEL, {"run_id": run_id})
    if result.rowcount == 0:
        raise RunNotFound(f"no active run with id {run_id}")


def list_filter_options(engine: Engine) -> FilterOptions:
    """List the source/country values a new run could be scoped to.

    Only values seen among jobs still pending extraction — a source or
    country with nothing left to do would otherwise show as a choice
    that starts a run and immediately completes with nothing done.

    Args:
        engine: The app-role engine.

    Returns:
        The available `FilterOptions`.
    """
    params = {"prompt_version": CURRENT_PROMPT_VERSION}
    with engine.connect() as conn:
        sources = [row.value for row in conn.execute(_SELECT_PENDING_SOURCES, params)]
        countries = [
            row.value for row in conn.execute(_SELECT_PENDING_COUNTRIES, params)
        ]
    return FilterOptions(sources=sources, countries=countries)
