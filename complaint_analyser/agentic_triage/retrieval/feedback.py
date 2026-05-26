from __future__ import annotations

from agentic_triage.core.schema import TriageResult
from agentic_triage.core.state import TriageState


def build_triage_result(state: TriageState) -> TriageResult:
    return TriageResult(
        input_id=state["input_id"],
        priority=state["priority"],
        dimension_scores=state["dimension_scores"],
        composite_score=state["composite_score"],
        confidence=state["confidence"],
        low_confidence_reason=state["low_confidence_reason"],
        triggered_keywords=state["triggered_keywords"],
        retrieved_references=state.get("retrieved_references", {}),
        reasoning=state["reasoning"],
        recommended_action=state["recommended_action"],
        analyst_override=state.get("analyst_override"),
    )


async def write_triage_result(
    state: TriageState,
    domain: str,  # noqa: ARG001
    *,
    auto: bool,  # noqa: ARG001
) -> None:
    # Stub — real Postgres write implemented in step 12
    pass


async def write_triage_result_from_cache(
    cached: TriageResult,
    item_id: str,
    batch_id: str,  # noqa: ARG001
    domain: str,  # noqa: ARG001
) -> None:
    # Re-stamp the cached result with the new item_id and write it.
    # Real Postgres write implemented in step 12.
    _ = cached.model_copy(update={"input_id": item_id})


async def ingest_confirmed_result(
    result: TriageResult,
    text: str,  # noqa: ARG001
    collection: str,  # noqa: ARG001
) -> None:
    # Stub — real Qdrant upsert implemented in step 14 (n8n feedback workflow)
    pass
