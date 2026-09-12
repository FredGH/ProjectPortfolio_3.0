"""DB-backed golden-set loader for job_categorisation — reads JOB-170's
hand-check table directly rather than duplicating it into a file (see
core.evals.golden's module docstring for why).
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.evals.golden import GoldenCase

_SELECT_REVIEWED_CASES = text(
    """
    SELECT r.job_group_id, d.title_raw, r.reviewed_category
    FROM classification.category_review_labels AS r
    INNER JOIN gold.dim_job AS d ON r.job_group_id = d.job_group_id
    """
)

# A `job_group_ids`-scoped variant of _SELECT_REVIEWED_CASES — same
# reasoning as write_job_category.py's own _SELECT_UNCLASSIFIED_SCOPED:
# a test seeding fixture rows into this SHARED table must not also
# pick up whatever unrelated real rows already exist there.
_SELECT_REVIEWED_CASES_SCOPED = text(
    """
    SELECT r.job_group_id, d.title_raw, r.reviewed_category
    FROM classification.category_review_labels AS r
    INNER JOIN gold.dim_job AS d ON r.job_group_id = d.job_group_id
    WHERE r.job_group_id = ANY(:job_group_ids)
    """
)


def load_job_categorisation_golden_set(
    engine: Engine, *, job_group_ids: list[str] | None = None
) -> list[GoldenCase]:
    """Load every hand-checked job_categorisation case.

    Args:
        engine: An engine with SELECT on `classification.
            category_review_labels` and `gold.dim_job`.
        job_group_ids: Restrict to these job_group_ids only, instead of
            every reviewed row. `None` (the default, and what
            production `run_eval` calls always pass) loads everything.

    Returns:
        One `GoldenCase` per reviewed job, keyed by `job_group_id`.
        Empty until JOB-170's hand-check has actually been done through
        the Categorisation Review page.
    """
    with engine.connect() as conn:
        if job_group_ids is None:
            rows = conn.execute(_SELECT_REVIEWED_CASES).all()
        else:
            rows = conn.execute(
                _SELECT_REVIEWED_CASES_SCOPED, {"job_group_ids": job_group_ids}
            ).all()
    return [
        GoldenCase(
            case_id=row.job_group_id,
            input={"title": row.title_raw},
            expected={"category": row.reviewed_category},
        )
        for row in rows
    ]
