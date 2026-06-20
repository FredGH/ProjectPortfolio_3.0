-- assert_no_duplicate_pk: parameterised composite primary key uniqueness checker.
-- Fails (returns rows) when any combination of the given columns appears more than once.
-- Usage in schema.yml at model level:
--   tests:
--     - assert_no_duplicate_pk:
--         column_names: ['col_a', 'col_b', 'col_c']

{% test assert_no_duplicate_pk(model, column_names) %}

SELECT
    {{ column_names | join(', ') }},
    COUNT(*) AS duplicate_count
FROM {{ model }}
GROUP BY {{ column_names | join(', ') }}
HAVING COUNT(*) > 1

{% endtest %}
