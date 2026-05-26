{{ config(unique_key='sat_algo_version_id') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['algo_id']) }} AS hub_algo_key,
        name,
        family,
        provider,
        _loaded_at AS load_timestamp,
        {{ dbt_utils.generate_surrogate_key(['algo_id', 'name', 'family', 'provider']) }} AS hash_diff
    FROM {{ source('stg_raw', 'algos') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY hub_algo_key ORDER BY load_timestamp DESC) AS rn
    FROM source
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['hub_algo_key', 'load_timestamp']) }} AS sat_algo_version_id,
    hub_algo_key,
    name,
    family,
    provider,
    load_timestamp,
    hash_diff
FROM ranked
WHERE rn = 1
