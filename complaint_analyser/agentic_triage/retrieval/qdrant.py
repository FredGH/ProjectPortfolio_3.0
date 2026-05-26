from __future__ import annotations

from typing import Callable

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue


class QdrantRetriever:
    """Collection-agnostic Qdrant retriever supporting dense and hybrid search modes.

    Hybrid mode over-retrieves (top_k * 2) so the cross-encoder reranker in the
    retrieve node has more candidates to reorder. True BM25 sparse vectors are
    added per collection via Phase 10 migrations; until then, hybrid degrades
    gracefully to dense with a wider candidate pool.
    """

    def __init__(
        self,
        client: QdrantClient,
        embed_fn: Callable[[list[str]], list[list[float]]],
    ) -> None:
        self._client = client
        self._embed_fn = embed_fn

    def search(
        self,
        collection: str,
        query: str,
        top_k: int = 5,
        search_mode: str = "hybrid",
        filter_by: dict | None = None,
    ) -> list[dict]:
        """Search a Qdrant collection and return payload-enriched hit dicts.

        Args:
            collection: Qdrant collection name.
            query: Raw query string — embedded internally.
            top_k: Number of results to return.
            search_mode: ``"dense"`` for plain vector search; ``"hybrid"``
                over-retrieves 2× top_k for reranker headroom; ``"sparse"``
                falls back to dense until sparse index is provisioned.
            filter_by: Optional ``{field: value}`` payload equality filters.

        Returns:
            List of dicts containing ``id``, ``score``, and all payload fields.
        """
        embedding = self._embed_fn([query])[0]
        query_filter = self._build_filter(filter_by) if filter_by else None
        fetch_k = top_k * 2 if search_mode == "hybrid" else top_k

        hits = self._client.search(
            collection_name=collection,
            query_vector=embedding,
            query_filter=query_filter,
            limit=fetch_k,
            with_payload=True,
            with_vectors=False,
        )

        results = [{"id": str(h.id), "score": h.score, **h.payload} for h in hits]
        return results[:top_k]

    @staticmethod
    def _build_filter(filter_by: dict) -> Filter:
        return Filter(
            must=[
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filter_by.items()
            ]
        )
