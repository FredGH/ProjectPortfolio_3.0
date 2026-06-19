-- brz_olist_orders: typed raw order headers from Olist. Grain: order_id (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    order_id::varchar(36)                            AS order_id,
    customer_id::varchar(36)                         AS customer_id,
    order_status::varchar(20)                        AS order_status,
    order_purchase_timestamp::timestamp_ntz          AS order_purchase_timestamp,
    TRY_CAST(order_approved_at AS timestamp_ntz)     AS order_approved_at,
    TRY_CAST(order_delivered_carrier_date AS timestamp_ntz)   AS order_delivered_carrier_date,
    TRY_CAST(order_delivered_customer_date AS timestamp_ntz)  AS order_delivered_customer_date,
    order_estimated_delivery_date::timestamp_ntz     AS order_estimated_delivery_date,
    CURRENT_TIMESTAMP()::timestamp_ntz               AS _loaded_at
FROM {{ source('olist', 'olist_orders') }}
