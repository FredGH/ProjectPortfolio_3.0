{{ config(unique_key='sat_venue_detail_id') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['venue_id']) }} AS hub_venue_key,
        name,
        country,
        type,
        _loaded_at AS load_timestamp,
        {{ dbt_utils.generate_surrogate_key(['venue_id', 'name', 'country', 'type']) }} AS hash_diff
    FROM {{ source('stg_raw', 'venues') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY hub_venue_key ORDER BY load_timestamp DESC) AS rn
    FROM source
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['hub_venue_key', 'load_timestamp']) }} AS sat_venue_detail_id,
    hub_venue_key,
    name,
    country,
    type,
    load_timestamp,
    hash_diff
FROM ranked
WHERE rn = 1
