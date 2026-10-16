-- The trend marts must account for exactly the jobs in the panel-filtered base:
-- no job dropped, none counted twice, none from outside the panel. Passes on
-- zero rows.

WITH base AS (SELECT COUNT(*) AS n FROM {{ ref('int_market_job') }})

SELECT 'fct_market_volume' AS mart, SUM(v.postings) AS total, base.n
FROM {{ ref('fct_market_volume') }} AS v, base
GROUP BY base.n
HAVING SUM(v.postings) <> base.n

UNION ALL

SELECT 'fct_market_composition', SUM(c.n_postings), base.n
FROM {{ ref('fct_market_composition') }} AS c, base
GROUP BY base.n
HAVING SUM(c.n_postings) <> base.n

UNION ALL

SELECT 'fct_posting_lifecycle', COUNT(*), base.n
FROM {{ ref('fct_posting_lifecycle') }}, base
GROUP BY base.n
HAVING COUNT(*) <> base.n
