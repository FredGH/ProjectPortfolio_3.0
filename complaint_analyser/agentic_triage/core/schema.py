from __future__ import annotations

from pydantic import BaseModel


class TriageResult(BaseModel):
    input_id: str
    priority: str
    dimension_scores: dict[str, int]
    composite_score: float
    confidence: float
    low_confidence_reason: str | None
    triggered_keywords: list[str]
    retrieved_references: dict[str, list[str]]
    reasoning: str
    recommended_action: str
    analyst_override: str | None = None
