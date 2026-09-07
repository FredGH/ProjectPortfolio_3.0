"""Batch write path for dedup.job_blocking_keys (PLAN.md Step 7).

Runs outside dbt for the same reason core.enrichment.
write_engagement_terms does (PLAN.md Step 5a) — the normalisation logic
is pure Python, so it has to execute once per row in application code.
dbt's dedup__candidate_pairs and dedup__exact_duplicates models
(dbt/models/dedup/) read this table's output via a source(), never
recomputing it.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.blocking_keys import compute_blocking_key

_SELECT_POSTINGS = text(
    "SELECT job_key, company, title, location, description "
    "FROM silver.silver__job_posting"
)

_UPSERT = text(
    """
    INSERT INTO dedup.job_blocking_keys (
        job_key, normalised_company, matching_title, country_iso,
        region, block_key, content_sha256
    ) VALUES (
        :job_key, :normalised_company, :matching_title, :country_iso,
        :region, :block_key, :content_sha256
    )
    ON CONFLICT (job_key) DO UPDATE SET
        normalised_company = EXCLUDED.normalised_company,
        matching_title = EXCLUDED.matching_title,
        country_iso = EXCLUDED.country_iso,
        region = EXCLUDED.region,
        block_key = EXCLUDED.block_key,
        content_sha256 = EXCLUDED.content_sha256,
        computed_at = now()
    """
)


def write_blocking_keys(engine: Engine) -> int:
    """Compute and upsert blocking keys for every posting.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone
            (no user_id, no RLS), same as silver.job_engagement_terms.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_POSTINGS).all()
        for row in rows:
            result = compute_blocking_key(
                row.company, row.title, row.location, row.description
            )
            conn.execute(
                _UPSERT,
                {
                    "job_key": row.job_key,
                    "normalised_company": result.normalised_company,
                    "matching_title": result.matching_title,
                    "country_iso": result.country_iso,
                    "region": result.region,
                    "block_key": result.block_key,
                    "content_sha256": result.content_sha256,
                },
            )
    return len(rows)
