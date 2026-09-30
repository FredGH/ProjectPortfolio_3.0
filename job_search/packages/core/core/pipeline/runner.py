"""Generic run lifecycle for pipeline.stage_run — every automated stage
except extract-job-skills (Task 8 migrates its own bespoke sub-batch
loop onto this table's start_run/get_active_run/request_cancel, but
keeps its own control flow rather than using run_stage below, which
assumes a stage completes in one blocking call).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from core.pipeline.registry import StageSpec

logger = logging.getLogger(__name__)


class RunError(ValueError):
    """Base error for an invalid run-lifecycle operation."""


class RunAlreadyActive(RunError):
    """Raised when `start_run` is called while any run is active,
    system-wide — the global one-active-run-at-a-time lock."""


class RunNotFound(RunError):
    """Raised when a run id names no run."""


@dataclass(frozen=True)
class RunSnapshot:
    """One run's full state.

    Attributes:
        run_id: The run's unique id.
        stage: Which stage this run is for.
        user_id: Who this run is scoped to, or None for a global stage.
        status: "running", "completed", "cancelled", or "failed".
        params: The input arguments this run was started with.
        progress_current: Items processed so far, or None if this
            stage can't report fine-grained progress.
        progress_total: Items expected, or None likewise.
        cancel_requested: Whether a cancel has been requested.
        result: The stage's own result dict, set once terminal.
        error_message: Set only when status == "failed".
        started_at: When the run began.
        updated_at: When progress was last recorded.
        finished_at: When the run reached a terminal status, or None
            while still running.
    """

    run_id: uuid.UUID
    stage: str
    user_id: uuid.UUID | None
    status: str
    params: dict
    progress_current: int | None
    progress_total: int | None
    cancel_requested: bool
    result: dict | None
    error_message: str | None
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None


_COLUMNS = (
    "run_id, stage, user_id, status, params, progress_current, progress_total, "
    "cancel_requested, result, error_message, started_at, updated_at, finished_at"
)
_SELECT_ACTIVE = text(f"SELECT {_COLUMNS} FROM pipeline.stage_run WHERE status = 'running'")
_SELECT_ONE = text(f"SELECT {_COLUMNS} FROM pipeline.stage_run WHERE run_id = :run_id")
_INSERT_RUNNING = text(
    "INSERT INTO pipeline.stage_run (stage, user_id, status, params) "
    "VALUES (:stage, :user_id, 'running', CAST(:params AS jsonb)) RETURNING run_id"
)
_REQUEST_CANCEL = text(
    "UPDATE pipeline.stage_run SET cancel_requested = TRUE "
    "WHERE run_id = :run_id AND status = 'running'"
)
_SELECT_CANCEL_REQUESTED = text(
    "SELECT cancel_requested FROM pipeline.stage_run WHERE run_id = :run_id"
)
_FINISH_RUN = text(
    "UPDATE pipeline.stage_run SET status = :status, result = CAST(:result AS jsonb), "
    "error_message = :error_message, finished_at = now(), updated_at = now() "
    "WHERE run_id = :run_id"
)


def _row_to_snapshot(row) -> RunSnapshot:
    import json

    return RunSnapshot(
        run_id=row.run_id,
        stage=row.stage,
        user_id=row.user_id,
        status=row.status,
        params=row.params if isinstance(row.params, dict) else json.loads(row.params),
        progress_current=row.progress_current,
        progress_total=row.progress_total,
        cancel_requested=row.cancel_requested,
        result=row.result
        if row.result is None or isinstance(row.result, dict)
        else json.loads(row.result),
        error_message=row.error_message,
        started_at=row.started_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def start_run(
    engine: Engine, *, stage: str, user_id: uuid.UUID | None, params: dict
) -> uuid.UUID:
    """Insert a `running` row for `stage`.

    Args:
        engine: The app-role engine.
        stage: Which stage this run is for.
        user_id: Who this run is scoped to, or None for a global stage.
        params: This run's input arguments, stored as JSONB.

    Returns:
        The new run's id.

    Raises:
        RunAlreadyActive: If any run, for any stage, is already `running`
            — the system-wide lock.
    """
    import json

    try:
        with engine.begin() as conn:
            return conn.execute(
                _INSERT_RUNNING,
                {"stage": stage, "user_id": user_id, "params": json.dumps(params)},
            ).scalar_one()
    except IntegrityError as exc:
        raise RunAlreadyActive("a pipeline run is already active") from exc


def get_active_run(engine: Engine) -> RunSnapshot | None:
    """Return the currently active run, if any.

    Args:
        engine: The app-role engine.

    Returns:
        The active run's snapshot, or None if nothing is running.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ACTIVE).first()
    return _row_to_snapshot(row) if row is not None else None


def get_run(engine: Engine, run_id: uuid.UUID) -> RunSnapshot | None:
    """Return one run's snapshot by id, active or finished.

    Args:
        engine: The app-role engine.
        run_id: The run to look up.

    Returns:
        The run's snapshot, or None if `run_id` is unknown.
    """
    with engine.connect() as conn:
        row = conn.execute(_SELECT_ONE, {"run_id": run_id}).first()
    return _row_to_snapshot(row) if row is not None else None


def request_cancel(engine: Engine, run_id: uuid.UUID) -> None:
    """Ask a running run to stop.

    Args:
        engine: The app-role engine.
        run_id: The run to cancel.

    Raises:
        RunNotFound: If `run_id` is unknown, or names a run that is not
            currently `running`.
    """
    with engine.begin() as conn:
        result = conn.execute(_REQUEST_CANCEL, {"run_id": run_id})
    if result.rowcount == 0:
        raise RunNotFound(f"no active run with id {run_id}")


def run_stage(run_id: uuid.UUID, engine: Engine, spec: StageSpec, params: dict) -> None:
    """Run a started run to completion, cancellation, or failure.

    Scheduled as a FastAPI `BackgroundTasks` callback by `POST
    /pipeline/stages/{stage}/run` (Task 9) for every stage except
    `extract-job-skills`, which most stages' own underlying function
    (a single blocking call, no natural per-item progress hook) makes
    the right shape for: check cancel, call `spec.run(params)` once,
    record the result. Never raises — any exception from `spec.run`
    marks the run `failed` with its message and stops.

    Args:
        run_id: The run to execute — must already be `running`.
        engine: The app-role engine.
        spec: Which stage to run.
        params: This run's input arguments (same dict passed to
            `start_run`, threaded through again here since
            `BackgroundTasks` needs a fresh call, not a stored closure).
    """
    import json

    with engine.connect() as conn:
        cancelled = conn.execute(_SELECT_CANCEL_REQUESTED, {"run_id": run_id}).scalar_one()
    if cancelled:
        with engine.begin() as conn:
            conn.execute(
                _FINISH_RUN,
                {"run_id": run_id, "status": "cancelled", "result": None, "error_message": None},
            )
        return
    try:
        result = spec.run(params)
    except Exception as exc:  # noqa: BLE001 — any hard failure ends the run
        logger.exception("pipeline run %s (%s) failed", run_id, spec.name)
        with engine.begin() as conn:
            conn.execute(
                _FINISH_RUN,
                {
                    "run_id": run_id,
                    "status": "failed",
                    "result": None,
                    "error_message": str(exc),
                },
            )
        return
    with engine.begin() as conn:
        conn.execute(
            _FINISH_RUN,
            {
                "run_id": run_id,
                "status": "completed",
                "result": json.dumps(result),
                "error_message": None,
            },
        )
