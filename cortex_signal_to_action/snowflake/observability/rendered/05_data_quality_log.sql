-- Phase 8: LOG_DATA_QUALITY stored procedure
-- Bulk-inserts dbt test results for a single run. Called from log_test_results()
-- on-run-end macro which passes test results as a VARIANT array.
-- Also handles per-test insert via LOG_DATA_QUALITY_SINGLE for one-at-a-time use.
--
-- Bulk signature (preferred from dbt on-run-end):
--   CALL LOG_DATA_QUALITY(
--     run_id       => '<invocation_id>',
--     env          => 'dev',
--     results_json => PARSE_JSON('[{"test_name":"...","status":"pass",...},...]')
--   );

USE ROLE SYSADMIN;
USE DATABASE CSTA_MARKETING_SHARED;
USE SCHEMA OBSERVABILITY;

-- LOG_DATA_QUALITY_SINGLE: insert one test result row — called per test result in on-run-end
CREATE OR REPLACE PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY_SINGLE(
    run_id        VARCHAR,
    env           VARCHAR,
    test_name     VARCHAR,
    model_name    VARCHAR,
    column_name   VARCHAR,
    status        VARCHAR,
    severity      VARCHAR,
    failure_count INTEGER
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    INSERT INTO CSTA_MARKETING_SHARED.OBSERVABILITY.DATA_QUALITY_LOG
        (run_id, env, test_name, model_name, column_name, status, severity, failure_count)
    VALUES
        (:run_id, :env, :test_name, :model_name, :column_name, :status, :severity, :failure_count);

    RETURN 'OK: ' || :test_name || ' → ' || :status;
END;
$$;

-- LOG_DATA_QUALITY: bulk insert from a VARIANT JSON array of test results
-- Each element: {"test_name": "...", "model_name": "...", "column_name": "...",
--                "status": "pass|fail|warn", "severity": "error|warn", "failure_count": 0}
CREATE OR REPLACE PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY(
    run_id       VARCHAR,
    env          VARCHAR,
    results_json VARIANT
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    INSERT INTO CSTA_MARKETING_SHARED.OBSERVABILITY.DATA_QUALITY_LOG
        (run_id, env, test_name, model_name, column_name, status, severity, failure_count)
    SELECT
        :run_id,
        :env,
        r.value:test_name::VARCHAR,
        r.value:model_name::VARCHAR,
        r.value:column_name::VARCHAR,
        r.value:status::VARCHAR,
        COALESCE(r.value:severity::VARCHAR, 'error'),
        COALESCE(r.value:failure_count::INTEGER, 0)
    FROM TABLE(FLATTEN(INPUT => :results_json)) AS r;

    RETURN 'OK: ' || ARRAY_SIZE(:results_json) || ' test results logged for ' || :run_id;
END;
$$;


GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY_SINGLE(
    VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, INTEGER
) TO ROLE CSTA_DBT_DEV_ROLE;
GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY(
    VARCHAR, VARCHAR, VARIANT
) TO ROLE CSTA_DBT_DEV_ROLE;

GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY_SINGLE(
    VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, INTEGER
) TO ROLE CSTA_DBT_UAT_ROLE;
GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY(
    VARCHAR, VARCHAR, VARIANT
) TO ROLE CSTA_DBT_UAT_ROLE;

GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY_SINGLE(
    VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, INTEGER
) TO ROLE CSTA_DBT_PROD_ROLE;
GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.LOG_DATA_QUALITY(
    VARCHAR, VARCHAR, VARIANT
) TO ROLE CSTA_DBT_PROD_ROLE;

