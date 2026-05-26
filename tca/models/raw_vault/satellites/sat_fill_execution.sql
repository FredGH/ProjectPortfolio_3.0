{{ config(unique_key='sat_fill_execution_id') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['fill_id']) }} AS hub_fill_key,
        order_id,
        counterparty_id,
        instrument_id,
        instrument_class,
        venue_id,
        fill_time,
        trade_date,
        fill_price,
        fill_quantity,
        side,
        market_impact_bps,
        commission_bps,
        currency,
        _loaded_at AS load_timestamp,
        {{ dbt_utils.generate_surrogate_key([
            'fill_id', 'fill_price', 'fill_quantity', 'venue_id'
        ]) }} AS hash_diff
    FROM {{ ref('stg_fills') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY hub_fill_key ORDER BY load_timestamp DESC) AS rn
    FROM source
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['hub_fill_key', 'load_timestamp']) }} AS sat_fill_execution_id,
    hub_fill_key,
    order_id,
    counterparty_id,
    instrument_id,
    instrument_class,
    venue_id,
    fill_time,
    trade_date,
    fill_price,
    fill_quantity,
    side,
    market_impact_bps,
    commission_bps,
    currency,
    load_timestamp,
    hash_diff
FROM ranked
WHERE rn = 1
