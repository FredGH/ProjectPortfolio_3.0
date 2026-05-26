{{ config(unique_key='hub_fill_key') }}

-- Adverse selection: did prices move against us immediately after our fill?
-- Adverse selection bps > 0 means counterparty had better information.
WITH fills AS (
    SELECT * FROM {{ ref('sat_fill_execution') }}
    {% if is_incremental() %}
    WHERE load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT
    f.hub_fill_key,
    f.order_id,
    f.instrument_id,
    f.instrument_class,
    f.counterparty_id,
    f.side,
    f.fill_price,
    f.fill_quantity,
    f.fill_time,
    pre.pre_fill_mid,
    post5.post_fill_mid_5m,
    post30.post_fill_mid_30m,
    CASE WHEN f.side = 'BUY'
         THEN ROUND(((f.fill_price - COALESCE(pre.pre_fill_mid, f.fill_price)) / NULLIF(f.fill_price, 0) * 10000)::NUMERIC, 4)
         ELSE ROUND(((COALESCE(pre.pre_fill_mid, f.fill_price) - f.fill_price) / NULLIF(f.fill_price, 0) * 10000)::NUMERIC, 4)
    END AS pre_trade_adverse_selection_bps,
    CASE WHEN f.side = 'BUY'
         THEN ROUND(((COALESCE(post5.post_fill_mid_5m, f.fill_price) - f.fill_price) / NULLIF(f.fill_price, 0) * 10000)::NUMERIC, 4)
         ELSE ROUND(((f.fill_price - COALESCE(post5.post_fill_mid_5m, f.fill_price)) / NULLIF(f.fill_price, 0) * 10000)::NUMERIC, 4)
    END AS post_trade_drift_5m_bps,
    CASE WHEN f.side = 'BUY'
         THEN ROUND(((COALESCE(post30.post_fill_mid_30m, f.fill_price) - f.fill_price) / NULLIF(f.fill_price, 0) * 10000)::NUMERIC, 4)
         ELSE ROUND(((f.fill_price - COALESCE(post30.post_fill_mid_30m, f.fill_price)) / NULLIF(f.fill_price, 0) * 10000)::NUMERIC, 4)
    END AS post_trade_drift_30m_bps,
    f.load_timestamp
FROM fills AS f
LEFT JOIN LATERAL (
    SELECT p.close AS pre_fill_mid
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = f.instrument_id
      AND p.ts < f.fill_time
      AND p.ts > f.fill_time - INTERVAL '5 minutes'
    ORDER BY p.ts DESC
    LIMIT 1
) AS pre ON true
LEFT JOIN LATERAL (
    SELECT p.close AS post_fill_mid_5m
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = f.instrument_id
      AND p.ts BETWEEN f.fill_time + INTERVAL '4 minutes'
                   AND f.fill_time + INTERVAL '6 minutes'
    ORDER BY p.ts
    LIMIT 1
) AS post5 ON true
LEFT JOIN LATERAL (
    SELECT p.close AS post_fill_mid_30m
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = f.instrument_id
      AND p.ts BETWEEN f.fill_time + INTERVAL '28 minutes'
                   AND f.fill_time + INTERVAL '32 minutes'
    ORDER BY p.ts
    LIMIT 1
) AS post30 ON true
