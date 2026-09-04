{#
    dbt's default generate_schema_name macro prefixes a model's custom
    schema with the target's own schema (e.g. "public_staging" for a
    model configured with +schema: staging under the local target's
    schema: public), which doesn't match this project's existing
    single-word schema convention (bronze, target_company's default
    schema). Override it so a configured custom schema is used exactly
    as named, with the target's schema as the fallback only when a model
    has no custom schema configured at all.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
