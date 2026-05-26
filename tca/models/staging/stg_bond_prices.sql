WITH source AS (
    SELECT * FROM {{ source('stg_raw', 'bond_prices') }}
)

SELECT
    instrument_id,
    CAST(price_date AS DATE) AS price_date,
    yield_pct,
    clean_price,
    accrued_interest,
    dirty_price,
    dv01_per_100,
    duration_years,
    coupon_pct,
    country,
    _loaded_at
FROM source
WHERE instrument_id IS NOT NULL
    AND clean_price > 0
