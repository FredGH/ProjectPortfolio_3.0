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

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from core.llm.types import LLMAdapter
from core.skills.write_job_skills import (
    CURRENT_PROMPT_VERSION,
    count_pending_jobs,
    write_job_skills,
)

logger = logging.getLogger(__name__)


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
        mapping_summary: One line on what the automatic skill mapping did
            once the run finished (or why it did not run / failed), or None
            while the run is unfinished or was started without mapping.
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
    mapping_summary: str | None


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
    "finished_at, mapping_summary"
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
        mapping_summary=row.mapping_summary,
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


MAPPING_SKIPPED_STOPPED = (
    "Skill mapping did not run because the run was stopped; it runs after the "
    "next completed run."
)
MAPPING_SKIPPED_FAILED = (
    "Skill mapping did not run because the run failed; it runs after the "
    "next completed run."
)

DEFAULT_BATCH_SIZE = 30
"""Jobs per sub-batch before the Ollama model is unloaded and the run
pauses — matches scripts/extract_in_batches.sh's proven-safe default.
Not user-configurable (spec's Non-goals): a higher value is what grew
Ollama's resident memory and froze the host machine once already."""

DEFAULT_PAUSE_SECONDS = 10.0
"""Pause after each unload before the next sub-batch — same value as
scripts/extract_in_batches.sh."""

_UPDATE_PROGRESS = text(
    "UPDATE silver.skill_extraction_run SET "
    "extracted_count = extracted_count + :extracted, "
    "failed_count = failed_count + :failed, "
    "updated_at = now() "
    "WHERE run_id = :run_id"
)
_SELECT_CANCEL_REQUESTED = text(
    "SELECT cancel_requested FROM silver.skill_extraction_run " "WHERE run_id = :run_id"
)
_FINISH_RUN = text(
    "UPDATE silver.skill_extraction_run SET status = :status, "
    "error_message = :error_message, mapping_summary = :mapping_summary, "
    "finished_at = now(), updated_at = now() "
    "WHERE run_id = :run_id"
)


def _record_progress(
    engine: Engine, run_id: uuid.UUID, extracted: int, failed: int
) -> None:
    """Add counts onto a run's running totals and bump its `updated_at`.

    Called after every job, so `updated_at` doubles as the run's heartbeat.

    Args:
        engine: The app-role engine.
        run_id: The run to update.
        extracted: Jobs to add to `extracted_count`.
        failed: Jobs to add to `failed_count`.
    """
    with engine.begin() as conn:
        conn.execute(
            _UPDATE_PROGRESS,
            {"run_id": run_id, "extracted": extracted, "failed": failed},
        )


def _is_cancel_requested(engine: Engine, run_id: uuid.UUID) -> bool:
    """Check whether a run's cancel flag has been set.

    Args:
        engine: The app-role engine.
        run_id: The run to check.

    Returns:
        The current `cancel_requested` value.
    """
    with engine.connect() as conn:
        return conn.execute(_SELECT_CANCEL_REQUESTED, {"run_id": run_id}).scalar_one()


def _finish_run(
    engine: Engine,
    run_id: uuid.UUID,
    *,
    status: str,
    error_message: str | None = None,
    mapping_summary: str | None = None,
) -> None:
    """Mark a run terminal.

    Args:
        engine: The app-role engine.
        run_id: The run to finish.
        status: "completed", "cancelled", or "failed".
        error_message: Set when `status == "failed"`.
        mapping_summary: What the automatic skill mapping did, or why it did
            not run; None when the run was started without mapping.
    """
    with engine.begin() as conn:
        conn.execute(
            _FINISH_RUN,
            {
                "run_id": run_id,
                "status": status,
                "error_message": error_message,
                "mapping_summary": mapping_summary,
            },
        )


def _run_mapping(map_skills: Callable[[], str] | None) -> str | None:
    """Run the post-run skill mapping, turning any failure into a summary.

    Mapping is best-effort: the extraction it follows already succeeded and
    is committed, so a mapping failure (embedding server down, model
    mismatch) must not turn a completed run into a failed one.

    Args:
        map_skills: The mapping hook, or None if none was given.

    Returns:
        The hook's summary, a "failed" line if it raised, or None if there
        was no hook.
    """
    if map_skills is None:
        return None
    try:
        return map_skills()
    except Exception as exc:  # noqa: BLE001 — best-effort, never fails the run
        logger.exception("skill mapping after the run failed")
        return f"Skill mapping failed: {exc}"


def _unload_model(http_client: httpx.Client, ollama_base_url: str, model: str) -> None:
    """Ask Ollama to free the model's memory immediately.

    Sends `keep_alive: 0` with no `prompt`, which unloads rather than
    running a completion — verified to behave identically whether
    Ollama is native or Docker-hosted, since it's a plain HTTP call.

    Args:
        http_client: The client to issue the request with.
        ollama_base_url: Ollama's base URL, e.g. "http://ollama:11434".
        model: The model tag to unload, e.g. "llama3.1:8b".

    Raises:
        httpx.HTTPError: If Ollama is unreachable or returns an error.
    """
    response = http_client.post(
        f"{ollama_base_url}/api/generate",
        json={"model": model, "keep_alive": 0},
    )
    response.raise_for_status()


