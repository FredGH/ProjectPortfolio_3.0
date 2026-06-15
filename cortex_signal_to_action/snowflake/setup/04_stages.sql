-- Bootstrap script: create the internal dbt artefact stage.
-- Run once manually as SYSADMIN after 03_roles.sql.
-- Terraform (terraform/modules/stages/) is the source of truth after bootstrap.
--
-- Stage path convention used across all environments:
--   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/<env>/latest/
--   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/<env>/<run_id>/   (historical snapshots)
--
-- Files uploaded after every dbt run (by UPLOAD_CSTA_DBT_ARTIFACTS stored procedure):
--   manifest.json       — model graph; used for --defer slim CI and lineage view
--   run_results.json    — pass/fail per node; parsed into PIPELINE_RUN_LOG
--   catalog.json        — column-level metadata; served by Observability Streamlit
--
-- Slim CI command that consumes this stage:
--   dbt test --select state:modified+ --defer --state @CSTA_DBT_ARTIFACTS/uat/latest/

USE ROLE SYSADMIN;

CREATE STAGE IF NOT EXISTS CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS
    ENCRYPTION        = (TYPE = 'SNOWFLAKE_SSE')
    DIRECTORY         = (ENABLE = TRUE)
    COMMENT           = 'Internal stage for dbt artefacts shared across dev/uat/prod environments';

-- Pre-create the env/latest/ folder markers so that UPLOAD_CSTA_DBT_ARTIFACTS
-- can PUT files without needing to create the path on first run.
-- Snowflake stages are flat (no real directories) but the / delimiter is
-- honoured by LIST and COPY commands and by the Streamlit lineage page.

-- dev
COPY FILES
    INTO   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/dev/latest/
    FROM   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/dev/latest/
    FILES  = ('.keep');    -- no-op placeholder; path is created on first PUT

-- uat
COPY FILES
    INTO   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/uat/latest/
    FROM   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/uat/latest/
    FILES  = ('.keep');

-- prod
COPY FILES
    INTO   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/prod/latest/
    FROM   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/prod/latest/
    FILES  = ('.keep');

-- Grant read access so that every CSTA_DBT_*_ROLE can fetch artefacts for --defer.
-- Write access is already granted via CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE
-- in 03_roles.sql; only USAGE on the stage object itself is needed here.
GRANT READ   ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_DBT_DEV_ROLE;
GRANT READ   ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_DBT_UAT_ROLE;
GRANT READ   ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_DBT_PROD_ROLE;
GRANT READ   ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_OBSERVER_ROLE;
GRANT WRITE  ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_DBT_DEV_ROLE;
GRANT WRITE  ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_DBT_UAT_ROLE;
GRANT WRITE  ON STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS TO ROLE CSTA_DBT_PROD_ROLE;
