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

-- Snowflake stages are flat — there are no real subdirectories.
-- The slash delimiter in path strings (e.g. dev/latest/) is honoured by LIST,
-- COPY INTO, and the Streamlit lineage view, but paths do not need to be
-- pre-created.  The UPLOAD_CSTA_DBT_ARTIFACTS stored procedure (Phase 9)
-- creates each env/latest/ path on its first PUT.
-- No SQL is needed here; any COPY FILES attempt referencing non-existent files
-- would fail on a fresh account.

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
