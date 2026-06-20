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

## Seed Data + MMM Synthetic Generator (Phase 2)

Run `dbt seed --target dev` to load both seed files into Snowflake after infrastructure is provisioned (Phase 1).

To regenerate `olist_mmm_weekly_spend.csv` from real Olist data, place `olist_orders_dataset.csv` and `olist_order_items_dataset.csv` in `data/` then run:
```bash
python seeds/generate_mmm_spend.py
```

| # | File | Type | Description | Key details |
|---|------|------|-------------|-------------|
| 1 | [seeds/generate_mmm_spend.py](seeds/generate_mmm_spend.py) | Python script | Reproducible MMM spend generator. Reads Olist orders + order_items CSVs when available; falls back to calibrated synthetic revenue. Fixed `numpy.random.default_rng(42)` seed ensures idempotent output. | Outputs `seeds/olist_mmm_weekly_spend.csv`. Place raw Olist CSVs in `data/` for real revenue. |
| 2 | [seeds/sample_feedback.csv](seeds/sample_feedback.csv) | dbt seed | 500 English-language customer reviews. | Columns: `feedback_id`, `customer_id`, `product`, `review_text`, `review_date`, `language`, `channel`. Date range: 2016-01-01 – 2018-08-31. |
| 3 | [seeds/olist_mmm_weekly_spend.csv](seeds/olist_mmm_weekly_spend.csv) | dbt seed | 138 ISO-week rows (2016-W01 → 2018-W34). Synthetic revenue with adstock-transformed spend for 5 channels + control variables. | Columns: `iso_week`, `week_start_date`, `weekly_revenue`, `tv_spend`, `paid_search_spend`, `social_spend`, `email_spend`, `display_spend`, `holiday_flag`, `black_friday_flag`, `competitor_index`, `avg_temperature`. |
| 4 | [docs/mmm_synthetic_data_assumptions.md](docs/mmm_synthetic_data_assumptions.md) | Documentation | Calibration choices, adstock parameters, seasonality adjustments, noise levels, and reproducibility notes. | Includes holiday logic (Brazilian calendar), temperature source (INMET), adstock half-lives, and known limitations. |
| 5 | [dbt_project.yml](dbt_project.yml) | dbt config | Added `seeds.cortex_signal_to_action.sample_feedback` and `olist_mmm_weekly_spend` column type blocks. | Explicit `varchar`/`date`/`float`/`integer` types prevent Snowflake type inference errors on `dbt seed`. |

---

## Bronze Layer (Phase 3)

Run `dbt run --select bronze` then `dbt test --select bronze` after loading raw Olist CSVs into Snowflake.

**Loading raw Olist tables:**
```sql
-- Load each CSV from the internal stage into the BRONZE schema (dev example):
COPY INTO CSTA_MARKETING_DEV.BRONZE.OLIST_ORDERS
FROM @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/data/olist_orders_dataset.csv
FILE_FORMAT = (TYPE = CSV FIELD_OPTIONALLY_ENCLOSED_BY = '"' SKIP_HEADER = 1);
-- Repeat for each table.
```

