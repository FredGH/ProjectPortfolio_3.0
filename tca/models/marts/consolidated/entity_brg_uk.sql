-- PB_UK legal entity view: orders booked via PrivateBank Capital Markets LLC UK.
SELECT
    f.*,
    'PB_UK' AS legal_entity
FROM {{ ref('fact_order_execution') }} AS f
JOIN {{ source('stg_raw', 'traders') }} AS t USING (trader_id)
WHERE t.legal_entity = 'PB_UK'
