"""Persistence for tailoring runs and their orphan bullets (Step 17).

Every function takes the app-role engine and a `user_id` and runs inside
`session_scope`, so both tables' RLS applies.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, Engine, text

from core.db.session import session_scope
from core.tailoring.schema import TailoredDocument


class StaleDecisionError(RuntimeError):
    """A decision cannot be saved because the run or orphan has moved on.

    Raised when the orphan was already decided (or is not visible to this
    user), or when the stored document differs from the one the decision
    was computed from.
    """


class RunAlreadyFinishedError(RuntimeError):
    """A run cannot be finished because it is no longer `generating`.

    Raised when the run was already finished (or is not visible to this
    user), so a late or duplicate finish never overwrites an outcome or
    duplicates its orphan rows.
    """


@dataclass(frozen=True)
class OrphanDraft:
    """A line the loop could not trace, about to be saved for a decision.

    Attributes:
        kind: `orphan` (no valid evidence ref) or `unsupported` (the critic
            rejected a reworded line).
        section: `summary` or `experience`.
        experience_index: The role, for an experience line.
        bullet_index: The line's position in that role.
        text: The line's text at the time of the run.
        claimed_refs: The `bullet_id`s the Tailor cited.
        issue: What is wrong, in words.
    """

    kind: str
    section: str
    experience_index: int | None
    bullet_index: int | None
    text: str
    claimed_refs: list[str]
    issue: str | None


@dataclass(frozen=True)
class StoredOrphan:
    """A saved orphan row.

    Attributes:
        id: Row id.
        tailored_cv_id: The run it belongs to.
        kind: `orphan` or `unsupported`.
        section: `summary` or `experience`.
        experience_index: The role, for an experience line.
        bullet_index: The line's position in that role.
        text: The line's text when recorded.
        claimed_refs: The `bullet_id`s the Tailor cited.
        issue: What is wrong, in words.
        status: `pending`, `linked` or `rejected`.
        evidence_ref: The truth-base bullet it was linked to, if any.
    """

    id: uuid.UUID
    tailored_cv_id: uuid.UUID
    kind: str
    section: str
    experience_index: int | None
    bullet_index: int | None
    text: str
    claimed_refs: list[str]
    issue: str | None
    status: str
    evidence_ref: str | None


@dataclass(frozen=True)
class StoredRun:
    """A saved tailoring run.

    Attributes:
        id: Run id.
        user_id: The owner.
        job_group_id: The target job.
        truth_base_version: The CV version it was built from.
        target_title: The injected title.
        status: `generating`, `needs_review`, `approved`, `failed`
            or `cancelled`.
        attempts: Tailor attempts made.
        error_message: Why it failed, if it did.
        document: The assembled document, once there is one.
        tailor_model: Model used by the Tailor.
        tailor_prompt_version: Tailor prompt version.
        critic_model: Model used by the critic.
        critic_prompt_version: Critic prompt version.
        created_at: When the run started.
        updated_at: When the row last changed (progress writes bump it).
        progress: Live progress written while `generating` (None until the
            first update; the API only exposes it while generating). A dict:
            `{"attempt": int, "max_attempts": int, "phase": "tailoring" |
            "checking" | "critic" | "saving", "message": str,
            "phase_started_at": ISO-8601 UTC string, "history": [str, ...]}`.
        orphans: Every orphan row, in position order.
        tailor_backend: `claude`, `native` or `docker`; None for a run made
            before the backend selector existed.
        usage: Token use and estimated cost, None until the first LLM call
            finishes. A dict: `{"calls": [{"task": "cv_tailoring" |
            "fabrication_critic", "model": str, "input_tokens": int,
            "output_tokens": int, "cost_usd": float | None}, ...],
            "input_tokens": int, "output_tokens": int,
            "cost_usd": float | None}`; the totals' `cost_usd` is None if
            any call's cost is None (unknown model).
    """

    id: uuid.UUID
    user_id: uuid.UUID
    job_group_id: str
    truth_base_version: int
    target_title: str
    status: str
    attempts: int
    error_message: str | None
    document: TailoredDocument | None
    tailor_model: str | None
    tailor_prompt_version: str | None
    critic_model: str | None
    critic_prompt_version: str | None
    created_at: datetime
    updated_at: datetime
    progress: dict | None
    orphans: list[StoredOrphan]
    tailor_backend: str | None = None
    usage: dict | None = None


_ORPHAN_COLUMNS = (
    "id, tailored_cv_id, kind, section, experience_index, bullet_index, text, "
    "claimed_refs, issue, status, evidence_ref"
)


def _orphan_from_row(row: object) -> StoredOrphan:
    """Build a `StoredOrphan` from a result row.

    Args:
        row: A row selected with `_ORPHAN_COLUMNS`.

    Returns:
        The dataclass.
    """
    return StoredOrphan(
        id=row.id,
        tailored_cv_id=row.tailored_cv_id,
        kind=row.kind,
        section=row.section,
        experience_index=row.experience_index,
        bullet_index=row.bullet_index,
        text=row.text,
        claimed_refs=list(row.claimed_refs or []),
        issue=row.issue,
        status=row.status,
        evidence_ref=row.evidence_ref,
    )


def create_run(
    engine: Engine,
    user_id: uuid.UUID,
    *,
    job_group_id: str,
    truth_base_version: int,
    target_title: str,
    tailor_backend: str | None = None,
) -> uuid.UUID:
    """Insert a new run in `generating` status.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        job_group_id: The target job.
        truth_base_version: The CV version the run will use.
        target_title: The injected title.
        tailor_backend: The backend id the Tailor will run on, if chosen.

    Returns:
        The new run's id.
    """
    with session_scope(engine, user_id=user_id) as conn:
        return conn.execute(
            text(
                "INSERT INTO tailoring.tailored_cv "
                "(user_id, job_group_id, truth_base_version, target_title, "
                "tailor_backend) "
                "VALUES (:user_id, :job_group_id, :version, :title, :backend) "
                "RETURNING id"
            ),
            {
                "backend": tailor_backend,
                "user_id": user_id,
                "job_group_id": job_group_id,
                "version": truth_base_version,
                "title": target_title,
            },
        ).scalar_one()


def latest_run_id(
    engine: Engine, user_id: uuid.UUID, job_group_id: str
) -> uuid.UUID | None:
    """Find the user's most recent run for a job.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        job_group_id: The target job.

    Returns:
        The newest run's id, or None if the user has no run for the job.
    """
    with session_scope(engine, user_id=user_id) as conn:
        return conn.execute(
            text(
                "SELECT id FROM tailoring.tailored_cv "
                "WHERE job_group_id = :job_group_id "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"job_group_id": job_group_id},
        ).scalar_one_or_none()


def set_progress(
    engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID, progress: dict
) -> None:
    """Record live progress on a run that is still `generating`.

    A run that already finished (or is not visible to this user) is left
    untouched: late progress is silently ignored, never an error.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run_id: The run.
        progress: The progress dict (see `StoredRun.progress`).
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "UPDATE tailoring.tailored_cv "
                "SET progress = CAST(:p AS jsonb), updated_at = now() "
                "WHERE id = :run_id AND user_id = :user_id "
                "AND status = 'generating'"
            ),
            {"p": json.dumps(progress), "run_id": run_id, "user_id": user_id},
        )


