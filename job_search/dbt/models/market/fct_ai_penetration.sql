-- fct_ai_penetration: share of postings per category and month that mention an
-- AI term (agents, RAG, evals, MCP, fine-tuning, vector stores, prompt
-- engineering, LLM, generative AI), plus an 'any' row (PLAN.md Step 21b).
-- Grain: (period_start, category, term). Targeted corpus. n_postings is the
-- denominator; share_delta_3m is NULL unless the month 3 back is comparable on
-- the same complete panel and matrix version.

WITH postings AS (

    SELECT first_seen_month AS period_start, category, COUNT(*) AS n_postings
    FROM {{ ref('int_market_job_text_flags') }}
    GROUP BY 1, 2

),

mentions AS (

    SELECT first_seen_month AS period_start, category, term, COUNT(*) AS n_with
    FROM {{ ref('int_market_job_text_flags') }}, UNNEST(ai_terms) AS term
    GROUP BY 1, 2, 3

    UNION ALL

    SELECT first_seen_month, category, 'any', COUNT(*)
    FROM {{ ref('int_market_job_text_flags') }}
    WHERE CARDINALITY(ai_terms) > 0
    GROUP BY 1, 2

),

shares AS (

    SELECT
        m.period_start,
        m.category,
        m.term,
        m.n_with,
        p.n_postings,
        m.n_with::numeric / p.n_postings AS share
    FROM mentions AS m
    INNER JOIN postings AS p USING (period_start, category)

)

SELECT
    s.period_start,
    s.category,
    s.term,
    s.n_with,
    s.n_postings,
    s.share,
    CASE WHEN st.change_3_ok THEN s.share - COALESCE(prev.share, 0) END AS share_delta_3m,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM shares AS s
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = s.period_start
LEFT JOIN shares AS prev
    ON prev.category = s.category AND prev.term = s.term
    AND prev.period_start = (s.period_start - INTERVAL '3 months')::date
