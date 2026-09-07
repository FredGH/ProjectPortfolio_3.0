"""Batch write path for silver.job_engagement_terms (PLAN.md Step 5a).

Runs outside dbt because the extraction logic (core.enrichment.
engagement_terms) needs to execute once per row in application code — the
same reason Step 10's future dedup.py sits outside dbt too. dbt's
silver__job_posting model (dbt/models/silver/) reads this table's output
via a source(), never recomputing it.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

from core.enrichment.engagement_terms import extract_engagement_terms

_SELECT_UNIONED = text(
    "SELECT job_key, description, salary_raw FROM intermediate.int_jobs__unioned"
)

_UPSERT = text(
    """
    INSERT INTO silver.job_engagement_terms (
        job_key, engagement_type, ir35_status, engagement_vehicle,
        rate_basis, rate_currency, rate_annualised,
        rate_daily_equivalent, contract_length_months,
        extension_likelihood
    ) VALUES (
        :job_key, :engagement_type, :ir35_status, :engagement_vehicle,
        :rate_basis, :rate_currency, :rate_annualised,
        :rate_daily_equivalent, :contract_length_months,
        :extension_likelihood
    )
    ON CONFLICT (job_key) DO UPDATE SET
        engagement_type = EXCLUDED.engagement_type,
        ir35_status = EXCLUDED.ir35_status,
        engagement_vehicle = EXCLUDED.engagement_vehicle,
        rate_basis = EXCLUDED.rate_basis,
        rate_currency = EXCLUDED.rate_currency,
        rate_annualised = EXCLUDED.rate_annualised,
        rate_daily_equivalent = EXCLUDED.rate_daily_equivalent,
        contract_length_months = EXCLUDED.contract_length_months,
        extension_likelihood = EXCLUDED.extension_likelihood,
        extracted_at = now()
    """
)


def write_engagement_terms(engine: Engine) -> int:
    """Extract and upsert engagement terms for every unioned job.

    Args:
        engine: The migration/owner engine — this table is SHARED-zone
            (no user_id, no RLS), same as bronze.raw_jobs.

    Returns:
        The number of rows written (inserted or updated).
    """
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_UNIONED).all()
        for row in rows:
            terms = extract_engagement_terms(row.description, row.salary_raw)
            conn.execute(
                _UPSERT,
                {
                    "job_key": row.job_key,
                    "engagement_type": terms.engagement_type,
                    "ir35_status": terms.ir35_status,
                    "engagement_vehicle": terms.engagement_vehicle,
                    "rate_basis": terms.rate_basis,
                    "rate_currency": terms.rate_currency,
                    "rate_annualised": terms.rate_annualised,
                    "rate_daily_equivalent": terms.rate_daily_equivalent,
                    "contract_length_months": terms.contract_length_months,
                    "extension_likelihood": terms.extension_likelihood,
                },
            )
    return len(rows)
