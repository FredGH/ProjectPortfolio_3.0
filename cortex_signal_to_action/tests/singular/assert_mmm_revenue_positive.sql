-- assert_mmm_revenue_positive.sql
-- weekly_revenue must be strictly positive for every row in slv_mmm_weekly.
-- Returns rows where revenue is zero or negative — zero rows = test passes.

SELECT
    iso_week,
    week_start_date,
    weekly_revenue
FROM {{ ref('slv_mmm_weekly') }}
WHERE weekly_revenue <= 0
