-- brz_olist_order_items: typed raw order line items. Grain: (order_id, order_item_id).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    order_id::varchar(36)               AS order_id,
    order_item_id::integer              AS order_item_id,
    product_id::varchar(36)             AS product_id,
    seller_id::varchar(36)              AS seller_id,
    shipping_limit_date::timestamp_ntz  AS shipping_limit_date,
    price::float                        AS price,
    freight_value::float                AS freight_value,
    CURRENT_TIMESTAMP()::timestamp_ntz  AS _loaded_at
FROM {{ source('olist', 'olist_order_items') }}
