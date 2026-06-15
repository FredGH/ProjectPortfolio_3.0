Build a production-grade data engineering project with the following specifications:

## PROJECT OVERVIEW
A Snowflake Cortex marketing intelligence pipeline for customer voice analytics 
(sentiment analysis, theme classification, next-best-action generation) using:
- Snowflake as the data platform (all compute and storage)
- dbt Core + MetricFlow running INSIDE Snowflake (via Snowflake-managed dbt)
- dbt artifacts stored in and shared across dev/uat/prod via a dedicated 
  Snowflake internal stage
- Snowflake Task DAGs as the scheduler (no external orchestrator)
- GitHub Actions for CI/CD across dev/uat/prod environments
- GitFlow branching strategy
- A comprehensive unit testing + observability framework (see dedicated section)

---

## DATASET STRATEGY

### Primary Source: Olist Brazilian E-Commerce (Kaggle, free)
The entire project is grounded in the Olist dataset — a real transactional 
dataset from a Brazilian e-commerce marketplace covering Jan 2016 – Aug 2018.
Download from: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
Download companion: https://www.kaggle.com/datasets/olistbr/marketing-funnel-olist

It ships as 9 CSV files that form the bronze layer source tables:

```
olist_orders_dataset.csv         → order timeline, delivery status, RFM base
olist_order_items_dataset.csv    → products, prices, freight → monetary value
olist_order_reviews_dataset.csv  → review text (Portuguese) + star rating
                                   → primary input for Cortex NLP workload
olist_customers_dataset.csv      → customer ID, city, state → segmentation
olist_order_payments_dataset.csv → payment type, instalments → profiling
olist_products_dataset.csv       → product categories → category affinity
olist_geolocation_dataset.csv    → lat/lng per zip code → geographic clustering
olist_sellers_dataset.csv        → seller geography → supply-side context
olist_mql_dataset.csv            → 8,000 MQLs with lead source / acquisition
                                   channel → marketing funnel conversion
```

### MMM Layer: Synthetic Weekly Media Spend (generated, calibrated to Olist)
Rather than using an unrelated external MMM dataset, generate a synthetic 
weekly media spend table that uses Olist's actual weekly order revenue as 
the MMM dependent variable. This keeps the entire project coherent — the 
MMM model genuinely attempts to explain real Olist revenue patterns.

Generate the synthetic spend table as follows:

1. Aggregate Olist weekly revenue:
   - Group olist_orders + olist_order_items by ISO week
   - Sum order value (price + freight) → weekly_revenue (BRL)
   - This becomes the MMM dependent variable

2. Generate synthetic weekly media spend columns calibrated to that revenue:
   - tv_spend:           base 15% of weekly revenue, high variance, 
                         strong adstock decay (geometric, theta=0.7)
   - paid_search_spend:  base 10% of weekly revenue, low variance,
                         immediate effect (adstock theta=0.2)
   - social_spend:       base 8% of weekly revenue, medium variance,
                         medium adstock (theta=0.4)
   - email_spend:        base 3% of weekly revenue, low variance,
                         immediate effect (theta=0.15)
   - display_spend:      base 5% of weekly revenue, high variance,
                         slow decay (theta=0.6)
   - Add realistic seasonality: Q4 (Nov-Dec) spend +40%, 
     Brazilian holidays (Carnival Feb, Black Friday Nov)
   - Add controlled noise: gaussian noise scaled to 8% of spend per channel

3. Add MMM control variables:
   - holiday_flag:       1 on Brazilian public holidays
   - black_friday_flag:  1 on Black Friday week
   - competitor_index:   synthetic index (random walk, mean=100, std=5)
   - avg_temperature:    seasonal sinusoidal pattern (Brazil climate)

4. Store the generation script in:
   seeds/generate_mmm_spend.py
   Output to: seeds/olist_mmm_weekly_spend.csv

5. Document the calibration assumptions in:
   docs/mmm_synthetic_data_assumptions.md

### Seed Data for Unit Testing (500 rows, English)
Keep a small synthetic seed file for pipeline unit tests and CI:

```
seeds/sample_feedback.csv
columns: feedback_id, customer_id, product, review_text, review_date,
         language, channel
purpose: fast CI runs without loading the full Olist dataset
```

---

## REPO STRUCTURE

```
cortex-marketing-intelligence/
├── .github/
│   └── workflows/
│       ├── ci_dev.yml
│       ├── ci_uat.yml
│       └── ci_prod.yml
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml.example
│   ├── packages.yml
│   ├── models/
│   │   ├── bronze/
│   │   │   ├── brz_olist_orders.sql
│   │   │   ├── brz_olist_order_items.sql
│   │   │   ├── brz_olist_order_reviews.sql
│   │   │   ├── brz_olist_customers.sql
│   │   │   ├── brz_olist_order_payments.sql
│   │   │   ├── brz_olist_products.sql
│   │   │   ├── brz_olist_geolocation.sql
│   │   │   ├── brz_olist_sellers.sql
│   │   │   ├── brz_olist_mql.sql
│   │   │   └── brz_mmm_weekly_spend.sql
│   │   ├── silver/
│   │   │   ├── slv_feedback_enriched.sql
│   │   │   ├── slv_customer_profile.sql
│   │   │   ├── slv_customer_rfm.sql
│   │   │   └── slv_mmm_weekly.sql
│   │   └── gold/
│   │       ├── mrt_customer_segments.sql
│   │       ├── mrt_sentiment_by_segment.sql
│   │       ├── mrt_mmm_attribution.sql
│   │       ├── mrt_funnel_conversion.sql
│   │       └── mrt_nba_actions.sql
│   ├── tests/
│   │   ├── generic/
│   │   │   ├── assert_sentiment_range.sql
│   │   │   ├── assert_cortex_not_null.sql
│   │   │   ├── assert_row_count_in_range.sql
│   │   │   ├── assert_no_duplicate_pk.sql
│   │   │   ├── assert_column_drift.sql
│   │   │   └── assert_metric_anomaly.sql
│   │   └── singular/
│   │       ├── assert_no_untranslated.sql
│   │       ├── assert_mmm_revenue_positive.sql
│   │       ├── assert_segment_coverage.sql
│   │       ├── assert_rfm_score_distribution.sql
│   │       └── assert_cortex_latency.sql
│   ├── macros/
│   │   ├── cortex_sentiment.sql
│   │   ├── generate_schema_name.sql
│   │   └── observability_hooks.sql
│   ├── semantic_models/
│   │   ├── sem_customer_feedback.yml
│   │   ├── sem_customer_segments.yml
│   │   ├── sem_mmm_attribution.yml
│   │   └── metrics.yml
│   └── analyses/
│       ├── row_count_audit.sql
│       └── cortex_cost_audit.sql
├── seeds/
│   ├── sample_feedback.csv
│   ├── olist_mmm_weekly_spend.csv
│   └── generate_mmm_spend.py
├── snowflake/
│   ├── setup/
│   │   ├── 01_databases.sql
│   │   ├── 02_warehouses.sql
│   │   ├── 03_roles.sql
│   │   ├── 04_stages.sql
│   │   └── 05_dbt_stored_procedure.sql
│   ├── cortex/
│   │   └── cortex_pipeline.sql
│   ├── tasks/
│   │   ├── task_dag_dev.sql
│   │   ├── task_dag_uat.sql
│   │   └── task_dag_prod.sql
│   ├── observability/
│   │   ├── 01_observability_schema.sql
│   │   ├── 02_pipeline_run_log.sql
│   │   ├── 03_model_row_counts.sql
│   │   ├── 04_cortex_usage_log.sql
│   │   ├── 05_data_quality_log.sql
│   │   ├── 06_anomaly_detection_task.sql
│   │   ├── 07_observability_streamlit.sql
│   │   └── 08_cost_daily.sql
│   └── container/
│       └── dbt_runner.yaml
├── terraform/
│   ├── versions.tf
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── modules/
│   │   ├── databases/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   │   ├── warehouses/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   │   ├── rbac/
│   │   │   ├── main.tf
│   │   │   ├── variables.tf
│   │   │   └── outputs.tf
│   │   └── stages/
│   │       ├── main.tf
│   │       ├── variables.tf
│   │       └── outputs.tf
│   └── environments/
│       ├── dev.tfvars
│       ├── uat.tfvars
│       └── prod.tfvars
└── docs/
    ├── architecture.md
    ├── testing_strategy.md
    └── mmm_synthetic_data_assumptions.md
```

