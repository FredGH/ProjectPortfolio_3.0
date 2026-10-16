-- PLAN.md Step 21a "Done when": every salary figure carries its n and
-- disclosure rate, the rate is consistent with them, and statistics are
-- published only with at least 5 disclosed figures. Passes on zero rows.

SELECT *
FROM {{ ref('fct_market_salary') }}
WHERE n_postings IS NULL
    OR n_disclosed IS NULL
    OR disclosure_rate IS NULL
    OR n_disclosed > n_postings
    OR ABS(disclosure_rate - n_disclosed::numeric / n_postings) > 0.0001
    OR (n_disclosed < 5 AND (median IS NOT NULL OR p25 IS NOT NULL OR p75 IS NOT NULL
        OR min_advertised IS NOT NULL OR max_advertised IS NOT NULL))
    OR (n_disclosed >= 5 AND (median IS NULL OR p25 > median OR median > p75))
