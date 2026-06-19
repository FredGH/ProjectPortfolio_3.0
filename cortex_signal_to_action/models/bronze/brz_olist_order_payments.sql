-- brz_olist_order_payments: typed payment records. Grain: (order_id, payment_sequential).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    order_id::varchar(36)              AS order_id,
    payment_sequential::integer        AS payment_sequential,
    payment_type::varchar(20)          AS payment_type,
    payment_installments::integer      AS payment_installments,
    payment_value::float               AS payment_value,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM {{ source('olist', 'olist_order_payments') }}
