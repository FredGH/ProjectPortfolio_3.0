"""The Step 14 skill mapper: raw skill string -> ESCO / custom skill id.

Cascade (deterministic, no LLM):
  1. `silver.skill_alias`   — curated, outranks ESCO (method "alias")
  2. `esco.skill_label`     — preferred/alt/hidden labels (method "label")
  3. pgvector nearest neighbour over `esco.skill_embedding`, accepted at
     or above `EMBEDDING_ACCEPT_COSINE` (method "embedding")
  4. otherwise unmapped -> `review_status = 'open'` for the review list.

`map_skill` is read-only; `map_strings`/`map_pending` persist results in
`silver.skill_mapping`, one row per distinct normalised string shared by
CV and JD skills, and never overwrite an existing row — a human
resolution is never clobbered by a re-run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, text

from core.skills.normalise import normalise_skill
from core.skills.vector import to_pgvector

EMBEDDING_ACCEPT_COSINE = 0.85
"""Minimum cosine similarity for the embedding stage to accept a match.

A starting value, NOT empirically tuned (same stance as Step 11a's
cutoffs). Set it after inspecting real ESCO neighbours: hand-check about
100 embedding matches on the Skill Review page and raise it if too many
are wrong, lower it if the review list is swamped with obvious matches.
After changing it, run `pipeline map-skills --remap-unresolved`."""


class EmbeddingModelMismatch(RuntimeError):
    """Raised when `esco.skill_embedding` holds a different model's vectors."""


@dataclass(frozen=True)
class SkillMatch:
    """The outcome of mapping one raw skill string.

    Attributes:
        skill_id: The ESCO id or `custom:<slug>`, or None if unmapped.
        method: "alias", "label", "embedding" or "none".
        score: Cosine similarity, for the embedding method only.
        candidate_skill_id: For an unmapped string, the nearest ESCO skill
            below the threshold (a suggestion for the review page).
        candidate_score: The candidate's cosine similarity.
    """

    skill_id: str | None
    method: str
    score: float | None = None
    candidate_skill_id: str | None = None
    candidate_score: float | None = None


@dataclass(frozen=True)
class MapSummary:
    """Counts from one `map_pending` run.

    Attributes:
        mapped: Strings that resolved to a skill.
        unmapped: Strings sent to the review list.
    """

    mapped: int
    unmapped: int


_ALIAS = text("SELECT skill_id FROM silver.skill_alias WHERE alias_norm = :n")
_LABEL = text(
    "SELECT skill_id FROM esco.skill_label WHERE label_norm = :n "
    "ORDER BY is_preferred DESC, skill_id LIMIT 1"
)
_NEAREST = text(
    "SELECT skill_id, 1 - (embedding <=> CAST(:q AS vector)) AS score "
    "FROM esco.skill_embedding WHERE embedding_model = :model "
    "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 1"
)
_OTHER_MODEL = text(
    "SELECT embedding_model FROM esco.skill_embedding "
    "WHERE embedding_model <> :model LIMIT 1"
)
_INSERT_MAPPING = text(
    "INSERT INTO silver.skill_mapping (raw_norm, raw_example, skill_id, method, "
    "score, candidate_skill_id, candidate_score, review_status, seen_in_cv) "
    "VALUES (:raw_norm, :raw_example, :skill_id, :method, :score, "
    ":candidate_skill_id, :candidate_score, :review_status, :seen_in_cv) "
    "ON CONFLICT (raw_norm) DO NOTHING"
)


def check_embedding_model(conn: Connection, embedding_model: str) -> None:
    """Refuse to map if the stored ESCO embeddings use a different model.

    Args:
        conn: An open connection.
        embedding_model: The model the query embeddings will come from.

    Raises:
        EmbeddingModelMismatch: If any stored embedding was made by another
            model (DECISIONS.md §2.8: mixed models corrupt similarity
            silently). An empty embedding table is allowed — the alias and
            label stages still work.
    """
    other = conn.execute(_OTHER_MODEL, {"model": embedding_model}).scalar_one_or_none()
    if other is not None:
        raise EmbeddingModelMismatch(
            f"esco.skill_embedding holds {other!r} vectors but the configured "
            f"model is {embedding_model!r} — run `pipeline embed-esco` to re-embed"
        )


