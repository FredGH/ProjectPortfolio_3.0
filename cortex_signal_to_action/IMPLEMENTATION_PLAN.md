# Implementation Plan — Cortex Signal to Action

Phased delivery for the Snowflake Cortex marketing intelligence pipeline.
Each phase is independently deployable and produces a testable, committed artifact.

---

## Phase 1 — Repo Scaffold, Terraform Infrastructure + RBAC

**Goal:** Empty but valid repo structure + all Snowflake objects provisioned via Terraform, with tiered RBAC in place.

Deliverables:

**Snowflake setup scripts (bootstrap only — run once manually):**
- `snowflake/setup/01_databases.sql` — create TERRAFORM_SVC user with key-pair auth + SYSADMIN grant; reference documentation only after bootstrap
- `snowflake/setup/02_warehouses.sql` — DBT_DEV_WH (XS) / DBT_UAT_WH (S) / DBT_PROD_WH (M); auto_suspend=60, auto_resume=true
- `snowflake/setup/03_roles.sql` — two-layer RBAC:
  - DB-level access roles: `<DB>_DB_READ`, `<DB>_DB_MODIFY` for each of the four databases
  - Schema-level access roles: `<DB>_<SCHEMA>_READ`, `_READ_WRITE`, `_READ_WRITE_CREATE` for every schema (BRONZE, SILVER, GOLD, ORCHESTRATION per env; OBSERVABILITY, ARTIFACTS in SHARED)
  - Functional roles assembled from access roles: `DBT_DEV_ROLE`, `DBT_UAT_ROLE`, `DBT_PROD_ROLE`, `CORTEX_ROLE` (db role), `OBSERVER_ROLE`, `STREAMLIT_ROLE`
  - Service account mapping: SVC_DBT_DEV/UAT/PROD, SVC_STREAMLIT, SVC_GITHUB_CI, TERRAFORM_SVC
- `snowflake/setup/04_stages.sql` — @MARKETING_SHARED.ARTIFACTS.DBT_ARTIFACTS internal stage with env/latest/ folder structure

**Terraform modules (source of truth after bootstrap):**
- `terraform/versions.tf` — Snowflake-Labs/snowflake provider ~0.98, Terraform ≥ 1.7
- `terraform/main.tf` + `terraform/variables.tf` + `terraform/outputs.tf`
- `terraform/modules/databases/` — `snowflake_database` + `snowflake_schema` resources for all four databases and their schemas
- `terraform/modules/warehouses/` — `snowflake_warehouse` resources (XS/S/M, auto-suspend/resume)
- `terraform/modules/rbac/` — all access roles, functional roles, and `snowflake_grant_privileges_to_role` resources mirroring `03_roles.sql`
- `terraform/modules/stages/` — `snowflake_stage` for DBT_ARTIFACTS; `snowflake_secret` for profiles_yml_dev/uat/prod
- `terraform/environments/dev.tfvars`, `uat.tfvars`, `prod.tfvars`
- S3 backend config (`terraform/environments/<env>.backend`)

**dbt scaffold:**
- `dbt_project.yml` — skeleton with bronze/silver/gold materialisation defaults, on-run-end hooks stubbed, vars block (`observability_enabled`, `drift_baseline_run_id`)
- `packages.yml` — dbt-utils, dbt-expectations, dbt-metricflow
- `profiles.yml.example` — dev/uat/prod targets using env variable substitution

Acceptance criteria:
- Bootstrap SQL scripts execute without error on a fresh Snowflake account
- `terraform init && terraform plan -var-file=environments/dev.tfvars` produces a clean plan with no errors
- `terraform apply` provisions all databases, schemas, warehouses, roles, and stage without drift
- `dbt deps` completes locally against `profiles.yml.example`
- Each service account can authenticate only with its assigned functional role (no privilege escalation)

---

## Phase 2 — Seed Data + MMM Synthetic Generator

**Goal:** All seed files present and loadable via `dbt seed`.

