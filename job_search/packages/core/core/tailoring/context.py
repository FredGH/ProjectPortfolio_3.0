"""Database reads for tailoring: the target job's context and the user's
top-scored candidate jobs (Step 17)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Engine, text

from core.db.session import session_scope
from core.tailoring.schema import JobContext, JobSkill

_SELECT_JOB = text(
    "SELECT job_group_id, title_for_display, company, description "
    "FROM gold.dim_job WHERE job_group_id = :job_group_id"
)
# `must_have` first (requirement_level = 'must_have' is true for them and
# sorts first under DESC), then the most-mentioned, then alphabetical so
# the order is stable. The label falls back to the skill id when the
# skill has no silver__skill row, so a job is never left with an
# unlabelled skill.
_SELECT_JOB_SKILLS = text(
    "SELECT b.skill_id, COALESCE(s.canonical_label, b.skill_id) AS label, "
    "b.requirement_level "
    "FROM silver.silver__bridge_job_skill AS b "
    "LEFT JOIN silver.silver__skill AS s ON s.skill_id = b.skill_id "
    "WHERE b.job_group_id = :job_group_id "
    "ORDER BY (b.requirement_level = 'must_have') DESC NULLS LAST, "
    "b.mention_count DESC NULLS LAST, label"
)
# The user's latest tailoring run per job comes from a LATERAL subquery;
# both tailoring.tailored_cv and scoring.job_score are RLS-scoped, so this
# must run inside session_scope for the user.
_SELECT_CANDIDATES = text(
    "SELECT js.job_group_id, d.title_for_display, d.company, js.final_score, "
    "t.id AS latest_run_id, t.status AS latest_status "
    "FROM scoring.job_score AS js "
    "JOIN gold.dim_job AS d ON d.job_group_id = js.job_group_id "
    "LEFT JOIN LATERAL ("
    "  SELECT id, status FROM tailoring.tailored_cv "
    "  WHERE user_id = js.user_id AND job_group_id = js.job_group_id "
    "  ORDER BY created_at DESC LIMIT 1"
    ") AS t ON true "
    "WHERE js.user_id = :user_id AND js.hard_filter_passed = true "
    "AND js.final_score IS NOT NULL "
    "ORDER BY js.final_score DESC, js.job_group_id "
    "LIMIT :limit"
)


@dataclass(frozen=True)
class CandidateJob:
    """One job the user could tailor a CV for.

    Attributes:
        job_group_id: The job's id.
        title_for_display: The job's display title (may be None).
        company: The employer (may be None).
        final_score: The user's blended score for the job.
        latest_run_id: The user's most recent tailoring run for it, if any.
        latest_status: That run's status, if any.
    """

    job_group_id: str
    title_for_display: str | None
    company: str | None
    final_score: float
    latest_run_id: uuid.UUID | None
    latest_status: str | None


def load_job_context(engine: Engine, job_group_id: str) -> JobContext | None:
    """Load what the Tailor and critic need to know about a job.

    Args:
        engine: The app-role engine.
        job_group_id: The job to load.

    Returns:
        The `JobContext`, or None when no such job exists. A job whose
        `title_for_display` is NULL loads with that field None — callers
        must treat that as "cannot be tailored".
    """
    with engine.connect() as conn:
        job = conn.execute(_SELECT_JOB, {"job_group_id": job_group_id}).one_or_none()
        if job is None:
            return None
        skills = [
            JobSkill(
                skill_id=row.skill_id,
                label=row.label,
                requirement_level=row.requirement_level,
            )
            for row in conn.execute(_SELECT_JOB_SKILLS, {"job_group_id": job_group_id})
        ]
    return JobContext(
        job_group_id=job.job_group_id,
        title_for_display=job.title_for_display,
        company=job.company,
        description=job.description or "",
        skills=skills,
    )


def list_candidates(
    engine: Engine, user_id: uuid.UUID, *, limit: int = 25
) -> list[CandidateJob]:
    """List a user's top-scored jobs, best first.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose scores to read.
        limit: Maximum jobs to return.

    Returns:
        Jobs that passed the hard filters and have a final score, each with
        the user's latest tailoring run for it, if any.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            _SELECT_CANDIDATES, {"user_id": user_id, "limit": limit}
        ).all()
    return [
        CandidateJob(
            job_group_id=row.job_group_id,
            title_for_display=row.title_for_display,
            company=row.company,
            final_score=float(row.final_score),
            latest_run_id=row.latest_run_id,
            latest_status=row.latest_status,
        )
        for row in rows
    ]
