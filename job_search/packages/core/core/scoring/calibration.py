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
            list(row.llm_missing_skills) if row.llm_missing_skills else None
        ),
        llm_stretch_flag=row.llm_stretch_flag,
    )
