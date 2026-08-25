{#
  observability_hooks.sql — four dbt post-run macros for Phase 8 observability.

  All macros are no-ops when var('observability_enabled', false) is false,
  which is the project default in dbt_project.yml. Enable for uat/prod by
  setting the var in profiles.yml or passing --vars '{"observability_enabled": true}'.

  Macro summary:
    log_model_health()   — post-hook on every model; INSERT into MODEL_HEALTH_LOG
    log_cortex_usage()   — post-hook on slv_feedback_enriched; INSERT into CORTEX_USAGE_LOG
    log_test_results()   — on-run-end; MERGE into PIPELINE_RUN_LOG + INSERT test rows
    trigger_alert()      — called by anomaly checks and cost alerts; CALL TRIGGER_ALERT proc

  The observability schema is CSTA_MARKETING_SHARED.OBSERVABILITY (provisioned in Phase 1).
  Tables are created by 01_observability_schema.sql (Phase 8 bootstrap).
#}

-- ---------------------------------------------------------------------------
-- log_model_health: INSERT one row per model after it builds successfully.
-- Available context in post-hooks: this (the relation), target, invocation_id.
-- rows_affected and execution_time_seconds are NULL here — the dbt result object
-- is only available in on-run-end. Override via the stored procedure in Phase 9
-- when RUN_DBT() parses run_results.json and backfills these columns.
-- ---------------------------------------------------------------------------
{% macro log_model_health() %}
{%- if var('observability_enabled', false) -%}
    INSERT INTO CSTA_MARKETING_SHARED.OBSERVABILITY.MODEL_HEALTH_LOG
        (run_id, env, model_name, schema_name, status)
    VALUES (
        '{{ invocation_id }}',
        '{{ target.name }}',
        '{{ this.identifier }}',
        '{{ this.schema }}',
        'success'
    )
{%- endif -%}
{% endmacro %}

-- ---------------------------------------------------------------------------
-- log_cortex_usage: aggregate Cortex call telemetry for the current session.
-- Call as a post-hook on slv_feedback_enriched (the only Cortex-heavy model).
-- Queries INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION — no replication lag,
-- covers only the current session, RESULT_LIMIT caps to the last 1000 queries.
-- ---------------------------------------------------------------------------
{% macro log_cortex_usage() %}
{%- if var('observability_enabled', false) -%}
    INSERT INTO CSTA_MARKETING_SHARED.OBSERVABILITY.CORTEX_USAGE_LOG
        (run_id, env, function_name, calls_count, avg_latency_seconds, null_output_count)
    SELECT
        '{{ invocation_id }}',
        '{{ target.name }}',
        cortex_fn,
        COUNT(*)                            AS calls_count,
        AVG(total_elapsed_time) / 1000.0    AS avg_latency_seconds,
        0                                   AS null_output_count
    FROM (
        SELECT
            CASE
                WHEN UPPER(query_text) LIKE '%AI_SENTIMENT%'      THEN 'AI_SENTIMENT'
                WHEN UPPER(query_text) LIKE '%CORTEX.TRANSLATE%'  THEN 'CORTEX.TRANSLATE'
                WHEN UPPER(query_text) LIKE '%CORTEX.COMPLETE%'   THEN 'CORTEX.COMPLETE'
            END AS cortex_fn,
            total_elapsed_time
        FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION(RESULT_LIMIT => 1000))
        WHERE start_time          >= CONVERT_TIMEZONE('UTC', '{{ run_started_at }}'::TIMESTAMP_NTZ)
          AND execution_status    =  'SUCCESS'
          AND (   UPPER(query_text) LIKE '%AI_SENTIMENT%'
               OR UPPER(query_text) LIKE '%CORTEX.TRANSLATE%'
               OR UPPER(query_text) LIKE '%CORTEX.COMPLETE%')
    ) AS q
    WHERE cortex_fn IS NOT NULL
    GROUP BY cortex_fn
{%- endif -%}
{% endmacro %}

-- ---------------------------------------------------------------------------
-- log_test_results: MERGE pipeline summary + INSERT all test results.
-- Called in on-run-end; has access to the `results` list and `run_started_at`.
-- Inserts one DATA_QUALITY_LOG row per dbt test result (pass, fail, warn, error).
-- ---------------------------------------------------------------------------
{% macro log_test_results() %}
{%- if var('observability_enabled', false) -%}

