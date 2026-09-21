"""Embed every ESCO skill's preferred label into `esco.skill_embedding`.

A derived, rebuildable cache (see migration 0022): one vector per skill,
recorded with the `embedding_model` that produced it. Only skills with
no embedding — or an embedding from a different model — are embedded,
so an interrupted run resumes where it stopped (each batch commits on
its own). Preferred labels only: alt labels are already matched exactly
by the mapper, and embedding them would be ~10x the calls for little gain.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Connection, Engine, text

from core.skills.vector import EMBEDDING_DIMENSION, to_pgvector

_SELECT_PENDING = text(
    "SELECT s.skill_id, s.preferred_label FROM esco.skill AS s "
    "LEFT JOIN esco.skill_embedding AS e ON e.skill_id = s.skill_id "
    "WHERE (e.skill_id IS NULL OR e.embedding_model <> :model) "
    "AND (CAST(:skill_ids AS text[]) IS NULL OR s.skill_id = ANY(:skill_ids)) "
    "ORDER BY s.skill_id"
)
_UPSERT = text(
    "INSERT INTO esco.skill_embedding (skill_id, embedding_model, embedding) "
    "VALUES (:skill_id, :model, CAST(:embedding AS vector)) "
    "ON CONFLICT (skill_id) DO UPDATE SET "
    "embedding_model = EXCLUDED.embedding_model, embedding = EXCLUDED.embedding"
)


def embedding_coverage(conn: Connection, model: str) -> tuple[int, int]:
    """Count ESCO skills and how many have an embedding from `model`.

    Args:
        conn: An open connection.
        model: The configured embedding model.

    Returns:
        ``(total skills, skills embedded with `model`)``.
    """
    total = conn.execute(text("SELECT count(*) FROM esco.skill")).scalar_one()
    embedded = conn.execute(
        text("SELECT count(*) FROM esco.skill_embedding WHERE embedding_model = :m"),
        {"m": model},
    ).scalar_one()
    return total, embedded


def embedding_coverage_warning(total: int, embedded: int) -> str | None:
    """Word a warning when ESCO embeddings are missing or incomplete.

    The mapper's similarity stage runs over `esco.skill_embedding`; with none
    of it every string that alias and label matching miss is left unmapped,
    and with only some of it good matches are missed, in both cases silently.

    Args:
        total: How many skills `esco.skill` holds.
        embedded: How many of them have an embedding from the configured model.

    Returns:
        The warning, or None if every skill is embedded.
    """
    if total == 0:
        return "esco.skill is empty — run `load-esco` first"
    if embedded == 0:
        return (
            "esco.skill_embedding is empty for the configured model, so the "
            "similarity stage will match nothing — run `embed-esco`"
        )
    if embedded < total:
        return (
            f"only {embedded} of {total} ESCO skills are embedded, so the "
            "similarity stage will miss matches — run `embed-esco` to finish"
        )
    return None


def embed_esco_skills(
    engine: Engine,
    *,
    embed: Callable[[str], list[float]],
    model: str,
    skill_ids: list[str] | None = None,
    batch_size: int = 200,
) -> int:
    """Embed ESCO skills that lack an embedding for `model`.

    Args:
        engine: The owner-role engine.
        embed: Maps a label to its embedding vector (Ollama in production,
            a fake in tests).
        model: The embedding model name, recorded on every row.
        skill_ids: Restrict the run to these skills; `None` (what the CLI
            passes) covers every skill. Exists so tests never embed a real
            ESCO dataset loaded in the shared dev DB.
        batch_size: Skills per committed transaction.

    Returns:
        The number of embeddings written.

    Raises:
        ValueError: If `embed` returns a vector that is not
            `EMBEDDING_DIMENSION` long (the batch is not written).
    """
    with engine.connect() as conn:
        pending = conn.execute(
            _SELECT_PENDING, {"model": model, "skill_ids": skill_ids}
        ).all()

    written = 0
    for start in range(0, len(pending), batch_size):
        params = []
        for row in pending[start : start + batch_size]:
            vector = embed(row.preferred_label)
            if len(vector) != EMBEDDING_DIMENSION:
                raise ValueError(
                    f"embedding for {row.skill_id} has {len(vector)} dimensions, "
                    f"expected {EMBEDDING_DIMENSION} — is {model!r} the right model?"
                )
            params.append(
                {
                    "skill_id": row.skill_id,
                    "model": model,
                    "embedding": to_pgvector(vector),
                }
            )
        with engine.begin() as conn:
            conn.execute(_UPSERT, params)
        written += len(params)
    return written
