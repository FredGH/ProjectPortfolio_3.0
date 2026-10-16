-- The agency name patterns must reproduce every example in
-- seeds/agency_cases.csv. Passes on zero rows.

SELECT company, expected, {{ agency_name_matches('company') }} AS derived
FROM {{ ref('agency_cases') }}
WHERE {{ agency_name_matches('company') }} <> (expected = 'true')
