-- Staging model for current_weather bronze table.
-- Casts types, renames ambiguous fields, and filters out internal metadata columns.

WITH source AS (
    SELECT * FROM {{ source('bronze', 'current_weather') }}
),

renamed AS (
    SELECT
        -- Location
        lat,
        lon,
        name                                        AS city_name,
        sys_country                                 AS country_code,
        CAST(timezone AS INTEGER)                   AS utc_offset_seconds,

        -- Observation time
        TO_TIMESTAMP(dt)                            AS observed_at,
        _fetched_at                                 AS fetched_at,

        -- Temperature (metric — °C)
        CAST(main_temp       AS DOUBLE)             AS temp_c,
        CAST(main_feels_like AS DOUBLE)             AS feels_like_c,
        CAST(main_temp_min   AS DOUBLE)             AS temp_min_c,
        CAST(main_temp_max   AS DOUBLE)             AS temp_max_c,

        -- Atmosphere
        CAST(main_pressure   AS INTEGER)            AS pressure_hpa,
        CAST(main_humidity   AS INTEGER)            AS humidity_pct,
        CAST(visibility      AS INTEGER)            AS visibility_m,

        -- Wind
        CAST(wind_speed      AS DOUBLE)             AS wind_speed_ms,
        CAST(wind_deg        AS INTEGER)            AS wind_direction_deg,

        -- Cloud cover
        CAST(clouds_all      AS INTEGER)            AS cloud_cover_pct,

        -- Sunrise / sunset
        TO_TIMESTAMP(sys_sunrise)                   AS sunrise_at,
        TO_TIMESTAMP(sys_sunset)                    AS sunset_at

    FROM source
)

SELECT
    lat,
    lon,
    city_name,
    country_code,
    utc_offset_seconds,
    observed_at,
    fetched_at,
    temp_c,
    feels_like_c,
    temp_min_c,
    temp_max_c,
    pressure_hpa,
    humidity_pct,
    visibility_m,
    wind_speed_ms,
    wind_direction_deg,
    cloud_cover_pct,
    sunrise_at,
    sunset_at
FROM renamed