Deliverables:
- `seeds/sample_feedback.csv` — 500 rows, English, columns: feedback_id, customer_id, product, review_text, review_date, language, channel
- `seeds/generate_mmm_spend.py` — reproducible script (fixed random seed) that:
  - Reads Olist orders + order_items CSVs
  - Aggregates weekly_revenue (price + freight by ISO week)
  - Generates tv_spend, paid_search_spend, social_spend, email_spend, display_spend with calibrated adstock, seasonality, and gaussian noise
  - Adds holiday_flag, black_friday_flag, competitor_index, avg_temperature
  - Writes `seeds/olist_mmm_weekly_spend.csv`
- `docs/mmm_synthetic_data_assumptions.md` — calibration choices, adstock parameters, noise levels
- `dbt_project.yml` seed configs — correct column types for both seed files

Acceptance criteria:
- `python seeds/generate_mmm_spend.py` is idempotent (same output on re-run)
- `dbt seed` loads both files without type errors
- olist_mmm_weekly_spend.csv contains ~130 rows (Jan 2016 – Aug 2018 ISO weeks)

---

## Phase 3 — Bronze Layer

**Goal:** All 10 raw Olist tables land as typed, timestamped dbt tables.

Deliverables:
- `models/bronze/brz_olist_orders.sql`
- `models/bronze/brz_olist_order_items.sql`
- `models/bronze/brz_olist_order_reviews.sql`
- `models/bronze/brz_olist_customers.sql`
- `models/bronze/brz_olist_order_payments.sql`
- `models/bronze/brz_olist_products.sql`
- `models/bronze/brz_olist_geolocation.sql`
- `models/bronze/brz_olist_sellers.sql`
- `models/bronze/brz_olist_mql.sql`
- `models/bronze/brz_mmm_weekly_spend.sql`
- `models/bronze/schema.yml` — Tier 1 tests: not_null on all PKs, unique on order_id / review_id, accepted_values for order_status and payment_type
- Source definitions in `sources.yml` pointing at the Snowflake stage / raw schema
- `analyses/row_count_audit.sql` — sanity query across all bronze tables

Acceptance criteria:
- `dbt run --select bronze` completes on dev
- `dbt test --select bronze` — all Tier 1 tests pass
- Row counts match Olist documentation (~100k orders, ~115k order items, ~100k reviews)

---

## Phase 4 — Silver Layer (non-Cortex models)

**Goal:** RFM scoring, customer profiles, and MMM weekly table built and tested — no Cortex dependency.

Deliverables:
- `models/silver/slv_customer_rfm.sql` — recency_days, frequency, monetary_value, rfm_score (1–5 per dimension), preferred_payment, product_diversity, customer_state
- `models/silver/slv_customer_profile.sql` — join RFM + brz_olist_mql, predicted_ltv placeholder (NULL until gold segmentation), churn_risk_score
- `models/silver/slv_mmm_weekly.sql` — aggregate Olist revenue to ISO week, join synthetic spend, add adstock-transformed spend columns
- `models/silver/schema.yml` — Tier 1 + Tier 2 tests: unique on customer_unique_id / iso_week, rfm_score between 1–5, recency_days >= 0, monetary_value > 0, weekly_revenue > 0, all spend columns >= 0
- `tests/singular/assert_rfm_score_distribution.sql` — each bucket (1–5) between 15% and 25%
- `tests/singular/assert_mmm_revenue_positive.sql`

Acceptance criteria:
- `dbt run --select silver` (excluding slv_feedback_enriched) completes on dev
- `dbt test --select slv_customer_rfm slv_customer_profile slv_mmm_weekly` — all pass
- slv_customer_rfm has ~100k rows; slv_mmm_weekly has ~130 rows

---

## Phase 5 — Cortex Integration (slv_feedback_enriched)

**Goal:** Review text translated, sentiment scored, themes extracted — all via Snowflake Cortex.

