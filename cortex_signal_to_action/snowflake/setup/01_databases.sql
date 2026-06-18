-- Bootstrap script: run ONCE manually as ACCOUNTADMIN on a fresh Snowflake account.
-- Creates TERRAFORM_SVC with key-pair auth and SYSADMIN/ACCOUNTADMIN grants,
-- then provisions all four databases and their schemas.
--
-- After this script runs, import the databases into Terraform state and let
-- Terraform become the source of truth for all object changes:
--   terraform import 'module.databases.snowflake_database.this["dev"]'    CSTA_MARKETING_DEV
--   terraform import 'module.databases.snowflake_database.this["uat"]'    CSTA_MARKETING_UAT
--   terraform import 'module.databases.snowflake_database.this["prod"]'   CSTA_MARKETING_PROD
--   terraform import 'module.databases.snowflake_database.this["shared"]' CSTA_MARKETING_SHARED

USE ROLE ACCOUNTADMIN;

-- ---------------------------------------------------------------------------
-- 1. Terraform service account
--    Replace <RSA_PUBLIC_KEY> with the base64 body of terraform_svc.pub
--    (strip the -----BEGIN/END PUBLIC KEY----- header and footer lines).
-- ---------------------------------------------------------------------------
CREATE USER IF NOT EXISTS TERRAFORM_SVC
    RSA_PUBLIC_KEY       = '<RSA_PUBLIC_KEY>'
    DEFAULT_ROLE         = SYSADMIN
    MUST_CHANGE_PASSWORD = FALSE
    COMMENT              = 'Terraform service account — key-pair auth only; never used for dbt';

-- SYSADMIN grants full control over databases, schemas, warehouses, and stages.
GRANT ROLE SYSADMIN TO USER TERRAFORM_SVC;

-- ACCOUNTADMIN is needed during bootstrap to import existing objects into
-- Terraform state and for the provider to grant SNOWFLAKE database roles
-- (e.g., CORTEX_USER).  The Terraform provider is configured to run as
-- ACCOUNTADMIN (see terraform/versions.tf).
--
-- POST-BOOTSTRAP: once `terraform import` is complete and you have confirmed
-- that all objects are tracked in state, revoke ACCOUNTADMIN and leave only
-- SYSADMIN to reduce the blast radius of a compromised key:
--
--   REVOKE ROLE ACCOUNTADMIN FROM USER TERRAFORM_SVC;
--
GRANT ROLE ACCOUNTADMIN TO USER TERRAFORM_SVC;

USE ROLE SYSADMIN;

-- ---------------------------------------------------------------------------
-- 2. Databases
--    Three environment databases with identical layer structure,
--    plus a shared database for cross-environment artefacts.
-- ---------------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS CSTA_MARKETING_DEV
    DATA_RETENTION_TIME_IN_DAYS = 1
    COMMENT = 'Development environment — feature branches and dev branch';

CREATE DATABASE IF NOT EXISTS CSTA_MARKETING_UAT
    DATA_RETENTION_TIME_IN_DAYS = 7
    COMMENT = 'UAT environment — uat branch; staging for prod promotion';

CREATE DATABASE IF NOT EXISTS CSTA_MARKETING_PROD
    DATA_RETENTION_TIME_IN_DAYS = 14
    COMMENT = 'Production environment — main branch; source of truth';

CREATE DATABASE IF NOT EXISTS CSTA_MARKETING_SHARED
    DATA_RETENTION_TIME_IN_DAYS = 14
    COMMENT = 'Shared artefact stage and observability schema — accessible to all envs';

-- ---------------------------------------------------------------------------
-- 3. Schemas — environment databases
--    Each env database has four layers: bronze (raw), silver (enriched),
--    gold (aggregated marts), orchestration (tasks and stored procedures).
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_DEV.BRONZE
    COMMENT = 'Raw typed tables sourced from Olist CSVs and synthetic seed data';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_DEV.SILVER
    COMMENT = 'Enriched and joined tables including Cortex NLP outputs';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_DEV.GOLD
    COMMENT = 'Consumption-ready mart tables — segments, MMM attribution, NBA actions';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_DEV.ORCHESTRATION
    COMMENT = 'Snowflake Task DAG definitions and dbt stored procedure';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_UAT.BRONZE
    COMMENT = 'Raw typed tables sourced from Olist CSVs and synthetic seed data';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_UAT.SILVER
    COMMENT = 'Enriched and joined tables including Cortex NLP outputs';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_UAT.GOLD
    COMMENT = 'Consumption-ready mart tables — segments, MMM attribution, NBA actions';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_UAT.ORCHESTRATION
    COMMENT = 'Snowflake Task DAG definitions and dbt stored procedure';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_PROD.BRONZE
    COMMENT = 'Raw typed tables sourced from Olist CSVs and synthetic seed data';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_PROD.SILVER
    COMMENT = 'Enriched and joined tables including Cortex NLP outputs';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_PROD.GOLD
    COMMENT = 'Consumption-ready mart tables — segments, MMM attribution, NBA actions';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_PROD.ORCHESTRATION
    COMMENT = 'Snowflake Task DAG definitions and dbt stored procedure';

-- ---------------------------------------------------------------------------
-- 4. Schemas — shared database
--    OBSERVABILITY houses all pipeline run, model health, Cortex usage,
--    data quality, and cost daily tables.
--    ARTIFACTS holds the internal dbt artefact stage used for slim CI.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_SHARED.OBSERVABILITY
    COMMENT = 'Pipeline run log, model health, Cortex usage, data quality, and cost tables';

CREATE SCHEMA IF NOT EXISTS CSTA_MARKETING_SHARED.ARTIFACTS
    COMMENT = 'Internal stage for dbt artefacts (manifest.json, run_results.json) shared across envs';
