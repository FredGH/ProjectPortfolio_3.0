"""pgvector helpers for the ESCO skill-embedding cache (Step 14)."""

from __future__ import annotations

from collections.abc import Sequence

EMBEDDING_DIMENSION = 768
"""Dimension of `nomic-embed-text`, the project's embedding model
(DECISIONS.md §2.8). Fixed in `esco.skill_embedding`'s column type; a
different model means re-running `embed-esco` after a migration."""


def to_pgvector(vector: Sequence[float]) -> str:
    """Render a vector as a pgvector text literal.

    Args:
        vector: The embedding values.

    Returns:
        A string like ``"[0.1,0.2]"``, for use as
        ``CAST(:param AS vector)`` — avoids needing a pgvector driver
        adapter.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"
