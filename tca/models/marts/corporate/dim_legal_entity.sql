SELECT
    hub_legal_entity_key,
    entity_bk AS entity_id,
    le.name,
    le.jurisdiction,
    le.mifid_lei,
    le._loaded_at AS load_timestamp
FROM {{ ref('hub_legal_entity') }} AS h
JOIN {{ source('stg_raw', 'legal_entities') }} AS le ON le.entity_id = h.entity_bk
