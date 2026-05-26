from __future__ import annotations

from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState

_LOW_RETRIEVAL_THRESHOLD = 0.6
_DIVERGENCE_THRESHOLD = 1.5
_RETRIEVAL_PENALTY = 0.5
_DIVERGENCE_PENALTY = 0.5


def compute_confidence(
    state: TriageState,
    config: DomainConfig,
) -> tuple[float, str | None]:
    """Return (confidence, low_confidence_reason) using structural signals only.

    Penalties:
    - low_retrieval:   any retrieval_score < 0.6  →  −0.5
    - high_divergence: any dimension deviates > 1.5 from precedent avg  →  −0.5
    Both penalties can apply simultaneously (floor = 0.0).
    """
    penalty = 0.0
    reason: str | None = None

    # Retrieval penalty
    low_retrieval = any(
        score < _LOW_RETRIEVAL_THRESHOLD for score in state["retrieval_scores"].values()
    )
    if low_retrieval:
        penalty += _RETRIEVAL_PENALTY
        reason = "low_retrieval_similarity"

    # Divergence penalty — skip when no precedent scores are available
    precedent_scores = state.get("precedent_scores") or {}
    if precedent_scores:
        max_divergence = max(
            abs(state["dimension_scores"].get(dim, 0) - precedent_scores[dim])
            for dim in precedent_scores
        )
        if max_divergence > _DIVERGENCE_THRESHOLD:
            penalty += _DIVERGENCE_PENALTY
            if reason is None:
                reason = "high_score_divergence"

    confidence = max(0.0, 1.0 - penalty)
    return confidence, reason
