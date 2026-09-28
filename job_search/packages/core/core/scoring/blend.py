"""Final stage of the scoring funnel: config-driven weighted blend
(PLAN.md Step 15). Before scoring.weight has a row for a user (before Step
16 calibrates), every present component is weighted equally — an
uncalibrated score is still computed, never left NULL, because Step 16
exists to fix the weights, not to unhide the score.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine, text

from core.db.session import session_scope

_COMPONENTS = ("vector_similarity", "reranker", "skill_coverage", "llm_fit")
_COLUMN_BY_COMPONENT = {
    "vector_similarity": "vector_similarity_score",
    "reranker": "reranker_score",
    "skill_coverage": "skill_coverage_score",
    "llm_fit": "llm_fit_score",
}

_SELECT_WEIGHTS = text(
    "SELECT component, weight FROM scoring.weight WHERE user_id = :user_id"
)
_SELECT_SCORES = text(
    "SELECT job_group_id, vector_similarity_score, reranker_score, "
    "skill_coverage_score, llm_fit_score FROM scoring.job_score "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_UPDATE_FINAL = text(
    "UPDATE scoring.job_score SET final_score = :final_score "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)


def compute_final_scores(app_engine: Engine, user_id: uuid.UUID) -> int:
    """Blend each hard-filter-passing job's present components into
    `final_score`.

    Args:
        app_engine: The app-role engine (RLS-enforced).
        user_id: Whose scores to blend.

    Returns:
        The number of jobs blended.
    """
    with session_scope(app_engine, user_id=user_id) as conn:
        fitted_weights = {
            r.component: float(r.weight)
            for r in conn.execute(_SELECT_WEIGHTS, {"user_id": user_id})
        }
        rows = conn.execute(_SELECT_SCORES, {"user_id": user_id}).mappings().all()
    blended = 0
    with session_scope(app_engine, user_id=user_id) as conn:
        for row in rows:
            present = {}
            for component in _COMPONENTS:
                value = row[_COLUMN_BY_COMPONENT[component]]
                if value is None:
                    continue
                value = float(value)
                if component == "llm_fit":
                    # llm_fit_score is stored 0-100 (human-readable) but
                    # every other component is ~[0,1] — rescale for the
                    # blend only, never mutating what is stored in the
                    # column itself.
                    value /= 100.0
                present[component] = value
            if not present:
                continue
            weights = {
                component: fitted_weights.get(component, 1.0 / len(present))
                for component in present
            }
            weight_sum = sum(weights.values())
            final = sum(present[c] * weights[c] for c in present) / weight_sum
            conn.execute(
                _UPDATE_FINAL,
                {
                    "user_id": user_id,
                    "job_group_id": row["job_group_id"],
                    "final_score": final,
                },
            )
            blended += 1
    return blended