| # | File | Type | Description | Key details |
|---|------|------|-------------|-------------|
| 1 | [models/bronze/sources.yml](models/bronze/sources.yml) | dbt source definition | Declares `olist` source pointing to raw Olist tables in the BRONZE schema. Includes `env_var('SNOWFLAKE_DATABASE')` for multi-env support. | 9 source tables defined. No `loaded_at_field` freshness on static Olist data. |
| 2 | [models/bronze/brz_olist_orders.sql](models/bronze/brz_olist_orders.sql) | dbt model (table) | Typed order headers. Casts nullable timestamps with `TRY_CAST`. | `order_approved_at`, `order_delivered_*` use `TRY_CAST` — these are NULL for non-delivered orders. |
| 3 | [models/bronze/brz_olist_order_items.sql](models/bronze/brz_olist_order_items.sql) | dbt model (table) | Typed order line items. Grain: `(order_id, order_item_id)`. | `price` and `freight_value` cast to float. |
| 4 | [models/bronze/brz_olist_order_reviews.sql](models/bronze/brz_olist_order_reviews.sql) | dbt model (table) | Typed customer reviews. `review_comment_title` and `_message` use `TRY_CAST` — many rows are NULL. | Grain: `review_id` (unique). ~100k rows expected. |
| 5 | [models/bronze/brz_olist_customers.sql](models/bronze/brz_olist_customers.sql) | dbt model (table) | Typed customer master. | `customer_id` is order-scoped; `customer_unique_id` is the de-duplicated customer key. |
| 6 | [models/bronze/brz_olist_order_payments.sql](models/bronze/brz_olist_order_payments.sql) | dbt model (table) | Typed payment records. Grain: `(order_id, payment_sequential)`. | Supports split payments (multiple rows per order). |
| 7 | [models/bronze/brz_olist_products.sql](models/bronze/brz_olist_products.sql) | dbt model (table) | Typed product catalogue. Dimension columns use `TRY_CAST` — sparse data in the Olist dataset. | Grain: `product_id` (unique). ~33k rows. |
| 8 | [models/bronze/brz_olist_geolocation.sql](models/bronze/brz_olist_geolocation.sql) | dbt model (table) | Typed zip-code geolocation. NOT unique by zip alone — grain is `(zip, city, state)`. | ~1M rows; duplicates by design in the Olist dataset. |
| 9 | [models/bronze/brz_olist_sellers.sql](models/bronze/brz_olist_sellers.sql) | dbt model (table) | Typed seller master. Grain: `seller_id` (unique). | ~3k rows. |
| 10 | [models/bronze/brz_olist_mql.sql](models/bronze/brz_olist_mql.sql) | dbt model (table) | Typed marketing qualified leads (seller acquisition). Grain: `mql_id` (unique). | `landing_page_id` and `origin` use `TRY_CAST` — some NULLs in source. |
| 11 | [models/bronze/brz_mmm_weekly_spend.sql](models/bronze/brz_mmm_weekly_spend.sql) | dbt model (table) | Typed MMM spend promoted from seed via `ref('olist_mmm_weekly_spend')`. Adds `_loaded_at`. | Grain: `iso_week` (unique). 138 rows. |
| 12 | [models/bronze/schema.yml](models/bronze/schema.yml) | dbt schema | Tier 1 tests across all 10 bronze models. | `unique` + `not_null` on all PKs; `accepted_values` on `order_status` (8 values) and `payment_type` (5 values); `accepted_values` on `review_score` (1–5). |
| 13 | [analyses/row_count_audit.sql](analyses/row_count_audit.sql) | dbt analysis | UNION ALL sanity query comparing actual vs expected row counts for all 10 bronze tables. | Compile with `dbt compile --select analyses/row_count_audit` then run the output SQL in Snowflake. |

---

## Silver Layer — non-Cortex (Phase 4)

Run `dbt run --select slv_customer_rfm slv_customer_profile slv_mmm_weekly` then the corresponding tests.

**Acceptance checks:**
```bash
dbt run  --select slv_customer_rfm slv_customer_profile slv_mmm_weekly --target dev
dbt test --select slv_customer_rfm slv_customer_profile slv_mmm_weekly --target dev
```

| # | File | Type | Description | Key details |
|---|------|------|-------------|-------------|
| 1 | [models/silver/slv_customer_rfm.sql](models/silver/slv_customer_rfm.sql) | dbt model (table) | RFM scores per `customer_unique_id` from delivered orders. Five CTEs: `delivered_orders`, `order_revenue`, `order_payment_type`, `product_diversity`, `preferred_payment`, then `rfm_raw` → `rfm_scored`. | `NTILE(5)` applied independently per dimension. Recency inverted: lower `recency_days` → higher score. Excludes customers with `monetary_value = 0`. ~100k rows. |
| 2 | [models/silver/slv_customer_profile.sql](models/silver/slv_customer_profile.sql) | dbt model (table) | Enriched customer profile: RFM + MQL acquisition channel + rule-based `churn_risk_score` + `predicted_ltv` placeholder. | MQL join is best-effort (`seller_id = mql_id` UUID match); most rows will have `acquisition_channel = 'unknown'` until closed_deals table is loaded. `predicted_ltv` is NULL until Phase 6. |
| 3 | [models/silver/slv_mmm_weekly.sql](models/silver/slv_mmm_weekly.sql) | dbt model (table) | ISO-week revenue (Olist COALESCE synthetic) joined with MMM spend + single-period adstock transforms. | Adstock α values: TV=0.50, paid_search=0.30, social=0.40, email=0.20, display=0.35. 138 rows. `ORDER BY iso_week` ensures deterministic `LAG()` windows. |
| 4 | [models/silver/schema.yml](models/silver/schema.yml) | dbt schema | Tier 1 + Tier 2 tests for all three silver models. | `unique` + `not_null` on `customer_unique_id` / `iso_week`; `dbt_expectations.expect_column_values_to_be_between` for `rfm_score` (1–5), `recency_days` (≥0), `monetary_value` (>0), `weekly_revenue` (>0), all spend and adstock columns (≥0). `accepted_values` on `churn_risk_score`, `holiday_flag`, `black_friday_flag`. |
| 5 | [tests/singular/assert_rfm_score_distribution.sql](tests/singular/assert_rfm_score_distribution.sql) | singular test | Checks that each NTILE(5) bucket for recency, frequency, and monetary dimensions contains 15–25% of customers. | Fails if any bucket falls outside the 15–25% band across all three dimensions. Uses `UNION ALL` to test all three scores in one query. |
| 6 | [tests/singular/assert_mmm_revenue_positive.sql](tests/singular/assert_mmm_revenue_positive.sql) | singular test | Asserts `weekly_revenue > 0` for every row in `slv_mmm_weekly`. | Returns offending rows — zero rows = pass. |

