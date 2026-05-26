{{ config(unique_key='lnk_fill_venue_key') }}

WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['fill_id', 'venue_id']) }} AS lnk_fill_venue_key,
        {{ dbt_utils.generate_surrogate_key(['fill_id']) }}   AS hub_fill_key,
        {{ dbt_utils.generate_surrogate_key(['venue_id']) }}  AS hub_venue_key,
        fill_id,
        venue_id,
        _loaded_at AS load_timestamp,
        'oms' AS record_source
    FROM {{ ref('stg_fills') }}
    WHERE venue_id IS NOT NULL
    {% if is_incremental() %}
    AND _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    lnk_fill_venue_key,
    hub_fill_key,
    hub_venue_key,
    fill_id,
    venue_id,
    load_timestamp,
    record_source
FROM source