def set_usage(
    engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID, usage: dict
) -> None:
    """Record token use and cost on a run that is still `generating`.

    A run that already finished (or is not visible to this user) is left
    untouched: a late write is silently ignored, never an error.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run_id: The run.
        usage: The usage dict (see `StoredRun.usage`).
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "UPDATE tailoring.tailored_cv "
                "SET usage = CAST(:u AS jsonb) "
                "WHERE id = :run_id AND user_id = :user_id "
                "AND status = 'generating'"
            ),
            {"u": json.dumps(usage), "run_id": run_id, "user_id": user_id},
        )


def cancel_run(engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    """Cancel a run that is still `generating`.

    Idempotent and safe: a finished, already cancelled or foreign run is
    left untouched.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run_id: The run to cancel.

    Returns:
        True iff this call moved the run from `generating` to `cancelled`.
    """
    with session_scope(engine, user_id=user_id) as conn:
        result = conn.execute(
            text(
                "UPDATE tailoring.tailored_cv SET status = 'cancelled', "
                "attempts = CASE WHEN jsonb_typeof(progress->'attempt') = 'number' "
                "THEN (progress->>'attempt')::int ELSE attempts END, "
                "progress = NULL, updated_at = now() "
                "WHERE id = :run_id AND user_id = :user_id "
                "AND status = 'generating'"
            ),
            {"run_id": run_id, "user_id": user_id},
        )
        return result.rowcount == 1


def is_cancelled(engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID) -> bool:
    """Tell whether a run has been cancelled.

    Args:
        engine: The app-role engine.
        user_id: The owner (RLS hides other users' runs).
        run_id: The run.

    Returns:
        True iff the run exists for this user with status `cancelled`.
    """
    with session_scope(engine, user_id=user_id) as conn:
        status = conn.execute(
            text("SELECT status FROM tailoring.tailored_cv WHERE id = :run_id"),
            {"run_id": run_id},
        ).scalar_one_or_none()
    return status == "cancelled"


def finish_run(
    engine: Engine,
    user_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    status: str,
    document: TailoredDocument | None,
    orphans: list[OrphanDraft],
    attempts: int,
    tailor_model: str | None = None,
    tailor_prompt_version: str | None = None,
    critic_model: str | None = None,
    critic_prompt_version: str | None = None,
    error_message: str | None = None,
    usage: dict | None = None,
) -> None:
    """Record a run's outcome and its orphan rows in one transaction.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        run_id: The run to finish.
        status: `needs_review`, `approved` or `failed`.
        document: The assembled document, or None for a failed run.
        orphans: The lines awaiting a decision.
        attempts: Tailor attempts made.
        tailor_model: Model used by the Tailor.
        tailor_prompt_version: Tailor prompt version.
        critic_model: Model used by the critic.
        critic_prompt_version: Critic prompt version.
        error_message: Why the run failed, if it did.
        usage: The run's token use and cost (see `StoredRun.usage`); None
            leaves whatever `set_usage` already recorded.

    Raises:
        RunAlreadyFinishedError: If the run is not `generating` (already
            finished) or not visible to this user. Nothing is written.
    """
    with session_scope(engine, user_id=user_id) as conn:
        finished = conn.execute(
            text(
                "UPDATE tailoring.tailored_cv SET status = :status, "
                "content = CAST(:content AS jsonb), attempts = :attempts, "
                "stretch = CAST(:stretch AS jsonb), tailor_model = :tailor_model, "
                "tailor_prompt_version = :tailor_prompt_version, "
                "critic_model = :critic_model, "
                "critic_prompt_version = :critic_prompt_version, "
                "error_message = :error_message, "
                "usage = COALESCE(CAST(:usage AS jsonb), usage), "
                "updated_at = now() "
                "WHERE id = :run_id AND user_id = :user_id "
                "AND status = 'generating'"
            ),
            {
                "status": status,
                "content": document.model_dump_json() if document else None,
                "attempts": attempts,
                "stretch": document.stretch.model_dump_json() if document else None,
                "tailor_model": tailor_model,
                "tailor_prompt_version": tailor_prompt_version,
                "critic_model": critic_model,
                "critic_prompt_version": critic_prompt_version,
                "error_message": error_message,
                "usage": json.dumps(usage) if usage is not None else None,
                "run_id": run_id,
                "user_id": user_id,
            },
        )
        if finished.rowcount != 1:
            raise RunAlreadyFinishedError(
                f"run {run_id} is not generating (already finished or not visible)"
            )
        for draft in orphans:
            conn.execute(
                text(
                    "INSERT INTO tailoring.orphan_bullet "
                    "(tailored_cv_id, user_id, kind, section, experience_index, "
                    "bullet_index, text, claimed_refs, issue) "
                    "VALUES (:run_id, :user_id, :kind, :section, :experience_index, "
                    ":bullet_index, :text, :claimed_refs, :issue)"
                ),
                {
                    "run_id": run_id,
                    "user_id": user_id,
                    "kind": draft.kind,
                    "section": draft.section,
                    "experience_index": draft.experience_index,
                    "bullet_index": draft.bullet_index,
                    "text": draft.text,
                    "claimed_refs": draft.claimed_refs,
                    "issue": draft.issue,
                },
            )


def read_run(engine: Engine, user_id: uuid.UUID, run_id: uuid.UUID) -> StoredRun | None:
    """Read a run with its document and orphans.

    Args:
        engine: The app-role engine.
        user_id: The owner (RLS hides other users' runs).
        run_id: The run to read.

    Returns:
        The `StoredRun`, or None if it does not exist for this user.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                "SELECT id, user_id, job_group_id, truth_base_version, target_title, "
                "status, attempts, error_message, content, tailor_model, "
                "tailor_prompt_version, critic_model, critic_prompt_version, "
                "created_at, updated_at, progress, tailor_backend, usage "
                "FROM tailoring.tailored_cv WHERE id = :run_id"
            ),
            {"run_id": run_id},
        ).one_or_none()
        if row is None:
            return None
        orphan_rows = conn.execute(
            text(
                f"SELECT {_ORPHAN_COLUMNS} FROM tailoring.orphan_bullet "
                "WHERE tailored_cv_id = :run_id "
                "ORDER BY section DESC, experience_index, bullet_index, "
                "(decided_at IS NOT NULL), decided_at, id"
            ),
            {"run_id": run_id},
        ).all()
    return StoredRun(
        id=row.id,
        user_id=row.user_id,
        job_group_id=row.job_group_id,
        truth_base_version=row.truth_base_version,
        target_title=row.target_title,
        status=row.status,
        attempts=row.attempts,
        error_message=row.error_message,
        document=(
            TailoredDocument.model_validate(row.content)
            if row.content is not None
            else None
        ),
        tailor_model=row.tailor_model,
        tailor_prompt_version=row.tailor_prompt_version,
        critic_model=row.critic_model,
        critic_prompt_version=row.critic_prompt_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        progress=row.progress,
        orphans=[_orphan_from_row(r) for r in orphan_rows],
        tailor_backend=row.tailor_backend,
        usage=row.usage,
    )


