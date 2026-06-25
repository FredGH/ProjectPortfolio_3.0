-- Phase 8: LOG_MODEL_HEALTH stored procedure
-- Called by the log_model_health() dbt post-hook macro after every model.
-- Inserts one row per (run_id, model_name). Duplicate inserts are safe —
-- Snowflake AUTOINCREMENT gives each row a unique log_id.
--
-- Signature:
--   CALL LOG_MODEL_HEALTH(
--     run_id                 => '<invocation_id>',
--     env                    => 'dev',
--     model_name             => 'slv_feedback_enriched',
--     schema_name            => 'SILVER',
--     status                 => 'success',
--     rows_affected          => 100432,
--     execution_time_seconds => 14.7
--   );

USE ROLE SYSADMIN;
USE DATABASE CSTA_MARKETING_SHARED;
USE SCHEMA OBSERVABILITY;

CREATE OR REPLACE PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_MODEL_HEALTH(
    run_id                 VARCHAR,
    env                    VARCHAR,
    model_name             VARCHAR,
    schema_name            VARCHAR,
    status                 VARCHAR,
    rows_affected          INTEGER,
    execution_time_seconds FLOAT
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    INSERT INTO CSTA_MARKETING_SHARED.OBSERVABILITY.MODEL_HEALTH_LOG
        (run_id, env, model_name, schema_name, status, rows_affected, execution_time_seconds)
    VALUES
        (:run_id, :env, :model_name, :schema_name, :status, :rows_affected, :execution_time_seconds);

    RETURN 'OK: ' || :model_name;
END;
$$;


GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_MODEL_HEALTH(
    VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, INTEGER, FLOAT
) TO ROLE CSTA_DBT_DEV_ROLE;

GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_MODEL_HEALTH(
    VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, INTEGER, FLOAT
) TO ROLE CSTA_DBT_UAT_ROLE;

GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_MODEL_HEALTH(
    VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, INTEGER, FLOAT
) TO ROLE CSTA_DBT_PROD_ROLE;

