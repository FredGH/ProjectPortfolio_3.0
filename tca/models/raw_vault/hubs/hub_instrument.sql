{{ config(unique_key='hub_instrument_key') }}

WITH source AS (
    SELECT
        instrument_id AS instrument_bk,
        _loaded_at AS load_timestamp,
        'ref_data' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['instrument_id']) }} AS hub_instrument_key
    FROM {{ ref('stg_instruments') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_instrument_key,
    instrument_bk,
    load_timestamp,
    record_source
FROM source
