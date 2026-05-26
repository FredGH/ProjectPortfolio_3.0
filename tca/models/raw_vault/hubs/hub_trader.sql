{{ config(unique_key='hub_trader_key') }}

WITH source AS (
    SELECT
        trader_id AS trader_bk,
        _loaded_at AS load_timestamp,
        'ref_data' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['trader_id']) }} AS hub_trader_key
    FROM {{ source('stg_raw', 'traders') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_trader_key,
    trader_bk,
    load_timestamp,
    record_source
FROM source
