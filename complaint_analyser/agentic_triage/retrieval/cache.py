from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from agentic_triage import settings
from agentic_triage.core.schema import TriageResult

CACHE_COLLECTION = "query_cache"
CACHE_THRESHOLD = 0.97  # intentionally strict — wrong priority > cache miss
VECTOR_DIM = 768  # nomic-embed-text output dimension


def ensure_cache_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if CACHE_COLLECTION not in existing:
        client.create_collection(
            CACHE_COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )


async def embed_text(text: str) -> list[float]:
    """Embed a single text string via Ollama (async)."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{settings.OLLAMA_HOST}/api/embed",
            json={"model": settings.EMBED_MODEL, "input": [text]},
        )
        r.raise_for_status()
    return r.json()["embeddings"][0]


def lookup_cache(
    client: QdrantClient,
    embedding: list[float],
    domain: str,
) -> TriageResult | None:
    """Return a cached TriageResult if cosine similarity ≥ 0.97, else None.

    Domain-scoped so a banking complaint cannot match a security alert cache entry.
    """
    hits = client.search(
        CACHE_COLLECTION,
        query_vector=embedding,
        query_filter=Filter(
            must=[FieldCondition(key="domain", match=MatchValue(value=domain))]
        ),
        limit=1,
        score_threshold=CACHE_THRESHOLD,
        with_payload=True,
        with_vectors=False,
    )
    if not hits:
        return None
    try:
        return TriageResult(**hits[0].payload["result"])
    except Exception:
        # Schema mismatch after a cache flush — treat as miss
        return None


def write_cache(
    client: QdrantClient,
    embedding: list[float],
    result: TriageResult,
    domain: str,
) -> None:
    """Upsert a TriageResult into the semantic cache with an ISO cached_at timestamp.

    TTL expiry (30-day delete-by-filter) is handled by n8n Workflow 3 — not here.
    """
    client.upsert(
        CACHE_COLLECTION,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "domain": domain,
                    "result": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        ],
    )
