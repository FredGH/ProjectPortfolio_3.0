# Limitations of the Snowflake DAG Scheduler Compared to Airflow/Dagster

---

## What Snowflake Tasks Do Well
Before the limitations — Tasks have improved significantly in 2024-2025. They support DAG dependencies, conditional branching, error handling, and serverless compute. For a single-platform Snowflake pipeline they are genuinely viable.

---

## The Real Limitations vs Airflow / Dagster

### 1. No Cross-System Orchestration
Tasks can only trigger Snowflake-native operations (SQL, stored procedures, Snowpark). You cannot natively:
- Trigger an external API call
- Run a dbt command on a remote runner
- Kick off a Spark job, a Python script on EC2, or a Fivetran sync
- Wait for an S3 file to land before proceeding

Airflow and Dagster orchestrate **across systems** — your entire data estate, not just one platform. In your project this is mitigated because everything runs inside Snowflake, but the moment you need one external step (e.g. pulling live ad spend from Google Ads API), Tasks hit a wall.

---

### 2. No Dynamic Task Generation
Airflow's dynamic DAGs and Dagster's dynamic assets/partitions let you programmatically generate task graphs at runtime — e.g. one task per country, one task per client, one task per partition.

Snowflake Tasks are **statically defined DDL**. You cannot loop over a list and generate 50 tasks dynamically. Every task must be explicitly created. For a single pipeline this is fine — for a multi-tenant or multi-client setup it becomes painful very quickly.

---

### 3. Primitive Retry and Backfill Logic
Airflow and Dagster have built-in:
- Per-task retry with exponential backoff
- Backfill commands (`airflow dags backfill -s 2024-01-01 -e 2024-03-01`)
- Partial DAG re-runs (re-run from a specific failed task)
- Catchup logic for missed scheduled runs

Snowflake Tasks have basic retry (you can wrap in a CALL with TRY/CATCH) but:
- No native backfill command
- No catchup for missed runs — if the warehouse was suspended and a scheduled task was skipped, it simply doesn't run
- Re-running a specific failed mid-DAG task requires manually calling the stored procedure — there's no UI or CLI command equivalent to Airflow's "Clear Task"

---

### 4. No Native Sensor / Trigger Patterns
Airflow has sensors — tasks that poll an external condition before proceeding:
- S3KeySensor (wait for file to land)
- ExternalTaskSensor (wait for another DAG to complete)
- HttpSensor (wait for API to return 200)

Dagster has asset sensors and freshness checks built into the asset graph.

Snowflake Tasks have **Streams** as a pseudo-sensor (trigger when a table has new data), which is powerful but only works for Snowflake table changes. There is no equivalent for external conditions.

---

### 5. Observability and Alerting are DIY
Airflow ships with a full UI: DAG graph view, task logs, run history, SLA miss alerts, Gantt charts, dependency graph. Dagster has an even richer asset-centric UI with lineage, partitions, and freshness status.

With Snowflake Tasks you get:
- `TASK_HISTORY` view in INFORMATION_SCHEMA (queryable but raw)
- No native UI beyond Snowsight's basic task graph
- No built-in alerting beyond `SYSTEM$SEND_EMAIL`
- This is exactly why the prompt includes a custom Streamlit observability dashboard — you are **building what Airflow gives you for free**

---

### 6. No Asset / Lineage Awareness
Dagster's killer feature is the **asset graph** — it understands that a task produces a dataset, tracks freshness, and lets you reason about what needs to be re-materialised when upstream data changes. It integrates natively with dbt's manifest.

Snowflake Tasks are **job-centric, not data-centric**. They know about task dependencies, not data dependencies. If `fct_orders` is stale because an upstream source was late, Tasks have no native way to express "re-run everything downstream of this asset."

---

### 7. Limited Parameterisation
Airflow and Dagster support rich runtime parameters — pass a date range, a client ID, a config dict at trigger time. Dagster's `RunConfig` and Airflow's `dag_run.conf` are full dictionaries.

Snowflake Tasks accept no runtime parameters natively. You work around this with stored procedure arguments (as in the prompt — `CALL RUN_DBT(target=>'prod', command=>'run')`), but this is manual and not composable.

---

### 8. No Built-in Testing / Expectation Framework
Dagster has asset checks — first-class data quality assertions that are part of the asset graph, visible in the UI, and blockable (prevent downstream materialisation if checks fail). Airflow has similar patterns via integrations.

Tasks have no equivalent. Again, the prompt works around this with the custom DATA_QUALITY_LOG and anomaly detection task — but it's bespoke infrastructure.

---

## Summary Decision Matrix

| Capability | Snowflake Tasks | Airflow | Dagster |
|---|---|---|---|
| Cross-system orchestration | ❌ Snowflake only | ✅ | ✅ |
| Dynamic task generation | ❌ Static DDL only | ✅ | ✅ |
| Backfill / catchup | ❌ Manual | ✅ Native | ✅ Native |
| Sensors / external triggers | ⚠️ Streams only | ✅ | ✅ |
| Built-in UI / observability | ⚠️ Basic Snowsight | ✅ Rich UI | ✅ Best in class |
| Asset / lineage awareness | ❌ | ⚠️ Via plugins | ✅ Native |
| Runtime parameterisation | ⚠️ Via SP args | ✅ | ✅ |
| Data quality integration | ❌ DIY | ⚠️ Via plugins | ✅ Asset checks |
| Setup complexity | ✅ Zero (native) | ❌ Infra needed | ⚠️ Moderate |
| Cost | ✅ Included | ❌ Infra cost | ❌ Infra cost |
| Data governance / security | ✅ Native Snowflake | ⚠️ External | ⚠️ External |

---

## The Honest Verdict for Your Project

Snowflake Tasks are the **right call for this specific project** because:
- Everything runs inside Snowflake — no cross-system needs
- No multi-tenant complexity requiring dynamic DAGs
- Zero infrastructure overhead (no Kubernetes, no managed Airflow)
- Data never leaves Snowflake governance boundary

The limitations only bite you when the project grows to need external triggers, backfill operations across months of data, or multi-client deployments. At that point Dagster (with its dbt integration and asset graph) would be the natural upgrade path — and the stored procedure / Task DAG architecture in the prompt is easy to swap out because the dbt layer is cleanly separated from the orchestration layer.