{{ config(unique_key='hub_order_key') }}

WITH source AS (
    SELECT
        order_id AS order_bk,
        _loaded_at AS load_timestamp,
        'oms' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['order_id']) }} AS hub_order_key
    FROM {{ ref('stg_orders') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_order_key,
    order_bk,
    load_timestamp,
    record_source
FROM source
