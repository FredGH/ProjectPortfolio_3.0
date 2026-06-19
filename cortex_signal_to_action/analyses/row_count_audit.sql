-- row_count_audit.sql
-- Sanity check: row counts across all bronze tables vs Olist documentation benchmarks.
-- Run via: dbt compile --select analyses/row_count_audit && snowsql -f target/compiled/.../row_count_audit.sql

SELECT 'brz_olist_orders'        AS model, COUNT(*) AS row_count, 99441  AS expected_approx FROM {{ ref('brz_olist_orders') }}
UNION ALL
SELECT 'brz_olist_order_items',           COUNT(*), 112650 FROM {{ ref('brz_olist_order_items') }}
UNION ALL
SELECT 'brz_olist_order_reviews',         COUNT(*), 100000 FROM {{ ref('brz_olist_order_reviews') }}
UNION ALL
SELECT 'brz_olist_customers',             COUNT(*), 99441  FROM {{ ref('brz_olist_customers') }}
UNION ALL
SELECT 'brz_olist_order_payments',        COUNT(*), 103886 FROM {{ ref('brz_olist_order_payments') }}
UNION ALL
SELECT 'brz_olist_products',              COUNT(*), 32951  FROM {{ ref('brz_olist_products') }}
UNION ALL
SELECT 'brz_olist_geolocation',           COUNT(*), 1000163 FROM {{ ref('brz_olist_geolocation') }}
UNION ALL
SELECT 'brz_olist_sellers',               COUNT(*), 3095   FROM {{ ref('brz_olist_sellers') }}
UNION ALL
SELECT 'brz_olist_mql',                   COUNT(*), 8000   FROM {{ ref('brz_olist_mql') }}
UNION ALL
SELECT 'brz_mmm_weekly_spend',            COUNT(*), 138    FROM {{ ref('brz_mmm_weekly_spend') }}
ORDER BY model
