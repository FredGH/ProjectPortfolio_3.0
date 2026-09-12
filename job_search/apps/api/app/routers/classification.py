"""GET /classification/jobs-to-review, POST /classification/reviews,
GET /classification/review-summary — the hand-check tooling for
PLAN.md Step 11a's "Done when" (JOB-170: hand-check 100 classifications,
require >=90% agreement).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Engine, Row, text

from core.classification.review import UNRESOLVED_COUNTRY as _UNRESOLVED_COUNTRY

router = APIRouter()

# Shared three-state `country_iso` filter clause for a query aliasing
# `gold.dim_job` as `d`: no filter when the param is NULL, NULL-only
# when it's the `_UNRESOLVED_COUNTRY` sentinel, exact match otherwise.
# CAST(...AS text) throughout, not a bare `:country_iso` — psycopg
# can't infer a bind parameter's type from an IS NULL/equality-only
# context ("could not determine data type of parameter"), and
# SQLAlchemy's `text()` bind syntax doesn't parse `:name::type`
# correctly right after the name.
_COUNTRY_FILTER_SQL = f"""
    AND (
        CAST(:country_iso AS text) IS NULL
        OR (
            CAST(:country_iso AS text) = '{_UNRESOLVED_COUNTRY}'
            AND d.country_iso IS NULL
        )
        OR d.country_iso = CAST(:country_iso AS text)
    )
"""

# Excludes rows whose `sources` are exclusively Jooble. Jooble's search
# API only ever returns a short pre-truncated "snippet" (see
# JoobleConnector's docstring) — never a full description — so a job
# whose surviving description came only from Jooble carries too little
# text for a human (or the pipeline) to categorise with any confidence.
# A job with a non-Jooble source alongside Jooble is unaffected: dedup
# survivorship already keeps the longest description across a cluster's
# members (core.dedup.survivorship.resolve_description), so it isn't
# stuck with the Jooble snippet.
_EXCLUDE_JOOBLE_ONLY_SQL = """
    AND NOT COALESCE(
        (
            SELECT bool_and(src ->> 'source_name' = 'jooble')
            FROM jsonb_array_elements(d.sources) AS src
        ),
        FALSE
    )
"""


def _normalize_country_iso(country_iso: str | None) -> str | None:
    """Treat an empty-string `country_iso` the same as an omitted one.

    A caller that always includes `country_iso` in its query params
    (rather than omitting the key for "no filter") sends `""`, not
    absence: httpx — what both this API's test suite and the review
    UI use — serializes a `None`-valued param as `?country_iso=`, and
    FastAPI's `Query(default=None)` then sees `""`. Without this,
    `""` would be read as a (non-matching) country code instead of
    "no filter".

    Args:
        country_iso: The raw query param value.

    Returns:
        `None` if `country_iso` was `None` or `""`, else `country_iso`
        unchanged.
    """
    return country_iso or None


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
        country_iso: The job's normalised country (e.g. "GB", "US"),
            or None if `core.normalisation.location` couldn't resolve
            one from `location`.
        region: The job's normalised UK ITL1 region code, or None
            (only ever set when `country_iso` is "GB").
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
    country_iso: str | None
    region: str | None
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


class JobsToReviewResponse(BaseModel):
    """A batch of jobs to review, plus the size of the full remaining pool.

    Attributes:
        jobs: Up to `limit` unreviewed `JobToReview` entries.
        total_unreviewed_count: Total unreviewed rows across all of
            `gold.dim_job`, not just this batch — lets a caller show
            progress against the whole pool (e.g. combined with
            `ReviewSummary.reviewed_count`) instead of resetting every
            time a fresh batch is fetched.
    """

    jobs: list[JobToReview]
    total_unreviewed_count: int


@router.get("/classification/jobs-to-review", response_model=JobsToReviewResponse)
def get_jobs_to_review(
    limit: int = 100,
    country_iso: str | None = Query(
        default=None,
        description=(
            "Restrict the sample to one country (e.g. 'GB', 'US'), or "
            f"'{_UNRESOLVED_COUNTRY}' for rows normalise_location "
            "couldn't resolve a country for. Omit for no filter."
        ),
    ),
    engine: Engine = Depends(get_app_db_engine),
) -> JobsToReviewResponse:
    """Return a stratified sample of unreviewed `gold.dim_job` rows.

    Stratified across `category` x `category_method` (so no cascade
    stage or category is invisible in the sample), with low-confidence
    rows biased to the front of each bucket — the rows PLAN.md's own
    review-list guidance already flags as most likely to be wrong.

    Rows whose `sources` are exclusively Jooble are excluded — see
    `_EXCLUDE_JOOBLE_ONLY_SQL`.

    Args:
        limit: Maximum number of jobs to return.
        country_iso: Optional country filter — see `Query`'s
            description above.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Up to `limit` unreviewed `JobToReview` entries, plus the total
        unreviewed count across the whole pool (both scoped to
        `country_iso` when given).
    """
    params = {"country_iso": _normalize_country_iso(country_iso)}
    with engine.connect() as conn:
        total_unreviewed_count = conn.execute(
            text(
                f"""
                SELECT count(*)
                FROM gold.dim_job AS d
                LEFT JOIN classification.category_review_labels AS r
                    ON d.job_group_id = r.job_group_id
                WHERE r.job_group_id IS NULL AND d.category IS NOT NULL
                {_COUNTRY_FILTER_SQL}
                {_EXCLUDE_JOOBLE_ONLY_SQL}
                """
            ),
            params,
        ).scalar_one()
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
                f"""
                SELECT count(DISTINCT (category, category_method))
                FROM gold.dim_job AS d
                LEFT JOIN classification.category_review_labels AS r
                    ON d.job_group_id = r.job_group_id
                WHERE r.job_group_id IS NULL AND d.category IS NOT NULL
                {_COUNTRY_FILTER_SQL}
                {_EXCLUDE_JOOBLE_ONLY_SQL}
                """
            ),
            params,
        ).scalar_one()
        per_bucket = max(1, limit // max(1, bucket_count))

        rows = conn.execute(
            text(
                f"""
                WITH unreviewed AS (
                    SELECT d.*
                    FROM gold.dim_job AS d
                    LEFT JOIN classification.category_review_labels AS r
                        ON d.job_group_id = r.job_group_id
                    WHERE r.job_group_id IS NULL
                        AND d.category IS NOT NULL
                        {_COUNTRY_FILTER_SQL}
                        {_EXCLUDE_JOOBLE_ONLY_SQL}
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
            {**params, "per_bucket": per_bucket, "limit": limit},
        ).all()

    return JobsToReviewResponse(
        jobs=[
            JobToReview(
                job_group_id=row.job_group_id,
                title_for_display=row.title_for_display,
                title_raw=row.title_raw,
                company=row.company,
                location=row.location,
                country_iso=row.country_iso,
                region=row.region,
                description=row.description,
                category=row.category,
                category_confidence=float(row.category_confidence),
                category_method=row.category_method,
                qa_category=row.qa_category,
                seniority_band=row.seniority_band,
            )
            for row in rows
        ],
        total_unreviewed_count=total_unreviewed_count,
    )


