-- Staging model for geocoding bronze table.

WITH source AS (
    SELECT * FROM {{ source('bronze', 'geocoding') }}
),

renamed AS (
    SELECT
        name            AS city_name,
        country         AS country_code,
        state,
        CAST(lat AS DOUBLE) AS lat,
        CAST(lon AS DOUBLE) AS lon,
        _fetched_at     AS fetched_at
    FROM source
)

SELECT
    city_name,
    country_code,
    state,
    lat,
    lon,
    fetched_at
FROM renamed