def map_skill(
    conn: Connection,
    raw: str,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> SkillMatch:
    """Resolve one raw skill string through the cascade (read-only).

    Args:
        conn: An open connection.
        raw: The skill string as written in a CV or JD.
        embed: Maps a string to its embedding (only called if the alias
            and label stages miss).
        embedding_model: The model `embed` uses; only embeddings from this
            model are searched.
        accept_threshold: Minimum cosine similarity to accept an embedding
            match.

    Returns:
        The `SkillMatch`; `method == "none"` means unmapped.
    """
    raw_norm = normalise_skill(raw)
    if not raw_norm:
        return SkillMatch(None, "none")

    alias = conn.execute(_ALIAS, {"n": raw_norm}).scalar_one_or_none()
    if alias is not None:
        return SkillMatch(alias, "alias")
    label = conn.execute(_LABEL, {"n": raw_norm}).scalar_one_or_none()
    if label is not None:
        return SkillMatch(label, "label")

    nearest = conn.execute(
        _NEAREST, {"q": to_pgvector(embed(raw_norm)), "model": embedding_model}
    ).one_or_none()
    if nearest is None:
        return SkillMatch(None, "none")
    score = float(nearest.score)
    if score >= accept_threshold:
        return SkillMatch(nearest.skill_id, "embedding", score=score)
    return SkillMatch(
        None, "none", candidate_skill_id=nearest.skill_id, candidate_score=score
    )


def map_strings(
    engine: Engine,
    raws: Sequence[str],
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    seen_in_cv: bool = False,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> dict[str, str | None]:
    """Map raw strings and persist a `silver.skill_mapping` row for new ones.

    Args:
        engine: The owner-role engine.
        raws: Raw skill strings; duplicates (after normalisation) and
            strings that normalise to empty are ignored.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        seen_in_cv: True when the strings come from a CV — flags the
            mapping rows (new or existing) as seen in a CV.
        accept_threshold: See `map_skill`.

    Returns:
        Map of normalised string to its skill id (None if unmapped),
        reflecting existing rows as well as newly mapped ones.

    Raises:
        EmbeddingModelMismatch: If the stored ESCO embeddings are from a
            different model.
    """
    first_spelling: dict[str, str] = {}
    for raw in raws:
        norm = normalise_skill(raw)
        if norm:
            first_spelling.setdefault(norm, raw)
    if not first_spelling:
        return {}

    result: dict[str, str | None] = {}
    with engine.begin() as conn:
        check_embedding_model(conn, embedding_model)
        existing = {
            row.raw_norm: row.skill_id
            for row in conn.execute(
                text(
                    "SELECT raw_norm, skill_id FROM silver.skill_mapping "
                    "WHERE raw_norm = ANY(:norms)"
                ),
                {"norms": sorted(first_spelling)},
            )
        }
        for norm, raw in first_spelling.items():
            if norm in existing:
                result[norm] = existing[norm]
                if seen_in_cv:
                    conn.execute(
                        text(
                            "UPDATE silver.skill_mapping SET seen_in_cv = true "
                            "WHERE raw_norm = :n"
                        ),
                        {"n": norm},
                    )
                continue
            match = map_skill(
                conn,
                raw,
                embed=embed,
                embedding_model=embedding_model,
                accept_threshold=accept_threshold,
            )
            conn.execute(
                _INSERT_MAPPING,
                {
                    "raw_norm": norm,
                    "raw_example": raw.strip(),
                    "skill_id": match.skill_id,
                    "method": match.method,
                    "score": match.score,
                    "candidate_skill_id": match.candidate_skill_id,
                    "candidate_score": match.candidate_score,
                    "review_status": "open" if match.skill_id is None else None,
                    "seen_in_cv": seen_in_cv,
                },
            )
            result[norm] = match.skill_id
    return result


_SELECT_UNMAPPED = text(
    "SELECT r.raw_norm, MIN(r.raw_skill) AS raw_example "
    "FROM silver.job_skill_raw AS r "
    "LEFT JOIN silver.skill_mapping AS m ON m.raw_norm = r.raw_norm "
    "WHERE m.raw_norm IS NULL "
    "AND (CAST(:raw_norms AS text[]) IS NULL OR r.raw_norm = ANY(:raw_norms)) "
    "GROUP BY r.raw_norm ORDER BY r.raw_norm"
)


def map_pending(
    engine: Engine,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    raw_norms: list[str] | None = None,
    accept_threshold: float = EMBEDDING_ACCEPT_COSINE,
) -> MapSummary:
    """Map every extracted JD skill string that has no mapping row yet.

    Args:
        engine: The owner-role engine.
        embed: Maps a string to its embedding.
        embedding_model: The embedding model in use.
        raw_norms: Restrict to these normalised strings; `None` (what the
            CLI passes) covers everything. Exists so tests never touch
            unrelated rows in the shared dev DB.
        accept_threshold: See `map_skill`.

    Returns:
        How many strings mapped vs. went to the review list.
    """
    with engine.connect() as conn:
        rows = conn.execute(_SELECT_UNMAPPED, {"raw_norms": raw_norms}).all()
    result = map_strings(
        engine,
        [row.raw_example for row in rows],
        embed=embed,
        embedding_model=embedding_model,
        accept_threshold=accept_threshold,
    )
    mapped = sum(1 for skill_id in result.values() if skill_id is not None)
    return MapSummary(mapped=mapped, unmapped=len(result) - mapped)


def remap_unresolved(engine: Engine, *, raw_norms: list[str] | None = None) -> int:
    """Delete auto-made mappings so the next `map_pending` re-maps them.

    Deletes rows with method `embedding`, or method `none` and status
    `open`. Never deletes `rejected`, `resolved` or `dismissed` rows — those
    carry a human decision. CV-only strings (not in `job_skill_raw`) are
    re-mapped by re-running `map-cv-skills`.

    Args:
        engine: The owner-role engine.
        raw_norms: Restrict to these strings; `None` covers all.

    Returns:
        The number of rows deleted.
    """
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM silver.skill_mapping "
                "WHERE (method = 'embedding' "
                "OR (method = 'none' AND review_status = 'open')) "
                "AND (CAST(:raw_norms AS text[]) IS NULL "
                "OR raw_norm = ANY(:raw_norms))"
            ),
            {"raw_norms": raw_norms},
        )
    return result.rowcount
