"""Stage 2 continued: vector similarity + cross-encoder rerank (PLAN.md
Step 15).

Guards against comparing chunks embedded under different embedding_model
values (mirrors core.skills.mapper.EmbeddingModelMismatch) — a stale job
vector compared against a CV re-embedded under a new model would produce a
confident-looking, meaningless number.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine, text

from core.db.session import session_scope

# Only these section pairs are compared — "compare like sections only"
# (DECISIONS.md §4). A CV section not listed here (education, certifications,
# projects) is never compared against any JD section.
_SECTION_PAIRS = {
    "experience": "responsibilities",
    "skills": "requirements",
    "summary": "company_blurb",
}

_SELECT_CV_CHUNKS = text(
    "SELECT section, embedding, embedding_model, chunk_text "
    "FROM scoring.cv_chunk_embedding WHERE user_id = :user_id"
)
_SELECT_JOB_CHUNKS = text(
    "SELECT job_group_id, section, embedding, embedding_model, chunk_text "
    "FROM scoring.job_chunk_embedding"
)
_SELECT_CANDIDATE_JOBS = text(
    "SELECT job_group_id FROM scoring.job_score "
    "WHERE user_id = :user_id AND hard_filter_passed = true"
)
_UPDATE_VECTOR_SCORE = text(
    "UPDATE scoring.job_score SET vector_similarity_score = :score, "
    "embedding_model = :embedding_model "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)
_UPDATE_RERANK_SCORE = text(
    "UPDATE scoring.job_score SET reranker_score = :score "
    "WHERE user_id = :user_id AND job_group_id = :job_group_id"
)
_CLEAR_RERANK_SCORES = text(
    "UPDATE scoring.job_score SET reranker_score = NULL "
    "WHERE user_id = :user_id AND job_group_id = ANY(:job_group_ids)"
)


@dataclass(frozen=True)
class SimilaritySummary:
    """Counts from one `run_similarity` run.

    Attributes:
        jobs_scored: Jobs considered this run (every hard-filter-passing
            candidate with at least one embedded JD chunk), whether or not
            it ended up with a non-NULL `vector_similarity_score`.
        mismatched: Of those, jobs whose `vector_similarity_score` was
            skipped (left NULL) solely because every comparable chunk pair
            was embedded under different `embedding_model` values.
    """

    jobs_scored: int
    mismatched: int


def _parse_vector(raw: str) -> list[float]:
    """Parse a pgvector text literal back into floats.

    Args:
        raw: The value as returned by psycopg for a `vector` column, e.g.
            ``"[0.1,0.2]"``. This project registers no pgvector driver
            adapter (see `core.skills.vector.to_pgvector`'s docstring), so
            the driver hands back the column's own text representation
            rather than a Python sequence.

    Returns:
        The vector's components as floats.
    """
    return [float(x) for x in raw.strip("[]").split(",")]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        The cosine similarity, in [-1, 1].
    """
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def run_similarity(
    app_engine: Engine,
    user_id: uuid.UUID,
    *,
    rerank: Callable[[str, str], float],
    top_n: int = 200,
) -> SimilaritySummary:
    """Score vector similarity for every hard-filter-passing job, then
    rerank the top `top_n` with a cross-encoder.

    Args:
        app_engine: The app-role engine (RLS-enforced for the per-user
            reads/writes; job_chunk_embedding itself has no RLS).
        user_id: Whose CV and scores to use.
        rerank: Given (CV text, JD text), returns a cross-encoder score.
            Injected so tests never load a real model.
        top_n: How many top-scoring jobs get a reranker score.

    Returns:
        A `SimilaritySummary` with the number of jobs considered
        (`jobs_scored`, including those skipped for a model mismatch, which
        get no score but are still "considered") and how many of those were
        skipped for a model mismatch (`mismatched`).
    """
    with session_scope(app_engine, user_id=user_id) as conn:
        cv_chunks = (
            conn.execute(_SELECT_CV_CHUNKS, {"user_id": user_id}).mappings().all()
        )
        candidate_jobs = {
            r.job_group_id
            for r in conn.execute(_SELECT_CANDIDATE_JOBS, {"user_id": user_id})
        }
    with app_engine.connect() as conn:
        job_chunks = conn.execute(_SELECT_JOB_CHUNKS).mappings().all()

    cv_by_section: dict[str, list[dict]] = {}
    for chunk in cv_chunks:
        cv_by_section.setdefault(chunk["section"], []).append(chunk)

    jobs: dict[str, list[dict]] = {}
    for chunk in job_chunks:
        if chunk["job_group_id"] in candidate_jobs:
            jobs.setdefault(chunk["job_group_id"], []).append(chunk)

    results: dict[str, float | None] = {}
    embedding_model_used: dict[str, str] = {}
    mismatched = 0
    for job_group_id, chunks in jobs.items():
        pair_scores: list[float] = []
        mismatch = False
        for cv_section, job_section in _SECTION_PAIRS.items():
            cv_side = cv_by_section.get(cv_section, [])
            job_side = [c for c in chunks if c["section"] == job_section]
            if not cv_side or not job_side:
                continue
            best = 0.0
            # Tracks whether any chunk pair for this section was actually
            # compared, since a genuinely-computed cosine of exactly 0.0 is
            # indistinguishable from `best`'s initial value — using `best`'s
            # truthiness to gate the append below would silently drop it.
            compared = False
            for cv_chunk in cv_side:
                for job_chunk in job_side:
                    if cv_chunk["embedding_model"] != job_chunk["embedding_model"]:
                        mismatch = True
                        continue
                    compared = True
                    embedding_model_used[job_group_id] = cv_chunk["embedding_model"]
                    score = _cosine(
                        _parse_vector(cv_chunk["embedding"]),
                        _parse_vector(job_chunk["embedding"]),
                    )
                    best = max(best, score)
            if compared:
                pair_scores.append(best)
        job_is_mismatched = mismatch and not pair_scores
        if job_is_mismatched:
            mismatched += 1
        results[job_group_id] = (
            None
            if job_is_mismatched
            else (sum(pair_scores) / len(pair_scores) if pair_scores else None)
        )

    with session_scope(app_engine, user_id=user_id) as conn:
        for job_group_id, score in results.items():
            conn.execute(
                _UPDATE_VECTOR_SCORE,
                {
                    "user_id": user_id,
                    "job_group_id": job_group_id,
                    "score": score,
                    "embedding_model": embedding_model_used.get(job_group_id),
                },
            )

    ranked = sorted(
        (j for j, s in results.items() if s is not None),
        key=lambda j: results[j],
        reverse=True,
    )[:top_n]
    job_text_by_id = {
        job_group_id: " ".join(c["chunk_text"] for c in chunks)
        for job_group_id, chunks in jobs.items()
    }
    cv_text = " ".join(c["chunk_text"] for c in cv_chunks)
    with session_scope(app_engine, user_id=user_id) as conn:
        # A job scored in a previous run that no longer makes this run's
        # top-n cut must not keep its old reranker_score: clear every job
        # considered this run first, then the loop below sets the real
        # value only for the current `ranked` subset.
        conn.execute(
            _CLEAR_RERANK_SCORES,
            {"user_id": user_id, "job_group_ids": list(results.keys())},
        )
        for job_group_id in ranked:
            conn.execute(
                _UPDATE_RERANK_SCORE,
                {
                    "user_id": user_id,
                    "job_group_id": job_group_id,
                    "score": rerank(cv_text, job_text_by_id[job_group_id]),
                },
            )
    return SimilaritySummary(jobs_scored=len(jobs), mismatched=mismatched)
