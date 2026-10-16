{#
    Market-mart helpers (PLAN.md Step 21a).

    work_model(text_expr): remote | hybrid | onsite | unknown from free text.
    Explicit "hybrid" wins; remote and on-site wording on their own decide;
    both together (without "hybrid") is ambiguous and stays unknown.
#}
{% macro work_model(text_expr) -%}
CASE
    WHEN {{ text_expr }} ~* '\y(hybrid|[0-9] days? (a|per) week (in|at) the office|[0-9] days? (in|at) the office per week)\y'
        THEN 'hybrid'
    WHEN {{ text_expr }} ~* '(fully|100%|completely) remote|remote[- ]first|work(ing)? from home|remote working|remote position|remote role'
        AND NOT {{ text_expr }} ~* '(on-?site|office[- ]based|in the office)'
        THEN 'remote'
    WHEN {{ text_expr }} ~* '(fully|100%) on-?site|on-?site (role|position|only)|office[- ]based|(in|at) the office [0-9] days?|in the office [0-9] days?|[0-9] days? (a|per) week (in|at) the office'
        AND NOT {{ text_expr }} ~* 'remote'
        THEN 'onsite'
    WHEN {{ text_expr }} ~* 'in the office'
        AND NOT {{ text_expr }} ~* 'remote'
        THEN 'onsite'
    ELSE 'unknown'
END
{%- endmacro %}

{#
    agency_name_matches(name_expr): true when the company name contains any
    pattern from the agency_name_patterns seed.
#}
{% macro agency_name_matches(name_expr) -%}
EXISTS (
    SELECT 1 FROM {{ ref('agency_name_patterns') }} AS agency_pattern
    WHERE LOWER({{ name_expr }}) LIKE '%' || agency_pattern.pattern || '%'
)
{%- endmacro %}
