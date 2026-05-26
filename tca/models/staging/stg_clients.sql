WITH source AS (
    SELECT * FROM {{ source('stg_raw', 'clients') }}
)

SELECT
    counterparty_id,
    name,
    type,
    country,
    lei,
    _loaded_at
FROM source
WHERE counterparty_id IS NOT NULL
