-- Selection bias (PLAN.md Step 11): a manually entered job must never reach a
-- market mart. Passes on zero rows.

SELECT j.job_group_id
FROM {{ ref('int_market_job') }} AS j
INNER JOIN {{ ref('dim_job') }} AS d USING (job_group_id)
WHERE d.entry_method <> 'api'
