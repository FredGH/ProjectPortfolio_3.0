# NOTES

## Snowflake setup scripts (bootstrap only — run once manually)

Run in order as indicated. After bootstrap, Terraform becomes the source of truth for all object changes.

| # | File | Role required | Snowflake objects created / affected | Notes |
|---|------|---------------|--------------------------------------|-------|
| 1 | [snowflake/setup/01_databases.sql](snowflake/setup/01_databases.sql) | `ACCOUNTADMIN` → `SYSADMIN` | User: `TERRAFORM_SVC` (key-pair auth). Databases: `CSTA_MARKETING_DEV`, `CSTA_MARKETING_UAT`, `CSTA_MARKETING_PROD`, `CSTA_MARKETING_SHARED`. Schemas: `BRONZE`, `SILVER`, `GOLD`, `ORCHESTRATION` on each env DB; `OBSERVABILITY`, `ARTIFACTS` on SHARED. | Replace `<RSA_PUBLIC_KEY>` placeholder before running. Import databases into Terraform state afterward (`terraform import`). |
| 2 | [snowflake/setup/02_warehouses.sql](snowflake/setup/02_warehouses.sql) | `SYSADMIN` | Warehouses: `CSTA_DBT_DEV_WH` (XSMALL), `CSTA_DBT_UAT_WH` (SMALL), `CSTA_DBT_PROD_WH` (MEDIUM). All created `INITIALLY_SUSPENDED` with `AUTO_SUSPEND = 60s`. | Sizing: DEV=XSMALL (cost-sensitive), UAT=SMALL, PROD=MEDIUM (Cortex workload + nightly schedule). |
| 3 | [snowflake/setup/03_roles.sql](snowflake/setup/03_roles.sql) | `SECURITYADMIN` → `SYSADMIN` → `ACCOUNTADMIN` | **Access roles** (2 per DB + 3 per schema × 14 schemas = 50 roles). **Functional roles**: `CSTA_DBT_DEV_ROLE`, `CSTA_DBT_UAT_ROLE`, `CSTA_DBT_PROD_ROLE`, `CSTA_CORTEX_ROLE`, `CSTA_OBSERVER_ROLE`, `CSTA_STREAMLIT_ROLE`. **Service account users**: `SVC_CSTA_DBT_DEV/UAT/PROD`, `SVC_STREAMLIT`, `SVC_GITHUB_CI_DEV/UAT/PROD`. Network rule + external access integration for Streamlit. | Replace all `<RSA_PUBLIC_KEY_*>` placeholders. Two-layer RBAC: access roles hold privileges, functional roles aggregate access roles. `CSTA_CORTEX_ROLE` wraps `SNOWFLAKE.CORTEX_USER` DB role. |
| 4 | [snowflake/setup/04_stages.sql](snowflake/setup/04_stages.sql) | `SYSADMIN` | Internal stage `CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS` (SSE-encrypted, directory-enabled). Pre-creates `dev/latest/`, `uat/latest/`, `prod/latest/` path markers. READ/WRITE grants on the stage to all `CSTA_DBT_*_ROLE` + READ to `CSTA_OBSERVER_ROLE`. | Stage stores `manifest.json`, `run_results.json`, `catalog.json` per env. Used by slim CI `--defer --state` pattern. |

---

## Terraform modules (source of truth after bootstrap)

Run after bootstrap scripts. Provider: `Snowflake-Labs/snowflake ~> 0.98`, Terraform `>= 1.7`.

State is stored **locally** (`terraform/environments/<env>.tfstate`) — no AWS required. To migrate to remote state later, swap `backend "local"` for `backend "s3"` in `versions.tf` and run `terraform init -migrate-state`.

**Workflow per environment (run from `terraform/` directory):**
```bash
terraform init -backend-config=environments/<env>.backend
terraform plan  -var-file=environments/<env>.tfvars
terraform apply -var-file=environments/<env>.tfvars
```

**Import bootstrapped objects into state (run once after bootstrap):**
```bash
terraform import 'module.databases.snowflake_database.this["dev"]'    CSTA_MARKETING_DEV
terraform import 'module.databases.snowflake_database.this["uat"]'    CSTA_MARKETING_UAT
terraform import 'module.databases.snowflake_database.this["prod"]'   CSTA_MARKETING_PROD
terraform import 'module.databases.snowflake_database.this["shared"]' CSTA_MARKETING_SHARED
```

**Post-apply step — populate profiles.yml secrets:**
```sql
ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_DEV  SET SECRET_STRING = '...';
ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_UAT  SET SECRET_STRING = '...';
ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_PROD SET SECRET_STRING = '...';
```