---

## UNIT TESTING FRAMEWORK

### Philosophy
Testing is organised into four tiers, applied progressively across the
pipeline. Every model must pass all applicable tier tests before promotion
from dev → uat → prod.

  Tier 1 — Schema & Integrity    : structural correctness, no nulls, no dupes
  Tier 2 — Business Rules        : domain constraints, valid ranges, logic
  Tier 3 — Cortex Output Quality : AI function output validation
  Tier 4 — Statistical / Drift   : distribution checks, anomaly detection

---

### Tier 1 — Schema & Integrity Tests

Applied to ALL models via schema.yml.

#### Standard dbt tests (apply to every model):
- not_null:
    bronze: all raw key columns (order_id, customer_id, review_id, etc.)
    silver: all derived key columns + all Cortex output columns
    gold:   all business keys + segment_id + channel + iso_week
- unique:
    brz_olist_orders:        order_id
    brz_olist_order_reviews: review_id
    slv_customer_rfm:        customer_unique_id
    slv_mmm_weekly:          iso_week
    mrt_customer_segments:   customer_unique_id
    mrt_mmm_attribution:     iso_week + channel (composite)
- relationships (referential integrity):
    slv_feedback_enriched.order_id       → brz_olist_orders.order_id
    slv_customer_rfm.customer_unique_id  → brz_olist_customers.customer_unique_id
    mrt_customer_segments.customer_unique_id →
      slv_customer_profile.customer_unique_id
    mrt_sentiment_by_segment.segment_id  → mrt_customer_segments.segment_id
- accepted_values:
    sentiment_label:   ['positive','negative','neutral','mixed']
    theme:             ['Product Quality','Delivery','Seller Service',
                        'Price','Packaging','Other']
    priority:          ['HIGH','MEDIUM','LOW']
    order_status:      ['delivered','shipped','canceled','unavailable',
                        'invoiced','processing','created','approved']
    payment_type:      ['credit_card','boleto','voucher','debit_card']

#### Custom generic test — assert_no_duplicate_pk.sql
Parameterised test that counts composite PKs and fails if any combination
appears more than once. Used for models without a single surrogate key:
  - mrt_mmm_attribution: (iso_week, channel)
  - mrt_sentiment_by_segment: (segment_id, product_category, iso_week)

#### Custom generic test — assert_row_count_in_range.sql
Parameterised: min_rows, max_rows.
Applied to every gold model to catch silent truncation or explosion:
  - mrt_customer_segments:    min=90000,  max=110000  (expect ~100k customers)
  - mrt_sentiment_by_segment: min=80000,  max=130000
  - slv_mmm_weekly:           min=100,    max=150     (expect ~130 weeks)
  - mrt_mmm_attribution:      min=500,    max=1000    (weeks × channels)

---

### Tier 2 — Business Rules Tests

#### Schema tests via dbt_utils and dbt_expectations:
- dbt_utils.expression_is_true:
    sentiment_score between -1 and 1
    weekly_revenue > 0
    recency_days >= 0
    frequency >= 1
    monetary_value > 0
    review_score between 1 and 5
    all spend columns >= 0
    roi is not null and roi > -10   (sanity: ROI not catastrophically negative)
- dbt_expectations.expect_column_values_to_be_between:
    rfm_score:          min=1,   max=5
    churn_risk_score:   min=0.0, max=1.0
    predicted_ltv:      min=0.0, max=50000.0  (BRL upper bound)
- dbt_expectations.expect_column_proportion_of_unique_values_to_be_between:
    segment_label: min=0.01  (each segment must have ≥1% of customers)
- dbt_expectations.expect_table_row_count_to_be_between:
    Applied to all gold models (same bounds as Tier 1 row count tests,
    expressed as dbt_expectations alternative)

#### Singular test — assert_rfm_score_distribution.sql
Verifies that RFM scores are not degenerate:
- Each score bucket (1–5) must contain between 15% and 25% of customers
- Fails if any single bucket contains >40% (model collapse indicator)

#### Singular test — assert_segment_coverage.sql
Every customer_unique_id in slv_customer_profile must appear in
mrt_customer_segments. Zero orphaned customers allowed in prod.

#### Singular test — assert_mmm_revenue_positive.sql
No row in slv_mmm_weekly may have weekly_revenue <= 0.

---

### Tier 3 — Cortex Output Quality Tests

These tests validate that AI function outputs meet quality thresholds,
not just structural validity.

#### Custom generic test — assert_cortex_not_null.sql
Parameterised by column name. Fails if any Cortex output column
(translated_review, sentiment_json, theme_json, nba_action_text)
contains NULL, empty string, or the literal string 'null'.

#### Custom generic test — assert_sentiment_range.sql
Fails if any sentiment_score value falls outside [-1.0, 1.0].
Applied to: slv_feedback_enriched, mrt_sentiment_by_segment.

#### Singular test — assert_no_untranslated.sql
After CORTEX.TRANSLATE pass, translated_review must not contain
high-frequency Portuguese stopwords:
  ('não', 'produto', 'entrega', 'recebi', 'comprei', 'chegou', 'muito')
Fails if >2% of rows still contain any of these terms.
Threshold is 2% (not 0%) to tolerate proper nouns and brand names.

#### Singular test — assert_cortex_latency.sql
Queries CSTA_MARKETING_SHARED.OBSERVABILITY.CORTEX_USAGE_LOG and fails
if avg Cortex processing time per review exceeds 5 seconds in the
last run. Acts as a performance regression gate.

#### Cortex output distribution check (via dbt_expectations):
- dbt_expectations.expect_column_proportion_of_unique_values_to_be_between
    on theme column: min=0.05 per theme
    (no single theme should dominate >60% of reviews — model bias indicator)
- dbt_expectations.expect_column_mean_to_be_between
    on sentiment_score: min=-0.2, max=0.5
    (Olist data is known to skew positive; extreme values signal model error)

---

### Tier 4 — Statistical / Drift Tests

These tests catch data drift, distribution shift, and anomalies that
schema tests alone cannot detect. Run in UAT and prod only (skip in dev
to keep CI fast).

#### Custom generic test — assert_column_drift.sql
Parameterised: column, baseline_mean, baseline_stddev, z_threshold (default 3).
Computes current run mean and stddev, fails if z-score > threshold.
Applied to:
  - sentiment_score:    baseline computed from first full Olist load
  - weekly_revenue:     baseline computed from historical avg
  - churn_risk_score:   baseline from first segmentation run
  - tv_spend:           sanity check on synthetic generation

#### Custom generic test — assert_metric_anomaly.sql
Parameterised: metric_name, lookback_weeks (default 4), threshold_pct (default 20).
Computes rolling average of a metric over lookback_weeks and fails if the
current week deviates by more than threshold_pct%.
Applied to:
  - total_reviews per week      (detects ingestion gaps)
  - negative_review_rate        (detects Cortex model drift)
  - avg_sentiment_score         (detects translation quality drift)
  - weekly_revenue              (detects data completeness issues)

