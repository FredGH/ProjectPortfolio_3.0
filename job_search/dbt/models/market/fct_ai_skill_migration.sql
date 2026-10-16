-- fct_ai_skill_migration: where AI-mentioning postings sit (PLAN.md Step 21b).
-- Grain: (period_start, category). When AI-adjacent skills stop being
-- concentrated in the AI categories and show up across data engineering,
-- analytics and platform roles, the transformation has reached those
-- categories. is_ai_category marks the categories AI work is expected to
-- concentrate in; non_ai_share_of_ai_postings (same value on each row of the
-- month) is the headline migration figure.

WITH ai AS (

    SELECT
        first_seen_month AS period_start,
        category,
        COUNT(*) AS ai_postings
    FROM {{ ref('int_market_job_text_flags') }}
    WHERE CARDINALITY(ai_terms) > 0
    GROUP BY 1, 2

),

totals AS (

    SELECT
        period_start,
        SUM(ai_postings) AS all_ai_postings,
        SUM(ai_postings) FILTER (
            WHERE category NOT IN ('ai_ml_engineer', 'data_scientist')
        ) AS non_ai_category_postings
    FROM ai
    GROUP BY period_start

)

SELECT
    a.period_start,
    a.category,
    (a.category IN ('ai_ml_engineer', 'data_scientist')) AS is_ai_category,
    a.ai_postings,
    t.all_ai_postings,
    a.ai_postings::numeric / t.all_ai_postings AS share_of_ai_postings,
    COALESCE(t.non_ai_category_postings, 0)::numeric / t.all_ai_postings AS non_ai_share_of_ai_postings,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM ai AS a
INNER JOIN totals AS t USING (period_start)
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = a.period_start
