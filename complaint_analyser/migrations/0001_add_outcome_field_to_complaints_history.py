"""Migration 0001: seed an ``outcome`` payload field on all complaints_history points.

Qdrant is schema-less, so this just initialises the field to ``None`` on every
existing point. New points written by the ingest pipeline carry the field already.
"""
from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

MIGRATION_ID = "0001_add_outcome_field_to_complaints_history"
_MIGRATION_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, MIGRATION_ID))


def _is_applied(client: QdrantClient) -> bool:
    hits, _ = client.scroll(
        "_migrations",
        scroll_filter=None,
        limit=1000,
        with_payload=True,
    )
    return any(h.payload.get("id") == MIGRATION_ID for h in hits)


def _chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def up(client: QdrantClient) -> None:
    if _is_applied(client):
        return

    offset = None
    while True:
        points, next_offset = client.scroll(
            "complaints_history",
            offset=offset,
            limit=100,
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            break
        client.set_payload(
            "complaints_history",
            payload={"outcome": None},
            points=[p.id for p in points],
        )
        if next_offset is None:
            break
        offset = next_offset

    from datetime import datetime, timezone

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
