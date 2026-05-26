{{ config(unique_key='hub_legal_entity_key') }}

WITH source AS (
    SELECT
        entity_id AS entity_bk,
        _loaded_at AS load_timestamp,
        'ref_data' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['entity_id']) }} AS hub_legal_entity_key
    FROM {{ source('stg_raw', 'legal_entities') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_legal_entity_key,
    entity_bk,
    load_timestamp,
    record_source
FROM source
