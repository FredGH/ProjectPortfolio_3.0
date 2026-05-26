"""Migration 0002: add a full-text payload index on ``regulatory_rules.text``.

Qdrant's text index enables BM25-style keyword scoring as a complement to the
dense vector search already present. The QdrantRetriever ``"hybrid"`` mode
over-retrieves 2× candidates for the cross-encoder reranker; this index
improves keyword recall within that candidate pool.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.models import PayloadSchemaType, PointStruct

MIGRATION_ID = "0002_add_hybrid_index_to_regulatory_rules"
_MIGRATION_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, MIGRATION_ID))


def _is_applied(client: QdrantClient) -> bool:
    hits, _ = client.scroll(
        "_migrations",
        scroll_filter=None,
        limit=1000,
        with_payload=True,
    )
    return any(h.payload.get("id") == MIGRATION_ID for h in hits)


def up(client: QdrantClient) -> None:
    if _is_applied(client):
        return

    client.create_payload_index(
        collection_name="regulatory_rules",
        field_name="text",
        field_schema=PayloadSchemaType.TEXT,
    )

    client.upsert(
        "_migrations",
        points=[
            PointStruct(
                id=_MIGRATION_UUID,
                vector=[0.0],
                payload={
                    "id": MIGRATION_ID,
                    "applied_at": datetime.now(tz=timezone.utc).isoformat(),
                },
            )
        ],
    )
