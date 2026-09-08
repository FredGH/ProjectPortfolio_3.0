"""GET /dedup/pairs-to-label, POST /dedup/labels, GET /dedup/calibration,
GET|POST /dedup/thresholds — PLAN.md Step 9.
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

from app.dependencies import get_app_db_engine
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Engine, Row, text

from core.dedup.calibration import LabeledPair, compute_precision_recall_curve

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
                        AND s.hard_veto = false
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

    # The dedup dbt layer runs out-of-band relative to silver (see
    # dbt/README.md's dedup bullet) — a scored pair can reference a
    # job_key whose silver__job_posting row doesn't exist yet (or
    # anymore) any time the two are run out of the documented
    # interleaved order. That's a reachable operational state, not a
    # bug, so skip such a pair rather than 500ing the whole batch over
    # one missing posting.
    pairs_to_label: list[PairToLabel] = []
    for row in rows:
        posting_a = postings_by_key.get(row.job_key_a)
        posting_b = postings_by_key.get(row.job_key_b)
        if posting_a is None or posting_b is None:
            continue
        pairs_to_label.append(
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
                posting_a=posting_a,
                posting_b=posting_b,
            )
        )
    return pairs_to_label


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


class ThresholdsRequest(BaseModel):
    """A newly-calibrated pair of thresholds.

    Attributes:
        auto_match_threshold: Pairs at or above this score auto-match.
        auto_reject_threshold: Pairs at or below this score auto-reject.
        measured_precision: The precision measured at auto_match_threshold.
        measured_recall: The recall measured at auto_match_threshold.
        labeled_pair_count: How many labeled pairs this calibration used.
        calibrated_by: Free-text identifier of who ran the calibration.
    """

    auto_match_threshold: float = Field(ge=0.0, le=1.0)
    auto_reject_threshold: float = Field(ge=0.0, le=1.0)
    measured_precision: float = Field(ge=0.0, le=1.0)
    measured_recall: float = Field(ge=0.0, le=1.0)
    labeled_pair_count: int
    calibrated_by: str | None = None

    @model_validator(mode="after")
    def _check_thresholds_are_not_transposed(self) -> ThresholdsRequest:
        """Ensure auto_reject_threshold sits below auto_match_threshold.

        A transposed or out-of-order pair would leave every pair
        outside a valid band, since `pairs-to-label`'s production-mode
        query only returns rows strictly between the two — silently
        bricking the review queue forever.

        Returns:
            This instance, unchanged, once validated.

        Raises:
            ValueError: If auto_reject_threshold is not strictly less
                than auto_match_threshold.
        """
        if self.auto_reject_threshold >= self.auto_match_threshold:
            raise ValueError(
                "auto_reject_threshold must be strictly less than "
                "auto_match_threshold "
                f"(got auto_reject_threshold={self.auto_reject_threshold}, "
                f"auto_match_threshold={self.auto_match_threshold})"
            )
        return self


class ThresholdsResponse(BaseModel):
    """The current (most recent) calibration run.

    Attributes:
        auto_match_threshold: The current auto-match cutoff.
        auto_reject_threshold: The current auto-reject cutoff.
        measured_precision: The precision measured at calibration time.
        measured_recall: The recall measured at calibration time.
        labeled_pair_count: How many labeled pairs informed this run.
        calibrated_at: When this calibration was recorded.
    """

    auto_match_threshold: float
    auto_reject_threshold: float
    measured_precision: float
    measured_recall: float
    labeled_pair_count: int
    calibrated_at: datetime.datetime


class CalibrationPoint(BaseModel):
    """One point on the precision-recall curve.

    Attributes:
        threshold: The candidate auto-match cutoff.
        precision: Precision at this threshold, or None (see
            core.dedup.calibration.ThresholdMetrics).
        recall: Recall at this threshold, or None.
        predicted_match_count: How many labeled pairs would auto-match.
    """

    threshold: float
    precision: float | None
    recall: float | None
    predicted_match_count: int


@router.get("/dedup/calibration", response_model=list[CalibrationPoint])
def get_calibration(
    engine: Engine = Depends(get_app_db_engine),
) -> list[CalibrationPoint]:
    """Compute the precision-recall curve from every current label.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        One `CalibrationPoint` per distinct blended_score among labeled
        pairs, sorted by descending threshold. Empty if no pairs are
        labeled yet.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT l.job_key_a, l.job_key_b, l.label, s.blended_score
                FROM dedup.pair_labels AS l
                INNER JOIN dedup.dedup__similarity_scores AS s
                    ON l.job_key_a = s.job_key_a AND l.job_key_b = s.job_key_b
                """
            )
        ).all()

    labeled_pairs = [
        LabeledPair(
            job_key_a=row.job_key_a,
            job_key_b=row.job_key_b,
            blended_score=float(row.blended_score),
            label=row.label,
        )
        for row in rows
    ]
    curve = compute_precision_recall_curve(labeled_pairs)
    return [
        CalibrationPoint(
            threshold=point.threshold,
            precision=point.precision,
            recall=point.recall,
            predicted_match_count=point.predicted_match_count,
        )
        for point in curve
    ]


@router.get("/dedup/thresholds", response_model=ThresholdsResponse | None)
def get_thresholds(
    engine: Engine = Depends(get_app_db_engine),
) -> ThresholdsResponse | None:
    """Return the current (most recently calibrated) thresholds.

    Args:
        engine: Injected via `get_app_db_engine`.

    Returns:
        The current `ThresholdsResponse`, or None if no calibration run
        has ever been recorded.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT auto_match_threshold, auto_reject_threshold, "
                "measured_precision, measured_recall, labeled_pair_count, "
                "calibrated_at FROM dedup.calibration_thresholds "
                "ORDER BY calibrated_at DESC LIMIT 1"
            )
        ).one_or_none()
    if row is None:
        return None
    return ThresholdsResponse(
        auto_match_threshold=float(row.auto_match_threshold),
        auto_reject_threshold=float(row.auto_reject_threshold),
        measured_precision=float(row.measured_precision),
        measured_recall=float(row.measured_recall),
        labeled_pair_count=row.labeled_pair_count,
        calibrated_at=row.calibrated_at,
    )


@router.post("/dedup/thresholds")
def post_thresholds(
    request: ThresholdsRequest,
    engine: Engine = Depends(get_app_db_engine),
) -> dict[str, str]:
    """Record a new calibration run.

    Args:
        request: The newly-calibrated thresholds and their measured
            precision/recall.
        engine: Injected via `get_app_db_engine`.

    Returns:
        `{"status": "ok"}`.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dedup.calibration_thresholds (
                    auto_match_threshold, auto_reject_threshold,
                    measured_precision, measured_recall, labeled_pair_count,
                    calibrated_by
                ) VALUES (
                    :auto_match_threshold, :auto_reject_threshold,
                    :measured_precision, :measured_recall,
                    :labeled_pair_count, :calibrated_by
                )
                """
            ),
            {
                "auto_match_threshold": request.auto_match_threshold,
                "auto_reject_threshold": request.auto_reject_threshold,
                "measured_precision": request.measured_precision,
                "measured_recall": request.measured_recall,
                "labeled_pair_count": request.labeled_pair_count,
                "calibrated_by": request.calibrated_by,
            },
        )
    return {"status": "ok"}