#### dbt_expectations distribution tests:
- dbt_expectations.expect_column_stdev_to_be_between
    sentiment_score:  min_value=0.1, max_value=0.8
    (very low stdev = degenerate output; very high = noise)
- dbt_expectations.expect_column_kl_divergence_to_be_less_than
    sentiment_label distribution vs prior run: threshold=0.1
    (KL divergence detects label distribution shift across runs)

---

### Test Execution Strategy per Environment

| Test Tier | dev (PR) | uat | prod |
|---|---|---|---|
| Tier 1 Schema & Integrity | ✅ all | ✅ all | ✅ all |
| Tier 2 Business Rules | ✅ modified models only (slim CI) | ✅ all | ✅ all |
| Tier 3 Cortex Quality | ✅ on sample_feedback.csv seed only | ✅ full Olist | ✅ full Olist |
| Tier 4 Drift / Statistical | ❌ skip | ✅ all | ✅ all |

Slim CI in dev uses:
  dbt test --select state:modified+ --defer --state @CSTA_DBT_ARTIFACTS/uat/latest/

In uat and prod, run all tests:
  dbt test --target uat
  dbt test --target prod

---

## OBSERVABILITY FRAMEWORK

### Philosophy
Observability is built natively inside Snowflake — no external tools
(no Monte Carlo, no Elementary, no Great Expectations server).
All observability data lives in CSTA_MARKETING_SHARED.OBSERVABILITY schema,
accessible to all environments and queryable via SQL or Streamlit.

Four observability domains:
  1. Pipeline Runs       — job-level execution tracking
  2. Model Health        — row counts, durations, materialisation status
  3. Cortex Usage        — AI function call volume, latency, cost
  4. Data Quality        — test results, anomaly flags, drift scores

---

### 1. Pipeline Run Log

Table: CSTA_MARKETING_SHARED.OBSERVABILITY.PIPELINE_RUN_LOG

Columns:
  run_id            STRING      (UUID, generated at Task DAG start)
  env               STRING      ('dev','uat','prod')
  trigger_source    STRING      ('scheduled_task','github_actions','manual')
  dbt_command       STRING      ('run','test','seed','full')
  started_at        TIMESTAMP_TZ
  completed_at      TIMESTAMP_TZ
  duration_seconds  NUMBER
  status            STRING      ('running','success','failure','partial')
  models_run        NUMBER
  models_errored    NUMBER
  tests_run         NUMBER
  tests_failed      NUMBER
  git_sha           STRING      (commit SHA injected by GitHub Actions)
  dbt_version       STRING
  error_message     STRING      (NULL on success)

Populated by:
- UPLOAD_CSTA_DBT_ARTIFACTS stored procedure (reads dbt run_results.json)
- Task DAG error handler on failure
- GitHub Actions CI via Snowflake CLI on completion

---

### 2. Model Health Log

Table: CSTA_MARKETING_SHARED.OBSERVABILITY.MODEL_HEALTH_LOG

Columns:
  run_id            STRING      (FK → PIPELINE_RUN_LOG)
  model_name        STRING
  layer             STRING      ('bronze','silver','gold')
  env               STRING
  execution_time_s  NUMBER
  rows_written      NUMBER
  rows_before       NUMBER      (for incremental models)
  rows_added        NUMBER      (rows_written - rows_before)
  bytes_written     NUMBER
  materialisation   STRING      ('table','incremental','view')
  status            STRING      ('success','error','skipped')
  error_message     STRING
  measured_at       TIMESTAMP_TZ

Populated by: dbt on-run-end hook via macro observability_hooks.sql
The hook queries INFORMATION_SCHEMA.TABLE_STORAGE_METRICS after each
model completes and inserts a row into MODEL_HEALTH_LOG.

Alert rule: if rows_added = 0 for any incremental silver/gold model
on a scheduled prod run → insert anomaly flag to DATA_QUALITY_LOG.

---

### 3. Cortex Usage Log

Table: CSTA_MARKETING_SHARED.OBSERVABILITY.CORTEX_USAGE_LOG

Columns:
  run_id              STRING
  model_name          STRING      (dbt model that called the function)
  cortex_function     STRING      ('TRANSLATE','SENTIMENT','AI_SENTIMENT',
                                   'COMPLETE','FORECAST')
  llm_model           STRING      ('claude-sonnet-4-20250514', etc.)
  rows_processed      NUMBER
  tokens_consumed     NUMBER      (from COUNT_TOKENS where applicable)
  avg_latency_ms      NUMBER
  total_latency_ms    NUMBER
  null_output_count   NUMBER      (Cortex returned NULL)
  error_count         NUMBER
  estimated_credits   NUMBER      (from METERING_DAILY_HISTORY)
  measured_at         TIMESTAMP_TZ

Populated by: observability_hooks.sql macro wrapping each Cortex call.
The macro records pre/post timestamps and row counts around every
Cortex function invocation in silver models.

Additional Snowflake-native cost tracking:
- Query SNOWFLAKE.ORGANIZATION_USAGE.METERING_DAILY_HISTORY
  filtered for AI_SERVICES service type after each run
- Store daily Cortex credit consumption in:
  CSTA_MARKETING_SHARED.OBSERVABILITY.CORTEX_COST_DAILY
  (date, env, credits_used, estimated_usd_cost)

---

### 4. Data Quality Log

Table: CSTA_MARKETING_SHARED.OBSERVABILITY.DATA_QUALITY_LOG

Columns:
  run_id            STRING
  test_name         STRING
  model_name        STRING
  column_name       STRING
  test_tier         NUMBER      (1, 2, 3, or 4)
  status            STRING      ('pass','fail','warn','error')
  failures          NUMBER      (rows failing the test)
  failure_pct       NUMBER      (failures / total rows)
  expected_value    STRING      (for range/drift tests: threshold)
  actual_value      STRING      (for range/drift tests: observed value)
  severity          STRING      ('critical','high','medium','low')
  env               STRING
  measured_at       TIMESTAMP_TZ

Populated by: dbt on-run-end hook that parses run_results.json
and inserts one row per test result after every dbt test invocation.

Severity classification:
  critical: Tier 1 failures (nulls in PKs, broken FK relationships)
  high:     Tier 2 failures (business rule violations, row count anomalies)
  medium:   Tier 3 failures (Cortex output quality degradation)
  low:      Tier 4 warnings (drift detected, within warn threshold)

Alert rules (implemented as Snowflake Tasks running after TASK_CSTA_DBT_TEST):
  - Any critical failure → SYSTEM$SEND_EMAIL immediately
  - >3 high failures in one run → SYSTEM$SEND_EMAIL
  - Any medium failure in prod → insert to alert queue, send digest daily

---

### 5. Anomaly Detection Task

File: snowflake/observability/06_anomaly_detection_task.sql

A dedicated Snowflake Task running after TASK_PUBLISH_ARTIFACTS
that performs post-run statistical checks not covered by dbt tests:

  TASK_ANOMALY_DETECTION
    AFTER TASK_PUBLISH_ARTIFACTS
  AS
    CALL RUN_ANOMALY_CHECKS();

The RUN_ANOMALY_CHECKS stored procedure:
1. Row count variance check:
   Compare each model's rows_written to 4-week rolling average.
   Flag if deviation > 20%.

2. Cortex cost spike check:
   Compare today's estimated_credits to 4-week average.
   Flag if >50% increase (unexpected volume or runaway loop).

3. Sentiment drift check:
   Compare this run's avg_sentiment_score to prior 4 runs.
   Flag if absolute delta > 0.15 (translation or model change).

4. Null rate trend check:
   Compare null_output_count / rows_processed to prior 4 runs.
   Flag if null rate increased by >5 percentage points.

