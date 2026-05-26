{{ config(materialized='table') }}

WITH latest_sat AS (
    SELECT DISTINCT ON (hub_client_key)
        hub_client_key,
        name,
        type,
        country,
        lei,
        load_timestamp
    FROM {{ ref('sat_client_profile') }}
    ORDER BY hub_client_key, load_timestamp DESC
)

SELECT
    h.hub_client_key,
    h.client_bk AS counterparty_id,
    s.name,
    s.type,
    s.country,
    s.lei,
    s.load_timestamp
FROM {{ ref('hub_client') }} AS h
JOIN latest_sat AS s USING (hub_client_key)
