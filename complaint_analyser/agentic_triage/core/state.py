from __future__ import annotations

from typing import TypedDict


class TriageState(TypedDict):
    input_id: str
    batch_id: str
    raw_text: str
    sanitized_text: str
    cleaned_text: str
    entities: dict[str, list[str]]
    triggered_keywords: list[str]
    retrieved_context: dict[str, list]
    retrieval_scores: dict[str, float]
    dimension_scores: dict[str, int]
    precedent_scores: dict[str, float]
    composite_score: float
    is_auto_p4: bool
    priority: str
    confidence: float
    low_confidence_reason: str | None
    loop_count: int
    reasoning: str
    recommended_action: str
    analyst_override: str | None
    hyde_text: str | None
    retrieval_queries: list[str]
    retrieved_references: dict[str, list[str]]  # role → list of point IDs; set by finalize
