{{ config(severity='warn') }}

-- Symmetric to assert_no_block_exceeds_five_hundred.sql, but for the
-- soft block (company alone) — PLAN.md Step 7 names this exact failure
-- mode ("Large employers with many similar roles are where this
-- shows up"). Runs at warn severity: a large employer having many
-- postings isn't wrong, just something the soft-block generation above
-- already excludes from pairing — this test is the visibility check
-- confirming that exclusion is working as intended.

SELECT
    normalised_company,
    COUNT(*) AS company_size
FROM {{ source('dedup_ingest', 'job_blocking_keys') }}
WHERE normalised_company != ''
GROUP BY normalised_company
HAVING COUNT(*) > 500
