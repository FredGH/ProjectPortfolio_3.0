"""Weight fitting and calibration for the scoring funnel (PLAN.md Step 16).

Rank correlation, not least-squares regression, is the fitting objective:
PLAN.md's own "Done when" criterion is about ranking agreement ("top 10 by
computed score substantially matches top 10 by hand ranking"), not about
predicting a label's exact numeric value.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from itertools import product

from scipy.stats import spearmanr
from sqlalchemy import Engine, text

from core.db.session import session_scope

_COMPONENTS = ("vector_similarity", "reranker", "skill_coverage", "llm_fit")

# Every multiple of 0.05 from 0.0 to 1.0 — the grid search's per-component
# candidate values. 1,771 four-tuples sum to 1.0 out of this grid; trivial
# to evaluate all of them (no need for a smarter optimizer at this scale).
_STEP = 0.05
_LEVELS = tuple(round(i * _STEP, 2) for i in range(int(1 / _STEP) + 1))


def _blend(row: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of a row's components — same formula as blend.py's
    inline computation, factored out here since this module calls it
    from both the grid search and the agreement check.

    Args:
        row: Component name -> score, e.g. {"vector_similarity": 0.8, ...}.
        weights: Component name -> weight. Assumed to sum to 1.0 (the
            grid search only ever produces such vectors).

    Returns:
        The weighted-mean blended score.
    """
    return sum(row[c] * weights[c] for c in _COMPONENTS)


def _spearman_agreement(
    rows: list[dict[str, float]],
    labels: list[float],
    weights: dict[str, float],
) -> float | None:
    """Spearman rank correlation between blended scores and labels.

    Args:
        rows: One dict of component scores per job, same order as `labels`.
        labels: The numeric label (1.0/0.5/0.0) for each row, same order.
        weights: The weight vector to blend `rows` with.

    Returns:
        The Spearman correlation, or None when it is mathematically
        undefined (e.g. every label is identical, so there is no rank
        variation to correlate against) — scipy returns `nan` in that
        case; this function converts `nan` to `None` so no caller ever
        has to special-case a NaN float.
    """
    blended = [_blend(row, weights) for row in rows]
    correlation, _p_value = spearmanr(blended, labels)
    if correlation != correlation:  # NaN != NaN is the classic NaN check
        return None
    return float(correlation)


def _grid_search_weights(
    fit_rows: list[dict[str, float]], fit_labels: list[float]
) -> dict[str, float]:
    """Find the weight vector maximizing Spearman agreement on the fit set.

    Searches every 4-tuple of multiples of 0.05 in [0, 1] that sums to
    1.0. Ties are broken toward the smoothest distribution (lowest max
    single weight), then by iteration order, for full determinism.

    Args:
        fit_rows: One dict of component scores per fit-set job.
        fit_labels: The numeric label for each row, same order.

    Returns:
        The winning weight vector, one entry per component in
        `_COMPONENTS`, summing to 1.0.
    """
    best_weights: dict[str, float] | None = None
    best_score = float("-inf")
    best_max_weight = float("inf")
    for combo in product(_LEVELS, repeat=len(_COMPONENTS)):
        if abs(sum(combo) - 1.0) > 1e-9:
            continue
        weights = dict(zip(_COMPONENTS, combo))
        agreement = _spearman_agreement(fit_rows, fit_labels, weights)
        if agreement is None:
            continue
        max_weight = max(combo)
        better = agreement > best_score + 1e-9
        tied_but_smoother = (
            abs(agreement - best_score) <= 1e-9 and max_weight < best_max_weight
        )
        if better or tied_but_smoother:
            best_weights = weights
            best_score = agreement
            best_max_weight = max_weight
    if best_weights is None:
        # Every candidate produced an undefined agreement (e.g. every
        # fit_label is identical) — fall back to equal weights, the same
        # placeholder blend.py itself uses pre-calibration.
        return {c: 1.0 / len(_COMPONENTS) for c in _COMPONENTS}
    return best_weights


