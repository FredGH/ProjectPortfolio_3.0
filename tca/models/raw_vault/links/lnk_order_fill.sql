{{ config(unique_key='lnk_order_fill_key') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['order_id', 'fill_id']) }} AS lnk_order_fill_key,
        {{ dbt_utils.generate_surrogate_key(['order_id']) }} AS hub_order_key,
        {{ dbt_utils.generate_surrogate_key(['fill_id']) }} AS hub_fill_key,
        order_id,
        fill_id,
        _loaded_at AS load_timestamp,
        'oms' AS record_source
    FROM {{ ref('stg_fills') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    lnk_order_fill_key,
    hub_order_key,
    hub_fill_key,
    order_id,
    fill_id,
    load_timestamp,
    record_source
FROM source
