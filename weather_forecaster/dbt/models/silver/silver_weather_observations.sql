-- Silver: enriched current weather observations.
-- Joins staged current weather with geocoding to resolve a canonical city name
-- and adds derived convenience columns. One row per location per fetch.

WITH weather AS (
    SELECT * FROM {{ ref('stg_current_weather') }}
),

geo AS (
    -- Take the highest-confidence geocoding result per (lat, lon)
    SELECT
        lat,
        lon,
        city_name   AS geo_city_name,
        country_code AS geo_country_code,
        state,
        ROW_NUMBER() OVER (PARTITION BY lat, lon ORDER BY fetched_at DESC) AS rn
    FROM {{ ref('stg_geocoding') }}
),

latest_geo AS (
    SELECT * FROM geo WHERE rn = 1
)

SELECT
    -- Identity — prefer geocoding name, fall back to API response name
    COALESCE(g.geo_city_name,    w.city_name)    AS city_name,
    COALESCE(g.geo_country_code, w.country_code) AS country_code,
    g.state,
    w.lat,
    w.lon,
    w.utc_offset_seconds,

    -- Observation time
    w.observed_at,
    w.fetched_at,

    -- Temperature
    w.temp_c,
    w.feels_like_c,
    w.temp_min_c,
    w.temp_max_c,

    -- Atmosphere
    w.humidity_pct,
    w.pressure_hpa,
    w.visibility_m,

    -- Wind
    w.wind_speed_ms,
    w.wind_direction_deg,

    -- Cloud cover
    w.cloud_cover_pct,

    -- Day / night boundaries
    w.sunrise_at,
    w.sunset_at,

    -- Derived
    w.temp_max_c - w.temp_min_c                  AS daily_temp_range_c,
    CASE
        WHEN w.cloud_cover_pct < 25  THEN 'clear'
        WHEN w.cloud_cover_pct < 50  THEN 'partly cloudy'
        WHEN w.cloud_cover_pct < 85  THEN 'mostly cloudy'
        ELSE 'overcast'
    END                                           AS cloud_description,
    CASE
        WHEN w.wind_speed_ms < 0.5  THEN 'calm'
        WHEN w.wind_speed_ms < 5.5  THEN 'light breeze'
        WHEN w.wind_speed_ms < 10.8 THEN 'moderate breeze'
        WHEN w.wind_speed_ms < 17.2 THEN 'strong breeze'
        ELSE 'gale or above'
    END                                           AS wind_description

FROM weather AS w
LEFT JOIN latest_geo AS g USING (lat, lon)
