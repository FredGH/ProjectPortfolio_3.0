-- fct_skill_cooccurrence: skill pairs within a category, with lift (PLAN.md
-- Step 21a). Grain: (category, skill_a, skill_b) with skill_a < skill_b.
-- lift = P(a and b) / (P(a) x P(b)) over the category's postings that have
-- any extracted skill; > 1 means the pair appears together more than chance.
-- Pairs seen in fewer than 3 postings are dropped (support floor).

WITH job_skills AS (

    SELECT DISTINCT b.job_group_id, j.category, b.skill_id
    FROM {{ ref('silver__bridge_job_skill') }} AS b
    INNER JOIN {{ ref('int_market_job') }} AS j
        ON b.job_group_id = j.job_group_id

),

category_totals AS (

    SELECT category, COUNT(DISTINCT job_group_id) AS category_jobs
    FROM job_skills
    GROUP BY category

),

skill_counts AS (

    SELECT category, skill_id, COUNT(*) AS skill_jobs
    FROM job_skills
    GROUP BY category, skill_id

),

pairs AS (

    SELECT
        a.category,
        a.skill_id AS skill_a,
        b.skill_id AS skill_b,
        COUNT(*) AS support
    FROM job_skills AS a
    INNER JOIN job_skills AS b
        ON a.job_group_id = b.job_group_id AND a.skill_id < b.skill_id
    GROUP BY 1, 2, 3
    HAVING COUNT(*) >= 3

)

SELECT
    p.category,
    p.skill_a,
    sa.canonical_label AS skill_a_label,
    p.skill_b,
    sb.canonical_label AS skill_b_label,
    p.support,
    p.support::numeric / t.category_jobs AS support_share,
    (p.support::numeric * t.category_jobs) / (ca.skill_jobs::numeric * cb.skill_jobs) AS lift
FROM pairs AS p
INNER JOIN category_totals AS t USING (category)
INNER JOIN skill_counts AS ca ON ca.category = p.category AND ca.skill_id = p.skill_a
INNER JOIN skill_counts AS cb ON cb.category = p.category AND cb.skill_id = p.skill_b
LEFT JOIN {{ ref('silver__skill') }} AS sa ON p.skill_a = sa.skill_id
LEFT JOIN {{ ref('silver__skill') }} AS sb ON p.skill_b = sb.skill_id
