-- fct_role_drift: how far a category's skill demand has moved (PLAN.md Step
-- 21b). Jensen-Shannon divergence between the skill distribution of a month and
-- the same category 6 / 12 months earlier. Grain: (period_start, category,
-- lookback_months). 0 means identical distributions; the natural-log maximum is
-- ln 2 (about 0.693). NULL unless the compared months share a complete panel
-- and matrix version: a source or keyword change is not drift.

WITH counts AS (

    SELECT
        s.first_seen_month AS period_start,
        s.category,
        s.skill_id,
        COUNT(DISTINCT s.job_group_id) AS freq
    FROM {{ ref('int_market_job_skill') }} AS s
    GROUP BY 1, 2, 3

),

dist AS (

    SELECT
        period_start,
        category,
        skill_id,
        freq::numeric / SUM(freq) OVER (PARTITION BY period_start, category) AS p
    FROM counts

),

months AS (

    SELECT DISTINCT period_start, category FROM counts

),

pairs AS (

    -- a (month, category) pair with a month lookback_months earlier to compare to
    SELECT
        m.period_start,
        m.category,
        lb.lookback_months,
        (m.period_start - MAKE_INTERVAL(months => lb.lookback_months))::date AS then_start
    FROM months AS m
    CROSS JOIN (VALUES (6), (12)) AS lb (lookback_months)
    WHERE EXISTS (
        SELECT 1 FROM months AS o
        WHERE o.category = m.category
            AND o.period_start = (m.period_start - MAKE_INTERVAL(months => lb.lookback_months))::date
    )

),

skills_union AS (

    SELECT pr.period_start, pr.category, pr.lookback_months, pr.then_start, d.skill_id
    FROM pairs AS pr
    INNER JOIN dist AS d
        ON d.period_start = pr.period_start AND d.category = pr.category
    UNION
    SELECT pr.period_start, pr.category, pr.lookback_months, pr.then_start, d.skill_id
    FROM pairs AS pr
    INNER JOIN dist AS d
        ON d.period_start = pr.then_start AND d.category = pr.category

),

aligned AS (

    SELECT
        u.period_start,
        u.category,
        u.lookback_months,
        COALESCE(cur.p, 0) AS p,
        COALESCE(old.p, 0) AS q
    FROM skills_union AS u
    LEFT JOIN dist AS cur
        ON cur.period_start = u.period_start AND cur.category = u.category
        AND cur.skill_id = u.skill_id
    LEFT JOIN dist AS old
        ON old.period_start = u.then_start AND old.category = u.category
        AND old.skill_id = u.skill_id

),

divergence AS (

    SELECT
        period_start,
        category,
        lookback_months,
        COUNT(*) FILTER (WHERE p > 0) AS skills_now,
        COUNT(*) FILTER (WHERE q > 0) AS skills_then,
        {{ js_divergence('p', 'q') }} AS js
    FROM aligned
    GROUP BY period_start, category, lookback_months

)

SELECT
    d.period_start,
    d.category,
    d.lookback_months,
    d.skills_now,
    d.skills_then,
    CASE
        WHEN d.lookback_months = 6 AND st.change_6_ok THEN d.js
        WHEN d.lookback_months = 12 AND st.change_12_ok THEN d.js
    END AS js_divergence,
    st.panel_complete,
    st.panel_sources,
    st.matrix_version,
    st.trend_break
FROM divergence AS d
INNER JOIN {{ ref('int_market_period_stamp') }} AS st
    ON st.grain = 'month' AND st.period_start = d.period_start
