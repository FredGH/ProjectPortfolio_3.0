-- Daily price benchmarks per instrument: VWAP, TWAP, open, close, edsp.
WITH daily_bars AS (
    SELECT
        instrument_id,
        bar_date,
        FIRST_VALUE(open) OVER w  AS session_open,
        MAX(high) OVER w          AS session_high,
        MIN(low)  OVER w          AS session_low,
        LAST_VALUE(close) OVER w  AS session_close,
        SUM(volume) OVER w        AS session_volume,
        SUM(close * volume) OVER w / NULLIF(SUM(volume) OVER w, 0) AS session_vwap,
        AVG(close) OVER w         AS session_twap,
        STDDEV(close) OVER w / NULLIF(AVG(close) OVER w, 0) * SQRT(1020) AS daily_vol_annualized
    FROM {{ ref('sat_price_tick') }}
    WHERE bar_hour_utc >= 7
      AND (bar_hour_utc < 15 OR (bar_hour_utc = 15 AND bar_minute_utc <= 30))
    WINDOW w AS (PARTITION BY instrument_id, bar_date)
),

deduplicated AS (
    SELECT DISTINCT ON (instrument_id, bar_date) *
    FROM daily_bars
    ORDER BY instrument_id, bar_date
),

edsp AS (
    SELECT instrument_id, edsp AS edsp_price, trade_date
    FROM {{ source('stg_raw', 'edsp_settlements') }}
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['d.instrument_id', 'd.bar_date::TEXT']) }} AS fact_id,
    d.instrument_id,
    d.bar_date AS price_date,
    d.session_open,
    d.session_high,
    d.session_low,
    d.session_close,
    d.session_vwap,
    d.session_twap,
    d.session_volume,
    d.daily_vol_annualized,
    e.edsp_price
FROM deduplicated AS d
LEFT JOIN edsp AS e
    ON e.instrument_id = d.instrument_id AND e.trade_date::DATE = d.bar_date
