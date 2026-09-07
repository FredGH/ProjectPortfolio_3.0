-- Every job whose rate_basis is not 'unknown' must have at least one of
-- the two comparable figures populated — the exact comparability fix
-- PLAN.md Step 5a describes.

SELECT job_key, rate_basis
FROM {{ ref('silver__job_posting') }}
WHERE rate_basis != 'unknown'
    AND rate_annualised IS NULL
    AND rate_daily_equivalent IS NULL
