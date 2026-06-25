# Testing Strategy — Cortex Signal to Action

Four-tier quality gate for a Snowflake Cortex dbt pipeline. Tiers are additive:
each tier builds on the ones below and targets a distinct class of failure.

---

## Tier Descriptions

| Tier | Name | What it catches | When it runs |
|------|------|----------------|--------------|
| 1 | Schema integrity | NULL PKs, type violations, broken FK references | Every env — blocks pipeline |
| 2 | Business invariants | Out-of-range values, impossible counts, structural assertions | Every env — blocks pipeline |
| 3 | Cortex output validity | NULL/empty Cortex outputs, sentiment score bounds, latency | dev + uat — blocks uat promote |
| 4 | Statistical drift & anomalies | Distribution shift, time-series anomalies, KL divergence | uat only — warns, does not block |

**Severity policy:**
- Tier 1 + Tier 2: `severity: error` — pipeline halts on failure.
- Tier 3: `severity: error` in uat; `severity: warn` in dev.
- Tier 4: `severity: warn` everywhere — surfaces alerts without blocking delivery.

---

## Environment Matrix

| Test type | dev | uat | prod |
|-----------|-----|-----|------|
| Tier 1 (schema) | ✅ run | ✅ run | ✅ run |
| Tier 2 (business) | ✅ run | ✅ run | ✅ run |
| Tier 3 (Cortex validity) | ✅ run (warn) | ✅ run (error) | ✅ run (error) |
| Tier 4 (drift / anomaly) | ⚠️ skip or warn | ✅ run (warn) | ✅ run (warn) |
| assert_cortex_latency | ⬜ disabled | ✅ enabled (Phase 8+) | ✅ enabled (Phase 8+) |

`assert_cortex_latency` is gated by `{{ config(enabled=var('observability_enabled', false)) }}`.
It is disabled by default and only activates once Phase 8 sets `observability_enabled: true` in
`dbt_project.yml` (or via `--vars '{"observability_enabled": true}'`). Before Phase 8 the table
`CORTEX_USAGE_LOG` does not exist and the test would compile-error if enabled.

---

## Full Test Inventory

### Generic tests (reusable across models)

| Test file | Tier | Parameters | Applied to |
|-----------|------|-----------|-----------|
| `tests/generic/assert_cortex_not_null.sql` | 3 | `column_name` | `sentiment_label`, `theme` in `slv_feedback_enriched` |
| `tests/generic/assert_sentiment_range.sql` | 3 | `min_value` (-1.0), `max_value` (1.0) | `sentiment_score`, all `aspect_*` columns |
| `tests/generic/assert_no_duplicate_pk.sql` | 2 | `column_names` (list) | `mrt_sentiment_by_segment`, `mrt_mmm_attribution` |
| `tests/generic/assert_row_count_in_range.sql` | 2 | `min_rows`, `max_rows` | All five gold models |
| `tests/generic/assert_column_drift.sql` | 4 | `baseline_mean`, `baseline_stddev`, `z_threshold` (3.0) | `sentiment_score`, `weekly_revenue`, `churn_risk_score`, `tv_spend` |
| `tests/generic/assert_metric_anomaly.sql` | 4 | `date_column`, `lookback_weeks` (8), `threshold_pct`, `aggregation_function` | `total_reviews`, `avg_sentiment_score`, `negative_review_pct` (gold); `weekly_revenue` (silver) |
| `tests/generic/assert_kl_divergence.sql` | 4 | `max_kl_divergence` (0.5) | `sentiment_label` in `slv_feedback_enriched` |

### Singular tests (model-specific assertions)

