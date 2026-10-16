-- Step 21b macro marts reuse the Step 21a stamps: a change/drift metric may be
-- present only when the stamps allow it (complete panel, same matrix version,
-- no version change inside either period). Passes on zero rows.

SELECT 'fct_role_drift' AS mart, period_start::text AS period
FROM {{ ref('fct_role_drift') }}
WHERE js_divergence IS NOT NULL AND (NOT panel_complete OR trend_break)

UNION ALL

SELECT 'fct_ai_penetration', period_start::text
FROM {{ ref('fct_ai_penetration') }}
WHERE share_delta_3m IS NOT NULL AND (NOT panel_complete OR trend_break)

UNION ALL

SELECT 'fct_displacement_language', period_start::text
FROM {{ ref('fct_displacement_language') }}
WHERE oversight_share_delta_3m IS NOT NULL AND (NOT panel_complete OR trend_break)

UNION ALL

SELECT 'fct_role_drift out of range', period_start::text
FROM {{ ref('fct_role_drift') }}
WHERE js_divergence < 0 OR js_divergence > LN(2) + 1e-9