class RegionOption(BaseModel):
    """One `country_iso` value observed in `gold.dim_job`, with counts.

    Attributes:
        country_iso: The country code (e.g. "GB", "US"), or None for
            rows `core.normalisation.location` couldn't resolve a
            country for — reported here rather than dropped, so the
            review UI's region picker can offer an explicit
            "unclassified" option.
        unreviewed_count: How many of this country's categorized jobs
            still need a review.
        total_count: How many of this country's categorized jobs exist
            in total (reviewed + unreviewed).
    """

    country_iso: str | None
    unreviewed_count: int
    total_count: int


@router.get("/classification/regions", response_model=list[RegionOption])
def get_regions(engine: Engine = Depends(get_app_db_engine)) -> list[RegionOption]:
    """List every `country_iso` value present, with review counts.

    Backs the review UI's region picker — grounds it in the countries
    that actually occur in the data instead of a hardcoded list.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        One `RegionOption` per distinct `country_iso` (including
        `None`), ordered by `total_count` descending.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    d.country_iso,
                    count(*) FILTER (WHERE r.job_group_id IS NULL) AS unreviewed_count,
                    count(*) AS total_count
                FROM gold.dim_job AS d
                LEFT JOIN classification.category_review_labels AS r
                    ON d.job_group_id = r.job_group_id
                WHERE d.category IS NOT NULL
                GROUP BY d.country_iso
                ORDER BY total_count DESC
                """
            )
        ).all()
    return [
        RegionOption(
            country_iso=row.country_iso,
            unreviewed_count=row.unreviewed_count,
            total_count=row.total_count,
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
    country_iso: str | None = Query(
        default=None,
        description=(
            "Scope the summary to one country (e.g. 'GB', 'US'), or "
            f"'{_UNRESOLVED_COUNTRY}' for rows with no resolved country. "
            "Omit for no filter."
        ),
    ),
    engine: Engine = Depends(get_app_db_engine),
) -> ReviewSummary:
    """Compute the current agreement rate against every review so far.

    Agreement is computed live against `gold.dim_job.category` rather
    than a stored flag, since a job's category can change on a later
    `classify-jobs` rerun (silver.job_category is UPSERTed).

    Args:
        country_iso: Optional country filter — see `Query`'s
            description above.
        engine: Injected via `get_app_db_engine`.

    Returns:
        The current `ReviewSummary`, scoped to `country_iso` when given.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT
                    r.reviewed_category,
                    d.category AS current_category,
                    d.category_method AS current_method
                FROM classification.category_review_labels AS r
                INNER JOIN gold.dim_job AS d
                    ON r.job_group_id = d.job_group_id
                WHERE TRUE
                {_COUNTRY_FILTER_SQL}
                """
            ),
            {"country_iso": _normalize_country_iso(country_iso)},
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