| Test file | Tier | Model | What it checks |
|-----------|------|-------|---------------|
| `tests/singular/assert_rfm_score_distribution.sql` | 2 | `slv_customer_rfm` | Each NTILE(5) bucket 15–25% of customers |
| `tests/singular/assert_mmm_revenue_positive.sql` | 1 | `slv_mmm_weekly` | `weekly_revenue > 0` for every row |
| `tests/singular/assert_no_untranslated.sql` | 3 | `slv_feedback_enriched` | <2% of translated reviews contain Portuguese stopwords |
| `tests/singular/assert_segment_coverage.sql` | 2 | `mrt_customer_segments` | Zero orphaned customers from `slv_customer_profile` |
| `tests/singular/assert_cortex_latency.sql` | 3 | `CORTEX_USAGE_LOG` | Avg Cortex latency per function ≤ 5s (requires Phase 8) |

### Schema.yml dbt_expectations tests (Tier 2 + Tier 4)

| Test | Model | Column | Tier |
|------|-------|--------|------|
| `expect_column_values_to_be_between` | `slv_customer_rfm` | `rfm_score`, `recency_days`, `monetary_value`, `frequency` | 2 |
| `expect_column_values_to_be_between` | `slv_mmm_weekly` | all spend + adstock columns | 2 |
| `expect_column_values_to_be_between` | gold models | `sentiment_score`, `roi`, `spend`, `conversion_rate` | 2 |
| `expect_column_mean_to_be_between` | `slv_feedback_enriched` | `sentiment_score` | 4 |
| `expect_column_stdev_to_be_between` | `slv_feedback_enriched` | `sentiment_score` | 4 |
| `expect_column_proportion_of_unique_values_to_be_between` | `slv_feedback_enriched` | `theme` | 4 |
| `expect_column_proportion_of_unique_values_to_be_between` | `mrt_customer_segments` | `segment_label` | 4 |
| `expect_column_proportion_of_unique_values_to_be_between` | `mrt_sentiment_by_segment` | `dominant_theme` | 4 |

---

## How to Add a Test

### Adding a Tier 1 or Tier 2 test

