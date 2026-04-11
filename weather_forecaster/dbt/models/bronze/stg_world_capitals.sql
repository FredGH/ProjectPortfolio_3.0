-- Staging model for the world capitals reference table.
-- Loaded once from data/world_capitals.json via the capitals_load Dagster asset.
-- One row per capital city with canonical name, country, and coordinates.

WITH source AS (
    SELECT * FROM {{ source('bronze', 'world_capitals') }}
)

SELECT
    city,
    country,
    country_code,
    CAST(lat AS DOUBLE) AS lat,
    CAST(lon AS DOUBLE) AS lon
FROM source