Deliverables:
- `macros/cortex_sentiment.sql` — reusable macro wrapping AI_SENTIMENT with TRY_CAST error handling and null-safe fallbacks
- `models/silver/slv_feedback_enriched.sql` — incremental (merge on review_id) applying in sequence:
  1. `SNOWFLAKE.CORTEX.TRANSLATE(review_comment_message, '', 'en')` → translated_review
  2. `AI_SENTIMENT(translated_review, [...])` → sentiment JSON parsed into sentiment_score, sentiment_label, aspect scores
  3. `SNOWFLAKE.CORTEX.COMPLETE('claude-sonnet-4-20250514', ...)` → theme_json, key_phrase
  4. churn_risk_flag = 1 WHERE review_score <= 2
- `models/silver/schema.yml` additions — Tier 1 + Tier 3 tests on all Cortex output columns
- `tests/generic/assert_cortex_not_null.sql` — parameterised, fails on NULL / empty / literal 'null'
- `tests/generic/assert_sentiment_range.sql` — sentiment_score between -1.0 and 1.0
- `tests/singular/assert_no_untranslated.sql` — <2% of rows contain Portuguese stopwords

Acceptance criteria:
- Full Olist reviews (~100k rows) processed on dev without Cortex errors
- All Tier 3 tests pass
- translated_review column contains English text for Portuguese source rows

---

## Phase 6 — Gold Layer

**Goal:** All five gold mart tables built with full business logic.

Deliverables:
- `models/gold/mrt_customer_segments.sql` — K-Means via Snowpark ML on slv_customer_profile; segment label named via CORTEX.COMPLETE
- `models/gold/mrt_sentiment_by_segment.sql` — aggregate sentiment + theme by segment_id, product_category, iso_week
- `models/gold/mrt_mmm_attribution.sql` — Ridge regression via Snowpark ML on slv_mmm_weekly; output channel contributions + ROI per iso_week + channel
- `models/gold/mrt_funnel_conversion.sql` — MQL → order conversion rates by acquisition channel from brz_olist_mql
- `models/gold/mrt_nba_actions.sql` — CORTEX.COMPLETE per segment → action, channel, message_draft
- `models/gold/schema.yml` — Tier 1 + Tier 2 tests: row count bounds on all gold models, composite PK uniqueness, ROI sanity check, segment coverage
- `tests/generic/assert_no_duplicate_pk.sql` — parameterised composite PK checker
- `tests/generic/assert_row_count_in_range.sql` — parameterised min/max row count
- `tests/singular/assert_segment_coverage.sql` — zero orphaned customers

Acceptance criteria:
- `dbt run --select gold` completes on dev
- mrt_customer_segments has 5–10 distinct segments; each segment ≥ 1% of customers
- mrt_mmm_attribution has ~130 × 5 = ~650 rows; no NULL ROI values

---

## Phase 7 — Testing Framework (Tiers 3 + 4)

**Goal:** Complete four-tier test suite wired up across all models.

Deliverables:
- `tests/generic/assert_column_drift.sql` — parameterised: column, baseline_mean, baseline_stddev, z_threshold; applied to sentiment_score, weekly_revenue, churn_risk_score, tv_spend
- `tests/generic/assert_metric_anomaly.sql` — parameterised: metric_name, lookback_weeks, threshold_pct; applied to total_reviews/week, negative_review_rate, avg_sentiment_score, weekly_revenue
- `tests/singular/assert_cortex_latency.sql` — queries CORTEX_USAGE_LOG, fails if avg latency > 5s
- All `schema.yml` files updated with dbt_expectations tests:
  - `expect_column_stdev_to_be_between` on sentiment_score
  - `expect_column_kl_divergence_to_be_less_than` on sentiment_label distribution
  - `expect_column_proportion_of_unique_values_to_be_between` on theme and segment_label
  - `expect_column_mean_to_be_between` on sentiment_score
- `docs/testing_strategy.md` — tier descriptions, full test inventory table, environment matrix, how to add a test, how to establish/update drift baselines

