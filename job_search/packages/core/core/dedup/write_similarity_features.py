"""Batch write path for dedup.job_similarity_features (PLAN.md Step 8).

Converts each SimHash fingerprint from Python's natural unsigned
representation to Postgres bigint's signed two's-complement range
before every INSERT — see this plan's Global Constraints for why this
conversion is required, not optional.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.dedup.similarity_features import compute_similarity_features

_SIGNED_BIGINT_OFFSET = 2**64
_SIGNED_BIGINT_MAX = 2**63 - 1

_SELECT_POSTINGS = text(
    "SELECT job_key, description, salary_raw FROM silver.silver__job_posting"
)

_UPSERT = text(
    """
    INSERT INTO dedup.job_similarity_features (
        job_key, description_simhash, rate_annualised, rate_currency,
        salary_band
    ) VALUES (
        :job_key, :description_simhash, :rate_annualised, :rate_currency,
        :salary_band
    )
    ON CONFLICT (job_key) DO UPDATE SET
        description_simhash = EXCLUDED.description_simhash,
        rate_annualised = EXCLUDED.rate_annualised,
        rate_currency = EXCLUDED.rate_currency,
        salary_band = EXCLUDED.salary_band,
        computed_at = now()
    """
)


def _to_signed_bigint(unsigned_value: int) -> int:
    """Convert an unsigned 64-bit value to Postgres bigint's signed range.

    Args:
        unsigned_value: A value in [0, 2**64).

    Returns:
        The same 64-bit pattern, reinterpreted as a signed integer in
        [-2**63, 2**63 - 1] — the range Postgres bigint accepts.
    """
    if unsigned_value > _SIGNED_BIGINT_MAX:
        return unsigned_value - _SIGNED_BIGINT_OFFSET
    return unsigned_value


def write_similarity_features(engine: Engine) -> int:
    """Compute and upsert similarity features for every posting.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_POSTINGS).all()
        for row in rows:
            features = compute_similarity_features(row.description, row.salary_raw)
            conn.execute(
                _UPSERT,
                {
                    "job_key": row.job_key,
                    "description_simhash": _to_signed_bigint(
                        features.description_simhash
                    ),
                    "rate_annualised": features.rate_annualised,
                    "rate_currency": features.rate_currency,
                    "salary_band": features.salary_band,
                },
            )
    return len(rows)
