{{ config(unique_key='hub_venue_key') }}

WITH source AS (
    SELECT
        venue_id AS venue_bk,
        _loaded_at AS load_timestamp,
        'ref_data' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['venue_id']) }} AS hub_venue_key
    FROM {{ source('stg_raw', 'venues') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_venue_key,
    venue_bk,
    load_timestamp,
    record_source
FROM source
