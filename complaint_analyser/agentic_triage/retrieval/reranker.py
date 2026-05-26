from __future__ import annotations

from sentence_transformers import CrossEncoder

_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_model: CrossEncoder | None = None


def _get_model() -> CrossEncoder:
    global _model
    if _model is None:
        _model = CrossEncoder(_MODEL_NAME)
    return _model


def rerank(query: str, chunks: list[dict], top_n: int | None = None) -> list[dict]:
    """Re-score and position chunks for U-shaped LLM attention.

    The highest-relevance chunk is placed at position 0 and the
    second-highest at the final position, exploiting the U-shaped attention
    curve where transformers attend most reliably to the edges of the context
    window (ARCHITECTURE.md Guard Rail 11).

    Args:
        query: The retrieval query used to score chunk relevance.
        chunks: Dicts from QdrantRetriever.search(), each containing a ``text`` key.
        top_n: If set, truncate to top_n chunks before position-ordering.

    Returns:
        Reordered list: highest-score at index 0, second-highest at index -1.
    """
    if not chunks:
        return chunks

    model = _get_model()
    texts = [c.get("text", "") for c in chunks]
    scores = model.predict([(query, t) for t in texts])

    ranked = [c for _, c in sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)]

    if top_n:
        ranked = ranked[:top_n]

    if len(ranked) >= 2:
        ranked = [ranked[0]] + ranked[2:] + [ranked[1]]

    return ranked
