# NOTES

## Snowflake setup scripts (bootstrap only — run once manually)

Run in order as indicated. After bootstrap, Terraform becomes the source of truth for all object changes.

| # | File | Role required | Snowflake objects created / affected | Notes |
|---|------|---------------|--------------------------------------|-------|
| 1 | [snowflake/setup/01_databases.sql](snowflake/setup/01_databases.sql) | `ACCOUNTADMIN` → `SYSADMIN` | User: `TERRAFORM_SVC` (key-pair auth). Databases: `CSTA_MARKETING_DEV`, `CSTA_MARKETING_UAT`, `CSTA_MARKETING_PROD`, `CSTA_MARKETING_SHARED`. Schemas: `BRONZE`, `SILVER`, `GOLD`, `ORCHESTRATION` on each env DB; `OBSERVABILITY`, `ARTIFACTS` on SHARED. | Replace `<RSA_PUBLIC_KEY>` placeholder before running. Import databases into Terraform state afterward (`terraform import`). |
| 2 | [snowflake/setup/02_warehouses.sql](snowflake/setup/02_warehouses.sql) | `SYSADMIN` | Warehouses: `CSTA_DBT_DEV_WH` (XSMALL), `CSTA_DBT_UAT_WH` (SMALL), `CSTA_DBT_PROD_WH` (MEDIUM). All created `INITIALLY_SUSPENDED` with `AUTO_SUSPEND = 60s`. | Sizing: DEV=XSMALL (cost-sensitive), UAT=SMALL, PROD=MEDIUM (Cortex workload + nightly schedule). |
| 3 | [snowflake/setup/03_roles.sql](snowflake/setup/03_roles.sql) | `SECURITYADMIN` → `SYSADMIN` → `ACCOUNTADMIN` | **Access roles** (2 per DB + 3 per schema × 14 schemas = 50 roles). **Functional roles**: `CSTA_DBT_DEV_ROLE`, `CSTA_DBT_UAT_ROLE`, `CSTA_DBT_PROD_ROLE`, `CSTA_CORTEX_ROLE`, `CSTA_OBSERVER_ROLE`, `CSTA_STREAMLIT_ROLE`. **Service account users**: `SVC_CSTA_DBT_DEV/UAT/PROD`, `SVC_STREAMLIT`, `SVC_GITHUB_CI_DEV/UAT/PROD`. Network rule + external access integration for Streamlit. | Replace all `<RSA_PUBLIC_KEY_*>` placeholders. Two-layer RBAC: access roles hold privileges, functional roles aggregate access roles. `CSTA_CORTEX_ROLE` wraps `SNOWFLAKE.CORTEX_USER` DB role. |
| 4 | [snowflake/setup/04_stages.sql](snowflake/setup/04_stages.sql) | `SYSADMIN` | Internal stage `CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS` (SSE-encrypted, directory-enabled). Pre-creates `dev/latest/`, `uat/latest/`, `prod/latest/` path markers. READ/WRITE grants on the stage to all `CSTA_DBT_*_ROLE` + READ to `CSTA_OBSERVER_ROLE`. | Stage stores `manifest.json`, `run_results.json`, `catalog.json` per env. Used by slim CI `--defer --state` pattern. |