---

## Cortex Integration — slv_feedback_enriched (Phase 5)

Run after Phase 4. The model is incremental — subsequent runs only process review_ids not yet in the table.

**Prerequisite:** `CSTA_CORTEX_ROLE` must be granted `SNOWFLAKE.CORTEX_USER` (provisioned in Phase 1). The service account running dbt must have this role active.

**Acceptance check:**
```bash
dbt run  --select slv_feedback_enriched --target dev
dbt test --select slv_feedback_enriched --target dev
```

| # | File | Type | Description | Key details |
|---|------|------|-------------|-------------|
| 1 | [macros/cortex_sentiment.sql](macros/cortex_sentiment.sql) | dbt macro | Null-safe wrapper around `AI_SENTIMENT`. Guards against NULL / blank input before calling Cortex. | Returns NULL when `text_col` is NULL or blank; otherwise returns `AI_SENTIMENT(text_col, aspects_array)` VARIANT. Two parameters: `text_col` (column expression), `aspects` (SQL ARRAY expression). |
| 2 | [models/silver/slv_feedback_enriched.sql](models/silver/slv_feedback_enriched.sql) | dbt model (incremental) | Translates Olist reviews to English, scores sentiment, extracts themes — all via Snowflake Cortex. | Incremental strategy: merge on `review_id`. Four CTEs: `source_reviews` (incremental filter), `translated` (CORTEX.TRANSLATE auto-detect → en), `sentiment_raw_scored` (AI_SENTIMENT × 4 aspects), `theme_raw_extracted` (CORTEX.COMPLETE → JSON). Output: `translated_review`, `sentiment_score`, `sentiment_label`, `aspect_product_quality/delivery/customer_service/price_value`, `theme_json`, `theme`, `key_phrase`, `churn_risk_flag`. |
| 3 | [models/silver/schema.yml](models/silver/schema.yml) | dbt schema | Added `slv_feedback_enriched` model entry with Tier 1 + Tier 3 tests. | Tier 1: `unique` + `not_null` on `review_id`, `not_null` on `order_id`/`review_score`/`churn_risk_flag`/`_loaded_at`. Tier 3: `assert_sentiment_range` on all five score columns; `assert_cortex_not_null` on `sentiment_label` and `theme` (filtered to rows where `translated_review IS NOT NULL`); `accepted_values` on `sentiment_label`, `theme`, `review_score`, `churn_risk_flag`. |
| 4 | [tests/generic/assert_cortex_not_null.sql](tests/generic/assert_cortex_not_null.sql) | dbt generic test | Fails when any Cortex output column is NULL, empty string, or the literal string `'null'`. | Parameterised: `{% test assert_cortex_not_null(model, column_name) %}`. Use in `schema.yml` as `- assert_cortex_not_null`. Combine with `config.where` to scope to non-null source rows. |
| 5 | [tests/generic/assert_sentiment_range.sql](tests/generic/assert_sentiment_range.sql) | dbt generic test | Fails when a sentiment or aspect score falls outside `[min_value, max_value]`. NULL values pass. | Parameterised: `min_value` (default -1.0), `max_value` (default 1.0). Applied to `sentiment_score` and all four `aspect_*` columns. |
| 6 | [tests/singular/assert_no_untranslated.sql](tests/singular/assert_no_untranslated.sql) | singular test | Checks that fewer than 2% of non-null `translated_review` rows contain Portuguese stopwords. | Stopwords: `não`, `que`, `para`, `com`, `uma`, `por`, `mas`. Returns a summary row if rate ≥ 2% — zero rows = pass. |
| 7 | [dbt_project.yml](dbt_project.yml) | dbt config | Added `tests/generic` to `macro-paths` so generic test macros in that directory are auto-discovered. | `macro-paths: ["macros", "tests/generic"]`. Required for `assert_cortex_not_null` and `assert_sentiment_range` to be recognised as generic tests by dbt. |

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
