-- fct_market_volume: category x region x week x seniority x work_model
-- (PLAN.md Step 21a). Grain: (period_start, category, region, seniority_band,
-- work_model). Trend date = first seen by us. WoW / MoM change is NULL, never
-- 0, unless the period and its comparison period share a complete panel and
-- matrix version (see int_market_period_stamp).

WITH base AS (

    SELECT
        first_seen_week AS period_start,
        category,
        region,
        seniority_band,
        work_model,
        COUNT(*) AS postings,
        COUNT(*) FILTER (WHERE NOT is_reposted) AS new_postings,
        COUNT(*) FILTER (WHERE is_reposted) AS reposted_postings
    FROM {{ ref('int_market_job') }}
    GROUP BY 1, 2, 3, 4, 5

)

SELECT
    b.period_start,
    b.category,
    b.region,
    b.seniority_band,
    b.work_model,
    b.postings,
    b.new_postings,
    b.reposted_postings,
    b.postings::numeric / SUM(b.postings) OVER (PARTITION BY b.period_start, b.category) AS share_of_category,
    CASE WHEN st.change_1_ok THEN b.postings - COALESCE(w.postings, 0) END AS wow_change,
    CASE WHEN st.change_1_ok AND COALESCE(w.postings, 0) > 0
        THEN (b.postings - w.postings)::numeric / w.postings END AS wow_pct,
    CASE WHEN st.change_4_ok THEN b.postings - COALESCE(m.postings, 0) END AS mom_change,
    CASE WHEN st.change_4_ok AND COALESCE(m.postings, 0) > 0
        THEN (b.postings - m.postings)::numeric / m.postings END AS mom_pct,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM base AS b
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'week' AND st.period_start = b.period_start
LEFT JOIN base AS w
    ON w.period_start = b.period_start - 7
    AND w.category = b.category AND w.region = b.region
    AND w.seniority_band = b.seniority_band AND w.work_model = b.work_model
LEFT JOIN base AS m
    ON m.period_start = b.period_start - 28
    AND m.category = b.category AND m.region = b.region
    AND m.seniority_band = b.seniority_band AND m.work_model = b.work_model
