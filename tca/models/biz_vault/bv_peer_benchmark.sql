{{ config(unique_key='hub_order_key') }}

-- Benchmarks: VWAP, TWAP, arrival, close, EDSP (futures).
-- VWAP / TWAP computed from intraday tick_bars during session window.
WITH orders AS (
    SELECT
        hub_order_key,
        instrument_id,
        instrument_class,
        side,
        order_time,
        trade_date,
        arrival_price,
        avg_fill_price,
        counterparty_id,
        load_timestamp
    FROM {{ ref('bv_order_enriched') }}
    WHERE avg_fill_price IS NOT NULL
    {% if is_incremental() %}
    AND load_timestamp > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

-- Session VWAP: volume-weighted average over full session
session_vwap AS (
    SELECT
        instrument_id,
        bar_date,
        SUM(close * volume) / NULLIF(SUM(volume), 0) AS vwap_price,
        AVG(close)                                    AS twap_price,
        SUM(volume)                                   AS total_volume,
        STDDEV(close) / NULLIF(AVG(close), 0) * 100  AS intraday_vol_pct
    FROM {{ ref('sat_price_tick') }}
    WHERE bar_hour_utc >= 7
      AND (bar_hour_utc < 15 OR (bar_hour_utc = 15 AND bar_minute_utc <= 30))
    GROUP BY instrument_id, bar_date
),

-- Session close: last bar price
session_close AS (
    SELECT DISTINCT ON (instrument_id, bar_date)
        instrument_id,
        bar_date,
        close AS close_price
    FROM {{ ref('sat_price_tick') }}
    ORDER BY instrument_id, bar_date, ts DESC
),

-- EDSP for futures
edsp AS (
    SELECT instrument_id, edsp
    FROM {{ source('stg_raw', 'edsp_settlements') }}
)

SELECT
    o.hub_order_key,
    o.instrument_id,
    o.instrument_class,
    o.side,
    o.trade_date,
    o.arrival_price,
    o.avg_fill_price,
    o.counterparty_id,
    sv.vwap_price,
    sv.twap_price,
    sc.close_price,
    e.edsp AS edsp_price,
    -- Slippage vs each benchmark (BUY: positive = paid more than benchmark)
    CASE WHEN o.side = 'BUY'
         THEN ROUND(((o.avg_fill_price - o.arrival_price) / NULLIF(o.arrival_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((o.arrival_price  - o.avg_fill_price) / NULLIF(o.arrival_price, 0) * 10000)::numeric, 4)
    END AS arrival_slippage_bps,
    CASE WHEN o.side = 'BUY'
         THEN ROUND(((o.avg_fill_price - sv.vwap_price) / NULLIF(sv.vwap_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((sv.vwap_price - o.avg_fill_price) / NULLIF(sv.vwap_price, 0) * 10000)::numeric, 4)
    END AS vwap_slippage_bps,
    CASE WHEN o.side = 'BUY'
         THEN ROUND(((o.avg_fill_price - sv.twap_price) / NULLIF(sv.twap_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((sv.twap_price - o.avg_fill_price) / NULLIF(sv.twap_price, 0) * 10000)::numeric, 4)
    END AS twap_slippage_bps,
    CASE WHEN o.side = 'BUY'
         THEN ROUND(((o.avg_fill_price - sc.close_price) / NULLIF(sc.close_price, 0) * 10000)::numeric, 4)
         ELSE ROUND(((sc.close_price - o.avg_fill_price) / NULLIF(sc.close_price, 0) * 10000)::numeric, 4)
    END AS close_slippage_bps,
    sv.total_volume    AS session_volume,
    sv.intraday_vol_pct,
    o.load_timestamp
FROM orders AS o
LEFT JOIN session_vwap AS sv
    ON sv.instrument_id = o.instrument_id AND sv.bar_date = o.trade_date
LEFT JOIN session_close AS sc
    ON sc.instrument_id = o.instrument_id AND sc.bar_date = o.trade_date
LEFT JOIN edsp AS e ON e.instrument_id = o.instrument_id
