"""Stage 2 (job side) of the scoring funnel: structural chunking and
embedding of job descriptions (PLAN.md Step 15).

Section detection is a heading-matching heuristic, not a parser: a short
line matching one of a fixed set of canonical phrasings starts a new
section. Real postings vary hugely in structure (aggregator HTML-to-text,
manual entries, ATS exports), so this cannot be perfect — a posting whose
headings don't match becomes one 'other' section rather than losing content
or raising. This is a documented, accepted quality boundary (see the design
spec), not a bug to chase to 100%.

Job embeddings are SHARED and computed once — no RLS on
scoring.job_chunk_embedding, matching the dedup.* pattern.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from llama_index.core.node_parser import SentenceSplitter
from sqlalchemy import Engine, text

from core.skills.vector import to_pgvector

_HEADING_MAX_CHARS = 60
_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "responsibilities",
        re.compile(
            r"^(responsibilities|what you.ll do|the role|key duties)\s*:?\s*$", re.I
        ),
    ),
    (
        "requirements",
        re.compile(
            r"^(requirements|about you|what we.re looking for|essential|"
            r"skills? (and|&) experience)\s*:?\s*$",
            re.I,
        ),
    ),
    (
        "nice_to_have",
        re.compile(r"^(nice to have|desirable|bonus|preferred)\s*:?\s*$", re.I),
    ),
    (
        "benefits",
        re.compile(r"^(benefits|what we offer|perks)\s*:?\s*$", re.I),
    ),
]

_CHUNK_SIZE_TOKENS = 200
_CHUNK_OVERLAP_TOKENS = 20


def detect_sections(description: str) -> list[tuple[str, str]]:
    """Split a job description into named sections by a heading heuristic.

    Args:
        description: The full job description text.

    Returns:
        `(section, text)` pairs in document order. Text before the first
        detected heading is `company_blurb`. If no heading is ever detected,
        the whole description is one `other` section — never dropped.
    """
    lines = description.splitlines()
    sections: list[tuple[str, list[str]]] = [("company_blurb", [])]
    found_any_heading = False
    for line in lines:
        stripped = line.strip()
        matched_section: str | None = None
        if stripped and len(stripped) <= _HEADING_MAX_CHARS:
            for section, pattern in _SECTION_PATTERNS:
                if pattern.match(stripped):
                    matched_section = section
                    break
        if matched_section:
            found_any_heading = True
            sections.append((matched_section, []))
        else:
            sections[-1][1].append(line)
    if not found_any_heading:
        return [("other", description.strip())]
    return [
        (section, "\n".join(body).strip())
        for section, body in sections
        if "\n".join(body).strip()
    ]


_SELECT_UNCHUNKED = text(
    "SELECT j.job_group_id, j.description FROM gold.dim_job AS j "
    "LEFT JOIN (SELECT DISTINCT job_group_id FROM scoring.job_chunk_embedding) "
    "AS c ON c.job_group_id = j.job_group_id "
    "WHERE c.job_group_id IS NULL AND j.description IS NOT NULL"
)
_INSERT_CHUNK = text(
    "INSERT INTO scoring.job_chunk_embedding "
    "(job_group_id, section, chunk_index, chunk_text, embedding, embedding_model) "
    "VALUES (:job_group_id, :section, :chunk_index, :chunk_text, "
    "CAST(:embedding AS vector), :embedding_model) "
    "ON CONFLICT (job_group_id, section, chunk_index) DO NOTHING"
)


def chunk_and_embed_jobs(
    engine: Engine,
    *,
    embed: Callable[[str], list[float]],
    embedding_model: str,
    limit: int | None = None,
) -> int:
    """Chunk and embed every job that has no chunks yet.

    Args:
        engine: The owner-role engine (job_chunk_embedding has no RLS).
        embed: Maps a string to its embedding. `core.embedding.ollama.
            embed_text` has no built-in query/passage prefix support, so the
            raw chunk text is embedded as-is (no `search_document:` prefix
            despite nomic-embed-text supporting one — adding it is future
            work, not required for a working similarity signal).
        embedding_model: Recorded on every row written.
        limit: Cap how many jobs to chunk this run; `None` covers all.

    Returns:
        The number of jobs newly chunked (0 if all were already done).
    """
    query = _SELECT_UNCHUNKED
    if limit is not None:
        query = text(query.text + " LIMIT :limit")
    with engine.connect() as conn:
        jobs = (
            conn.execute(query, {"limit": limit} if limit is not None else {})
            .mappings()
            .all()
        )
    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE_TOKENS, chunk_overlap=_CHUNK_OVERLAP_TOKENS
    )
    for job in jobs:
        with engine.begin() as conn:
            for section, section_text in detect_sections(job["description"]):
                for chunk_index, chunk_text in enumerate(
                    splitter.split_text(section_text)
                ):
                    conn.execute(
                        _INSERT_CHUNK,
                        {
                            "job_group_id": job["job_group_id"],
                            "section": section,
                            "chunk_index": chunk_index,
                            "chunk_text": chunk_text,
                            "embedding": to_pgvector(embed(chunk_text)),
                            "embedding_model": embedding_model,
                        },
                    )
    return len(jobs)
