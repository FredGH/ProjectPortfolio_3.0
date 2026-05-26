WITH source AS (
    SELECT * FROM {{ source('stg_raw', 'tick_bars') }}
)

SELECT
    bar_id,
    instrument_id,
    ts,
    CAST(ts AS DATE) AS bar_date,
    EXTRACT(HOUR FROM ts)   AS bar_hour_utc,
    EXTRACT(MINUTE FROM ts) AS bar_minute_utc,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    trade_date,
    _loaded_at
FROM source
WHERE bar_id IS NOT NULL
    AND instrument_id IS NOT NULL
    AND close > 0
    AND volume >= 0