{%- set test_results   = results | selectattr('node.resource_type', 'equalto', 'test')  | list -%}
{%- set model_results  = results | selectattr('node.resource_type', 'equalto', 'model') | list -%}
{%- set n_failed_tests = test_results  | selectattr('status', 'in', ['fail', 'error']) | list | length -%}
{%- set n_failed_models= model_results | selectattr('status', 'equalto', 'error')       | list | length -%}
{%- set run_status     = 'partial_success' if n_failed_tests > 0 or n_failed_models > 0 else 'success' -%}

    -- 1. Upsert pipeline run summary row
    MERGE INTO CSTA_MARKETING_SHARED.OBSERVABILITY.PIPELINE_RUN_LOG AS tgt
    USING (
        SELECT
            '{{ invocation_id }}'                AS run_id,
            '{{ target.name }}'                  AS env,
            'run+test'                           AS command,
            '{{ run_status }}'                   AS status,
            '{{ run_started_at }}'::TIMESTAMP_NTZ AS started_at,
            SYSDATE()                            AS finished_at,
            {{ model_results | length }}         AS models_run,
            {{ test_results  | length }}         AS tests_run,
            {{ n_failed_models }}                AS models_failed,
            {{ n_failed_tests }}                 AS tests_failed,
            '{{ env_var("GIT_SHA", "") }}'       AS git_sha
    ) AS src ON tgt.run_id = src.run_id
    WHEN MATCHED THEN UPDATE SET
        status           = src.status,
        finished_at      = src.finished_at,
        duration_seconds = DATEDIFF('second', src.started_at, src.finished_at),
        models_run       = src.models_run,
        tests_run        = src.tests_run,
        models_failed    = src.models_failed,
        tests_failed     = src.tests_failed,
        git_sha          = COALESCE(src.git_sha, tgt.git_sha)
    WHEN NOT MATCHED THEN INSERT (
        run_id, env, command, status, started_at, finished_at,
        duration_seconds, models_run, tests_run, models_failed, tests_failed,
        git_sha, invocation_id
    ) VALUES (
        src.run_id, src.env, src.command, src.status, src.started_at, src.finished_at,
        DATEDIFF('second', src.started_at, src.finished_at),
        src.models_run, src.tests_run, src.models_failed, src.tests_failed,
        src.git_sha, src.run_id
    );

    -- 2. Upload dbt artifacts marker
    CALL CSTA_MARKETING_SHARED.OBSERVABILITY.UPLOAD_DBT_ARTIFACTS(
        '{{ target.name }}',
        '{{ invocation_id }}'
    );

    -- 3. INSERT one DATA_QUALITY_LOG row per test result
    {% for result in test_results %}
    INSERT INTO CSTA_MARKETING_SHARED.OBSERVABILITY.DATA_QUALITY_LOG
        (run_id, env, test_name, model_name, column_name, status, severity, failure_count)
    VALUES (
        '{{ invocation_id }}',
        '{{ target.name }}',
        '{{ result.node.name | replace("'", "''") }}',
        '{{ (result.node.attached_node | default("") | replace("model.cortex_signal_to_action.", "")) | replace("'", "''") }}',
        '{{ result.node.column_name | default("") | replace("'", "''") }}',
        '{{ result.status }}',
        '{{ result.node.config.get("severity", "error") }}',
        {{ result.failures | default(0) }}
    );
    {% endfor %}

{%- endif -%}
{% endmacro %}

-- ---------------------------------------------------------------------------
-- trigger_alert: log a named alert to DATA_QUALITY_LOG via the stored procedure.
-- Used by anomaly checks and cost checks. The Phase 9 stored procedure and
-- Phase 10 CI workflow both read DATA_QUALITY_LOG status='fail' entries.
-- ---------------------------------------------------------------------------
{% macro trigger_alert(alert_type, alert_message, env=none) %}
{%- if var('observability_enabled', false) -%}
    CALL CSTA_MARKETING_SHARED.OBSERVABILITY.TRIGGER_ALERT(
        '{{ alert_type }}',
        '{{ alert_message | replace("'", "''") }}',
        '{{ env or target.name }}'
    )
{%- endif -%}
{% endmacro %}
