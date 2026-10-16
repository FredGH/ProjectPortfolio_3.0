-- The deterministic work_model rules must reproduce every example phrase in
-- seeds/work_model_cases.csv. Passes on zero rows.

SELECT text, expected, {{ work_model('text') }} AS derived
FROM {{ ref('work_model_cases') }}
WHERE {{ work_model('text') }} <> expected
