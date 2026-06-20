-- assert_cortex_not_null: fails when a Cortex output column is NULL, blank, or the literal string 'null'.
-- Parameterised — reference in schema.yml as `- assert_cortex_not_null`.
-- Returns offending rows — zero rows = test passes.

{% test assert_cortex_not_null(model, column_name) %}

SELECT {{ column_name }}
FROM {{ model }}
WHERE
    {{ column_name }} IS NULL
    OR TRIM({{ column_name }}::varchar) = ''
    OR LOWER(TRIM({{ column_name }}::varchar)) = 'null'

{% endtest %}
