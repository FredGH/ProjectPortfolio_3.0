-- int_market_job_skill: int_market_job x its extracted skills (Step 14).
-- Grain: (job_group_id, skill_id).

SELECT
    b.job_group_id,
    b.skill_id,
    b.requirement_level,
    j.category,
    j.region,
    j.contract_type,
    j.first_seen_month,
    j.annual_salary,
    j.day_rate
FROM {{ ref('silver__bridge_job_skill') }} AS b
INNER JOIN {{ ref('int_market_job') }} AS j
    ON b.job_group_id = j.job_group_id