Acceptance criteria:
- `dbt test` on full Olist data: Tier 1 and Tier 2 all pass; Tier 3 all pass; Tier 4 runs in UAT without false positives
- Test inventory table in testing_strategy.md covers every test file

---

## Phase 8 — Observability Framework + Cost Report

**Goal:** All observability tables populated after every dbt run; cost report running independently; 6-page Streamlit dashboard live in Snowflake.

Deliverables:

**Pipeline observability tables and hooks:**
- `snowflake/observability/01_observability_schema.sql` — CREATE SCHEMA + five tables: PIPELINE_RUN_LOG, MODEL_HEALTH_LOG, CORTEX_USAGE_LOG, DATA_QUALITY_LOG, COST_DAILY; full column definitions and inline comments
- `snowflake/observability/02_pipeline_run_log.sql` through `05_data_quality_log.sql` — INSERT/MERGE stored procedures for each pipeline observability table
- `macros/observability_hooks.sql` — all four macros: `log_model_health()`, `log_cortex_usage()`, `log_test_results()`, `trigger_alert()`; all guarded by `observability_enabled` var
- `dbt_project.yml` updated — post-hook `{{ log_model_health() }}` on every model; on-run-end: `CALL UPLOAD_DBT_ARTIFACTS(...)` + `{{ log_test_results() }}`
- `snowflake/observability/06_anomaly_detection_task.sql` — TASK_ANOMALY_DETECTION with four checks: row count variance (>20%), Cortex cost spike (>50%), sentiment drift (delta >0.15), null rate increase (>5pp)

**Cost & resource consumption report:**
- `snowflake/observability/08_cost_daily.sql`:
  - `COST_DAILY` table (report_date, env, component, resource_name, credits_used, estimated_usd_cost, query_count, storage_tb, measured_at)
  - `POPULATE_COST_DAILY` stored procedure merging four sources via MERGE ON (report_date, env, component, resource_name):
    - `SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY` → warehouse_compute component
    - `SNOWFLAKE.ACCOUNT_USAGE.SERVERLESS_TASK_HISTORY` → serverless_tasks component
    - `MARKETING_SHARED.OBSERVABILITY.CORTEX_COST_DAILY` → cortex_ai component
    - `SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE` → storage component
  - `TASK_COST_REPORT` — independent daily task, cron 06:00 UTC, no AFTER dependency on pipeline DAG; includes MTD budget alert logic calling `trigger_alert` on breach
  - `GRANT SELECT ON COST_DAILY TO ROLE OBSERVER_ROLE`

**Streamlit dashboard:**
- `snowflake/observability/07_observability_streamlit.sql` — full 6-page Streamlit app:
  - Page 1: Pipeline Overview — run history, success rate trend, last run summary card
  - Page 2: Model Health — row count trends, incremental rows added, execution time heatmap
  - Page 3: Cortex Usage & Cost — credit consumption by function, latency trend, null output rate, monthly forecast
  - Page 4: Data Quality — test pass/fail by tier, failing tests table, drift score trends, open alerts
  - Page 5: Lineage & Coverage — dbt lineage from manifest.json, test coverage % per model, uncovered model highlights
  - Page 6: Cost & Credits — daily stacked bar by component, cost by env, credits/run trend, cost efficiency metric (credits per 1k reviews), monthly forecast, configurable unit price widget, budget alert threshold
  - Charts via Altair; deployed via `CREATE STREAMLIT ... FROM @stage`
- `analyses/cortex_cost_audit.sql` — ad-hoc query over METERING_DAILY_HISTORY

Acceptance criteria:
- After `dbt run + dbt test` on dev: all five observability tables have rows
- Streamlit app deploys without error and all 6 pages render data
- TASK_ANOMALY_DETECTION executes without SQL errors on a populated observability schema
- `EXECUTE TASK TASK_COST_REPORT` populates COST_DAILY from all four sources; runs independently of the pipeline DAG
- MTD budget alert fires (test with a low threshold) and calls `trigger_alert`

