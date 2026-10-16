-- PLAN.md Step 21a's dbt test: a change metric may exist only when the period
-- AND the period it is compared with share a complete panel, the same
-- panel_sources and the same matrix version, with no version change inside
-- either. This recomputes that from the stamps of BOTH periods independently of
-- the model's own expression. Passes on zero rows.

WITH stamps AS (SELECT * FROM {{ ref('int_market_period_stamp') }}),

violations AS (

    -- weekly volume: WoW compares with 7 days earlier, MoM with 28
    SELECT 'volume wow' AS metric, v.period_start::date AS period, 7 AS days_back, 'week' AS grain
    FROM {{ ref('fct_market_volume') }} AS v
    WHERE v.wow_change IS NOT NULL OR v.wow_pct IS NOT NULL
    UNION ALL
    SELECT 'volume mom', v.period_start::date, 28, 'week'
    FROM {{ ref('fct_market_volume') }} AS v
    WHERE v.mom_change IS NOT NULL OR v.mom_pct IS NOT NULL

)

SELECT x.metric, x.period
FROM violations AS x
INNER JOIN stamps AS cur ON cur.grain = x.grain AND cur.period_start = x.period
LEFT JOIN stamps AS prev ON prev.grain = x.grain AND prev.period_start = x.period - x.days_back
WHERE prev.period_start IS NULL
    OR NOT cur.panel_complete OR NOT prev.panel_complete
    OR cur.panel_sources <> prev.panel_sources
    OR cur.matrix_version <> prev.matrix_version
    OR cur.matrix_mixed OR prev.matrix_mixed

UNION ALL

SELECT 'skill rank_delta_90d', d.period_start::text::date
FROM {{ ref('fct_skill_demand') }} AS d
INNER JOIN stamps AS cur ON cur.grain = 'month' AND cur.period_start = d.period_start
LEFT JOIN stamps AS prev
    ON prev.grain = 'month' AND prev.period_start = (d.period_start - INTERVAL '3 months')::date
WHERE d.rank_delta_90d IS NOT NULL
    AND (prev.period_start IS NULL
        OR NOT cur.panel_complete OR NOT prev.panel_complete
        OR cur.panel_sources <> prev.panel_sources
        OR cur.matrix_version <> prev.matrix_version
        OR cur.matrix_mixed OR prev.matrix_mixed)

UNION ALL

SELECT 'employer is_new_entrant', e.period_start::text::date
FROM {{ ref('fct_employer_activity') }} AS e
INNER JOIN stamps AS cur ON cur.grain = 'month' AND cur.period_start = e.period_start
LEFT JOIN stamps AS prev
    ON prev.grain = 'month' AND prev.period_start = (e.period_start - INTERVAL '1 month')::date
WHERE e.is_new_entrant IS NOT NULL
    AND (prev.period_start IS NULL
        OR NOT cur.panel_complete OR NOT prev.panel_complete
        OR cur.panel_sources <> prev.panel_sources
        OR cur.matrix_version <> prev.matrix_version
        OR cur.matrix_mixed OR prev.matrix_mixed)

UNION ALL

-- The stamps themselves must be honest about the seed.
SELECT 'stamp panel_complete', st.period_start
FROM stamps AS st
WHERE st.panel_complete
    AND EXISTS (SELECT 1 FROM {{ ref('source_panel') }} AS s WHERE s.active_from > st.period_start)
