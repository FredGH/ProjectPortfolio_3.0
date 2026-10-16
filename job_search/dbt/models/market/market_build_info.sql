-- market_build_info: one row describing the last build of the Step 21a marts,
-- for the Market Marts page (PLAN.md Step 22 schedules the build; this lets a
-- stale one be seen). Grain: one row.

SELECT
    CURRENT_TIMESTAMP AS built_at,
    COUNT(*) AS n_jobs,
    MIN(first_seen_at) AS first_seen_min,
    MAX(first_seen_at) AS first_seen_max,
    (SELECT STRING_AGG(source_name, ',' ORDER BY source_name) FROM {{ ref('source_panel') }}) AS panel_sources,
    (SELECT matrix_version FROM {{ ref('query_matrix_versions') }} ORDER BY effective_from DESC LIMIT 1) AS matrix_version,
    (SELECT COUNT(*) FROM {{ ref('int_market_period_stamp') }} WHERE panel_complete) AS complete_periods,
    (SELECT COUNT(*) FROM {{ ref('int_market_period_stamp') }}) AS periods
FROM {{ ref('int_market_job') }}
