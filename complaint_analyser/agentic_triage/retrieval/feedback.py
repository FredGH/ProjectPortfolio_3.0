from __future__ import annotations

import json
import uuid

import asyncpg
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from agentic_triage.core.schema import TriageResult
from agentic_triage.core.state import TriageState
from agentic_triage.retrieval.cache import embed_text

_INSERT_RESULT = """
    INSERT INTO triage_results (
        input_id, batch_id, domain, priority, composite_score,
        dimension_scores, confidence, low_confidence_reason,
        triggered_keywords, retrieved_references, reasoning, recommended_action,
        is_auto_p4
    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
    ON CONFLICT (input_id) DO NOTHING
"""


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
    db: asyncpg.Pool,
    state: TriageState,
    domain: str,
    *,
    auto: bool,  # noqa: ARG001
) -> None:
    result = build_triage_result(state)
    await db.execute(
        _INSERT_RESULT,
        result.input_id,
        state["batch_id"],
        domain,
        result.priority,
        result.composite_score,
        json.dumps(result.dimension_scores),
        result.confidence,
        result.low_confidence_reason,
        result.triggered_keywords,
        json.dumps(result.retrieved_references),
        result.reasoning,
        result.recommended_action,
        state.get("is_auto_p4", False),
    )


async def write_triage_result_from_cache(
    db: asyncpg.Pool,
    cached: TriageResult,
    item_id: str,
    batch_id: str,
    domain: str,
) -> None:
    await db.execute(
        _INSERT_RESULT,
        item_id,
        batch_id,
        domain,
        cached.priority,
        cached.composite_score,
        json.dumps(cached.dimension_scores),
        cached.confidence,
        cached.low_confidence_reason,
        cached.triggered_keywords,
        json.dumps(cached.retrieved_references),
        cached.reasoning,
        cached.recommended_action,
        False,
    )


async def ingest_confirmed_result(
    result: TriageResult,
    text: str,
    collection: str,
    qdrant: QdrantClient,
) -> None:
    """Embed text and upsert the confirmed result into a Qdrant collection.

    Used by the analyst override feedback path to write corrected examples
    back into complaints_history as authoritative precedents.
    """
    embedding = await embed_text(text)
    point = PointStruct(
        id=str(uuid.uuid5(uuid.NAMESPACE_DNS, result.input_id)),
        vector=embedding,
        payload={
            "text": text,
            "input_id": result.input_id,
            "priority": result.analyst_override or result.priority,
            "dimension_scores": result.dimension_scores,
            "source": "analyst_override",
        },
    )
    qdrant.upsert(collection_name=collection, points=[point])
