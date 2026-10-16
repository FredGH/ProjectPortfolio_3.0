-- Every source behind a market job must be in the source panel seed: this
-- keeps the test_source_* rows and any unlisted source out of every mart.
-- Passes on zero rows.

SELECT j.job_group_id, s.source_name
FROM {{ ref('int_market_job') }} AS j,
    UNNEST(j.panel_sources) AS s(source_name)
WHERE s.source_name NOT IN (SELECT source_name FROM {{ ref('source_panel') }})
