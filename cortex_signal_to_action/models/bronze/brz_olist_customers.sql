-- brz_olist_customers: typed customer master. Grain: customer_id (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    customer_id::varchar(36)              AS customer_id,
    customer_unique_id::varchar(36)       AS customer_unique_id,
    customer_zip_code_prefix::varchar(10) AS customer_zip_code_prefix,
    customer_city::varchar(100)           AS customer_city,
    customer_state::varchar(2)            AS customer_state,
    CURRENT_TIMESTAMP()::timestamp_ntz    AS _loaded_at
FROM {{ source('olist', 'olist_customers') }}
