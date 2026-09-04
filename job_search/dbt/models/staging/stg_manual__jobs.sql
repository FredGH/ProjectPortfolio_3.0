-- stg_manual__jobs: one row per manually-pasted posting fetch, mapped to
-- the shared staging contract. bronze.raw_jobs is append-only/versioned,
-- so source_job_id can repeat across fetched_at/payload_sha256 versions
-- of the same posting — see int_jobs__unioned for the current-state
-- collapse. Unlike the four API-sourced models, title/company/
-- location here come from best-effort LLM extraction (already merged
-- with any user override at ingestion time) and can be genuinely NULL
-- when extraction failed — never coerced to a placeholder, per PLAN.md's
-- "never coerce unknown to a default" rule.

SELECT
    source_name,
    source_job_id,
    job_url,
    job_url_canonical,
    entry_method,
    payload -> 'parsed' ->> 'title' AS title,
    payload -> 'parsed' ->> 'company' AS company,
    payload -> 'parsed' ->> 'location' AS location,
    payload ->> 'raw_text' AS description,
    payload -> 'parsed' ->> 'salary' AS salary_raw,
    NULLIF(payload ->> 'posted_date', '')::timestamptz AS posted_at,
    fetched_at,
    run_id,
    payload_sha256
FROM {{ source('bronze', 'raw_jobs') }}
WHERE entry_method = 'manual'
