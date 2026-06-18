-- Override dbt's default generate_schema_name so models land in the Snowflake
-- schemas provisioned by 01_databases.sql instead of the {target}_{layer} default.
--
-- dev  → {target.schema}_{custom_schema}  e.g. DBT_FREDERIC_BRONZE
--          (developer-namespaced; target.schema comes from profiles.yml
--           DBT_{{ env_var('DBT_DEVELOPER') }} substitution)
-- uat / prod → {custom_schema} directly   e.g. BRONZE, SILVER, GOLD
--          (shared layer schemas — match the Snowflake schemas created in bootstrap)
--
-- This macro must exist before any dbt run (Phase 2+).  It was moved from
-- Phase 12 to Phase 1 scaffold because all model phases depend on correct
-- schema routing.

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- elif target.name == 'dev' -%}
        {{ default_schema }}_{{ custom_schema_name | trim | upper }}
    {%- else -%}
        {{ custom_schema_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
