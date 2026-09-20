"""Text embeddings via a local Ollama server (PLAN.md Step 11a, stage 2).

Unlike core.llm's adapters (which return completions), `embed_text`
returns a vector. Step 11a uses it ephemerally: compute a vector,
compare it, and discard it — no database column, no index. That use is
deliberately outside DECISIONS.md §2.8 / PLAN.md Step 15's
embedding-dimension-and-index-type deferral — that deferral is
specifically about Step 15's persisted, indexed pgvector store (CV/JD
chunk vectors reused across many future queries), not a one-shot
nearest-centroid comparison. See the Step 11a plan's own scope note for
the full reasoning.

Since Step 14 (ESCO skill normalisation), `embed_text` is also the
source of the persisted ESCO label vectors in `esco.skill_embedding`.
That table is a derived, rebuildable cache (one vector per skill, with
the embedding model recorded per row, no ANN index — see migration
0022), not the Step 15 store, so the deferral above still stands.

Separate from core.llm.adapters.ollama.OllamaAdapter deliberately:
that class implements the LLMAdapter Protocol's `.complete()` method
for Ollama's `/api/generate` endpoint — embeddings are a different
HTTP endpoint returning a different shape, not a completion, so
forcing it into the same Protocol would be a mismatch, not reuse.
"""

from __future__ import annotations

import httpx


def embed_text(
    text: str, *, base_url: str, model: str, client: httpx.Client
) -> list[float]:
    """Compute one embedding vector via Ollama's /api/embeddings endpoint.

    Args:
        text: The text to embed.
        base_url: Base URL of the Ollama server, e.g.
            "http://localhost:11434".
        model: The Ollama embedding model tag, e.g. "nomic-embed-text"
            (Settings.embedding_model's default).
        client: The HTTP client to issue the request with.

    Returns:
        The embedding vector as a list of floats.
    """
    # Lowercase text to avoid Ollama/nomic-embed-text serving bug where short
    # Title-Case phrases ("Data Engineer", "Product Manager") collapse to an
    # identical, content-independent vector; lowercased variants preserve correct
    # cosine ordering across the full vocabulary.
    response = client.post(
        f"{base_url}/api/embeddings",
        json={"model": model, "prompt": text.lower()},
    )
    response.raise_for_status()
    return response.json()["embedding"]