| # | File | Resource type | Snowflake objects created / managed | Notes |
|---|------|---------------|--------------------------------------|-------|
| 1 | [terraform/versions.tf](terraform/versions.tf) | `terraform` block | Provider pin (`Snowflake-Labs/snowflake ~> 0.98`), Terraform version constraint (`>= 1.7`), local backend declaration | Backend path set at `terraform init` with `-backend-config`; no AWS required |
| 2 | [terraform/variables.tf](terraform/variables.tf) | `variable` | Input variables: `snowflake_organization_name`, `snowflake_account_name`, `snowflake_user`, `snowflake_private_key_path`, `environment` | `account` was split into `organization_name` + `account_name` in `snowflakedb/snowflake`. Values from `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`. `snowflake_private_key_path` is sensitive — read via `file()` at plan time |
| 3 | [terraform/main.tf](terraform/main.tf) | `module` | Calls four child modules: `databases`, `warehouses`, `rbac`, `stages` with explicit `depends_on` ordering | Single root plan manages all objects across all envs |
| 4 | [terraform/outputs.tf](terraform/outputs.tf) | `output` | Exposes: `database_names`, `warehouse_names`, `artifact_stage_url`, `functional_role_names` | — |
| 5 | [terraform/modules/databases/main.tf](terraform/modules/databases/main.tf) | `snowflake_database`, `snowflake_schema` | 4 databases (`CSTA_MARKETING_DEV/UAT/PROD/SHARED`) + 14 schemas (4 per env DB: BRONZE, SILVER, GOLD, ORCHESTRATION; 2 on SHARED: OBSERVABILITY, ARTIFACTS) | Uses `for_each` on flattened `db_schema_pairs` local |
| 6 | [terraform/modules/databases/outputs.tf](terraform/modules/databases/outputs.tf) | `output` | Exposes `database_names`, `databases`, `schemas` | — |
| 7 | [terraform/modules/warehouses/main.tf](terraform/modules/warehouses/main.tf) | `snowflake_warehouse` | 3 warehouses: `CSTA_DBT_DEV_WH` (XSMALL), `CSTA_DBT_UAT_WH` (SMALL), `CSTA_DBT_PROD_WH` (MEDIUM) — all `auto_suspend=60`, `initially_suspended=true` | — |
| 8 | [terraform/modules/warehouses/outputs.tf](terraform/modules/warehouses/outputs.tf) | `output` | Exposes `warehouse_names`, `warehouses` | — |
| 9 | [terraform/modules/rbac/locals.tf](terraform/modules/rbac/locals.tf) | `locals` | Defines `all_databases`, `all_schemas`, `db_access_role_keys` (8), `all_schema_role_keys` (42), tier filter maps (`schema_all_tiers` / `schema_rw_tiers` / `schema_rwc_tiers`), functional role grant lists, warehouse usage grants, `cortex_role_recipients`, `sysadmin_role_grants`, `execute_task_roles` | Central data layer; all grant resources derive keys from these locals |
| 10 | [terraform/modules/rbac/roles.tf](terraform/modules/rbac/roles.tf) | `snowflake_account_role` | 8 DB-level access roles + 42 schema-level access roles + 9 functional roles = **59 roles** | Comments on each role mirror 03_roles.sql |
| 11 | [terraform/modules/rbac/grants_db.tf](terraform/modules/rbac/grants_db.tf) | `snowflake_grant_privileges_to_account_role` | USAGE on database → `DB_READ` (4 grants); USAGE + CREATE SCHEMA on database → `DB_MODIFY` (8 grants) | Mirrors SECTION 4 of 03_roles.sql |
| 12 | [terraform/modules/rbac/grants_schema.tf](terraform/modules/rbac/grants_schema.tf) | `snowflake_grant_privileges_to_account_role` | 12 resource blocks via `for_each`: USAGE (42), SELECT on all/future TABLES (42+42), SELECT on all/future VIEWS (42+42), DML on all/future TABLES (28+28), CREATE TABLE/VIEW/STAGE/PROCEDURE/TASK (14×5) | Mirrors SECTION 4 of 03_roles.sql; ~336 Terraform-managed grant instances |
| 13 | [terraform/modules/rbac/functional.tf](terraform/modules/rbac/functional.tf) | `snowflake_grant_account_role`, `snowflake_grant_database_role`, `snowflake_grant_privileges_to_account_role` | Role-to-role grants (access → functional), SNOWFLAKE.CORTEX_USER DB role → `CSTA_CORTEX_ROLE`, CSTA_CORTEX_ROLE → dbt/dev roles, warehouse USAGE grants, EXECUTE TASK account privilege, SYSADMIN anchoring | Mirrors SECTIONS 5–7 of 03_roles.sql |
| 14 | [terraform/modules/rbac/outputs.tf](terraform/modules/rbac/outputs.tf) | `output` | Exposes `functional_role_names`, `db_access_role_names`, `schema_access_role_names` | — |
| 15 | [terraform/modules/stages/main.tf](terraform/modules/stages/main.tf) | `snowflake_stage`, `snowflake_secret_with_generic_string`, `snowflake_grant_privileges_to_account_role` | Stage `CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS` (SSE, directory-enabled) + READ/WRITE grants + 3 secrets: `PROFILES_YML_DEV/UAT/PROD` | Secret `secret_string` is empty at apply; populate manually post-deploy |
| 16 | [terraform/modules/stages/variables.tf](terraform/modules/stages/variables.tf) | — | No required variables | — |
| 17 | [terraform/modules/stages/outputs.tf](terraform/modules/stages/outputs.tf) | `output` | Exposes `artifact_stage_url`, `profiles_secret_names` | — |
| 18 | [terraform/environments/dev.tfvars](terraform/environments/dev.tfvars) | tfvars | Dev Snowflake account and key path | Replace `your-account` placeholder with actual Snowflake account identifier |
| 19 | [terraform/environments/uat.tfvars](terraform/environments/uat.tfvars) | tfvars | UAT account credential overrides | Same placeholder |
| 20 | [terraform/environments/prod.tfvars](terraform/environments/prod.tfvars) | tfvars | Prod account credential overrides | Same placeholder |
| 21 | [terraform/environments/dev.backend](terraform/environments/dev.backend) | backend partial config | Local backend: `path = environments/dev.tfstate` | Applied via `terraform init -backend-config=environments/dev.backend`; no AWS needed |
| 22 | [terraform/environments/uat.backend](terraform/environments/uat.backend) | backend partial config | Local backend: `path = environments/uat.tfstate` | Same pattern |
| 23 | [terraform/environments/prod.backend](terraform/environments/prod.backend) | backend partial config | Local backend: `path = environments/prod.tfstate` | Same pattern |

