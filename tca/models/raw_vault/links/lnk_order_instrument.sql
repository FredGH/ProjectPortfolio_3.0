{{ config(unique_key='lnk_order_instrument_key') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['order_id', 'instrument_id']) }} AS lnk_order_instrument_key,
        {{ dbt_utils.generate_surrogate_key(['order_id']) }}       AS hub_order_key,
        {{ dbt_utils.generate_surrogate_key(['instrument_id']) }}  AS hub_instrument_key,
        order_id,
        instrument_id,
        instrument_class,
        _loaded_at AS load_timestamp,
        'oms' AS record_source
    FROM {{ ref('stg_orders') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    lnk_order_instrument_key,
    hub_order_key,
    hub_instrument_key,
    order_id,
    instrument_id,
    instrument_class,
    load_timestamp,
    record_source
FROM source
