WITH source AS (
    SELECT * FROM {{ source('stg_raw', 'fills') }}
)

SELECT
    fill_id,
    order_id,
    counterparty_id,
    instrument_id,
    instrument_class,
    venue_id,
    fill_time,
    CAST(fill_time AS DATE) AS trade_date,
    fill_price,
    fill_quantity,
    side,
    market_impact_bps,
    commission_bps,
    currency,
    _loaded_at
FROM source
WHERE fill_id IS NOT NULL
    AND order_id IS NOT NULL
    AND fill_price > 0
    AND fill_quantity > 0
