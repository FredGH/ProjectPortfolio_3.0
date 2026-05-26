SELECT
    hub_trader_key,
    trader_bk AS trader_id,
    t.name,
    t.desk,
    t.legal_entity,
    t.primary_asset_class,
    t.seniority,
    t._loaded_at AS load_timestamp
FROM {{ ref('hub_trader') }} AS h
JOIN {{ source('stg_raw', 'traders') }} AS t ON t.trader_id = h.trader_bk
