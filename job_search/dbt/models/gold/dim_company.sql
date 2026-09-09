-- dim_company: one row per normalised company (PLAN.md Step 11).
-- Grain: company_key (unique). Greenfield — no prior dim_company or
-- company-identity convention exists in this codebase; this model
-- introduces one, reusing Step 7's normalise_company output (already
-- computed for dedup blocking and exposed on dim_job as
-- normalised_company) as the grouping key. normalise_company is
-- explicitly documented as matching-only, never a display string
-- (core/normalisation/company.py) — so a separate display name is
-- resolved here as the most frequent raw company spelling within the
-- group (ties broken alphabetically for determinism).

WITH company_spellings AS (

    SELECT
        job_group_id,
        normalised_company,
        company AS raw_company
    FROM {{ ref('dim_job') }}
    WHERE normalised_company != ''

),

spelling_counts AS (

    SELECT
        normalised_company,
        raw_company,
        COUNT(*) AS spelling_count
    FROM company_spellings
    GROUP BY normalised_company, raw_company

),

ranked_spellings AS (

    SELECT
        normalised_company,
        raw_company,
        ROW_NUMBER() OVER (
            PARTITION BY normalised_company
            ORDER BY spelling_count DESC, raw_company
        ) AS spelling_rank
    FROM spelling_counts

),

job_counts AS (

    SELECT
        normalised_company,
        COUNT(DISTINCT job_group_id) AS job_count
    FROM company_spellings
    GROUP BY normalised_company

)

SELECT
    {{ dbt_utils.generate_surrogate_key(['ranked_spellings.normalised_company']) }}
        AS company_key,
    ranked_spellings.normalised_company,
    ranked_spellings.raw_company AS company_name,
    job_counts.job_count
FROM ranked_spellings
INNER JOIN job_counts USING (normalised_company)
WHERE ranked_spellings.spelling_rank = 1
