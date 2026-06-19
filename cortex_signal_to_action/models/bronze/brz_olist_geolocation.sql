-- brz_olist_geolocation: typed zip-code geolocation. Grain: (zip_code_prefix, city, state) — zip alone is not unique.

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    geolocation_zip_code_prefix::varchar(10) AS geolocation_zip_code_prefix,
    geolocation_lat::float                   AS geolocation_lat,
    geolocation_lng::float                   AS geolocation_lng,
    geolocation_city::varchar(100)           AS geolocation_city,
    geolocation_state::varchar(2)            AS geolocation_state,
    CURRENT_TIMESTAMP()::timestamp_ntz       AS _loaded_at
FROM {{ source('olist', 'olist_geolocation') }}
