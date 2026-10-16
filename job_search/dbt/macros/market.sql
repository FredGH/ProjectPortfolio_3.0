{#
    Market-mart helpers (PLAN.md Step 21a).

    work_model(text_expr): remote | hybrid | onsite | unknown from free text.
    Explicit "hybrid" wins; remote and on-site wording on their own decide;
    both together (without "hybrid") is ambiguous and stays unknown.
#}
{% macro work_model(text_expr) -%}
CASE
    -- Hybrid: "hybrid" as a working pattern (not "hybrid cloud" etc.) or 1-4
    -- days in the office.
    WHEN {{ text_expr }} ~* '(\yhybrid\y(?! (cloud|infrastructure|environments?|architectures?|deployments?|multi-?cloud|data|systems?))|[1-4] days? (a|per) week (in|at) the office|[1-4] days? (in|at) the office|in the office [1-4] days?)'
        THEN 'hybrid'
    -- On-site: 5 days in the office, "fully on-site", "office-based"; any
    -- mention of remote makes it ambiguous.
    WHEN {{ text_expr }} ~* '(5 days? (a|per) week (in|at) the office|(in|at) the office 5 days?|5 days? (in|at) the office|fully on-?site|100% on-?site|on-?site (role|position|only)|office[- ]based)'
        AND NOT {{ text_expr }} ~* '\yremote\y'
        THEN 'onsite'
    -- Remote: explicit wording, not negated ("not a remote role"), no office
    -- wording alongside.
    WHEN {{ text_expr }} ~* '((fully|100%|completely) remote|remote[- ]first|work(ing)? from home|remote working|remote (position|role|opportunity))'
        AND NOT {{ text_expr }} ~* '(\y(not|no|non)[- ](a |an )?(fully )?remote|(on-?site|office[- ]based|in the office))'
        THEN 'remote'
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
