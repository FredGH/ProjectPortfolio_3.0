"""Stage 2 (CV side) of the scoring funnel: chunking and embedding a user's
CV truth base (PLAN.md Step 15).

Unlike the job side, the CV's structure is already known (core.cv.schema) —
no heading detection needed. CV embeddings are per-user and never shared
(RLS on scoring.cv_chunk_embedding).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from llama_index.core.node_parser import SentenceSplitter
from sqlalchemy import Engine, text

from core.cv.schema import CVTruthBase
from core.cv.store import read_truth_base
from core.db.session import session_scope
from core.skills.vector import to_pgvector

_CHUNK_SIZE_TOKENS = 200
_CHUNK_OVERLAP_TOKENS = 20


def build_cv_sections(truth_base: CVTruthBase) -> list[tuple[str, str, str]]:
    """Turn a CV truth base into named, embeddable sections.

    Args:
        truth_base: The user's CV truth base.

    Returns:
        `(section, source_ref, text)` triples. `source_ref` traces back to
        the originating CV entry (e.g. a bullet_id) for later display;
        empty string where there is no finer-grained ref (e.g. `summary`,
        `skills`). A section with no content is omitted, not emitted empty.
    """
    sections: list[tuple[str, str, str]] = []
    if truth_base.summary:
        sections.append(("summary", "", truth_base.summary))
    if truth_base.skills:
        sections.append(("skills", "", ", ".join(s.name for s in truth_base.skills)))
    for exp in truth_base.experience:
        for bullet in exp.bullets:
            sections.append(
                (
                    "experience",
                    bullet.bullet_id,
                    f"{exp.title} at {exp.company}: {bullet.text}",
                )
            )
    if truth_base.education:
        sections.append(
            (
                "education",
                "",
                "; ".join(
                    f"{e.qualification or ''} {e.institution}".strip()
                    for e in truth_base.education
                ),
            )
        )
    if truth_base.qualifications:
        sections.append(
            (
                "certifications",
                "",
                "; ".join(q.name for q in truth_base.qualifications),
            )
        )
    if truth_base.projects:
        sections.append(
            (
                "projects",
                "",
                "; ".join(f"{p.name}: {p.description}" for p in truth_base.projects),
            )
        )
    return sections


_SELECT_EXISTING_VERSION = text(
    "SELECT DISTINCT cv_version FROM scoring.cv_chunk_embedding "
    "WHERE user_id = :user_id LIMIT 1"
)
_DELETE_FOR_USER = text(
    "DELETE FROM scoring.cv_chunk_embedding WHERE user_id = :user_id"
)
_INSERT_CHUNK = text(
    "INSERT INTO scoring.cv_chunk_embedding "
    "(user_id, cv_version, section, chunk_index, source_ref, chunk_text, "
    "embedding, embedding_model) "
    "VALUES (:user_id, :cv_version, :section, :chunk_index, :source_ref, "
    ":chunk_text, CAST(:embedding AS vector), :embedding_model)"
)


def chunk_and_embed_cv(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    refresh: bool = False,
) -> int:
    """Chunk and embed one user's current CV truth base.

    Args:
        app_engine: The app-role engine (RLS-enforced).
        user_id: Whose CV to chunk.
        embed: Maps a string to its embedding — the same raw-text
            `embed_text` call the job side uses (no query/passage prefix;
            see `job_chunking.chunk_and_embed_jobs`'s docstring).
        embedding_model: Recorded on every row written.
        refresh: Recompute even if this CV version already has chunks
            (e.g. after an embedding-model change). Without it, a call for
            a version that already has rows writes nothing.

    Returns:
        The number of chunks written (0 if skipped because already current).

    Raises:
        LookupError: If the user has no CV truth base.
    """
    stored = read_truth_base(app_engine, user_id)
    if stored is None:
        raise LookupError(f"user {user_id} has no CV truth base")
    with session_scope(app_engine, user_id=user_id) as conn:
        existing_version = conn.execute(
            _SELECT_EXISTING_VERSION, {"user_id": user_id}
        ).scalar_one_or_none()
    if existing_version == stored.version and not refresh:
        return 0
    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE_TOKENS, chunk_overlap=_CHUNK_OVERLAP_TOKENS
    )
    written = 0
    # Multiple `build_cv_sections` entries can share the same section name
    # (e.g. one "experience" entry per bullet), so chunk_index must run
    # per section NAME across the whole loop, not restart for each entry —
    # otherwise a second entry's first chunk collides with the first
    # entry's chunk_index=0 on the table's (user_id, cv_version, section,
    # chunk_index) primary key.
    section_chunk_counters: dict[str, int] = {}
    with session_scope(app_engine, user_id=user_id) as conn:
        conn.execute(_DELETE_FOR_USER, {"user_id": user_id})
        for section, source_ref, section_text in build_cv_sections(stored.truth_base):
            for chunk_text in splitter.split_text(section_text):
                chunk_index = section_chunk_counters.get(section, 0)
                conn.execute(
                    _INSERT_CHUNK,
                    {
                        "user_id": user_id,
                        "cv_version": stored.version,
                        "section": section,
                        "chunk_index": chunk_index,
                        "source_ref": source_ref,
                        "chunk_text": chunk_text,
                        "embedding": to_pgvector(embed(chunk_text)),
                        "embedding_model": embedding_model,
                    },
                )
                section_chunk_counters[section] = chunk_index + 1
                written += 1
    return written