1. Open the relevant `models/<layer>/schema.yml`.
2. Add the test under the `tests:` key of the target column (column-level) or model (model-level).
3. For generic schema tests (`unique`, `not_null`, `accepted_values`, `relationships`), no extra files needed.
4. For `dbt_expectations` tests, confirm the test name exists in the [dbt_expectations docs](https://github.com/calogica/dbt-expectations).
5. Run `dbt test --select <model_name> --target dev` to verify locally.

### Adding a Tier 3 or Tier 4 generic test

1. Create `tests/generic/assert_<name>.sql` following the `{% test ... %}` macro pattern.
2. The macro name must match the filename (e.g. `assert_column_drift` in `assert_column_drift.sql`).
3. `tests/generic/` is already in `macro-paths` in `dbt_project.yml` — no further config needed.
4. Add the test to the relevant `schema.yml` with `severity: warn` for Tier 4.
5. Document it in this file under the test inventory tables.

### Adding a singular test

1. Create `tests/singular/assert_<what_must_be_true>.sql`.
2. The query must return **zero rows** to pass — any returned row is a failure.
3. Use `{{ ref('model_name') }}` for inter-model references.
4. Run `dbt test --select assert_<name> --target dev` to verify.

---

## How to Establish or Update Drift Baselines

Drift baselines in `assert_column_drift` are hardcoded in `schema.yml`. They represent the expected
distribution of a column during normal pipeline operation. Stale baselines cause false positives.

### First-time baseline establishment

After Phase 5 and Phase 6 are both green on a full Olist dataset run:

```sql
-- Run on the dev or uat schema to capture baseline statistics.
SELECT
    'sentiment_score' AS column_name,
    ROUND(AVG(sentiment_score), 6) AS baseline_mean,
    ROUND(STDDEV(sentiment_score), 6) AS baseline_stddev
FROM <env_db>.SILVER.SLV_FEEDBACK_ENRICHED
WHERE sentiment_score IS NOT NULL

UNION ALL

SELECT
    'weekly_revenue',
    ROUND(AVG(weekly_revenue), 2),
    ROUND(STDDEV(weekly_revenue), 2)
FROM <env_db>.SILVER.SLV_MMM_WEEKLY

UNION ALL

SELECT
    'churn_risk_score',
    ROUND(AVG(churn_risk_score), 4),
    ROUND(STDDEV(churn_risk_score), 4)
FROM <env_db>.GOLD.MRT_CUSTOMER_SEGMENTS

UNION ALL

SELECT
    'tv_spend',
    ROUND(AVG(tv_spend), 2),
    ROUND(STDDEV(tv_spend), 2)
FROM <env_db>.SILVER.SLV_MMM_WEEKLY;
```

Copy the output values into the `baseline_mean` and `baseline_stddev` parameters in `schema.yml`.
The `z_threshold: 3.0` covers 99.7% of a normal distribution — tighten to 2.5 if you want
earlier warnings, loosen to 3.5 to reduce noise in production.

### Updating baselines

Re-run the query above after any of these events:
- A new batch of Olist data is added (e.g. extending the date range).
- The MMM synthetic data generator is recalibrated with different parameters.
- A deliberate business change shifts the expected distribution (e.g. entering a new market).

Update the values in `schema.yml` and commit with a comment explaining why the baseline changed.

### Interpreting a drift alert

A `warn` from `assert_column_drift` means the column mean has moved more than `z_threshold`
standard deviations from the recorded baseline. Possible causes:

| Cause | Action |
|-------|--------|
| New data batch with different characteristics | Re-establish baseline; no bug |
| Cortex model behaviour change (model swap) | Investigate Cortex output quality; file Snowflake support ticket if needed |
| Data quality regression upstream | Check bronze layer test results; trace to source |
| Baseline is stale | Re-run establishment query and update `schema.yml` |

---

## Drift Baselines Reference (current)

| Column | Model | `baseline_mean` | `baseline_stddev` | `z_threshold` | Notes |
|--------|-------|----------------|------------------|--------------|-------|
| `sentiment_score` | `slv_feedback_enriched` | 0.05 | 0.45 | 3.0 | Calibrated to synthetic/Olist sample; update after first full run |
| `weekly_revenue` | `slv_mmm_weekly` | 150000 | 50000 | 3.0 | Synthetic DGP calibration value |
| `churn_risk_score` | `mrt_customer_segments` | 0.35 | 0.25 | 3.0 | Rule-based score distribution |
| `tv_spend` | `slv_mmm_weekly` | 25000 | 8000 | 3.0 | Synthetic DGP calibration value |

> **Note:** Baselines marked "Calibrated to synthetic/Olist sample" should be re-established
> after the first successful full-data run in uat. See the query above.

---

## KL Divergence Interpretation (`assert_kl_divergence`)

`assert_kl_divergence` measures how far the observed categorical distribution is from a uniform
distribution over all observed categories.

| KL value | Interpretation |
|----------|---------------|
| 0.0 | Perfectly uniform — all categories equally represented |
| 0.0–0.3 | Near-uniform — healthy distribution |
| 0.3–0.5 | Moderately skewed — watch for trend |
| > 0.5 | Concentrated — investigate if a category is being over- or under-produced by Cortex |

`max_kl_divergence: 0.5` (the default) flags concentration comparable to one category holding
~70% of all rows in a five-category distribution. Tighten to 0.3 to detect earlier skew.

---

## Running the Full Test Suite

```bash
# All tiers, all models:
dbt test --target uat

# Tier 1 + 2 only (fast gate):
dbt test --select tag:bronze tag:silver tag:gold --exclude assert_column_drift assert_metric_anomaly assert_kl_divergence assert_cortex_latency --target dev

# Tier 4 only (drift checks):
dbt test --select assert_column_drift assert_metric_anomaly assert_kl_divergence --target uat

# Single model:
dbt test --select slv_feedback_enriched --target dev
```
