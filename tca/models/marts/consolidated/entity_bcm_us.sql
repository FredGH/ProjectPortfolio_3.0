-- BCM_US legal entity view: orders booked via PrivateBank Capital Markets LLC.
SELECT
    f.*,
    'BCM_US' AS legal_entity
FROM {{ ref('fact_order_execution') }} AS f
JOIN {{ source('stg_raw', 'traders') }} AS t USING (trader_id)
WHERE t.legal_entity = 'BCM_US'