def read_orphan(
    engine: Engine, user_id: uuid.UUID, orphan_id: uuid.UUID
) -> StoredOrphan | None:
    """Read one orphan row.

    Args:
        engine: The app-role engine.
        user_id: The owner (RLS hides other users' rows).
        orphan_id: The orphan to read.

    Returns:
        The `StoredOrphan`, or None if it does not exist for this user.
    """
    with session_scope(engine, user_id=user_id) as conn:
        row = conn.execute(
            text(
                f"SELECT {_ORPHAN_COLUMNS} FROM tailoring.orphan_bullet "
                "WHERE id = :orphan_id"
            ),
            {"orphan_id": orphan_id},
        ).one_or_none()
    return _orphan_from_row(row) if row is not None else None


def _shift_later_bullets(
    conn: Connection, run_id: uuid.UUID, experience_index: int, bullet_index: int
) -> None:
    """Close the gap left by a removed bullet in the run's other orphans.

    Args:
        conn: The open transaction.
        run_id: The run.
        experience_index: The role the bullet was removed from.
        bullet_index: The removed bullet's position.
    """
    conn.execute(
        text(
            "UPDATE tailoring.orphan_bullet SET bullet_index = bullet_index - 1 "
            "WHERE tailored_cv_id = :run_id AND section = 'experience' "
            "AND experience_index = :experience_index "
            "AND bullet_index > :bullet_index"
        ),
        {
            "run_id": run_id,
            "experience_index": experience_index,
            "bullet_index": bullet_index,
        },
    )


