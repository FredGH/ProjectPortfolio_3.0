{{ config(unique_key='hub_client_key') }}

WITH source AS (
    SELECT
        counterparty_id AS client_bk,
        _loaded_at AS load_timestamp,
        'ref_data' AS record_source,
        {{ dbt_utils.generate_surrogate_key(['counterparty_id']) }} AS hub_client_key
    FROM {{ ref('stg_clients') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
)

SELECT DISTINCT
    hub_client_key,
    client_bk,
    load_timestamp,
    record_source
FROM source
