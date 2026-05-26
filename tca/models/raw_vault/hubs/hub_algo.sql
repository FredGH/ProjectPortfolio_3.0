{{ config(unique_key='hub_algo_key') }}

WITH source AS (
    SELECT
        algo_id AS algo_bk,
        _loaded_at AS load_timestamp,
        'ref_data' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['algo_id']) }} AS hub_algo_key
    FROM {{ source('stg_raw', 'algos') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_algo_key,
    algo_bk,
    load_timestamp,
    record_source
FROM source
