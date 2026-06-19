-- assert_rfm_score_distribution.sql
-- Each NTILE(5) dimension bucket should contain 15–25% of customers.
-- Checks recency_score, frequency_score, and monetary_score independently.
-- Returns rows for any bucket outside that band — zero rows = test passes.

WITH score_buckets AS (
    SELECT recency_score   AS score, 'recency'   AS dimension FROM {{ ref('slv_customer_rfm') }}
    UNION ALL
    SELECT frequency_score,          'frequency'              FROM {{ ref('slv_customer_rfm') }}
    UNION ALL
    SELECT monetary_score,           'monetary'               FROM {{ ref('slv_customer_rfm') }}
),

bucket_pct AS (
    SELECT
        dimension,
        score,
        COUNT(*) AS n,
        COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY dimension) AS pct
    FROM score_buckets
    GROUP BY dimension, score
)

SELECT
    dimension,
    score,
    n,
    ROUND(pct, 2) AS pct
FROM bucket_pct
WHERE pct < 15 OR pct > 25
