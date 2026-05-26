from __future__ import annotations

from agentic_triage.core.config import DomainConfig
from agentic_triage.core.state import TriageState

# Entities that always carry signal — their presence blocks auto-P4
_HIGH_VALUE_ENTITY_LABELS = {"MONEY", "ORG", "DATE", "PERSON"}

_AUTO_P4_REASONING = "Auto-classified: no keywords, no signal entities, no retrieval match."
_AUTO_P4_CONFIDENCE = 0.95
_RETRIEVAL_SIGNAL_THRESHOLD = 0.45


def is_auto_p4(state: TriageState, config: DomainConfig) -> bool:  # noqa: ARG001
    """Return True when the LLM path can be skipped safely.

    All three conditions must hold — a false negative (unnecessary LLM call) is
    always preferable to a false positive (P1 silently classified as P4).
    """
    if bool(state["triggered_keywords"]):
        return False

    entities = state["entities"]
    if set(entities.keys()) & _HIGH_VALUE_ENTITY_LABELS and any(
        entities.get(lbl) for lbl in _HIGH_VALUE_ENTITY_LABELS
    ):
        return False

    if max(state["retrieval_scores"].values(), default=0.0) >= _RETRIEVAL_SIGNAL_THRESHOLD:
        return False

    return True


def apply_auto_p4(state: TriageState, config: DomainConfig) -> TriageState:
    """Populate all state fields required by finalize without calling the LLM."""
    lowest = config.priority_levels[-1]  # list is ordered highest → lowest
    return {
        **state,
        "is_auto_p4": True,
        "priority": lowest.label,
        "composite_score": 0.0,
        "dimension_scores": {d.name: 0 for d in config.scoring_dimensions},
        "confidence": _AUTO_P4_CONFIDENCE,
        "low_confidence_reason": None,
        "reasoning": _AUTO_P4_REASONING,
        "recommended_action": lowest.recommended_action,
    }
