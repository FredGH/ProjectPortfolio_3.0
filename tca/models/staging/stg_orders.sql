WITH source AS (
    SELECT * FROM {{ source('stg_raw', 'orders') }}
)

SELECT
    order_id,
    instrument_id,
    instrument_class,
    side,
    order_type,
    order_quantity AS quantity,
    arrival_price,
    limit_price,
    order_time,
    CAST(order_time AS DATE) AS trade_date,
    counterparty_id,
    trader_id,
    algo_id,
    venue_id,
    currency,
    status,
    client_order_id,
    _loaded_at
FROM source
WHERE order_id IS NOT NULL
    AND instrument_class IN ('equity', 'equity_future', 'fixed_income', 'fx_derivative')
    AND side IN ('BUY', 'SELL')
