-- A job that is still live has not disappeared, so it has no duration; a job
-- that is gone has one that is never negative. Passes on zero rows.

SELECT job_group_id
FROM {{ ref('fct_posting_lifecycle') }}
WHERE (still_live AND days_live IS NOT NULL)
    OR (NOT still_live AND (days_live IS NULL OR days_live < 0))
