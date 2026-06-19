-- brz_olist_mql: typed marketing qualified leads (seller acquisition). Grain: mql_id (unique).

{{ config(materialized='table', tags=['bronze']) }}

SELECT
    mql_id::varchar(36)                AS mql_id,
    first_contact_date::date           AS first_contact_date,
    TRY_CAST(landing_page_id AS varchar(50)) AS landing_page_id,
    TRY_CAST(origin AS varchar(50))          AS origin,
    CURRENT_TIMESTAMP()::timestamp_ntz AS _loaded_at
FROM {{ source('olist', 'olist_mql') }}