---

## Phase 9 — Snowflake Orchestration (Task DAGs + dbt Stored Procedure)

**Goal:** End-to-end pipeline runs on a schedule inside Snowflake with no external runner.

Deliverables:
- `snowflake/setup/05_dbt_stored_procedure.sql` — Python stored procedure that:
  - Installs dbt-core + dbt-snowflake at runtime (or uses pre-built container image)
  - Injects profiles.yml from Snowflake Secrets
  - Runs the requested dbt command (`run`, `test`, `seed`, `full`)
  - Parses run_results.json → inserts to PIPELINE_RUN_LOG and DATA_QUALITY_LOG on success or failure
  - Signature: `CALL RUN_DBT(target => 'prod', command => 'run', select => 'tag:daily')`
- `snowflake/container/dbt_runner.yaml` — Container Services spec for Option A cold-start optimisation
- `snowflake/tasks/task_dag_prod.sql` — 5-task DAG: TASK_ROOT (cron 04:00 UTC) → TASK_DBT_RUN → TASK_DBT_TEST → TASK_PUBLISH_ARTIFACTS → TASK_ANOMALY_DETECTION; error handler inserts FAILED to PIPELINE_RUN_LOG
- `snowflake/tasks/task_dag_uat.sql` — same DAG, cron 02:00 UTC, all 5 tasks
- `snowflake/tasks/task_dag_dev.sql` — manual/on-demand only, 3 tasks (no ANOMALY_DETECTION, no schedule)
- `snowflake/cortex/cortex_pipeline.sql` — standalone Cortex function test harness for validating TRANSLATE + AI_SENTIMENT + COMPLETE against a 10-row sample

Acceptance criteria:
- Manual `EXECUTE TASK TASK_ROOT` on dev environment completes full dbt run → test → artifact upload cycle
- PIPELINE_RUN_LOG row created with correct status, duration, models_run, tests_run
- prod and uat Task DAGs resume without error; prod scheduled task visible in TASK_HISTORY

---

## Phase 10 — CI/CD (GitHub Actions)

**Goal:** Three GitHub Actions workflows gate every PR and push with Snowflake-backed test results.

Deliverables:
- `.github/workflows/ci_dev.yml` — triggers on PR to dev; steps: checkout → dbt compile (local, no Snowflake) → SQLFluff lint on changed models → trigger Snowflake Task on dev → poll status → post tier summary as PR comment ("Tier 1: 12/12 ✅  Tier 2: 8/8 ✅  Tier 3: 4/4 ✅  Tier 4: skipped")
- `.github/workflows/ci_uat.yml` — triggers on push to uat; full run + test; fail workflow if critical/high DATA_QUALITY_LOG entries; post results as job summary
- `.github/workflows/ci_prod.yml` — triggers on push to main; full run + test; post formatted run summary; on critical failure: fail workflow + post to Slack
- All workflows pin `Snowflake-Labs/snowflake-cli-action@v1` with `cli-version: "3.x.x"` and authenticate via key-pair (GitHub Secrets: SNOWFLAKE_PRIVATE_KEY, SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER)
- Slim CI dev command documented: `dbt test --select state:modified+ --defer --state @DBT_ARTIFACTS/uat/latest/`

Acceptance criteria:
- Opening a test PR to dev triggers ci_dev.yml and posts a tier summary comment
- Pushing to uat triggers ci_uat.yml and surfaces DATA_QUALITY_LOG failures if any exist
- All three workflows authenticate to Snowflake via key-pair without interactive prompts

---

## Phase 11 — MetricFlow Semantic Layer

**Goal:** Six business metrics queryable via MetricFlow / dbt Semantic Layer.

