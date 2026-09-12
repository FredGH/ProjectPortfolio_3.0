-- fct_market_demand: the base demand fact (PLAN.md Step 11), one row
-- per dim_job, filtered to entry_method = 'api'.
-- Grain: job_group_id (unique, FK to dim_job).
--
-- Selection-bias filter: manual entries are jobs a user chose to paste
-- in themselves, not postings the pipeline discovered independently.
-- If they flowed into a market-facing fact, the resulting chart would
-- measure the user's own browsing habits, not the market (PLAN.md
-- Step 11). Manual entries still flow freely into dim_job/scoring/the
-- application pipeline, where this bias is harmless — this filter
-- exists ONLY here, not upstream, so a future model built on dim_job
-- doesn't inherit it by accident.
--
-- No category column yet: Step 11a (job categorisation) is the next
-- step and is explicitly blocked BY this one finishing first
-- (plan/backlog.yml STEP-11 blocks STEP-11A) — category cannot exist
-- before it runs. This model is deliberately grained at job_group_id
-- rather than pre-aggregated to (category x region x week): that
-- coarser aggregation is PLAN.md's separately-specified fct_market_
-- volume (category x region x week x seniority x work_model, PLAN.md
-- line 1484), built once Step 11a supplies category/seniority_band.

SELECT
    dj.job_group_id,
    dc.company_key,
    dj.country_iso,
    dj.region,
    dj.posted_at,
    DATE_TRUNC('week', dj.posted_at) AS posted_week,
    dj.entry_method
FROM {{ ref('dim_job') }} AS dj
LEFT JOIN {{ ref('dim_company') }} AS dc
    ON dj.normalised_company = dc.normalised_company
WHERE dj.entry_method = 'api'