@dataclass(frozen=True)
class JobLabel:
    """One hand-labeled job.

    Attributes:
        job_group_id: The labeled job's identity.
        label: "strong" | "maybe" | "no".
        labeled_at: When this label was last written.
    """

    job_group_id: str
    label: str
    labeled_at: datetime


def read_labels(engine: Engine, user_id: uuid.UUID) -> list[JobLabel]:
    """Read every label this user has recorded, newest first.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose labels to read.

    Returns:
        Every `JobLabel`, ordered by `labeled_at` descending. Empty list
        if this user has never labeled anything.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(
            text(
                "SELECT job_group_id, label, labeled_at FROM scoring.job_label "
                "WHERE user_id = :user_id ORDER BY labeled_at DESC"
            ),
            {"user_id": user_id},
        ).all()
    return [JobLabel(row.job_group_id, row.label, row.labeled_at) for row in rows]


_UPSERT_LABEL = text(
    "INSERT INTO scoring.job_label (user_id, job_group_id, label, labeled_at) "
    "VALUES (:user_id, :job_group_id, :label, now()) "
    "ON CONFLICT (user_id, job_group_id) DO UPDATE SET "
    "label = EXCLUDED.label, labeled_at = now()"
)


def write_label(
    engine: Engine, user_id: uuid.UUID, job_group_id: str, label: str
) -> None:
    """Create or overwrite one job's label.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose label this is.
        job_group_id: The job being labeled.
        label: "strong" | "maybe" | "no".
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            _UPSERT_LABEL,
            {"user_id": user_id, "job_group_id": job_group_id, "label": label},
        )


def delete_label(engine: Engine, user_id: uuid.UUID, job_group_id: str) -> None:
    """Remove one job's label, if it exists.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose label to remove.
        job_group_id: The job to un-label.
    """
    with session_scope(engine, user_id=user_id) as conn:
        conn.execute(
            text(
                "DELETE FROM scoring.job_label "
                "WHERE user_id = :user_id AND job_group_id = :job_group_id"
            ),
            {"user_id": user_id, "job_group_id": job_group_id},
        )


@dataclass(frozen=True)
class LabelCandidate:
    """One job to show a human for hand-labeling, with enough context to
    judge fit confidently.

    Attributes:
        job_group_id: The candidate job's identity.
        title: `gold.dim_job.title_for_display`.
        company: The employer name.
        location: The posting's location string.
        engagement_type: permanent/contract/ftc/interim.
        description: The full job description.
        vector_similarity_score: Stage-2 component score.
        reranker_score: Stage-2 (cross-encoder) component score.
        skill_coverage_score: Stage-3 component score.
        llm_fit_score: Stage-4 component score, 0-100.
        llm_rationale: The LLM re-rank's free-text rationale, if present.
        llm_missing_skills: Skills the LLM flagged as missing, if present.
        llm_stretch_flag: Whether the LLM flagged this as a stretch role.
    """

    job_group_id: str
    title: str
    company: str
    location: str
    engagement_type: str
    description: str
    vector_similarity_score: float
    reranker_score: float
    skill_coverage_score: float
    llm_fit_score: float
    llm_rationale: str | None
    llm_missing_skills: list[str] | None
    llm_stretch_flag: bool | None


_SELECT_ELIGIBLE_CANDIDATES = text(
    "SELECT s.job_group_id, j.title_for_display, j.company, j.location, "
    "j.engagement_type, j.description, s.vector_similarity_score, "
    "s.reranker_score, s.skill_coverage_score, s.llm_fit_score, "
    "s.llm_rationale, s.llm_missing_skills, s.llm_stretch_flag "
    "FROM scoring.job_score s "
    "JOIN gold.dim_job j ON j.job_group_id = s.job_group_id "
    "WHERE s.user_id = :user_id AND s.hard_filter_passed = true "
    "AND s.vector_similarity_score IS NOT NULL "
    "AND s.reranker_score IS NOT NULL "
    "AND s.skill_coverage_score IS NOT NULL "
    "AND s.llm_fit_score IS NOT NULL "
    "AND s.job_group_id NOT IN ("
    "SELECT job_group_id FROM scoring.job_label WHERE user_id = :user_id)"
)


