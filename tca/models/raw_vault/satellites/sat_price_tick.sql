{{ config(
    unique_key='sat_price_tick_id',
    post_hook=[
        "DROP INDEX IF EXISTS raw_vault.idx_sat_price_tick_instrument_ts",
        "CREATE INDEX idx_sat_price_tick_instrument_ts ON {{ this }} (instrument_id, ts DESC)",
        "DROP INDEX IF EXISTS raw_vault.idx_sat_price_tick_instrument_bardate_ts",
        "CREATE INDEX idx_sat_price_tick_instrument_bardate_ts ON {{ this }} (instrument_id, bar_date, ts DESC)"
    ]
) }}

-- TimescaleDB converts this table to a hypertable after first materialization.
-- Chunk interval: 7 days (see init.sql hypertable setup).
WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['instrument_id']) }} AS hub_instrument_key,
        bar_id,
        instrument_id,
        ts,
        bar_date,
        bar_hour_utc,
        bar_minute_utc,
        open,
        high,
        low,
        close,
        volume,
        vwap,
        _loaded_at AS load_timestamp
    FROM {{ ref('stg_tick_bars') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['bar_id']) }} AS sat_price_tick_id,
    hub_instrument_key,
    bar_id,
    instrument_id,
    ts,
    bar_date,
    bar_hour_utc,
    bar_minute_utc,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    load_timestamp
FROM source
