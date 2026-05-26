{{ config(unique_key='lnk_order_algo_key') }}

-- Only creates a link when an algorithm was used (algo_id IS NOT NULL).
WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['order_id', 'algo_id']) }} AS lnk_order_algo_key,
        {{ dbt_utils.generate_surrogate_key(['order_id']) }}  AS hub_order_key,
        {{ dbt_utils.generate_surrogate_key(['algo_id']) }}   AS hub_algo_key,
        order_id,
        algo_id,
        _loaded_at AS load_timestamp,
        'oms' AS record_source
    FROM {{ ref('stg_orders') }}
    WHERE algo_id IS NOT NULL
    {% if is_incremental() %}
    AND _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    lnk_order_algo_key,
    hub_order_key,
    hub_algo_key,
    order_id,
    algo_id,
    load_timestamp,
    record_source
FROM source
