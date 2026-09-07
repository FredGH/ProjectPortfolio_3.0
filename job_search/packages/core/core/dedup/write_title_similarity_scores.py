"""Batch write path for dedup.pair_title_scores (PLAN.md Step 8).

Runs outside dbt because rapidfuzz's token_set_ratio has no SQL
equivalent — the one Step 8 signal that must be computed in Python per
PAIR rather than per job. Reads dedup__candidate_pairs (a dbt-built
table, not a plain migration-owned source) directly via SQL, since
recomputing the self-join in Python would duplicate Step 7's logic.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.title_similarity import title_token_set_ratio

_SELECT_PAIRS_WITH_TITLES = text(
    """
    SELECT
        p.job_key_a,
        p.job_key_b,
        a.matching_title AS title_a,
        b.matching_title AS title_b
    FROM dedup.dedup__candidate_pairs AS p
    INNER JOIN dedup.job_blocking_keys AS a ON p.job_key_a = a.job_key
    INNER JOIN dedup.job_blocking_keys AS b ON p.job_key_b = b.job_key
    """
)

_UPSERT = text(
    """
    INSERT INTO dedup.pair_title_scores (
        job_key_a, job_key_b, title_token_set_ratio
    ) VALUES (
        :job_key_a, :job_key_b, :title_token_set_ratio
    )
    ON CONFLICT (job_key_a, job_key_b) DO UPDATE SET
        title_token_set_ratio = EXCLUDED.title_token_set_ratio,
        computed_at = now()
    """
)


def write_title_similarity_scores(engine: Engine) -> int:
    """Compute and upsert title similarity for every candidate pair.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_PAIRS_WITH_TITLES).all()
        for row in rows:
            ratio = title_token_set_ratio(row.title_a, row.title_b)
            conn.execute(
                _UPSERT,
                {
                    "job_key_a": row.job_key_a,
                    "job_key_b": row.job_key_b,
                    "title_token_set_ratio": ratio,
                },
            )
    return len(rows)
