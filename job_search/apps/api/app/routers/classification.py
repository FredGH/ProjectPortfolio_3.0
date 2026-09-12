"""GET /classification/jobs-to-review, POST /classification/reviews,
GET /classification/review-summary — the hand-check tooling for
PLAN.md Step 11a's "Done when" (JOB-170: hand-check 100 classifications,
require >=90% agreement).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Engine, Row, text

router = APIRouter()

# Mirrors config/category_map.yml's 7-value taxonomy and
# core.classification.seniority's 5-value band set, plus the DB check
# constraints on classification.category_review_labels (0014) — kept
# here as Literal types so an invalid value 422s at the API boundary
# instead of surfacing as a raw Postgres CheckViolation.
_Category = Literal[
    "software_engineer",
    "data_engineer",
    "data_scientist",
    "ai_ml_engineer",
    "analytics_engineer",
    "platform_devops",
    "other",
]
_SeniorityBand = Literal["junior", "mid", "senior", "lead", "principal"]


class JobToReview(BaseModel):
    """One `gold.dim_job` row, ready for a human categorisation review.

    Attributes:
        job_group_id: The job's stable identity key.
        title_for_display: The job's display title.
        title_raw: The job's source title, verbatim.
        company: The job's company name, or None.
        location: The job's location, or None.
        description: The job's description, or None.
        category: The pipeline's assigned category.
        category_confidence: Confidence in `category`, 0.0-1.0.
        category_method: Which cascade stage assigned `category`.
        qa_category: The pipeline's assigned qa_category, or None.
        seniority_band: The pipeline's assigned seniority band.
    """

    job_group_id: str
    title_for_display: str | None
    title_raw: str | None
    company: str | None
    location: str | None
    description: str | None
    category: str
    category_confidence: float
    category_method: str
    qa_category: str | None
    seniority_band: str


class ReviewRequest(BaseModel):
    """A human's verdict on one job's categorisation.

    Attributes:
        job_group_id: The job being reviewed.
        reviewed_category: The category the reviewer judges correct.
        reviewed_seniority_band: The seniority band the reviewer judges
            correct.
        reviewed_by: Free-text identifier of who reviewed it, if given.
        notes: Free-text explanation, e.g. why this disagrees with the
            pipeline's assignment.
    """

    job_group_id: str
    reviewed_category: _Category
    reviewed_seniority_band: _SeniorityBand
    reviewed_by: str | None = None
    notes: str | None = None


@router.get("/classification/jobs-to-review", response_model=list[JobToReview])
def get_jobs_to_review(
    limit: int = 100,
    engine: Engine = Depends(get_app_db_engine),
) -> list[JobToReview]:
    """Return a stratified sample of unreviewed `gold.dim_job` rows.

    Stratified across `category` x `category_method` (so no cascade
    stage or category is invisible in the sample), with low-confidence
    rows biased to the front of each bucket — the rows PLAN.md's own
    review-list guidance already flags as most likely to be wrong.

    Args:
        limit: Maximum number of jobs to return.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Up to `limit` unreviewed `JobToReview` entries.
    """
    with engine.connect() as conn:
        # Bucket count isn't fixed (it's however many (category,
        # category_method) combos currently have unreviewed rows), so
        # compute it first — same `per_bucket = max(1, limit // N)`
        # approach as GET /dedup/pairs-to-label's bootstrap-mode query,
        # applied here with a data-derived N instead of a fixed 10.
        # Deliberately no final `ORDER BY random() LIMIT` re-shuffle
        # across the whole candidate set afterwards — that would let a
        # small bucket's rows get randomly dropped even after this
        # per-bucket quota already guaranteed them a place.
        bucket_count = conn.execute(
            text(
                """
                SELECT count(DISTINCT (category, category_method))
                FROM gold.dim_job AS d
                LEFT JOIN classification.category_review_labels AS r
                    ON d.job_group_id = r.job_group_id
                WHERE r.job_group_id IS NULL AND d.category IS NOT NULL
                """
            )
        ).scalar_one()
        per_bucket = max(1, limit // max(1, bucket_count))

        rows = conn.execute(
            text(
                """
                WITH unreviewed AS (
                    SELECT d.*
                    FROM gold.dim_job AS d
                    LEFT JOIN classification.category_review_labels AS r
                        ON d.job_group_id = r.job_group_id
                    WHERE r.job_group_id IS NULL
                        AND d.category IS NOT NULL
                ),
                bucketed AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY category, category_method
                            ORDER BY category_confidence ASC, random()
                        ) AS rn
                    FROM unreviewed
                )
                SELECT *
                FROM bucketed
                WHERE rn <= :per_bucket
                ORDER BY category, category_method, rn
                LIMIT :limit
                """
            ),
            {"per_bucket": per_bucket, "limit": limit},
        ).all()

    return [
        JobToReview(
            job_group_id=row.job_group_id,
            title_for_display=row.title_for_display,
            title_raw=row.title_raw,
            company=row.company,
            location=row.location,
            description=row.description,
            category=row.category,
            category_confidence=float(row.category_confidence),
            category_method=row.category_method,
            qa_category=row.qa_category,
            seniority_band=row.seniority_band,
        )
        for row in rows
    ]


@router.post("/classification/reviews")
def post_review(
    request: ReviewRequest,
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, str]:
    """Record (or update) a human's categorisation verdict for one job.

    Args:
        request: The verdict being recorded.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO classification.category_review_labels (
                    job_group_id, reviewed_category, reviewed_seniority_band,
                    reviewed_by, notes
                ) VALUES (
                    :job_group_id, :reviewed_category, :reviewed_seniority_band,
                    :reviewed_by, :notes
                )
                ON CONFLICT (job_group_id) DO UPDATE SET
                    reviewed_category = EXCLUDED.reviewed_category,
                    reviewed_seniority_band = EXCLUDED.reviewed_seniority_band,
                    reviewed_by = EXCLUDED.reviewed_by,
                    notes = EXCLUDED.notes,
                    reviewed_at = now()
                """
            ),
            {
                "job_group_id": request.job_group_id,
                "reviewed_category": request.reviewed_category,
                "reviewed_seniority_band": request.reviewed_seniority_band,
                "reviewed_by": request.reviewed_by,
                "notes": request.notes,
            },
        )
    return {"status": "ok"}


class CategoryBreakdown(BaseModel):
    """Agreement broken down by one category or method value.

    Attributes:
        key: The category or category_method value this row covers.
        reviewed_count: How many reviews fall under this key.
        agree_count: How many of those agree with the pipeline's
            current `category`.
    """

    key: str
    reviewed_count: int
    agree_count: int


class ReviewSummary(BaseModel):
    """The current hand-check progress against JOB-170's target.

    Attributes:
        reviewed_count: Total jobs reviewed so far.
        agree_count: How many reviews agree with the pipeline's
            current `category` (computed live — never stored, since
            `category` can change on a `classify-jobs` rerun).
        agreement_rate: `agree_count / reviewed_count`, or None if
            nothing has been reviewed yet.
        by_category: Agreement broken down by the pipeline's current
            `category`.
        by_method: Agreement broken down by `category_method`.
    """

    reviewed_count: int
    agree_count: int
    agreement_rate: float | None
    by_category: list[CategoryBreakdown]
    by_method: list[CategoryBreakdown]


def _breakdown(rows: Sequence[Row[Any]], key_column: str) -> list[CategoryBreakdown]:
    """Group already-fetched review rows into per-key agreement counts.

    Args:
        rows: Rows carrying `key_column`, `reviewed_category`, and
            `current_category` (the row's current `dim_job.category`).
        key_column: Which row attribute to group by — "current_category"
            or "current_method".

    Returns:
        One `CategoryBreakdown` per distinct value of `key_column`.
    """
    totals: dict[str, int] = {}
    agrees: dict[str, int] = {}
    for row in rows:
        key = getattr(row, key_column)
        totals[key] = totals.get(key, 0) + 1
        if row.reviewed_category == row.current_category:
            agrees[key] = agrees.get(key, 0) + 1
    return [
        CategoryBreakdown(key=key, reviewed_count=count, agree_count=agrees.get(key, 0))
        for key, count in sorted(totals.items())
    ]


@router.get("/classification/review-summary", response_model=ReviewSummary)
def get_review_summary(
    engine: Engine = Depends(get_app_db_engine),
) -> ReviewSummary:
    """Compute the current agreement rate against every review so far.

    Agreement is computed live against `gold.dim_job.category` rather
    than a stored flag, since a job's category can change on a later
    `classify-jobs` rerun (silver.job_category is UPSERTed).

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The current `ReviewSummary`.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    r.reviewed_category,
                    d.category AS current_category,
                    d.category_method AS current_method
                FROM classification.category_review_labels AS r
                INNER JOIN gold.dim_job AS d
                    ON r.job_group_id = d.job_group_id
                """
            )
        ).all()

    reviewed_count = len(rows)
    agree_count = sum(
        1 for row in rows if row.reviewed_category == row.current_category
    )
    return ReviewSummary(
        reviewed_count=reviewed_count,
        agree_count=agree_count,
        agreement_rate=(agree_count / reviewed_count) if reviewed_count else None,
        by_category=_breakdown(rows, "current_category"),
        by_method=_breakdown(rows, "current_method"),
    )
