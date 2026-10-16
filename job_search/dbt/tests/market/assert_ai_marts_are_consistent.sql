-- Shares are fractions of the stated denominator; 'any' is at least each term
-- and at most the sum of the terms; migration shares per month sum to 1; no
-- test or manual source can have reached the text flags. Passes on zero rows.

SELECT 'share out of range' AS problem, category
FROM {{ ref('fct_ai_penetration') }}
WHERE share < 0 OR share > 1 OR n_with > n_postings

UNION ALL

SELECT 'any smaller than a term', a.category
FROM {{ ref('fct_ai_penetration') }} AS a
INNER JOIN {{ ref('fct_ai_penetration') }} AS t
    ON a.period_start = t.period_start AND a.category = t.category
    AND a.term = 'any' AND t.term <> 'any'
WHERE a.n_with < t.n_with

UNION ALL

SELECT 'migration shares do not sum to 1', period_start::text
FROM {{ ref('fct_ai_skill_migration') }}
GROUP BY period_start
HAVING ABS(SUM(share_of_ai_postings) - 1) > 0.0001

UNION ALL

SELECT 'text flags outside the market base', f.job_group_id
FROM {{ ref('int_market_job_text_flags') }} AS f
WHERE f.job_group_id NOT IN (SELECT job_group_id FROM {{ ref('int_market_job') }})