def save_decision(
    engine: Engine,
    user_id: uuid.UUID,
    *,
    orphan: StoredOrphan,
    status: str,
    evidence_ref: str | None,
    document: TailoredDocument,
    base_document: TailoredDocument,
    removed_position: tuple[int, int] | None,
) -> str:
    """Persist one orphan decision and the updated document atomically.

    Args:
        engine: The app-role engine.
        user_id: The owner.
        orphan: The orphan being decided.
        status: `linked` or `rejected`.
        evidence_ref: The linked bullet id, for a link.
        document: The document after the decision was applied.
        base_document: The document the decision was computed from; the
            stored document must still equal it (compare-and-swap).
        removed_position: `(experience_index, bullet_index)` of a removed
            line, so later orphans in that role can be re-indexed.

    Returns:
        The run's new status: `approved` when no orphan is left pending,
        else `needs_review`.

    Raises:
        StaleDecisionError: If the run is not visible, the stored document
            differs from `base_document`, or the orphan is no longer
            pending at the recorded position (already decided, shifted by
            another decision, or not visible to this user).
    """
    with session_scope(engine, user_id=user_id) as conn:
        locked = conn.execute(
            text(
                "SELECT content FROM tailoring.tailored_cv "
                "WHERE id = :run_id FOR UPDATE"
            ),
            {"run_id": orphan.tailored_cv_id},
        ).one_or_none()
        if locked is None or locked.content is None:
            raise StaleDecisionError("this tailored CV is not available — reload")
        if TailoredDocument.model_validate(locked.content) != base_document:
            raise StaleDecisionError(
                "the tailored CV changed since you opened it — reload"
            )
        updated = conn.execute(
            text(
                "UPDATE tailoring.orphan_bullet SET status = :status, "
                "evidence_ref = :evidence_ref, decided_at = now() "
                "WHERE id = :orphan_id AND tailored_cv_id = :run_id "
                "AND status = 'pending' "
                # The position the decision was computed for must still be
                # the orphan's: a same-role removal may have shifted it.
                "AND experience_index IS NOT DISTINCT FROM "
                "CAST(:experience_index AS integer) "
                "AND bullet_index IS NOT DISTINCT FROM CAST(:bullet_index AS integer)"
            ),
            {
                "status": status,
                "evidence_ref": evidence_ref,
                "orphan_id": orphan.id,
                "run_id": orphan.tailored_cv_id,
                "experience_index": orphan.experience_index,
                "bullet_index": orphan.bullet_index,
            },
        )
        if updated.rowcount != 1:
            raise StaleDecisionError(
                "this line was already decided or has moved since you opened "
                "it — reload"
            )
        if removed_position is not None:
            _shift_later_bullets(
                conn, orphan.tailored_cv_id, removed_position[0], removed_position[1]
            )
        pending = conn.execute(
            text(
                "SELECT count(*) FROM tailoring.orphan_bullet "
                "WHERE tailored_cv_id = :run_id AND status = 'pending'"
            ),
            {"run_id": orphan.tailored_cv_id},
        ).scalar_one()
        run_status = "approved" if pending == 0 else "needs_review"
        conn.execute(
            text(
                "UPDATE tailoring.tailored_cv SET content = CAST(:content AS jsonb), "
                "status = :status, updated_at = now() WHERE id = :run_id"
            ),
            {
                "content": document.model_dump_json(),
                "status": run_status,
                "run_id": orphan.tailored_cv_id,
            },
        )
    return run_status
