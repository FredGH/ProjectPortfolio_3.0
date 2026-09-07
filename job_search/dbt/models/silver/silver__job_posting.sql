-- silver__job_posting: one row per int_jobs__unioned job, enriched with
-- engagement type, IR35 status and normalised rate figures. Grain:
-- job_key (unique).

SELECT
    j.job_key,
    j.source_name,
    j.source_job_id,
    j.job_url,
    j.job_url_canonical,
    j.entry_method,
    j.title,
    j.company,
    j.location,
    j.description,
    j.salary_raw,
    j.posted_at,
    COALESCE(t.engagement_type, 'unknown') AS engagement_type,
    COALESCE(t.ir35_status, 'unknown') AS ir35_status,
    COALESCE(t.engagement_vehicle, 'unknown') AS engagement_vehicle,
    COALESCE(t.rate_basis, 'unknown') AS rate_basis,
    t.rate_currency,
    t.rate_annualised_gbp,
    t.rate_daily_gbp_equivalent,
    t.contract_length_months,
    COALESCE(t.extension_likelihood, 'unstated') AS extension_likelihood
FROM {{ ref('int_jobs__unioned') }} AS j
LEFT JOIN {{ source('silver_ingest', 'job_engagement_terms') }} AS t
    USING (job_key)
