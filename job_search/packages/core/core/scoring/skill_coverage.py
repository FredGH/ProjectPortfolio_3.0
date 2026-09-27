"""Stage 3 of the scoring funnel: skill coverage (PLAN.md Step 15).

Independent of stages 2/2b — only needs Step 14's silver.silver__bridge_job_skill
and the CV truth base's skills. A must-have hit counts more than a
nice-to-have hit; a skill's contribution decays with how long since it was
last used, since a skill last touched a decade ago is not the same asset as
one used last quarter.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Engine, text

from core.cv.store import read_truth_base
from core.db.session import session_scope

_MUST_HAVE_WEIGHT = 2.0
_NICE_TO_HAVE_WEIGHT = 1.0
_DECAY_START_YEARS = 5.0
"""Years of disuse after which a skill's contribution starts decaying."""
_DECAY_FLOOR_YEARS = 10.0
"""Years of disuse at which a skill's contribution reaches zero."""

_SELECT_CANDIDATE_JOBS = text(
    "SELECT job_group_id FROM scoring.job_score "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_SELECT_JOB_SKILLS = text(
    "SELECT skill_id, requirement_level FROM silver.silver__bridge_job_skill "
    "WHERE job_group_id = :job_group_id"
)
_UPDATE_COVERAGE = text(
    "UPDATE scoring.job_score SET skill_coverage_score = :score "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def _recency_weight(last_used: str | None, as_of: datetime.date) -> float:
    """How much a skill counts, based on how recently it was used.

    Args:
        last_used: "YYYY-MM", or None if unstated (treated as fully current
            — a CV that never states dates should not be penalised for it).
        as_of: The date to measure recency against.

    Returns:
        1.0 for a skill used within `_DECAY_START_YEARS`, linearly falling
        to 0.0 at `_DECAY_FLOOR_YEARS`, 1.0 if `last_used` is unstated.
    """
    if last_used is None:
        return 1.0
    year, month = (int(p) for p in last_used.split("-"))
    used_date = datetime.date(year, month, 1)
    years_since = (as_of - used_date).days / 365.25
    if years_since <= _DECAY_START_YEARS:
        return 1.0
    if years_since >= _DECAY_FLOOR_YEARS:
        return 0.0
    span = _DECAY_FLOOR_YEARS - _DECAY_START_YEARS
    return 1.0 - (years_since - _DECAY_START_YEARS) / span


def run_skill_coverage(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    as_of: datetime.date | None = None,
) -> int:
    """Score skill coverage for every hard-filter-passing job.

    Every candidate job's `skill_coverage_score` is unconditionally
    rewritten on each run: NULL for a job with no `silver.silver__bridge_job_skill`
    rows (no data to score coverage from — "unknown," not "zero coverage"),
    the weighted-hit/weighted-total ratio otherwise. This matters on a
    re-run: a job whose bridge rows disappeared (e.g. a re-extraction wiped
    them) must not be left with a stale score from a previous run.

    Args:
        app_engine: The app-role engine (RLS-enforced for the CV read and
            the per-user score write; `bridge_job_skill` itself is shared).
        user_id: Whose CV skills to match against.
        as_of: The date to measure recency decay against; defaults to today.
            Injectable so tests are deterministic.

    Returns:
        The number of jobs scored with a non-NULL `skill_coverage_score`.

    Raises:
        LookupError: If the user has no CV truth base.
    """
    as_of = as_of or datetime.date.today()
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")
    cv_skills = {
        s.canonical_id: _recency_weight(s.last_used, as_of)
        for s in stored.truth_base.skills
        if s.canonical_id is not None
    }
    with session_scope(app_engine, user_id=user_id) as conn:
        job_ids = [
            r.job_group_id
            for r in conn.execute(_SELECT_CANDIDATE_JOBS, {"user_id": user_id})
        ]
    with app_engine.connect() as conn:
        job_skills_by_job = {
            job_group_id: conn.execute(
                _SELECT_JOB_SKILLS, {"job_group_id": job_group_id}
            )
            .mappings()
            .all()
            for job_group_id in job_ids
        }
    scored = 0
    with session_scope(app_engine, user_id=user_id) as conn:
        for job_group_id, job_skills in job_skills_by_job.items():
            if not job_skills:
                # No bridge rows means no data to score coverage from —
                # "unknown," not "zero coverage." Rewrite to NULL
                # unconditionally so a re-run clears a stale score left by
                # bridge rows that have since disappeared.
                conn.execute(
                    _UPDATE_COVERAGE,
                    {"user_id": user_id, "job_group_id": job_group_id, "score": None},
                )
                continue
            total = hit = 0.0
            for row in job_skills:
                weight = (
                    _MUST_HAVE_WEIGHT
                    if row["requirement_level"] == "must_have"
                    else _NICE_TO_HAVE_WEIGHT
                )
                total += weight
                if row["skill_id"] in cv_skills:
                    hit += weight * cv_skills[row["skill_id"]]
            conn.execute(
                _UPDATE_COVERAGE,
                {
                    "user_id": user_id,
                    "job_group_id": job_group_id,
                    "score": hit / total,
                },
            )
            scored += 1
    return scored
