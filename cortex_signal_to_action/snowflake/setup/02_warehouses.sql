-- Bootstrap script: provision compute warehouses for each environment.
-- Run once manually as SYSADMIN after 01_databases.sql.
-- Terraform (terraform/modules/warehouses/) is the source of truth after bootstrap.
--
-- Sizing rationale:
--   CSTA_DBT_DEV_WH   XSMALL — developer runs; low concurrency, cost-sensitive
--   CSTA_DBT_UAT_WH   SMALL  — UAT full pipeline; slightly higher throughput
--   CSTA_DBT_PROD_WH  MEDIUM — production + Cortex workload; scheduled nightly
--
-- auto_suspend = 60s to minimise idle credit burn.
-- INITIALLY_SUSPENDED avoids consuming credits before the first query.

USE ROLE SYSADMIN;

CREATE WAREHOUSE IF NOT EXISTS CSTA_DBT_DEV_WH
    WAREHOUSE_SIZE      = 'XSMALL'
    AUTO_SUSPEND        = 60
    AUTO_RESUME         = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT             = 'Dev environment — feature branch dbt runs and ad-hoc analyst queries';

CREATE WAREHOUSE IF NOT EXISTS CSTA_DBT_UAT_WH
    WAREHOUSE_SIZE      = 'SMALL'
    AUTO_SUSPEND        = 60
    AUTO_RESUME         = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT             = 'UAT environment — scheduled uat branch pipeline (cron 02:00 UTC)';

CREATE WAREHOUSE IF NOT EXISTS CSTA_DBT_PROD_WH
    WAREHOUSE_SIZE      = 'MEDIUM'
    AUTO_SUSPEND        = 60
    AUTO_RESUME         = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT             = 'Prod environment — scheduled prod pipeline (cron 04:00 UTC) and TASK_COST_REPORT';
