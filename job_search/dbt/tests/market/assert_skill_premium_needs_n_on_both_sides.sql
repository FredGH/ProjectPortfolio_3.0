-- A salary premium needs at least 5 disclosed figures with AND without the
-- skill. Passes on zero rows.

SELECT *
FROM {{ ref('fct_skill_salary_premium') }}
WHERE premium IS NOT NULL AND (n_with < 5 OR n_without < 5)
    OR premium IS NOT NULL AND ABS(premium - (median_with - median_without)) > 0.0001
