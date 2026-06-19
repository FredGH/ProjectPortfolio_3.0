-- brz_olist_sellers: typed seller master. Grain: seller_id (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    seller_id::varchar(36)              AS seller_id,
    seller_zip_code_prefix::varchar(10) AS seller_zip_code_prefix,
    seller_city::varchar(100)           AS seller_city,
    seller_state::varchar(2)            AS seller_state,
    CURRENT_TIMESTAMP()::timestamp_ntz  AS _loaded_at
FROM {{ source('olist', 'olist_sellers') }}
