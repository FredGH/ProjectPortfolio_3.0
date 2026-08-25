-- Phase 8: CSTA Observability Dashboard — Streamlit in Snowflake (SiS)
-- 6-page dashboard: Pipeline Overview, Model Health, Cortex Usage & Cost,
--                   Data Quality, Lineage & Coverage, Cost & Credits.
--
-- DEPLOY STEPS (from SnowSQL or Snowflake CLI):
--   Step 1 — upload the Python app to the artifact stage:
--     PUT file://snowflake/observability/streamlit_app.py
--         @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/streamlit/
--         AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--
--   Step 2 — render and run this SQL file:
--     python scripts/render.py dev --subdir observability
--     -- then run rendered/07_observability_streamlit.sql in a Snowflake worksheet
--
-- The STREAMLIT object must be created inside the OBSERVABILITY schema so that
-- STREAMLIT_ROLE (granted USAGE on the schema in 03_roles.sql) can launch it.

USE ROLE SYSADMIN;
USE DATABASE CSTA_MARKETING_SHARED;
USE SCHEMA OBSERVABILITY;

-- UPLOAD_DBT_ARTIFACTS: called from dbt on-run-end to push manifest + run_results to stage.
-- Phase 9 (stored procedure) calls this as a SnowSQL command; this proc handles the SQL side.
-- Actual file upload (PUT) is done by the dbt Python stored procedure (Phase 9).
-- This proc records that an upload happened and is a hook target in dbt_project.yml.
CREATE OR REPLACE PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.UPLOAD_DBT_ARTIFACTS(
    env       VARCHAR,
    run_id    VARCHAR
)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    -- Upsert a pointer row so the lineage page knows which run's artifacts are current.
    MERGE INTO CSTA_MARKETING_SHARED.OBSERVABILITY.PIPELINE_RUN_LOG AS tgt
    USING (SELECT :run_id AS run_id, :env AS env) AS src
    ON tgt.run_id = src.run_id
    WHEN MATCHED THEN UPDATE SET
        -- Sets git_sha as a marker that artifacts were uploaded
        git_sha = COALESCE(tgt.git_sha, 'artifacts_uploaded_' || TO_CHAR(SYSDATE(), 'YYYYMMDD_HH24MISS'));

    RETURN 'OK: artifacts marker written for ' || :run_id || ' (' || :env || ')';
END;
$$;

-- CREATE the Streamlit app pointing to the uploaded Python file on the artifacts stage.
-- The ROOT_LOCATION is the stage folder where streamlit_app.py was uploaded.
CREATE OR REPLACE STREAMLIT CSTA_MARKETING_SHARED.OBSERVABILITY.CSTA_OBSERVABILITY_DASHBOARD
    ROOT_LOCATION = 'CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/streamlit'
    MAIN_FILE     = '/streamlit_app.py'
    QUERY_WAREHOUSE = CSTA_DBT_DEV_WH
    TITLE         = 'CSTA Observability Dashboard'
    COMMENT       = '6-page observability dashboard: pipeline health, Cortex usage, data quality, cost & credits.';

-- Grants — STREAMLIT_ROLE can launch the app; OBSERVER_ROLE can view it



GRANT USAGE ON STREAMLIT CSTA_MARKETING_SHARED.OBSERVABILITY.CSTA_OBSERVABILITY_DASHBOARD
    TO ROLE CSTA_STREAMLIT_ROLE;

GRANT USAGE ON STREAMLIT CSTA_MARKETING_SHARED.OBSERVABILITY.CSTA_OBSERVABILITY_DASHBOARD
    TO ROLE CSTA_OBSERVER_ROLE;


GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.UPLOAD_DBT_ARTIFACTS(
    VARCHAR, VARCHAR
) TO ROLE CSTA_DBT_DEV_ROLE;

GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.UPLOAD_DBT_ARTIFACTS(
    VARCHAR, VARCHAR
) TO ROLE CSTA_DBT_UAT_ROLE;

GRANT USAGE ON PROCEDURE CSTA_MARKETING_SHARED.OBSERVABILITY.UPLOAD_DBT_ARTIFACTS(
    VARCHAR, VARCHAR
) TO ROLE CSTA_DBT_PROD_ROLE;

