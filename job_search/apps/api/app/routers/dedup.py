"""GET /dedup/pairs-to-label, POST /dedup/labels, GET /dedup/calibration,
GET|POST /dedup/thresholds — PLAN.md Step 9.
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import Engine, Row, text

router = APIRouter()


class PostingSummary(BaseModel):
    """Enough of a posting to review it side by side with another.

    Attributes:
        job_key: The posting's identity key.
        title: The posting's title, or None.
        company: The posting's company, or None.
        location: The posting's location, or None.
        description: The posting's description, or None.
        job_url_canonical: The canonicalised job URL.
        posted_at: When the posting was posted, if known.
    """

    job_key: str
    title: str | None
    company: str | None
    location: str | None
    description: str | None
    job_url_canonical: str
    posted_at: datetime.datetime | None


class SimilarityScores(BaseModel):
    """One candidate pair's Step 8 similarity components.

    Attributes:
        job_key_a: The pair's first job.
        job_key_b: The pair's second job.
        blended_score: The overall weighted score.
        company_similarity: Company-name trigram similarity, 0-1.
        title_similarity: Title token-set-ratio similarity, 0-1.
        description_similarity: Description SimHash-derived similarity, 0-1.
        location_similarity: Location match score, 0-1.
        date_diff_days: Absolute days between the two posted_at values.
        salary_similarity: Salary-band overlap score, 0-1.
    """

    job_key_a: str
    job_key_b: str
    blended_score: float
    company_similarity: float
    title_similarity: float
    description_similarity: float
    location_similarity: float
    date_diff_days: float
    salary_similarity: float


class PairToLabel(BaseModel):
    """One candidate pair, with both postings' details, ready to review.

    Attributes:
        scores: The pair's similarity components.
        posting_a: The first posting's details.
        posting_b: The second posting's details.
    """

    scores: SimilarityScores
    posting_a: PostingSummary
    posting_b: PostingSummary


class LabelRequest(BaseModel):
    """A human's match/not-match decision on one candidate pair.

    Attributes:
        job_key_a: The pair's first job.
        job_key_b: The pair's second job.
        label: "match" or "not_match".
        labeled_by: Free-text identifier of who labeled it, if given.
    """

    job_key_a: str
    job_key_b: str
    label: Literal["match", "not_match"]
    labeled_by: str | None = None


_POSTING_COLUMNS = (
    "job_key, title, company, location, description, " "job_url_canonical, posted_at"
)


def _posting_summary(row: Row[Any]) -> PostingSummary:
    """Build a PostingSummary from one silver__job_posting row.

    Args:
        row: A SQLAlchemy result row with `_POSTING_COLUMNS`' fields.

    Returns:
        The `PostingSummary`.
    """
    return PostingSummary(
        job_key=row.job_key,
        title=row.title,
        company=row.company,
        location=row.location,
        description=row.description,
        job_url_canonical=row.job_url_canonical,
        posted_at=row.posted_at,
    )


@router.get("/dedup/pairs-to-label", response_model=list[PairToLabel])
def get_pairs_to_label(
    limit: int = 50,
    engine: Engine = Depends(get_app_db_engine),
) -> list[PairToLabel]:
    """Return candidate pairs needing a human label.

    Bootstrap mode (no calibration_thresholds row yet): a stratified
    sample across the actual population deciles of blended_score, since
    real data is heavily concentrated in a narrow range (confirmed live
    in this project: most non-veto pairs score between 0.5 and 0.8) —
    a fixed-range split would starve the sparse high/low deciles.
    Production mode (thresholds exist): pairs strictly between the two
    thresholds — the actual "middle band" review queue.

    Args:
        limit: Maximum number of pairs to return.
        engine: Injected via `get_app_db_engine`.

    Returns:
        Up to `limit` unlabeled `PairToLabel` entries.
    """
    with engine.connect() as conn:
        thresholds_row = conn.execute(
            text(
                "SELECT auto_match_threshold, auto_reject_threshold "
                "FROM dedup.calibration_thresholds "
                "ORDER BY calibrated_at DESC LIMIT 1"
            )
        ).one_or_none()

        if thresholds_row is None:
            per_bucket = max(1, limit // 10)
            rows = conn.execute(
                text(
                    """
                    WITH unlabeled AS (
                        SELECT s.*
                        FROM dedup.dedup__similarity_scores AS s
                        LEFT JOIN dedup.pair_labels AS l
                            ON s.job_key_a = l.job_key_a
                            AND s.job_key_b = l.job_key_b
                        WHERE l.job_key_a IS NULL AND s.hard_veto = false
                    ),
                    bucketed AS (
                        SELECT
                            *,
                            NTILE(10) OVER (ORDER BY blended_score) AS score_bucket
                        FROM unlabeled
                    ),
                    ranked AS (
                        SELECT
                            *,
                            ROW_NUMBER() OVER (
                                PARTITION BY score_bucket ORDER BY random()
                            ) AS rn
                        FROM bucketed
                    )
                    SELECT
                        job_key_a, job_key_b, match_type, company_similarity,
                        title_similarity, description_similarity,
                        location_similarity, date_diff_days, salary_similarity,
                        blended_score
                    FROM ranked
                    WHERE rn <= :per_bucket
                    ORDER BY score_bucket, rn
                    LIMIT :limit
                    """
                ),
                {"per_bucket": per_bucket, "limit": limit},
            ).all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT
                        s.job_key_a, s.job_key_b, s.match_type,
                        s.company_similarity, s.title_similarity,
                        s.description_similarity, s.location_similarity,
                        s.date_diff_days, s.salary_similarity, s.blended_score
                    FROM dedup.dedup__similarity_scores AS s
                    LEFT JOIN dedup.pair_labels AS l
                        ON s.job_key_a = l.job_key_a AND s.job_key_b = l.job_key_b
                    WHERE l.job_key_a IS NULL
                        AND s.blended_score > :auto_reject
                        AND s.blended_score < :auto_match
                    ORDER BY random()
                    LIMIT :limit
                    """
                ),
                {
                    "auto_reject": thresholds_row.auto_reject_threshold,
                    "auto_match": thresholds_row.auto_match_threshold,
                    "limit": limit,
                },
            ).all()

        if not rows:
            return []

        job_keys = {row.job_key_a for row in rows} | {row.job_key_b for row in rows}
        posting_rows = conn.execute(
            text(
                f"SELECT {_POSTING_COLUMNS} FROM silver.silver__job_posting "
                "WHERE job_key = ANY(:job_keys)"
            ),
            {"job_keys": list(job_keys)},
        ).all()
        postings_by_key = {row.job_key: _posting_summary(row) for row in posting_rows}

    return [
        PairToLabel(
            scores=SimilarityScores(
                job_key_a=row.job_key_a,
                job_key_b=row.job_key_b,
                blended_score=float(row.blended_score),
                company_similarity=float(row.company_similarity),
                title_similarity=float(row.title_similarity),
                description_similarity=float(row.description_similarity),
                location_similarity=float(row.location_similarity),
                date_diff_days=float(row.date_diff_days),
                salary_similarity=float(row.salary_similarity),
            ),
            posting_a=postings_by_key[row.job_key_a],
            posting_b=postings_by_key[row.job_key_b],
        )
        for row in rows
    ]


@router.post("/dedup/labels")
def post_label(
    request: LabelRequest,
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, str]:
    """Record (or update) a human's label for one candidate pair.

    Args:
        request: The label being recorded.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dedup.pair_labels (
                    job_key_a, job_key_b, label, labeled_by
                ) VALUES (
                    :job_key_a, :job_key_b, :label, :labeled_by
                )
                ON CONFLICT (job_key_a, job_key_b) DO UPDATE SET
                    label = EXCLUDED.label,
                    labeled_by = EXCLUDED.labeled_by,
                    labeled_at = now()
                """
            ),
            {
                "job_key_a": request.job_key_a,
                "job_key_b": request.job_key_b,
                "label": request.label,
                "labeled_by": request.labeled_by,
            },
        )
    return {"status": "ok"}