Deliverables:
- `semantic_models/sem_customer_feedback.yml` — entities: customer_id, segment_id; dimensions: review_date (time primary), product_category, segment_label, sentiment_label, theme; measures: total_reviews, avg_sentiment_score, negative_review_count, churn_risk_count
- `semantic_models/sem_customer_segments.yml` — entities: customer_unique_id; dimensions: segment_label, customer_state, preferred_payment; measures: customer_count, avg_monetary_value, avg_frequency, avg_recency_days, avg_predicted_ltv
- `semantic_models/sem_mmm_attribution.yml` — entities: iso_week; dimensions: channel, is_holiday, is_black_friday; measures: total_spend, total_attributed_revenue, avg_roi, avg_marginal_roi
- `semantic_models/metrics.yml` — six metrics: sentiment_score (simple), negative_review_rate (ratio), sentiment_trend (cumulative 4-week), customer_churn_rate (ratio), channel_roi (simple), revenue_per_segment (simple)

Acceptance criteria:
- `dbt parse` completes without semantic model errors
- `dbt sl query --metrics sentiment_score --group-by review_date__week` returns data
- All six metrics resolve without ambiguous join path errors

---

## Phase 12 — Documentation & Polish

**Goal:** Complete docs suite; architecture diagram; project production-ready.

Deliverables:
- `docs/architecture.md` — ASCII pipeline diagram: Olist CSVs → Snowpipe → Bronze → Silver (Cortex) → Gold (Segments + MMM + NBA) → MetricFlow → Task DAG → Artifact Stage → Observability Schema → Streamlit Dashboard ← GitHub Actions CI/CD; decision matrix for Container Services (Option A) vs Notebook (Option B): cost / latency / maintenance / env parity
- `docs/testing_strategy.md` — finalised: tier descriptions + rationale, full test inventory table (test name, model, tier, severity), environment matrix, how to add a test, how to update drift baselines
- `docs/mmm_synthetic_data_assumptions.md` — finalised: all calibration choices, adstock parameters, seasonality adjustments, noise levels, reproducibility notes
- `macros/generate_schema_name.sql` — dev: developer-namespaced (DBT_<user>_<layer>); uat/prod: shared layer schemas
- Final review: all schema.yml descriptions populated for every model and column at gold layer; SQLFluff passes clean on all models; `dbt docs generate` produces valid catalog

Acceptance criteria:
- `dbt docs generate` completes without warnings
- SQLFluff lint returns zero errors across all models
- README or docs/architecture.md contains a working quickstart (clone → setup Snowflake → run pipeline)

---

## Delivery Summary

| Phase | Focus | Key Output |
|---|---|---|
| 1 | Repo + Terraform Infra + RBAC | Terraform modules, tiered access roles, dbt scaffold |
| 2 | Seed Data | MMM generator, sample_feedback.csv |
| 3 | Bronze Layer | 10 typed source models + Tier 1 tests |
| 4 | Silver (non-Cortex) | RFM, profiles, MMM weekly |
| 5 | Cortex Integration | slv_feedback_enriched + cortex macro |
| 6 | Gold Layer | 5 mart models + Snowpark ML |
| 7 | Test Framework | Tiers 3+4 custom tests |
| 8 | Observability + Cost Report | 5 log tables + COST_DAILY + TASK_COST_REPORT + 6-page Streamlit |
| 9 | Orchestration | Task DAGs + dbt stored procedure |
| 10 | CI/CD | 3 GitHub Actions workflows + terraform plan step |
| 11 | Semantic Layer | MetricFlow models + 6 metrics |
| 12 | Docs & Polish | Architecture diagram, final cleanup |

Each phase can be delivered as a standalone PR. Phases 1–3 are the critical path — nothing else can start without them. Phases 4–6 are sequential on each other. Phases 7–12 can be parallelised once Phase 6 is complete.

> **Note on Terraform in CI/CD:** Phase 10 should add `terraform plan` as a pre-step in each GitHub Actions workflow, running before the dbt compile step. `terraform apply` only runs on merge to the target branch. The SNOWFLAKE_PRIVATE_KEY secret used by the Snowflake CLI is reused by the Terraform Snowflake provider; no additional secrets are required.