All flags are written to DATA_QUALITY_LOG with severity = 'medium'
and trigger the daily digest email.

---

### 6. Observability Streamlit Dashboard

File: snowflake/observability/07_observability_streamlit.sql

A Snowflake Streamlit app deployed within Snowflake (no external hosting)
that provides a real-time observability UI. Pages:

  Page 1 — Pipeline Overview
    - Run history table (last 30 runs) with status, duration, model count
    - Success rate trend (last 90 days)
    - Last run summary card: models run, tests passed/failed, duration

  Page 2 — Model Health
    - Row count trends per model (line chart, last 30 runs)
    - Incremental rows added per run (bar chart)
    - Models with zero-row warnings highlighted in red
    - Execution time heatmap (model × run)

  Page 3 — Cortex Usage & Cost
    - Daily credit consumption by function (stacked bar)
    - Latency trend per Cortex function (line chart)
    - Null output rate per model (should be near 0%)
    - Monthly cost forecast based on trailing 30-day average
    - Alert if daily credits exceed configurable threshold

  Page 4 — Data Quality
    - Test pass/fail rate by tier (donut chart)
    - Failing tests table with model, column, failure %, severity
    - Drift score trends for key metrics (sentiment_score, weekly_revenue)
    - Open alerts table (unresolved critical/high failures)

  Page 5 — Lineage & Coverage
    - dbt model lineage rendered from manifest.json
      (read from @CSTA_DBT_ARTIFACTS/prod/latest/manifest.json)
    - Test coverage % per model and layer
    - Models with no tests highlighted as coverage gaps

  Page 6 — Cost & Credits
    - Daily cost breakdown by component: stacked bar chart
      (warehouse compute / Cortex AI / serverless tasks / storage)
    - Cost by environment (dev vs uat vs prod) — bar chart, last 30 days
    - Credits per pipeline run trend (line chart)
    - Cost efficiency metric: credits per 1,000 reviews processed
    - Monthly forecast: trailing 30-day average × remaining days in month
    - Configurable credit unit price ($/credit, default $3.00 — editable widget)
    - Budget alert threshold: configurable; highlights bar red when exceeded

---

### 7. macros/observability_hooks.sql

A dbt macro file containing all hooks that feed the observability tables.
Called via dbt's on-run-end and model-level post-hook configuration.

Key macros:
  log_model_health()
    Called in post-hook for every model.
    Queries TABLE_STORAGE_METRICS, inserts to MODEL_HEALTH_LOG.

  log_cortex_usage(function_name, model_name, rows_processed)
    Called as a wrapper inside silver models around Cortex calls.
    Records pre/post timestamps, counts NULLs, estimates tokens.

  log_test_results()
    Called in on-run-end after dbt test.
    Parses {{ results }} context variable, inserts to DATA_QUALITY_LOG.

  trigger_alert(severity, message, run_id)
    Called by anomaly detection hooks.
    Routes to SYSTEM$SEND_EMAIL for critical/high, or alert queue for lower.

---

### 8. Cost & Resource Consumption Report

File: snowflake/observability/08_cost_daily.sql

A daily rollup of all Snowflake spend across four cost dimensions, stored
in a single table and surfaced via Streamlit Page 6.