def run_loop(
    run_id: uuid.UUID,
    engine: Engine,
    *,
    adapters: dict[str, LLMAdapter],
    http_client: httpx.Client,
    ollama_base_url: str,
    model: str,
    provider: str,
    sources: list[str] | None,
    countries: list[str] | None,
    map_skills: Callable[[], str] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    pause_seconds: float = DEFAULT_PAUSE_SECONDS,
) -> None:
    """Run a started extraction run to completion, cancellation, or failure.

    Scheduled as a FastAPI `BackgroundTasks` callback by `POST
    /skills/extraction-runs` — runs off the request/response cycle.
    Repeats a bounded `write_job_skills` sub-batch, unloads the Ollama
    model, and pauses, until nothing is left pending in scope or a cancel is
    requested. Progress is committed after **every job** (so the page's
    counts and `updated_at` move per job), and a cancel is noticed before
    the next job — Stop takes effect within one job, not one sub-batch.
    Never raises — any exception from a sub-batch (a hard failure like a
    lost DB connection or an unreachable Ollama; an individual job's own
    failure is already handled inside `write_job_skills` and never raises)
    marks the run `failed` and stops.

    A job that fails is retried once, in the next pass. If a pass then makes no
    progress: with nothing ever extracted the run **fails** (Ollama or the
    model is broken for this scope); if it had extracted jobs, only
    stubborn leftovers remain, so the run **completes** and they stay pending
    for a later run.

    When the run **completes** and `map_skills` was given, it is called once
    before the run is marked finished, and its one-line result is stored on
    the run. It is skipped after a stop or a failure (so Stop stays
    responsive and a broken Ollama is not asked to embed); the next
    completed run maps everything still unmapped.

    Args:
        run_id: The run to execute — must already be `running` (i.e.
            `start_run` returned a non-zero `total_pending`).
        engine: The app-role engine.
        adapters: Every available LLM adapter, keyed by provider.
        http_client: The client used for the Ollama unload call.
        ollama_base_url: Ollama's base URL.
        model: The extraction model tag to unload between sub-batches —
            resolve via `core.llm.task_config.load_task_config
            ("skill_extraction").model`, never hardcoded.
        provider: The provider `skill_extraction` is routed to —
            resolve via `core.llm.task_config.load_task_config
            ("skill_extraction").provider`. The unload call and pause
            are Ollama-specific and are skipped unless this is
            `"ollama"`, so a non-Ollama routing never sends it a
            malformed request and fails the run.
        sources: The run's source scope.
        countries: The run's country scope.
        map_skills: Zero-argument function that maps the newly extracted
            skill strings and returns a one-line summary (see
            `core.skills.post_run_mapping.build_post_run_mapping`); None to
            skip mapping.
        batch_size: Jobs per sub-batch.
        pause_seconds: Pause after each unload.
    """

    extracted_total = 0
    failed_jobs: set[str] = set()

    def job_done(job_group_id: str, extracted: bool) -> None:
        nonlocal extracted_total
        # A job that fails is retried once in the next pass; count it as failed
        # only the first time so `failed_count` means distinct jobs. The row is
        # still touched either way, so `updated_at` stays a per-job heartbeat.
        first_failure = not extracted and job_group_id not in failed_jobs
        if not extracted:
            failed_jobs.add(job_group_id)
        extracted_total += int(extracted)
        _record_progress(engine, run_id, int(extracted), int(first_failure))

    def should_stop() -> bool:
        return _is_cancel_requested(engine, run_id)

    skipped_on_failure = MAPPING_SKIPPED_FAILED if map_skills is not None else None
    try:
        while True:
            summary = write_job_skills(
                engine,
                adapters=adapters,
                limit=batch_size,
                sources=sources,
                countries=countries,
                on_job_done=job_done,
                should_stop=should_stop,
            )
            cancelled = _is_cancel_requested(engine, run_id)
            stalled = (
                not cancelled
                and summary.extracted_jobs == 0
                and summary.failed_jobs > 0
            )
            if stalled and extracted_total == 0:
                # No forward progress and none ever made: the model or Ollama
                # is broken for this scope, not just a few stubborn jobs.
                # Looping would be a livelock (a failed job never gets a
                # `job_skill_extraction` row, so it stays "pending" and is
                # re-selected forever) — unbounded LLM spend and the
                # single-active-run slot held — so stop instead of retrying.
                _finish_run(
                    engine,
                    run_id,
                    status="failed",
                    error_message=(
                        f"{summary.failed_jobs} job(s) failed with no "
                        "progress; stopping"
                    ),
                    mapping_summary=skipped_on_failure,
                )
                return
            if provider == "ollama":
                _unload_model(http_client, ollama_base_url, model)
            if cancelled:
                _finish_run(
                    engine,
                    run_id,
                    status="cancelled",
                    mapping_summary=(
                        MAPPING_SKIPPED_STOPPED if map_skills is not None else None
                    ),
                )
                return
            if provider == "ollama":
                time.sleep(pause_seconds)
            remaining = count_pending_jobs(engine, sources=sources, countries=countries)
            # `stalled` here means the run did make progress earlier and only
            # jobs that keep failing are left: it has done all it can. They
            # stay pending for a later run, so this is a completion.
            if remaining == 0 or stalled:
                _finish_run(
                    engine,
                    run_id,
                    status="completed",
                    mapping_summary=_run_mapping(map_skills),
                )
                return
    except Exception as exc:  # noqa: BLE001 — any hard failure ends the run
        logger.exception("extraction run %s failed", run_id)
        _finish_run(
            engine,
            run_id,
            status="failed",
            error_message=str(exc),
            mapping_summary=skipped_on_failure,
        )