---

## dbt scaffold (Phase 1)

Run `dbt deps` after copying `profiles.yml.example` → `~/.dbt/profiles.yml` and populating env vars.

```bash
cp profiles.yml.example ~/.dbt/profiles.yml   # then set env vars below
export SNOWFLAKE_ACCOUNT=<account-identifier>
export SNOWFLAKE_PRIVATE_KEY_PATH=~/.ssh/snowflake_rsa_key.p8
export DBT_DEVELOPER=$(whoami)                 # dev schema prefix
dbt deps
dbt compile
```

| # | File | Purpose | Key config | Notes |
|---|------|---------|------------|-------|
| 1 | [dbt_project.yml](dbt_project.yml) | dbt project root config | `materialized: table` default for bronze/silver/gold; `on-run-end: []` stub (populated Phase 8); `vars: observability_enabled: false`, `drift_baseline_run_id: null` | Project name: `cortex_signal_to_action`. Schema tags applied per layer. `on-run-end` stub will be filled with `log_test_results()` + `CALL UPLOAD_DBT_ARTIFACTS(...)` in Phase 8. |
| 2 | [packages.yml](packages.yml) | dbt package dependencies | `dbt-labs/dbt_utils >=1.1`, `calogica/dbt_expectations >=0.10` | Run `dbt deps` to install. `dbt_expectations` used for Tier 3/4 tests (Phases 7+). MetricFlow (Phase 11) is NOT a dbt Hub package — install via `pip install dbt-metricflow[snowflake]`. |
| 3 | [profiles.yml.example](profiles.yml.example) | Snowflake connection profiles for dev / uat / prod | All credentials via `env_var()`. Dev: `CSTA_DBT_DEV_ROLE` / `CSTA_MARKETING_DEV` / `CSTA_DBT_DEV_WH` / developer-namespaced schema. UAT: `CSTA_DBT_UAT_ROLE` / 8 threads. Prod: `CSTA_DBT_PROD_ROLE` / 16 threads. | Copy to `~/.dbt/profiles.yml`. In Snowflake Task execution (Phase 9), profile is injected from Snowflake Secrets. `query_tag` set per target for warehouse cost attribution. |
| 4 | [macros/generate_schema_name.sql](macros/generate_schema_name.sql) | Schema routing macro | dev → `{target.schema}_{layer}` (e.g. `DBT_FREDERIC_BRONZE`); uat/prod → `{layer}` directly (e.g. `BRONZE`) | **Required from Phase 2 onward.** Without this, dbt's default macro creates `dbt_bronze` etc. instead of routing to the provisioned Snowflake schemas. Moved from Phase 12 to Phase 1 scaffold. |
