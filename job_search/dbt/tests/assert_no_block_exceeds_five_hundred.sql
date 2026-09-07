{{ config(severity='warn') }}

-- PLAN.md Step 7: "measure block size distribution... if any block
-- exceeds a few hundred records, your key is too coarse." Runs at warn
-- severity (see the config below) — this is a monitoring signal, not a
-- hard build-blocking assertion, since a single large employer having a
-- genuinely big block isn't wrong, just worth knowing about.

SELECT
    block_key,
    COUNT(*) AS block_size
FROM {{ source('dedup_ingest', 'job_blocking_keys') }}
WHERE block_key != ''
GROUP BY block_key
HAVING COUNT(*) > 500
