-- fct_skill_salary_premium: median salary of postings requiring a skill minus
-- the median of those that do not, per category and contract type (PLAN.md
-- Step 21a). Grain: (category, contract_type, skill_id). Cross-sectional.
-- Permanent figures are GBP per year, contract figures GBP per day, never
-- mixed. The premium is published only when both sides have n >= 5.

WITH figure_jobs AS (

    SELECT
        job_group_id,
        category,
        contract_type,
        CASE contract_type
            WHEN 'permanent' THEN annual_salary
            WHEN 'contract' THEN day_rate
        END AS figure
    FROM {{ ref('int_market_job') }}
    WHERE contract_type IN ('permanent', 'contract')

),

disclosed AS (

    SELECT * FROM figure_jobs WHERE figure IS NOT NULL

),

skills AS (

    SELECT DISTINCT d.category, d.contract_type, b.skill_id
    FROM disclosed AS d
    INNER JOIN {{ ref('silver__bridge_job_skill') }} AS b
        ON d.job_group_id = b.job_group_id

),

with_skill AS (

    SELECT
        s.category,
        s.contract_type,
        s.skill_id,
        COUNT(*) AS n_with,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.figure) AS median_with
    FROM skills AS s
    INNER JOIN disclosed AS d
        ON d.category = s.category AND d.contract_type = s.contract_type
    INNER JOIN {{ ref('silver__bridge_job_skill') }} AS b
        ON b.job_group_id = d.job_group_id AND b.skill_id = s.skill_id
    GROUP BY 1, 2, 3

),

without_skill AS (

    SELECT
        s.category,
        s.contract_type,
        s.skill_id,
        COUNT(*) AS n_without,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY d.figure) AS median_without
    FROM skills AS s
    INNER JOIN disclosed AS d
        ON d.category = s.category AND d.contract_type = s.contract_type
    WHERE NOT EXISTS (
        SELECT 1 FROM {{ ref('silver__bridge_job_skill') }} AS b
        WHERE b.job_group_id = d.job_group_id AND b.skill_id = s.skill_id
    )
    GROUP BY 1, 2, 3

)

SELECT
    w.category,
    w.contract_type,
    w.skill_id,
    sk.canonical_label AS skill_label,
    CASE w.contract_type WHEN 'permanent' THEN 'GBP per year' ELSE 'GBP per day' END AS unit,
    w.n_with,
    COALESCE(o.n_without, 0) AS n_without,
    CASE WHEN w.n_with >= 5 THEN w.median_with END AS median_with,
    CASE WHEN COALESCE(o.n_without, 0) >= 5 THEN o.median_without END AS median_without,
    CASE WHEN w.n_with >= 5 AND COALESCE(o.n_without, 0) >= 5
        THEN w.median_with - o.median_without END AS premium
FROM with_skill AS w
LEFT JOIN without_skill AS o
    USING (category, contract_type, skill_id)
LEFT JOIN {{ ref('silver__skill') }} AS sk
    ON w.skill_id = sk.skill_id