def pick_labeling_candidate(
    engine: Engine, user_id: uuid.UUID
) -> LabelCandidate | None:
    """Pick a random eligible, not-yet-labeled job for hand-labeling.

    Eligible means every one of the four scoring components is present
    (a job missing one can't inform that component's weight) and
    `hard_filter_passed`. Picks uniformly at random among every eligible,
    unlabeled candidate — with at most 50 jobs ever reaching all-four-
    present (the LLM-rerank stage's own cap), stratifying further into
    score bands is unnecessary complexity for a pool this small.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose candidate pool to pick from.

    Returns:
        One `LabelCandidate`, or None if nothing eligible remains unlabeled.
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(_SELECT_ELIGIBLE_CANDIDATES, {"user_id": user_id}).all()
    if not rows:
        return None
    row = random.choice(rows)
    return LabelCandidate(
        job_group_id=row.job_group_id,
        title=row.title_for_display,
        company=row.company,
        location=row.location,
        engagement_type=row.engagement_type,
        description=row.description,
        vector_similarity_score=float(row.vector_similarity_score),
        reranker_score=float(row.reranker_score),
        skill_coverage_score=float(row.skill_coverage_score),
        llm_fit_score=float(row.llm_fit_score),
        llm_rationale=row.llm_rationale,
        llm_missing_skills=(
            list(row.llm_missing_skills) if row.llm_missing_skills is not None else None
        ),
        llm_stretch_flag=row.llm_stretch_flag,
    )


@dataclass(frozen=True)
class CalibrationPreview:
    """The result of a fit run, not yet persisted.

    Attributes:
        fit_count: How many labeled jobs were used to fit the weights
            (always 20 for a normal run, but see the Review Focus note on
            labels whose job_score row has since disappeared).
        holdout_count: How many labeled jobs were held out and never used
            for fitting (always 10 for a normal run, same caveat).
        weights: Component name -> fitted weight, summing to 1.0.
        holdout_agreement: Spearman correlation between the fitted-weight
            blended score and the numeric label, computed on the holdout
            set only. None when undefined (e.g. every holdout label is
            identical).
        embedding_model: The embedding model in use for the fit-set jobs
            at fit time — recorded so a later embedding-model change can
            be detected as making this calibration stale.
    """

    fit_count: int
    holdout_count: int
    weights: dict[str, float]
    holdout_agreement: float | None
    embedding_model: str


_SELECT_LABELED_JOB_SCORES = text(
    "SELECT l.job_group_id, l.label, s.vector_similarity_score, "
    "s.reranker_score, s.skill_coverage_score, s.llm_fit_score, "
    "s.embedding_model "
    "FROM scoring.job_label l "
    "JOIN scoring.job_score s "
    "ON s.user_id = l.user_id AND s.job_group_id = l.job_group_id "
    "WHERE l.user_id = :user_id "
    # Postgres gives no row-order guarantee for a plain SELECT — without
    # this ORDER BY, `random.Random(seed).sample(rows, 30)` would only be
    # reproducible by luck (same process, no intervening writes), not by
    # design. A stable key makes the sample deterministic across separate
    # calls, processes, and time, for the same underlying label set.
    "ORDER BY l.job_group_id"
)

_LABEL_TO_NUMERIC = {"strong": 1.0, "maybe": 0.5, "no": 0.0}


def split_and_fit(
    engine: Engine, user_id: uuid.UUID, seed: int = 0
) -> CalibrationPreview:
    """Sample 30 labeled jobs, split 20 fit / 10 holdout, fit weights,
    and measure holdout agreement. Writes nothing.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose labels/scores to fit against.
        seed: Random seed for the 30-of-N sample and the fit/holdout
            split — the same seed always produces the same split for the
            same label set, so a re-run is reproducible.

    Returns:
        The fit result, ready either to inspect (a preview) or persist
        via `save_calibration`.

    Raises:
        ValueError: If fewer than 30 labels have a matching
            `scoring.job_score` row (a label whose job has since dropped
            out of the scored pool doesn't count — see the module's
            Review Focus note on a vanished job_score row).
    """
    with session_scope(engine, user_id=user_id) as conn:
        rows = conn.execute(_SELECT_LABELED_JOB_SCORES, {"user_id": user_id}).all()
    if len(rows) < 30:
        raise ValueError(
            f"split_and_fit needs at least 30 labeled jobs with a current "
            f"score, have {len(rows)}"
        )
    sample = random.Random(seed).sample(rows, 30)
    fit_rows_raw, holdout_rows_raw = sample[:20], sample[20:]

    def _to_component_row(row) -> dict[str, float]:
        return {
            "vector_similarity": float(row.vector_similarity_score),
            "reranker": float(row.reranker_score),
            "skill_coverage": float(row.skill_coverage_score),
            "llm_fit": float(row.llm_fit_score) / 100.0,
        }

    fit_rows = [_to_component_row(r) for r in fit_rows_raw]
    fit_labels = [_LABEL_TO_NUMERIC[r.label] for r in fit_rows_raw]
    holdout_rows = [_to_component_row(r) for r in holdout_rows_raw]
    holdout_labels = [_LABEL_TO_NUMERIC[r.label] for r in holdout_rows_raw]

    weights = _grid_search_weights(fit_rows, fit_labels)
    holdout_agreement = _spearman_agreement(holdout_rows, holdout_labels, weights)

    return CalibrationPreview(
        fit_count=len(fit_rows),
        holdout_count=len(holdout_rows),
        weights=weights,
        holdout_agreement=holdout_agreement,
        embedding_model=sample[0].embedding_model,
    )


_UPSERT_WEIGHT = text(
    "INSERT INTO scoring.weight (user_id, component, weight, fitted_at) "
    "VALUES (:user_id, :component, :weight, now()) "
    "ON CONFLICT (user_id, component) DO UPDATE SET "
    "weight = EXCLUDED.weight, fitted_at = now()"
)
_INSERT_CALIBRATION_RUN = text(
    "INSERT INTO scoring.calibration_run (user_id, fit_count, holdout_count, "
    "vector_similarity_weight, reranker_weight, skill_coverage_weight, "
    "llm_fit_weight, holdout_agreement, embedding_model, calibrated_by) "
    "VALUES (:user_id, :fit_count, :holdout_count, :vector_similarity_weight, "
    ":reranker_weight, :skill_coverage_weight, :llm_fit_weight, "
    ":holdout_agreement, :embedding_model, :calibrated_by)"
)


def save_calibration(
    engine: Engine,
    user_id: uuid.UUID,
    preview: CalibrationPreview,
    calibrated_by: str | None = None,
) -> None:
    """Persist a previewed calibration: upsert scoring.weight and append
    a scoring.calibration_run history row, in one transaction.

    Args:
        engine: The app-role engine (RLS-enforced).
        user_id: Whose weights to save.
        preview: The result of a prior `split_and_fit` call.
        calibrated_by: Optional free-text attribution (mirrors
            `dedup.calibration_thresholds.calibrated_by`).
    """
    with session_scope(engine, user_id=user_id) as conn:
        for component, weight in preview.weights.items():
            conn.execute(
                _UPSERT_WEIGHT,
                {"user_id": user_id, "component": component, "weight": weight},
            )
        conn.execute(
            _INSERT_CALIBRATION_RUN,
            {
                "user_id": user_id,
                "fit_count": preview.fit_count,
                "holdout_count": preview.holdout_count,
                "vector_similarity_weight": preview.weights["vector_similarity"],
                "reranker_weight": preview.weights["reranker"],
                "skill_coverage_weight": preview.weights["skill_coverage"],
                "llm_fit_weight": preview.weights["llm_fit"],
                "holdout_agreement": preview.holdout_agreement,
                "embedding_model": preview.embedding_model,
                "calibrated_by": calibrated_by,
            },
        )
