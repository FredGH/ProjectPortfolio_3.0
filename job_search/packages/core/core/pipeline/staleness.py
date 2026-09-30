"""Dependency-based staleness: timestamp-order comparison against the
STAGES dependency graph -- never content-aware (spec's explicit
Non-goal). A stage is:
  - "blocked" if it has never completed AND any of its dependencies
    has never completed either (there's nothing to even be stale
    against yet);
  - "stale" if it HAS completed, but at least one dependency's own
    last-completed time is more recent than its own;
  - otherwise fresh.
A stage that has never completed but whose dependencies all have IS
NOT "blocked" by this definition -- it just has no last_completed_at
and is_stale=False, since "never run" and "stale" are different
things the UI (Task 10) shows differently (a disabled Run button with
a reason, vs. a staleness badge).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, text

from core.pipeline.registry import STAGES


@dataclass(frozen=True)
class StageState:
    """One automated stage's current state, for one user (or global).

    Attributes:
        last_completed_at: When this stage last reached `completed`,
            or None if it never has.
        last_status: The most recent run's status regardless of
            outcome ("running"/"completed"/"cancelled"/"failed"), or
            None if it has never run at all -- shown in the UI even
            when the last attempt failed.
        is_blocked: True if this stage has never completed and at
            least one of its dependencies has never completed either.
        is_stale: True if this stage has completed, but a dependency
            has completed more recently.
        stale_because: The dependency name responsible, or None.
    """

    last_completed_at: datetime | None
    last_status: str | None
    is_blocked: bool
    is_stale: bool
    stale_because: str | None


_SELECT_LAST_RUN = text(
    "SELECT status, finished_at FROM pipeline.stage_run "
    "WHERE stage = :stage AND (user_id = :user_id OR (:user_id IS NULL AND user_id IS NULL)) "
    "ORDER BY started_at DESC LIMIT 1"
)
_SELECT_LAST_COMPLETED = text(
    "SELECT finished_at FROM pipeline.stage_run "
    "WHERE stage = :stage AND (user_id = :user_id OR (:user_id IS NULL AND user_id IS NULL)) "
    "AND status = 'completed' ORDER BY finished_at DESC LIMIT 1"
)


def compute_stage_states(engine: Engine, *, user_id: UUID | None) -> dict[str, StageState]:
    """Compute every automated stage's current state for one scope.

    Args:
        engine: The app-role engine.
        user_id: Whose per-user stage runs to read; ignored (matched
            against NULL) for global stages, since a global stage's
            rows always have `user_id IS NULL`. When `user_id=None`,
            per-user stages report "never completed" (fail-closed
            behavior — a per-user stage called with no user ID has no
            rows to inspect, so all such stages appear blocked).

    Returns:
        Every `STAGES` name mapped to its `StageState`.
    """
    last_completed: dict[str, datetime | None] = {}
    last_status: dict[str, str | None] = {}
    with engine.connect() as conn:
        for name, spec in STAGES.items():
            scope_user_id = user_id if spec.per_user else None
            last_row = conn.execute(
                _SELECT_LAST_RUN, {"stage": name, "user_id": scope_user_id}
            ).first()
            last_status[name] = last_row.status if last_row is not None else None
            completed_row = conn.execute(
                _SELECT_LAST_COMPLETED, {"stage": name, "user_id": scope_user_id}
            ).first()
            last_completed[name] = (
                completed_row.finished_at if completed_row is not None else None
            )

    states: dict[str, StageState] = {}
    for name, spec in STAGES.items():
        own_completed = last_completed[name]
        if own_completed is None:
            any_dep_never_ran = any(last_completed[dep] is None for dep in spec.depends_on)
            states[name] = StageState(
                last_completed_at=None, last_status=last_status[name],
                is_blocked=any_dep_never_ran, is_stale=False, stale_because=None,
            )
            continue
        stale_because = next(
            (
                dep for dep in spec.depends_on
                if last_completed[dep] is not None and last_completed[dep] > own_completed
            ),
            None,
        )
        states[name] = StageState(
            last_completed_at=own_completed, last_status=last_status[name],
            is_blocked=False, is_stale=stale_because is not None,
            stale_because=stale_because,
        )
    return states
