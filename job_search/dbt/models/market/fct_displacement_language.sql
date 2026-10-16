-- fct_displacement_language: postings that describe OVERSEEING automated work
-- ("review and validate model outputs", "human in the loop") versus PERFORMING
-- it ("build the pipeline") (PLAN.md Step 21b). Grain: (period_start,
-- category). A slow, weak signal built from short phrase lists
-- (seeds/displacement_phrases.csv): read it as a direction, not a measurement.

SELECT
    f.first_seen_month AS period_start,
    f.category,
    COUNT(*) AS n_postings,
    COUNT(*) FILTER (WHERE f.mentions_oversight) AS n_oversight,
    COUNT(*) FILTER (WHERE f.mentions_build) AS n_build,
    COUNT(*) FILTER (WHERE f.mentions_oversight)::numeric / COUNT(*) AS oversight_share,
    COUNT(*) FILTER (WHERE f.mentions_build)::numeric / COUNT(*) AS build_share,
    CASE WHEN st.change_3_ok THEN
        COUNT(*) FILTER (WHERE f.mentions_oversight)::numeric / COUNT(*)
        - COALESCE(prev.oversight_share, 0)
    END AS oversight_share_delta_3m,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM {{ ref('int_market_job_text_flags') }} AS f
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = f.first_seen_month
LEFT JOIN (
    SELECT
        first_seen_month AS period_start,
        category,
        COUNT(*) FILTER (WHERE mentions_oversight)::numeric / COUNT(*) AS oversight_share
    FROM {{ ref('int_market_job_text_flags') }}
    GROUP BY 1, 2
) AS prev
    ON prev.category = f.category
    AND prev.period_start = (f.first_seen_month - INTERVAL '3 months')::date
GROUP BY
    f.first_seen_month, f.category,
    st.panel_complete, st.panel_sources, st.matrix_version, st.trend_break,
    st.change_3_ok, prev.oversight_share
