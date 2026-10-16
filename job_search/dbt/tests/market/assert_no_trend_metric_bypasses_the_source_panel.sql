-- PLAN.md Step 21a's dbt test: a change metric may exist only on a period whose
-- panel is complete, whose comparison period shares that panel and whose matrix
-- version did not change. Every trend mart carries the stamps; any change
-- metric present on a row that fails them is a bypass. Passes on zero rows.

SELECT 'fct_market_volume' AS mart, period_start::text AS period
FROM {{ ref('fct_market_volume') }}
WHERE (NOT panel_complete OR trend_break)
    AND (wow_change IS NOT NULL OR wow_pct IS NOT NULL OR mom_change IS NOT NULL OR mom_pct IS NOT NULL)

UNION ALL

SELECT 'fct_skill_demand', period_start::text
FROM {{ ref('fct_skill_demand') }}
WHERE (NOT panel_complete OR trend_break) AND rank_delta_90d IS NOT NULL

UNION ALL

SELECT 'fct_employer_activity', period_start::text
FROM {{ ref('fct_employer_activity') }}
WHERE (NOT panel_complete OR trend_break) AND is_new_entrant IS NOT NULL

UNION ALL

-- The stamps themselves must be honest: a period is complete only if every
-- seed source was active before it started.
SELECT 'int_market_period_stamp', st.period_start::text
FROM {{ ref('int_market_period_stamp') }} AS st
WHERE st.panel_complete
    AND EXISTS (
        SELECT 1 FROM {{ ref('source_panel') }} AS s WHERE s.active_from > st.period_start
    )
