{{ config(materialized='table') }}

WITH latest_sat AS (
    SELECT DISTINCT ON (hub_algo_key)
        hub_algo_key,
        name,
        family,
        provider,
        load_timestamp
    FROM {{ ref('sat_algo_version') }}
    ORDER BY hub_algo_key, load_timestamp DESC
)

SELECT
    h.hub_algo_key,
    h.algo_bk AS algo_id,
    s.name,
    s.family,
    s.provider,
    s.load_timestamp
FROM {{ ref('hub_algo') }} AS h
JOIN latest_sat AS s USING (hub_algo_key)
