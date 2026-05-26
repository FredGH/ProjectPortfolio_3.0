{{ config(unique_key='hub_order_key') }}

-- Alpha decay: price continuation in direction of trade after execution.
-- Alpha > 0 = trade was well-timed (price moved in our favour after execution).
WITH enriched AS (
    SELECT
        hub_order_key,
        instrument_id,
        side,
        order_time,
        trade_date,
        avg_fill_price,
        counterparty_id,
        trader_id,
        load_timestamp
    FROM {{ ref('bv_order_enriched') }}
    WHERE avg_fill_price IS NOT NULL
    {% if is_incremental() %}
    AND load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT
    e.hub_order_key,
    e.instrument_id,
    e.side,
    e.order_time,
    e.trade_date,
    e.counterparty_id,
    e.trader_id,
    e.avg_fill_price,
    p30.price_t30m,
    p1h.price_t1h,
    p4h.price_t4h,
    sc.close_price AS price_close,
    CASE WHEN e.side = 'BUY'
         THEN ROUND(((p30.price_t30m - e.avg_fill_price) / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((e.avg_fill_price - p30.price_t30m) / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
    END AS alpha_t30m_bps,
    CASE WHEN e.side = 'BUY'
         THEN ROUND(((p1h.price_t1h  - e.avg_fill_price) / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((e.avg_fill_price - p1h.price_t1h)  / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
    END AS alpha_t1h_bps,
    CASE WHEN e.side = 'BUY'
         THEN ROUND(((p4h.price_t4h  - e.avg_fill_price) / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((e.avg_fill_price - p4h.price_t4h)  / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
    END AS alpha_t4h_bps,
    CASE WHEN e.side = 'BUY'
         THEN ROUND(((sc.close_price - e.avg_fill_price) / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((e.avg_fill_price - sc.close_price) / NULLIF(e.avg_fill_price, 0) * 10000)::numeric, 4)
    END AS alpha_close_bps,
    CASE
        WHEN sc.close_price IS NULL THEN 'UNKNOWN'
        WHEN ABS(sc.close_price - e.avg_fill_price) / NULLIF(e.avg_fill_price, 0) * 10000 < 80  THEN 'LOW'
        WHEN ABS(sc.close_price - e.avg_fill_price) / NULLIF(e.avg_fill_price, 0) * 10000 < 150 THEN 'MEDIUM'
        ELSE 'HIGH'
    END AS vol_regime,
    e.load_timestamp
FROM enriched AS e
LEFT JOIN LATERAL (
    SELECT p.close AS price_t30m
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = e.instrument_id
      AND p.ts BETWEEN e.order_time + INTERVAL '28 minutes'
                   AND e.order_time + INTERVAL '32 minutes'
    ORDER BY ABS(EXTRACT(EPOCH FROM (p.ts - (e.order_time + INTERVAL '30 minutes'))))
    LIMIT 1
) AS p30 ON true
LEFT JOIN LATERAL (
    SELECT p.close AS price_t1h
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = e.instrument_id
      AND p.ts BETWEEN e.order_time + INTERVAL '58 minutes'
                   AND e.order_time + INTERVAL '62 minutes'
    ORDER BY ABS(EXTRACT(EPOCH FROM (p.ts - (e.order_time + INTERVAL '60 minutes'))))
    LIMIT 1
) AS p1h ON true
LEFT JOIN LATERAL (
    SELECT p.close AS price_t4h
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = e.instrument_id
      AND p.ts BETWEEN e.order_time + INTERVAL '238 minutes'
                   AND e.order_time + INTERVAL '242 minutes'
    ORDER BY ABS(EXTRACT(EPOCH FROM (p.ts - (e.order_time + INTERVAL '240 minutes'))))
    LIMIT 1
) AS p4h ON true
LEFT JOIN LATERAL (
    SELECT p.close AS close_price
    FROM {{ ref('sat_price_tick') }} AS p
    WHERE p.instrument_id = e.instrument_id
      AND p.bar_date = e.trade_date
    ORDER BY p.ts DESC
    LIMIT 1
) AS sc ON true
