{{ config(materialized='table') }}

WITH latest_sat AS (
    SELECT DISTINCT ON (hub_venue_key)
        hub_venue_key,
        name,
        country,
        type,
        load_timestamp
    FROM {{ ref('sat_venue_detail') }}
    ORDER BY hub_venue_key, load_timestamp DESC
)

SELECT
    h.hub_venue_key,
    h.venue_bk AS venue_id,
    s.name,
    s.country,
    s.type,
    s.load_timestamp
FROM {{ ref('hub_venue') }} AS h
JOIN latest_sat AS s USING (hub_venue_key)
