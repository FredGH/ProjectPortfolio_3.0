{{ config(unique_key='sat_client_profile_id') }}

-- PII-sensitive: name, country, LEI — marked in catalog.datasets.
WITH source AS (
    SELECT
        {{ dbt_utils.generate_surrogate_key(['counterparty_id']) }} AS hub_client_key,
        name,
        type,
        country,
        lei,
        _loaded_at AS load_timestamp,
        {{ dbt_utils.generate_surrogate_key(['counterparty_id', 'name', 'type', 'country']) }} AS hash_diff
    FROM {{ ref('stg_clients') }}
    {% if is_incremental() %}
    WHERE _loaded_at > (SELECT MAX(load_timestamp) FROM {{ this }})
    {% endif %}
),

ranked AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY hub_client_key ORDER BY load_timestamp DESC) AS rn
    FROM source
)

SELECT
    {{ dbt_utils.generate_surrogate_key(['hub_client_key', 'load_timestamp']) }} AS sat_client_profile_id,
    hub_client_key,
    name,
    type,
    country,
    lei,
    load_timestamp,
    hash_diff
FROM ranked
WHERE rn = 1
