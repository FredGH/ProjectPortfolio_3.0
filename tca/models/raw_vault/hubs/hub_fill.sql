{{ config(unique_key='hub_fill_key') }}

WITH source AS (
    SELECT
        fill_id AS fill_bk,
        _loaded_at AS load_timestamp,
        'oms' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['fill_id']) }} AS hub_fill_key
    FROM {{ ref('stg_fills') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_fill_key,
    fill_bk,
    load_timestamp,
    record_source
FROM source