Note: `SNOWFLAKE.ACCOUNT_USAGE` views carry a ~45-minute ingestion lag.
`TASK_COST_REPORT` is therefore scheduled independently of the pipeline
DAG at 06:00 UTC (after the previous day's ACCOUNT_USAGE data is settled).

#### Table: CSTA_MARKETING_SHARED.OBSERVABILITY.COST_DAILY

| Column             | Type           | Description                                         |
|--------------------|----------------|-----------------------------------------------------|
| report_date        | DATE           | Calendar day of spend                               |
| env                | STRING         | 'dev' / 'uat' / 'prod' / 'shared'                  |
| component          | STRING         | 'warehouse_compute' / 'cortex_ai' / 'serverless_tasks' / 'storage' |
| resource_name      | STRING         | WH name, Cortex function, 'TASKS', or schema name   |
| credits_used       | NUMBER         | Snowflake credits consumed                          |
| estimated_usd_cost | NUMBER         | credits × configurable unit price (default $3.00)  |
| query_count        | NUMBER         | Queries executed (warehouse_compute only)           |
| storage_tb         | NUMBER         | Average TB billed (storage only)                    |
| measured_at        | TIMESTAMP_TZ   | Row insert time                                     |

#### Data Sources per Component

```sql
-- warehouse_compute
SELECT  DATE(start_time)         AS report_date,
        warehouse_name           AS resource_name,
        SUM(credits_used)        AS credits_used,
        COUNT(*)                 AS query_count
FROM    SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
GROUP BY 1, 2;

-- serverless_tasks
SELECT  DATE(start_time)         AS report_date,
        'TASKS'                  AS resource_name,
        SUM(credits_used)        AS credits_used
FROM    SNOWFLAKE.ACCOUNT_USAGE.SERVERLESS_TASK_HISTORY
GROUP BY 1;

-- cortex_ai  (already collected by existing hooks)
SELECT  report_date,
        cortex_function          AS resource_name,
        SUM(credits_used)        AS credits_used
FROM    CSTA_MARKETING_SHARED.OBSERVABILITY.CORTEX_COST_DAILY
GROUP BY 1, 2;

-- storage
SELECT  DATE(usage_date)         AS report_date,
        'STORAGE'                AS resource_name,
        AVG(storage_bytes + stage_bytes + failsafe_bytes)
          / POWER(1024, 4)       AS storage_tb
FROM    SNOWFLAKE.ACCOUNT_USAGE.STORAGE_USAGE
GROUP BY 1;
```

#### TASK_COST_REPORT (independent daily task)

```sql
CREATE OR REPLACE TASK CSTA_MARKETING_SHARED.OBSERVABILITY.TASK_COST_REPORT
  WAREHOUSE  = CSTA_DBT_DEV_WH
  SCHEDULE   = 'USING CRON 0 6 * * * UTC'
AS
  CALL POPULATE_COST_DAILY();
```

Runs outside the pipeline DAG — no `AFTER` dependency — so a pipeline
failure never blocks cost data collection. `POPULATE_COST_DAILY` merges
yesterday's rows from the four sources above into `COST_DAILY`.

#### Budget Alert Rule

If `SUM(credits_used)` for the current calendar month exceeds the
configured `MONTHLY_CREDIT_BUDGET` variable, `TASK_COST_REPORT` calls
`trigger_alert('high', 'Monthly credit budget exceeded', NULL)` which
routes to `SYSTEM$SEND_EMAIL`.

---

## RUNNING DBT INSIDE SNOWFLAKE

dbt Core runs inside Snowflake using a Python Stored Procedure,
NOT on an external runner.

### Approach: dbt via Snowflake Python Stored Procedure
- Package dbt-core + dbt-snowflake into a Snowflake Python Stored Procedure
- The stored procedure accepts a target argument ('dev', 'uat', or 'prod')
  and executes: dbt deps → dbt seed → dbt run → dbt test → dbt docs generate
- profiles.yml is injected at runtime using Snowflake Secrets
  (SNOWFLAKE.SECRET) — no credentials hardcoded in the procedure
- The stored procedure is defined in:
  snowflake/setup/05_dbt_stored_procedure.sql

Example stored procedure signature:
  CALL RUN_DBT(target => 'prod', command => 'run', select => 'tag:daily')

### dbt Artifact Sharing Across Environments

All dbt artifacts (manifest.json, run_results.json, catalog.json) are
written to a shared internal Snowflake stage after every run:

  @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/<env>/latest/

Stage structure:
  @CSTA_DBT_ARTIFACTS/dev/latest/manifest.json
  @CSTA_DBT_ARTIFACTS/dev/latest/run_results.json
  @CSTA_DBT_ARTIFACTS/uat/latest/manifest.json
  @CSTA_DBT_ARTIFACTS/uat/latest/run_results.json
  @CSTA_DBT_ARTIFACTS/prod/latest/manifest.json
  @CSTA_DBT_ARTIFACTS/prod/latest/run_results.json

This enables:
1. Slim CI: UAT defers to prod manifest for state comparison
   (--defer --state @CSTA_DBT_ARTIFACTS/prod/latest/)
2. Dev defers to UAT manifest
3. dbt docs served from prod artifacts via the Observability Streamlit app
4. Cross-environment lineage visibility

Add a post-hook in dbt_project.yml to upload artifacts after every run:
  on-run-end:
    - "CALL UPLOAD_CSTA_DBT_ARTIFACTS('{{ target.name }}')"
    - "{{ log_test_results() }}"

The UPLOAD_CSTA_DBT_ARTIFACTS stored procedure:
  - Copies manifest.json, run_results.json, catalog.json
    from the container working directory to the appropriate stage path
  - Parses run_results.json and inserts to PIPELINE_RUN_LOG
  - Parses run_results.json test results and inserts to DATA_QUALITY_LOG

---

## SNOWFLAKE TASK DAG SCHEDULER

### prod Task DAG (snowflake/tasks/task_dag_prod.sql):

```sql
CREATE OR REPLACE TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_ROOT
  WAREHOUSE = CSTA_DBT_PROD_WH
  SCHEDULE = 'USING CRON 0 4 * * * UTC'
AS
  CALL CHECK_SOURCE_FRESHNESS();

CREATE OR REPLACE TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_CSTA_DBT_RUN
  WAREHOUSE = CSTA_DBT_PROD_WH
  AFTER CSTA_MARKETING_PROD.ORCHESTRATION.TASK_ROOT
AS
  CALL RUN_DBT(target => 'prod', command => 'run');

CREATE OR REPLACE TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_CSTA_DBT_TEST
  WAREHOUSE = CSTA_DBT_PROD_WH
  AFTER CSTA_MARKETING_PROD.ORCHESTRATION.TASK_CSTA_DBT_RUN
AS
  CALL RUN_DBT(target => 'prod', command => 'test');

CREATE OR REPLACE TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_PUBLISH_ARTIFACTS
  WAREHOUSE = CSTA_DBT_PROD_WH
  AFTER CSTA_MARKETING_PROD.ORCHESTRATION.TASK_CSTA_DBT_TEST
AS
  CALL UPLOAD_CSTA_DBT_ARTIFACTS('prod');

CREATE OR REPLACE TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_ANOMALY_DETECTION
  WAREHOUSE = CSTA_DBT_PROD_WH
  AFTER CSTA_MARKETING_PROD.ORCHESTRATION.TASK_PUBLISH_ARTIFACTS
AS
  CALL RUN_ANOMALY_CHECKS();

-- Resume all tasks (root last)
ALTER TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_ANOMALY_DETECTION RESUME;
ALTER TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_PUBLISH_ARTIFACTS RESUME;
ALTER TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_CSTA_DBT_TEST RESUME;
ALTER TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_CSTA_DBT_RUN RESUME;
ALTER TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_ROOT RESUME;
```

Define equivalent DAGs for:
- dev: manual/on-demand only (no SCHEDULE), no anomaly detection task
- uat: SCHEDULE = 'USING CRON 0 2 * * * UTC', include anomaly detection

### Task error handling:
- Use SYSTEM$TASK_RUNTIME_INFO() for task context in stored procedures
- On any task failure: insert FAILED record to PIPELINE_RUN_LOG
- On critical/high DATA_QUALITY_LOG entries: CALL SYSTEM$SEND_EMAIL(...)
- On anomaly detection flags: insert to alert queue + daily digest email

---

## GITHUB ACTIONS — REVISED ROLE

### ci_dev.yml — triggers on: PR to dev branch
1. checkout + setup Python + install dbt-snowflake (compile only)
2. dbt deps + dbt compile (local syntax check, no Snowflake connection)
3. SQLFluff lint on changed models
4. Trigger via Snowflake CLI: snow task execute TASK_ROOT --env dev
5. Poll task status and surface DATA_QUALITY_LOG failures in PR comment
6. Post test tier summary as PR comment:
   "Tier 1: 12/12 ✅  Tier 2: 8/8 ✅  Tier 3: 4/4 ✅  Tier 4: skipped"

### ci_uat.yml — triggers on: push to uat branch
1. checkout + dbt compile
2. Trigger: snow task execute TASK_ROOT --env uat
3. Poll until complete
4. Query DATA_QUALITY_LOG for any failures, fail workflow if critical/high
5. Post full test results summary as GitHub Actions job summary

### ci_prod.yml — triggers on: push to main
1. checkout + dbt compile
2. Trigger: snow task execute TASK_ROOT --env prod
3. Poll until complete
4. Query PIPELINE_RUN_LOG + DATA_QUALITY_LOG for run summary
5. Post formatted summary as GitHub Actions job summary
6. On any critical failure: fail the workflow and post to Slack

All Snowflake CLI calls authenticate via key-pair auth:
GitHub Secrets: SNOWFLAKE_PRIVATE_KEY, SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER

---

## TERRAFORM INFRASTRUCTURE

Snowflake infrastructure is provisioned via Terraform using the
`Snowflake-Labs/snowflake` provider. This replaces the imperative
`snowflake/setup/` SQL scripts with idempotent, plan-before-apply
infrastructure-as-code. The SQL scripts remain as reference documentation
but are no longer the source of truth after bootstrapping.

### Provider and State

**terraform/versions.tf**

```hcl
terraform {
  required_providers {
    snowflake = {
      source  = "Snowflake-Labs/snowflake"
      version = "~> 0.98"
    }
  }
  required_version = ">= 1.7"
}
```

**Remote state backend (S3)**

```hcl
backend "s3" {
  bucket  = "your-tf-state-bucket"
  key     = "cortex-marketing/terraform.tfstate"
  region  = "eu-west-1"
  encrypt = true
}
```

### Resources Managed

terraform/modules/databases/main.tf:
  - snowflake_database:      CSTA_MARKETING_DEV, CSTA_MARKETING_UAT, CSTA_MARKETING_PROD,
                             CSTA_MARKETING_SHARED
  - snowflake_schema:        BRONZE, SILVER, GOLD, ORCHESTRATION per env
                             database; OBSERVABILITY, ARTIFACTS in
                             CSTA_MARKETING_SHARED

terraform/modules/warehouses/main.tf:
  - snowflake_warehouse:     CSTA_DBT_DEV_WH (XS), CSTA_DBT_UAT_WH (S), CSTA_DBT_PROD_WH (M)
                             auto_suspend=60, auto_resume=true

terraform/modules/rbac/main.tf:
  - All access roles and functional roles (see RBAC section)
  - All GRANT statements as snowflake_grant_privileges_to_role resources

terraform/modules/stages/main.tf:
  - snowflake_stage:         @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS
                             (internal stage, encryption=SSE)
  - snowflake_secret:        profiles_yml_dev, profiles_yml_uat,
                             profiles_yml_prod (injected into stored procedure)

### Per-Environment Variables (terraform/environments/\<env\>.tfvars)

```hcl
snowflake_account = "your-account"
snowflake_user    = "TERRAFORM_SVC"
snowflake_role    = "SYSADMIN"
env               = "dev"              # or "uat" / "prod"
dbt_wh_size       = "XSMALL"          # or "SMALL" / "MEDIUM"
```

### CI/CD Integration

terraform plan on every PR (added to ci_dev.yml / ci_uat.yml / ci_prod.yml):

```bash
terraform init -backend-config=environments/<env>.backend
terraform plan -var-file=environments/<env>.tfvars -out=tfplan
# post plan output as PR comment — no apply on PR
```

terraform apply on merge (runs before dbt steps in each ci_*.yml):

```bash
terraform apply -auto-approve tfplan
```

### Bootstrapping from the SQL Scripts

The snowflake/setup/ SQL scripts are run once manually to create the
TERRAFORM_SVC user and grant it SYSADMIN:

1. Run `01_databases.sql` manually to create `TERRAFORM_SVC` + key-pair auth
2. Import existing resources into state:

```bash
terraform import snowflake_database.dev    CSTA_MARKETING_DEV
terraform import snowflake_database.uat    CSTA_MARKETING_UAT
terraform import snowflake_database.prod   CSTA_MARKETING_PROD
terraform import snowflake_database.shared CSTA_MARKETING_SHARED
```

3. From this point all infrastructure changes go through `terraform apply` only.

---

## ENVIRONMENT STRATEGY

- CSTA_MARKETING_DEV    → feature branches + dev branch
- CSTA_MARKETING_UAT    → uat branch
- CSTA_MARKETING_PROD   → main branch
- CSTA_MARKETING_SHARED → artifact stage + observability schema + run log
                     (accessible to all three roles)

dbt schema routing via generate_schema_name macro:
- dev:  developer-namespaced (e.g. CSTA_DBT_FMARECHAL_BRONZE)
- uat:  shared schemas (BRONZE, SILVER, GOLD) in CSTA_MARKETING_UAT
- prod: shared schemas in CSTA_MARKETING_PROD

---

## DBT MODELS — KEY LOGIC

### Bronze layer
All bronze models are thin wrappers over raw staged Olist CSV files:
- Cast all columns to correct types
- Add _loaded_at ingestion timestamp
- No business logic
- Materialized as tables (incremental where watermark column exists)
- post-hook: {{ log_model_health() }}

### silver/slv_feedback_enriched.sql
Source: brz_olist_order_reviews + brz_olist_orders + brz_olist_products
Apply Cortex functions in sequence on review_comment_message:
1. SNOWFLAKE.CORTEX.TRANSLATE(review_comment_message, '', 'en')
   → translated_review
2. AI_SENTIMENT(translated_review,
     ['product_quality','delivery','seller_service','price','packaging'])
   → sentiment JSON (overall + aspect-based scores)
3. SNOWFLAKE.CORTEX.COMPLETE('claude-sonnet-4-20250514', ...)
   → theme_json + key_phrase
4. Parse all JSON outputs into typed columns
5. Derive churn_risk_flag = 1 where review_score <= 2
Config: incremental, merge on review_id
post-hook: {{ log_model_health() }}, {{ log_cortex_usage(...) }}

### silver/slv_customer_rfm.sql
Compute RFM scores + product_diversity + preferred_payment + customer_state
Config: table
post-hook: {{ log_model_health() }}

### silver/slv_customer_profile.sql
Join slv_customer_rfm + slv_feedback_enriched + brz_olist_mql
Config: table
post-hook: {{ log_model_health() }}

### silver/slv_mmm_weekly.sql
Aggregate Olist revenue to ISO week + join synthetic spend
Config: table
post-hook: {{ log_model_health() }}

### gold/mrt_customer_segments.sql
K-Means clustering via Snowpark ML on slv_customer_profile
Segment label naming via CORTEX.COMPLETE
Config: table
post-hook: {{ log_model_health() }}, {{ log_cortex_usage(...) }}

### gold/mrt_sentiment_by_segment.sql
Aggregate sentiment + theme by segment + category + week
Config: table
post-hook: {{ log_model_health() }}

### gold/mrt_mmm_attribution.sql
Ridge regression via Snowpark ML on slv_mmm_weekly
Output channel contributions + ROI
Config: table
post-hook: {{ log_model_health() }}

### gold/mrt_funnel_conversion.sql
MQL → order conversion rates by acquisition channel
Config: table
post-hook: {{ log_model_health() }}

### gold/mrt_nba_actions.sql
CORTEX.COMPLETE per segment → action + channel + message_draft
Config: table
post-hook: {{ log_model_health() }}, {{ log_cortex_usage(...) }}

---

## METRICFLOW SEMANTIC LAYER

### sem_customer_feedback.yml
Entities: customer_id (primary), segment_id (foreign)
Dimensions: review_date (time, primary), product_category, segment_label,
            sentiment_label, theme (categorical)
Measures: total_reviews, avg_sentiment_score, negative_review_count,
          churn_risk_count

### sem_customer_segments.yml
Entities: customer_unique_id (primary)
Dimensions: segment_label, customer_state, preferred_payment (categorical)
Measures: customer_count, avg_monetary_value, avg_frequency,
          avg_recency_days, avg_predicted_ltv

### sem_mmm_attribution.yml
Entities: iso_week (primary)
Dimensions: channel, is_holiday, is_black_friday (categorical)
Measures: total_spend, total_attributed_revenue, avg_roi, avg_marginal_roi

### metrics.yml
- sentiment_score:        type simple,     avg_sentiment_score
- negative_review_rate:   type ratio,      negative_review_count / total_reviews
- sentiment_trend:        type cumulative, window 4 weeks
- customer_churn_rate:    type ratio,      churn_risk_count / customer_count
- channel_roi:            type simple,     avg_roi
- revenue_per_segment:    type simple,     avg_monetary_value

---

## SNOWFLAKE RBAC

RBAC follows Snowflake's recommended two-layer pattern:
  - Access roles: granted specific privileges on named objects
  - Functional roles: granted sets of access roles matching a job function
  - Role hierarchy: SYSADMIN → functional roles → access roles

This limits blast radius — a compromised service account loses only the
privileges of its functional role, not blanket account-wide access.

---

### Access Roles — Database Level

One pair per database (CSTA_MARKETING_DEV, CSTA_MARKETING_UAT, CSTA_MARKETING_PROD,
CSTA_MARKETING_SHARED). Substitute `<DB>` for each database name.

```sql
-- <DB>_DB_READ
GRANT USAGE ON DATABASE <DB>;

-- <DB>_DB_MODIFY  (dev only — enables per-developer schema namespacing)
GRANT USAGE         ON DATABASE <DB>;
GRANT CREATE SCHEMA ON DATABASE <DB>;
```

---

### Access Roles — Schema Level

Three tiers per schema, named `<DB>_<SCHEMA>_READ` / `_READ_WRITE` /
`_READ_WRITE_CREATE`. Substitute `<DB>` and `<SCHEMA>` for each combination.

```sql
-- <DB>_<SCHEMA>_READ
GRANT USAGE  ON SCHEMA                    <DB>.<SCHEMA>;
GRANT SELECT ON ALL TABLES   IN SCHEMA    <DB>.<SCHEMA>;
GRANT SELECT ON FUTURE TABLES IN SCHEMA   <DB>.<SCHEMA>;
GRANT SELECT ON ALL VIEWS    IN SCHEMA    <DB>.<SCHEMA>;
GRANT SELECT ON FUTURE VIEWS  IN SCHEMA   <DB>.<SCHEMA>;

-- <DB>_<SCHEMA>_READ_WRITE  (all _READ grants, plus:)
GRANT INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA <DB>.<SCHEMA>;
GRANT INSERT, UPDATE, DELETE ON FUTURE TABLES IN SCHEMA <DB>.<SCHEMA>;

-- <DB>_<SCHEMA>_READ_WRITE_CREATE  (all _READ_WRITE grants, plus:)
GRANT CREATE TABLE     ON SCHEMA <DB>.<SCHEMA>;
GRANT CREATE VIEW      ON SCHEMA <DB>.<SCHEMA>;
GRANT CREATE STAGE     ON SCHEMA <DB>.<SCHEMA>;
GRANT CREATE PROCEDURE ON SCHEMA <DB>.<SCHEMA>;
GRANT CREATE TASK      ON SCHEMA <DB>.<SCHEMA>;
```

Schemas covered per database:

| Database         | Schemas                                    |
|------------------|--------------------------------------------|
| CSTA_MARKETING_DEV    | BRONZE, SILVER, GOLD, ORCHESTRATION        |
| CSTA_MARKETING_UAT    | BRONZE, SILVER, GOLD, ORCHESTRATION        |
| CSTA_MARKETING_PROD   | BRONZE, SILVER, GOLD, ORCHESTRATION        |
| CSTA_MARKETING_SHARED | OBSERVABILITY, ARTIFACTS                   |

---

### Functional Roles

```
CSTA_DBT_DEV_ROLE
  ← CSTA_MARKETING_DEV_DB_MODIFY
  ← CSTA_MARKETING_DEV_BRONZE_READ_WRITE_CREATE
  ← CSTA_MARKETING_DEV_SILVER_READ_WRITE_CREATE
  ← CSTA_MARKETING_DEV_GOLD_READ_WRITE_CREATE
  ← CSTA_MARKETING_DEV_ORCHESTRATION_READ_WRITE_CREATE
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_DEV_WH
  + GRANT EXECUTE TASK ON ACCOUNT  (scoped to dev task ownership)

CSTA_DBT_UAT_ROLE
  ← CSTA_MARKETING_UAT_DB_READ
  ← CSTA_MARKETING_UAT_BRONZE_READ_WRITE_CREATE
  ← CSTA_MARKETING_UAT_SILVER_READ_WRITE_CREATE
  ← CSTA_MARKETING_UAT_GOLD_READ_WRITE_CREATE
  ← CSTA_MARKETING_UAT_ORCHESTRATION_READ_WRITE_CREATE
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_UAT_WH

CSTA_DBT_PROD_ROLE
  ← CSTA_MARKETING_PROD_DB_READ
  ← CSTA_MARKETING_PROD_BRONZE_READ_WRITE_CREATE
  ← CSTA_MARKETING_PROD_SILVER_READ_WRITE_CREATE
  ← CSTA_MARKETING_PROD_GOLD_READ_WRITE_CREATE
  ← CSTA_MARKETING_PROD_ORCHESTRATION_READ_WRITE_CREATE
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_PROD_WH

CSTA_DBT_UAT_CI_ROLE  (CI service identity for UAT — least-privilege)
  + GRANT OPERATE ON TASK CSTA_MARKETING_UAT.ORCHESTRATION.TASK_ROOT
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ  (poll PIPELINE_RUN_LOG / DATA_QUALITY_LOG)
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ      (fetch --defer state in compile step)
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_DEV_WH
  -- no schema-level privileges on CSTA_MARKETING_UAT — the Task DAG runs as the task owner

CSTA_DBT_PROD_CI_ROLE  (CI service identity for prod — least-privilege)
  + GRANT OPERATE ON TASK CSTA_MARKETING_PROD.ORCHESTRATION.TASK_ROOT
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ  (poll PIPELINE_RUN_LOG / DATA_QUALITY_LOG)
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ      (fetch --defer state in compile step)
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_DEV_WH
  -- no schema-level privileges on CSTA_MARKETING_PROD — the Task DAG runs as the task owner

CSTA_CORTEX_ROLE  (database role — granted to all CSTA_DBT_*_ROLEs)
  + GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER

CSTA_OBSERVER_ROLE
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ
  ← CSTA_MARKETING_PROD_GOLD_READ          (read-only mart access for analysts)
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_DEV_WH  (analysts use dev WH, not prod)

CSTA_STREAMLIT_ROLE  (service identity for the Observability Streamlit app)
  ← CSTA_OBSERVER_ROLE
  + GRANT USAGE ON INTEGRATION CSTA_STREAMLIT_INTEGRATION

CSTA_ANALYST_ROLE  (human read-only — UAT and PROD GOLD only)
  ← CSTA_MARKETING_UAT_DB_READ
  ← CSTA_MARKETING_UAT_GOLD_READ
  ← CSTA_MARKETING_PROD_DB_READ
  ← CSTA_MARKETING_PROD_GOLD_READ
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_DEV_WH
  -- no silver, bronze, or observability access

CSTA_DEV_ROLE  (human developer — full DEV access)
  ← CSTA_MARKETING_DEV_DB_MODIFY
  ← CSTA_MARKETING_DEV_BRONZE_READ_WRITE_CREATE
  ← CSTA_MARKETING_DEV_SILVER_READ_WRITE_CREATE
  ← CSTA_MARKETING_DEV_GOLD_READ_WRITE_CREATE
  ← CSTA_MARKETING_DEV_ORCHESTRATION_READ_WRITE_CREATE
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE
  ← CSTA_CORTEX_ROLE
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_DEV_WH
  + GRANT EXECUTE TASK ON ACCOUNT
  -- mirrors CSTA_DBT_DEV_ROLE but assigned to named human users, not service accounts

CSTA_UAT_DEV_ROLE  (developer / QA engineer — full UAT access)
  ← CSTA_MARKETING_UAT_DB_READ
  ← CSTA_MARKETING_UAT_BRONZE_READ_WRITE_CREATE
  ← CSTA_MARKETING_UAT_SILVER_READ_WRITE_CREATE
  ← CSTA_MARKETING_UAT_GOLD_READ_WRITE_CREATE
  ← CSTA_MARKETING_UAT_ORCHESTRATION_READ_WRITE_CREATE
  ← CSTA_MARKETING_SHARED_DB_READ
  ← CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE
  ← CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE
  ← CSTA_CORTEX_ROLE
  + GRANT USAGE ON WAREHOUSE CSTA_DBT_UAT_WH
  -- mirrors CSTA_DBT_UAT_ROLE but assigned to named human users, not service accounts
```

---

### Role Hierarchy Summary

```
SYSADMIN
├── CSTA_DBT_DEV_ROLE
│     └── (access roles: CSTA_MARKETING_DEV DB/schema tiers + SHARED)
├── CSTA_DBT_UAT_ROLE
│     └── (access roles: CSTA_MARKETING_UAT DB/schema tiers + SHARED)
├── CSTA_DBT_UAT_CI_ROLE
│     └── (OPERATE on UAT task root + SHARED observability/artifacts read)
├── CSTA_DBT_PROD_ROLE
│     └── (access roles: CSTA_MARKETING_PROD DB/schema tiers + SHARED)
├── CSTA_DBT_PROD_CI_ROLE
│     └── (OPERATE on prod task root + SHARED observability/artifacts read)
├── CSTA_OBSERVER_ROLE
│     └── (access roles: SHARED read + PROD gold read)
├── CSTA_STREAMLIT_ROLE
│     └── ← CSTA_OBSERVER_ROLE
├── CSTA_ANALYST_ROLE
│     └── (UAT + PROD gold read only — no bronze, silver, or observability)
├── CSTA_DEV_ROLE
│     └── (access roles: CSTA_MARKETING_DEV DB/schema tiers + SHARED; human users)
└── CSTA_UAT_DEV_ROLE
      └── (access roles: CSTA_MARKETING_UAT DB/schema tiers + SHARED; human users)

CSTA_CORTEX_ROLE (database role) granted to CSTA_DBT_DEV_ROLE, CSTA_DBT_UAT_ROLE, CSTA_DBT_PROD_ROLE,
                                             CSTA_DEV_ROLE, CSTA_UAT_DEV_ROLE
```

---

### Service Account Mapping

| Service Account      | Functional Role(s)                         | Notes                                                                              |
|----------------------|--------------------------------------------|------------------------------------------------------------------------------------|
| SVC_CSTA_DBT_DEV     | CSTA_DBT_DEV_ROLE + CSTA_CORTEX_ROLE       | Executes dbt run/test in dev; task DAG owner for dev tasks                         |
| SVC_CSTA_DBT_UAT     | CSTA_DBT_UAT_ROLE + CSTA_CORTEX_ROLE       | Executes dbt run/test in UAT (Task DAG owner; dbt runs as this identity)           |
| SVC_CSTA_DBT_PROD    | CSTA_DBT_PROD_ROLE + CSTA_CORTEX_ROLE      | Executes dbt run/test in prod (Task DAG owner; dbt runs as this identity)          |
| SVC_STREAMLIT        | CSTA_STREAMLIT_ROLE                        | Observability Streamlit app service identity                                       |
| SVC_GITHUB_CI_DEV    | CSTA_DBT_DEV_ROLE                          | GHA ci_dev.yml; triggers dev Task DAG + polls observability                        |
| SVC_GITHUB_CI_UAT    | CSTA_DBT_UAT_CI_ROLE                       | GHA ci_uat.yml; task OPERATE only — no write access to UAT schemas                 |
| SVC_GITHUB_CI_PROD   | CSTA_DBT_PROD_CI_ROLE                      | GHA ci_prod.yml; task OPERATE only — no write access to prod schemas               |
| TERRAFORM_SVC        | SYSADMIN (ACCOUNTADMIN bootstrap-only)     | Key-pair auth; ACCOUNTADMIN revoked after initial `terraform import` is complete   |

---

### Human User Role Mapping

| Role                | Access scope                                         | Intended for                                        |
|---------------------|------------------------------------------------------|-----------------------------------------------------|
| `CSTA_ANALYST_ROLE` | SELECT on `CSTA_MARKETING_UAT.GOLD` + `CSTA_MARKETING_PROD.GOLD` only | Business analysts querying mart tables via SQL or Snowflake UI |
| `CSTA_DEV_ROLE`     | Full `READ_WRITE_CREATE` on all `CSTA_MARKETING_DEV` schemas + SHARED | Human developers building and debugging models in DEV |
| `CSTA_UAT_DEV_ROLE` | Full `READ_WRITE_CREATE` on all `CSTA_MARKETING_UAT` schemas + SHARED | Developers and QA engineers validating models in UAT |

Key distinctions from service account roles:
- `CSTA_ANALYST_ROLE` is strictly narrower than `CSTA_OBSERVER_ROLE` — no observability schema access, gold marts only, and covers both UAT and PROD so analysts can compare environments.
- `CSTA_DEV_ROLE` and `CSTA_UAT_DEV_ROLE` mirror their service account counterparts (`CSTA_DBT_DEV_ROLE` / `CSTA_DBT_UAT_ROLE`) in privileges but are assigned to named human users via `GRANT ROLE ... TO USER <username>` — never to shared service accounts.
- No human role is granted access to `CSTA_MARKETING_PROD` write paths; production data is only modified by the Task DAG running as `SVC_CSTA_DBT_PROD`.

---

## ADDITIONAL REQUIREMENTS

1. snowflake/setup/05_dbt_stored_procedure.sql:
   Full Python stored procedure that installs dbt-core + dbt-snowflake
   at runtime, injects profiles.yml from Snowflake Secrets, runs the
   requested dbt command, parses run_results.json, and inserts to
   PIPELINE_RUN_LOG and DATA_QUALITY_LOG on completion or failure.

2. profiles.yml.example: all three targets using env variable substitution

3. packages.yml: dbt-utils, dbt-expectations, dbt-metricflow

4. dbt_project.yml:
   - bronze = table, silver = incremental, gold = table
   - on-run-end hooks: UPLOAD_CSTA_DBT_ARTIFACTS + log_test_results()
   - model-level post-hooks: log_model_health()
   - vars: observability_enabled (true/false, default true)
           drift_baseline_run_id (set after first full prod load)

5. macros/cortex_sentiment.sql:
   Reusable macro wrapping AI_SENTIMENT with TRY_CAST error handling
   on JSON output and null-safe fallback values.

6. macros/observability_hooks.sql:
   All four observability macros: log_model_health, log_cortex_usage,
   log_test_results, trigger_alert. Each macro should be guarded by
   the observability_enabled var so it can be disabled in dev for speed.

7. snowflake/observability/01_observability_schema.sql:
   CREATE SCHEMA + all four observability tables with full column
   definitions, data types, and inline comments.

8. snowflake/observability/07_observability_streamlit.sql:
   Full Snowflake Streamlit app with all 6 pages as described.
   Use st.tabs() for page navigation. Charts via Altair or Plotly.
   App should be deployable with: CREATE STREAMLIT ... FROM @stage.

9. seeds/generate_mmm_spend.py:
   Reproducible Python script (fixed random seed) generating
   olist_mmm_weekly_spend.csv with inline calibration comments.

10. docs/architecture.md:
    ASCII diagram of the full pipeline including the observability
    feedback loop:
    Olist CSVs → Snowpipe → Bronze → Silver (Cortex) →
    Gold (Segments + MMM + NBA) → MetricFlow →
    Task DAG → Artifact Stage → Observability Schema →
    Streamlit Dashboard ← GitHub Actions CI/CD

11. docs/testing_strategy.md:
    Full description of the four-tier testing framework:
    - Tier descriptions and rationale
    - Test inventory table (test name, model, tier, severity)
    - Environment matrix (which tiers run where)
    - How to add a new test
    - How drift baselines are established and updated

12. docs/mmm_synthetic_data_assumptions.md:
    All calibration choices for the synthetic spend table.

13. COLD-START OPTIMISATION (dbt Stored Procedure):
    Option A — Snowflake Container Services (preferred):
    - Pre-built Docker image with dbt-core + dbt-snowflake
    - Pushed to SNOWFLAKE.IMAGE_REGISTRY
    - Run via EXECUTE JOB SERVICE
    - Container spec: snowflake/container/dbt_runner.yaml

    Option B — Snowflake Notebook with pre-installed kernel (fallback)

    Document both in docs/architecture.md with decision matrix:
    cost, latency, maintenance overhead, environment parity.

14. SNOWFLAKE CLI VERSION PINNING (GitHub Actions):
    Pin in all three workflow files:
    - uses: Snowflake-Labs/snowflake-cli-action@v1
      with:
        cli-version: "3.x.x"
    # TODO: review CLI version quarterly —
    # https://github.com/Snowflake-Labs/snowflake-cli/releases

15. All SQL in Snowflake dialect. dbt configs in YAML v2.
    MetricFlow in dbt Core 1.8+ syntax.
    Streamlit app compatible with Snowflake-hosted Streamlit
    (no external dependencies beyond snowflake-snowpark-python).

16. snowflake/observability/08_cost_daily.sql:
    - CREATE TABLE COST_DAILY with full column definitions
    - POPULATE_COST_DAILY stored procedure merging the four source queries
      (warehouse_metering, serverless_task_history, cortex_cost_daily,
      storage_usage) into COST_DAILY via MERGE ON (report_date, env,
      component, resource_name)
    - TASK_COST_REPORT definition (independent daily schedule, 06:00 UTC)
    - Budget alert logic inside POPULATE_COST_DAILY comparing MTD credits
      to MONTHLY_CREDIT_BUDGET parameter and calling trigger_alert on breach
    - GRANT SELECT ON COST_DAILY TO ROLE CSTA_OBSERVER_ROLE