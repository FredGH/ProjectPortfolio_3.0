-- assert_dim_job_has_a_category_for_every_row: dim_job's join to
-- job_category is LEFT (so a not-yet-classified row still appears),
-- but every row SHOULD eventually get classified — this catches a
-- classify-jobs run that silently stopped partway (e.g. hit an
-- unhandled exception on one row and never retried the rest) rather
-- than letting `category IS NULL` rows accumulate invisibly. A test
-- passes on zero rows returned.

SELECT job_group_id
FROM {{ ref('dim_job') }}
WHERE category IS NULL
