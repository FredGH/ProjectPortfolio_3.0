-- fct_market_salary: category x region x month x contract_type (PLAN.md Step
-- 21a). Grain: (period_start, category, region, contract_type).
--
-- n_postings, n_disclosed and disclosure_rate are on EVERY row: UK salary
-- disclosure runs well under half of postings, so a median without its
-- denominator misleads. Statistics are published only when n_disclosed >= 5.
-- Permanent figures are GBP per year; contract figures are GBP per day.

WITH jobs AS (

    SELECT
        first_seen_month AS period_start,
        category,
        region,
        contract_type,
        CASE contract_type
            WHEN 'permanent' THEN annual_salary
            WHEN 'contract' THEN day_rate
        END AS figure
    FROM {{ ref('int_market_job') }}
    WHERE contract_type IN ('permanent', 'contract')

)

SELECT
    j.period_start,
    j.category,
    j.region,
    j.contract_type,
    CASE j.contract_type WHEN 'permanent' THEN 'GBP per year' ELSE 'GBP per day' END AS unit,
    COUNT(*) AS n_postings,
    COUNT(j.figure) AS n_disclosed,
    COUNT(j.figure)::numeric / COUNT(*) AS disclosure_rate,
    CASE WHEN COUNT(j.figure) >= 5 THEN PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY j.figure) END AS median,
    CASE WHEN COUNT(j.figure) >= 5 THEN PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY j.figure) END AS p25,
    CASE WHEN COUNT(j.figure) >= 5 THEN PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY j.figure) END AS p75,
    CASE WHEN COUNT(j.figure) >= 5
        THEN PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY j.figure)
            - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY j.figure) END AS iqr,
    CASE WHEN COUNT(j.figure) >= 5 THEN MIN(j.figure) END AS min_advertised,
    CASE WHEN COUNT(j.figure) >= 5 THEN MAX(j.figure) END AS max_advertised,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM jobs AS j
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = j.period_start
GROUP BY
    j.period_start, j.category, j.region, j.contract_type,
    st.panel_complete, st.panel_sources, st.matrix_version, st.trend_break
