{{ config(unique_key='sat_order_details_id') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['order_id']) }} AS hub_order_key,
        instrument_id,
        instrument_class,
        side,
        order_type,
        quantity,
        arrival_price,
        limit_price,
        order_time,
        trade_date,
        counterparty_id,
        trader_id,
        algo_id,
        venue_id,
        currency,
        status,
        client_order_id,
        _loaded_at AS load_timestamp,
        {{ dbt_utils.generate_surrogate_key([
            'order_id', 'instrument_id', 'side', 'order_type',
            'quantity', 'arrival_price', 'status'
        ]) }} AS hash_diff
    FROM {{ ref('stg_orders') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY hub_order_key ORDER BY load_timestamp DESC) AS rn
    FROM source
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['hub_order_key', 'load_timestamp']) }} AS sat_order_details_id,
    hub_order_key,
    instrument_id,
    instrument_class,
    side,
    order_type,
    quantity,
    arrival_price,
    limit_price,
    order_time,
    trade_date,
    counterparty_id,
    trader_id,
    algo_id,
    venue_id,
    currency,
    status,
    client_order_id,
    load_timestamp,
    hash_diff
FROM ranked
WHERE rn = 1
