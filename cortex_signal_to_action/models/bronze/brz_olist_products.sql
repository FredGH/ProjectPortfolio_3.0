-- brz_olist_products: typed product catalogue. Grain: product_id (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    product_id::varchar(36)                       AS product_id,
    TRY_CAST(product_category_name AS varchar(100)) AS product_category_name,
    TRY_CAST(product_name_length AS integer)        AS product_name_length,
    TRY_CAST(product_description_length AS integer) AS product_description_length,
    TRY_CAST(product_photos_qty AS integer)         AS product_photos_qty,
    TRY_CAST(product_weight_g AS float)             AS product_weight_g,
    TRY_CAST(product_length_cm AS float)            AS product_length_cm,
    TRY_CAST(product_height_cm AS float)            AS product_height_cm,
    TRY_CAST(product_width_cm AS float)             AS product_width_cm,
    CURRENT_TIMESTAMP()::timestamp_ntz              AS _loaded_at
FROM {{ source('olist', 'olist_products') }}
