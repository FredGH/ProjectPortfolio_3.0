-- dedup__candidate_pairs: one row per candidate pair sharing a block
-- key (hard block) or company alone (soft block) — PLAN.md Step 7's
-- pair-generation step, feeding Step 8's similarity scoring. Grain:
-- (job_key_a, job_key_b, match_type), job_key_a < job_key_b so each
-- unordered pair appears once per match_type it qualifies under.

WITH keys AS (

    SELECT * FROM {{ source('dedup_ingest', 'job_blocking_keys') }}

),

-- Hard block: same company, same truncated title, same resolved
-- country. The primary blocking mechanism.
block_pairs AS (

    SELECT
        a.job_key AS job_key_a,
        b.job_key AS job_key_b,
        'block' AS match_type
    FROM keys AS a
    INNER JOIN keys AS b
        ON a.block_key = b.block_key
        AND a.job_key < b.job_key

),

-- Company sizes, used to exclude large employers from the soft block —
-- soft-block's purpose (catching title-divergent duplicates) doesn't
-- scale to employers with hundreds of genuinely distinct postings; they
-- still get hard-block matching, just not soft.
company_sizes AS (

    SELECT
        normalised_company,
        COUNT(*) AS company_size
    FROM keys
    WHERE normalised_company != ''
    GROUP BY normalised_company

),

-- Soft block: same company only, titles diverging too badly to share a
-- 12-character prefix (PLAN.md's "Senior Data Engineer" vs "Data
-- Platform Engineer" example). Excludes pairs already caught by the
-- hard block, so a pair never appears under both match_types. Also
-- excludes companies with more than 500 postings — see company_sizes.
soft_block_pairs AS (

    SELECT
        a.job_key AS job_key_a,
        b.job_key AS job_key_b,
        'soft_block' AS match_type
    FROM keys AS a
    INNER JOIN keys AS b
        ON a.normalised_company = b.normalised_company
        AND a.job_key < b.job_key
    INNER JOIN company_sizes AS cs
        ON a.normalised_company = cs.normalised_company
    WHERE a.normalised_company != ''
        AND cs.company_size <= 500

)

SELECT * FROM block_pairs
UNION ALL
SELECT sb.*
FROM soft_block_pairs AS sb
LEFT JOIN block_pairs AS bp
    ON sb.job_key_a = bp.job_key_a AND sb.job_key_b = bp.job_key_b
WHERE bp.job_key_a IS NULL
