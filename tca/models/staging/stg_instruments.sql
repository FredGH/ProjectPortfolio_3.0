WITH source AS (
    SELECT * FROM {{ source('stg_raw', 'instruments') }}
)

SELECT
    instrument_id,
    isin,
    name,
    instrument_class,
    currency,
    exchange,
    sector,
    country_of_risk,
    coupon_rate,
    maturity_date,
    expiry_date,
    contract_size,
    underlying_id,
    base_currency,
    quote_currency,
    tenor,
    _loaded_at
FROM source
WHERE instrument_id IS NOT NULL
    AND instrument_class IN ('equity', 'equity_future', 'fixed_income', 'fx_derivative')
