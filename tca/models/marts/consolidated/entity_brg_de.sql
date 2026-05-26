-- PB_DE legal entity view: orders booked via PrivateBank Bank Hamburg.
SELECT
    f.*,
    'PB_DE' AS legal_entity
FROM {{ ref('fact_order_execution') }} AS f
JOIN {{ source('stg_raw', 'traders') }} AS t USING (trader_id)
WHERE t.legal_entity = 'PB_DE'
