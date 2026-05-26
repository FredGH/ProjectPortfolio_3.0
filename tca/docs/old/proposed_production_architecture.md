# Proposed Production Architecture — PrivateBank TCA Platform

> Transition from the local PoC (PostgreSQL · Docker Compose · 400 synthetic orders) to a gold-standard, multi-entity, cloud-native TCA platform capable of processing **24 million records per month** across three legal entities with Snowflake as the primary data warehouse and Tableau as the integrated analytics layer.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Context: PoC vs Production Delta](#2-context-poc-vs-production-delta)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Legal Entity Model](#4-legal-entity-model)
5. [Ingestion Layer](#5-ingestion-layer)
6. [Snowflake Data Platform](#6-snowflake-data-platform)
7. [Transformation Layer — dbt on Snowflake](#7-transformation-layer--dbt-on-snowflake)
8. [Analytics & ML Layer](#8-analytics--ml-layer)
9. [API Layer](#9-api-layer)
10. [Frontend & Tableau Integration](#10-frontend--tableau-integration)
11. [Orchestration](#11-orchestration)
12. [Assessment by Dimension](#12-assessment-by-dimension)
  - 12.1 [Scalability](#121-scalability)
    - 12.2 [Extensibility](#122-extensibility)
    - 12.3 [Maintainability](#123-maintainability)
    - 12.4 [Security](#124-security)
    - 12.5 [Reliability](#125-reliability)
    - 12.6 [Auditability](#126-auditability)
    - 12.7 [Observability & Testing](#127-observability--testing)
    - 12.8 [Portability](#128-portability)
    - 12.9 [CI/CD & Deployment](#129-cicd--deployment)
    - 12.10 [Cost Model](#1210-cost-model)
13. [Migration Path from PoC](#13-migration-path-from-poc)
14. [Decision Register](#14-decision-register)

---

## 1. Executive Summary

The PoC demonstrates the full logical architecture: Data Vault 2.0, counterparty isolation, MiFID II compliance, JWT RBAC, and ML-assisted pre-trade estimation. The production system preserves every one of those patterns but replaces or upgrades every infrastructure component:


| Dimension      | PoC                                       | Production                                                            |
| -------------- | ----------------------------------------- | --------------------------------------------------------------------- |
| Database       | PostgreSQL 16 + TimescaleDB (single node) | Snowflake (multi-account, multi-region)                               |
| Legal entities | Single virtual entity                     | PrivateBank DE/EU · PrivateBank UK · BCM US — separate Snowflake accounts |
| Record volume  | ~400 orders/day                           | ~800 k orders/day · 24 M records/month                                |
| Streaming      | Redis Streams + custom consumer           | Apache Kafka (Confluent Cloud) + Snowpipe                             |
| Orchestration  | Airflow LocalExecutor (Docker)            | Astronomer (Airflow on Kubernetes) + dbt Cloud                        |
| Dashboards     | Angular SPA only                          | Angular SPA + Tableau Embedded Analytics (Connected to Snowflake)     |
| Auth           | Custom JWT RS256                          | Okta OIDC/SAML → Snowflake SCIM + FastAPI JWT                         |
| Deployment     | Docker Compose (local)                    | Kubernetes (EKS/GKE) + Helm + ArgoCD                                  |
| IaC            | None                                      | Terraform (Snowflake objects, K8s resources, networking)              |


---

## 2. Context: PoC vs Production Delta

### Component Interaction Story (PoC)

In the PoC a single Docker Compose network hosts all layers. The `dag_ingest_batch` DAG writes dlt pipelines into `stg_raw` on a single PostgreSQL instance. dbt runs sequentially against the same host. FastAPI reads from `mart_trading_risk.fact_order_execution` via SQLAlchemy. Angular calls FastAPI behind an nginx reverse proxy. Redis carries intra-day fills for a single mock stream. There is no multi-tenancy across legal entities, no data residency separation, no HA, and no CI/CD.

### Verbose Component Interaction Story (Production)

On a production trading morning at 07:00 CET:

1. **OMS/EMS feeds** (FIX 4.4 gateway, Fidessa/Flextrade) publish order and fill events to **Confluent Cloud Kafka** topics partitioned by `legal_entity_id`. A dedicated topic exists per entity: `pb-de.fills`, `pb-uk.fills`, `bcm-us.fills`.
2. **Snowpipe connectors** (Kafka → Snowflake) land raw JSON into `stg_raw.orders_stream` and `stg_raw.fills_stream` transient tables inside each entity's Snowflake account with sub-minute latency. A Snowpipe micro-batch runs every 60 seconds.
3. **dbt Cloud** scheduled jobs run the full Data Vault 2.0 pipeline: staging → raw_vault → biz_vault → marts. The `bv_tca_costs` model recalculates Almgren-Chriss slippage. All dbt models run inside a dedicated **TRANSFORM** Snowflake virtual warehouse (L, auto-suspend 5 min) that is separate from the query warehouse.
4. **Tableau** connects to a dedicated **BI** Snowflake virtual warehouse (M, auto-suspend 2 min) via a Snowflake OAuth service account. Tableau Server (or Tableau Cloud) renders dashboards embedded in the Angular SPA. Row-level security is enforced at the Snowflake layer via **row access policies** keyed on `counterparty_id`; Tableau passes the authenticated user's `email` attribute to Snowflake, which maps it to a counterparty policy group.
5. **FastAPI** (deployed as a Kubernetes Deployment, 3–10 replicas behind an AWS ALB) authenticates users against **Okta** (OIDC), issues short-lived internal JWTs carrying `role`, `legal_entity`, and `counterparty_id`, and queries `mart_trading_risk.fact_order_execution` via the **API** Snowflake virtual warehouse (S, auto-suspend 1 min). All warehouse auto-suspend means zero compute cost at idle.
6. **MiFID II regulatory exports** are triggered by a dedicated Airflow DAG (`tca_mifid_rts27_eod`) that materialises a Snowflake table into an S3-encrypted object, signs it with a PGP key, and delivers it to the regulator's SFTP endpoint. A hash of the file is written to an immutable audit log table in Snowflake with `FAIL SAFE` enabled.

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL DATA SOURCES                                                          │
│  OMS/EMS (FIX 4.4)  ·  Market Data Vendors (Bloomberg/Refinitiv)               │
│  Eurex EDSP  ·  Bond Pricing  ·  Reference Data (Symphony/Fidessa)              │
└──────────────────────────────┬──────────────────────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   KAFKA             │  Confluent Cloud
                    │   (per legal entity │  · pb-de.* topics
                    │    topic partition) │  · pb-uk.* topics
                    │                    │  · bcm-us.* topics
                    └──────────┬──────────┘
                               │  Kafka Connect (Snowflake Sink)
              ┌────────────────▼──────────────────────┐
              │   SNOWFLAKE  (multi-account)           │
              │                                        │
              │  Account: pb-eu  (eu-central-1)       │
              │  Account: pb-uk  (eu-west-2)          │
              │  Account: bcm-us  (us-east-1)          │
              │                                        │
              │  Databases per account:                │
              │    RAW_DB       ← Snowpipe landing     │
              │    VAULT_DB     ← Data Vault 2.0       │
              │    MART_DB      ← Information Marts    │
              │    SHARED_DB    ← Cross-entity views   │
              │                                        │
              │  Global: Snowflake Data Sharing        │
              │  → mart_consolidated (read-only share) │
              └──────────┬────────────────────────────┘
                         │
         ┌───────────────┼──────────────────┐
         │               │                  │
   ┌─────▼─────┐  ┌──────▼──────┐  ┌───────▼──────┐
   │  dbt Cloud │  │  FastAPI    │  │   Tableau    │
   │  (TRANSFORM│  │  (K8s, 3-10 │  │   (BI WH)   │
   │   WH)      │  │   replicas) │  │   Embedded   │
   └─────┬─────┘  └──────┬──────┘  └───────┬──────┘
         │               │                  │
   ┌─────▼───────────────▼──────────────────▼──────┐
   │   KUBERNETES CLUSTER  (EKS / GKE)              │
   │   · FastAPI Deployment (HPA: 3–10 pods)       │
   │   · Astronomer Airflow                        │
   │   · Angular SPA (nginx, CDN-backed)           │
   │   · ML service (prediction microservice)      │
   └───────────────────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  OKTA (IdP)         │
              │  OIDC / SAML SSO    │
              │  SCIM → Snowflake   │
              └─────────────────────┘
```

---

## 4. Legal Entity Model

### 4.1 Account-per-Entity Strategy

Each legal entity maps to a dedicated Snowflake account. This satisfies data residency requirements (GDPR for EU/UK, SEC for US) and provides blast-radius isolation — a misconfiguration in the US account cannot affect EU data.


| Legal Entity    | Snowflake Account     | AWS Region  | Data Residency        | Regulatory Scope                 |
| --------------- | --------------------- | ----------- | --------------------- | -------------------------------- |
| PrivateBank DE/EU | `pb-eu.eu-central-1` | Frankfurt   | GDPR · MiFID II/MiFIR | EEA equities, FI, FX derivatives |
| PrivateBank UK    | `pb-uk.eu-west-2`    | London      | UK GDPR · UK MiFIR    | UK equities post-Brexit          |
| BCM US          | `bcm-us.us-east-1`    | N. Virginia | SEC Rule 606 · FINRA  | US equities, listed derivatives  |


### 4.2 Cross-Entity Consolidation via Snowflake Data Sharing

The global head-of-trading and risk views are served via a Snowflake **secure data share** from a dedicated consolidation account (`pb-global`) that holds read-only materialised copies of each entity's `mart_consolidated` database. Cross-entity joins never leave the Snowflake platform and incur no egress cost.

```
pb-eu  ──[secure share]──► pb-global.mart_consolidated.entity_pb_eu
pb-uk  ──[secure share]──► pb-global.mart_consolidated.entity_pb_uk
bcm-us  ──[secure share]──► pb-global.mart_consolidated.entity_bcm_us
```

Tableau connects to `pb-global` for the executive/consolidated dashboard; per-entity dashboards connect to the respective entity account.

### 4.3 Database Layout (per account)

```
RAW_DB
  └── STG_RAW          ← Snowpipe landing (transient tables, 1-day retention)
        ├── ORDERS_STREAM
        ├── FILLS_STREAM
        ├── TICK_BARS_STREAM
        └── RT_FILLS_STREAM

VAULT_DB
  ├── RAW_VAULT        ← Hubs · Links · Satellites (immutable, append-only)
  └── BIZ_VAULT        ← Derived Satellites · PIT · Bridge

MART_DB
  ├── MART_TRADING_RISK
  ├── MART_MARKET_DATA
  ├── MART_CORPORATE
  └── MART_CONSOLIDATED

SHARED_DB              ← Read-only data share (received from pb-global)
```

---

## 5. Ingestion Layer

### 5.1 Real-Time Path (replaces Redis Streams)

**Confluent Cloud Kafka** replaces Redis Streams. Kafka provides durable, replayable, partitioned logs — essential when 24 M records/month arrive across three entities with varying latencies (FIX fills are near-real-time; bond prices arrive T+1).

```
FIX Gateway → Kafka Connect (FIX Source Connector)
            → Topic: pb-de.orders   (partitioned by instrument_class)
            → Topic: pb-de.fills    (partitioned by counterparty_id)
            → Topic: pb-de.ticks    (partitioned by symbol)
```

**Snowflake Kafka Connector** (Snowpipe Streaming) writes from Kafka topics directly into Snowflake transient staging tables with exactly-once semantics. No bespoke consumer code to maintain.

### 5.2 Batch Path (replaces dlt Docker task)

**Fivetran** manages reference data sources (Bloomberg, Refinitiv, Fidessa) that expose REST or JDBC connectors. Fivetran's normalised schemas land directly into `RAW_DB.STG_RAW`. For sources without a Fivetran connector (bespoke OMS APIs), **dlt 1.5.x** pipelines run inside Kubernetes CronJobs, unchanged from the PoC modulo the destination connection string.

The key operational benefit: Fivetran handles schema evolution automatically. When Bloomberg adds a new field, it appears in staging without a pipeline code change.

### 5.3 Backfill Strategy (24 M records/month)

24 M records/month ≈ 800 k records/day ≈ 33 k records/hour. Snowpipe handles this comfortably — it is designed for tens of millions of rows per hour. For historical backfills (e.g., onboarding a new legal entity or reprocessing corrections):

- **Zero-copy cloning**: Clone the production `VAULT_DB` to a `BACKFILL_VAULT_DB` and run dbt against it without touching production.
- **Parallel COPY INTO**: Snowflake's `COPY INTO` can load from S3 in parallel using multiple files; a 24 M row backfill from S3 Parquet completes in 5–10 minutes on an XL warehouse.
- **Time Travel rollback**: If a backfill introduces bad data, `CREATE OR REPLACE TABLE ... CLONE ... AT (TIMESTAMP => ...)` restores the pre-backfill state within 90 days.

---

## 6. Snowflake Data Platform

### 6.1 Virtual Warehouse Strategy

Compute and storage are separated in Snowflake. Virtual warehouses are sized independently per workload type. All warehouses auto-suspend after idle time.


| Warehouse      | Size | Auto-suspend | Used by                              | Rationale                                                                |
| -------------- | ---- | ------------ | ------------------------------------ | ------------------------------------------------------------------------ |
| `WH_INGEST`    | S    | 2 min        | Snowpipe micro-batches, COPY INTO    | Low concurrency, frequent short bursts                                   |
| `WH_TRANSFORM` | L    | 5 min        | dbt Cloud jobs (vault + mart builds) | CPU-intensive joins on 24 M rows; L reduces wall-clock time              |
| `WH_API`       | S    | 1 min        | FastAPI / tca_service queries        | Low-latency reads, high concurrency; S is sufficient with result caching |
| `WH_BI`        | M    | 2 min        | Tableau extracts and live queries    | Dashboard refresh cadence; M handles 50 concurrent Tableau sessions      |
| `WH_BACKFILL`  | XL   | 10 min       | Historical backfill jobs (on-demand) | Spin up only for bulk loads; XL = 16× parallelism                        |
| `WH_ANALYTICS` | M    | 5 min        | ML scoring, ad-hoc analytics         | Snowpark Python UDFs, anomaly detection                                  |


Estimated monthly credit consumption at steady state: ~400 credits/month ≈ $800–1,200/month at list price before enterprise discounts.

### 6.2 Data Vault 2.0 on Snowflake

The PoC Data Vault 2.0 model is preserved with minor adaptations:

- **Hash keys**: `SHA2(...)` replaces `MD5(...)` (Snowflake recommends SHA2-256 for collision resistance).
- **Clustering keys**: Hubs and satellites are clustered on `_loaded_at::date` + business key for partition pruning on incremental runs.
- **Automatic Clustering**: Enabled on `fact_order_execution` and `sat_fill_execution` (high-cardinality, time-partitioned tables). Snowflake reclusters in the background with no manual maintenance.
- **Transient tables** for `stg_raw` (no Fail-Safe overhead; data is ephemeral by design).
- **Permanent tables** for `raw_vault`, `biz_vault`, `mart_`* (90-day Time Travel + 7-day Fail-Safe).

### 6.3 Row-Level Security

Snowflake **row access policies** enforce counterparty isolation at the storage layer — independent of the application layer. Even a direct SQL query by a Tableau user cannot bypass it.

```sql
CREATE ROW ACCESS POLICY rls_counterparty
  AS (counterparty_id VARCHAR) RETURNS BOOLEAN ->
    CURRENT_ROLE() IN ('ROLE_ADMIN', 'ROLE_HEAD_OF_TRADING')
    OR counterparty_id = GET(CURRENT_USER_ATTRIBUTES(), 'counterparty_id');

ALTER TABLE mart_trading_risk.fact_order_execution
  ADD ROW ACCESS POLICY rls_counterparty ON (counterparty_id);
```

Okta attributes (`counterparty_id`, `role`) are synced to Snowflake via **SCIM**, mapped to Snowflake custom user attributes. The policy evaluates per-row at query time with no performance degradation (Snowflake applies the predicate during pruning).

### 6.4 Column-Level Security (PII / Sensitive Data)

**Dynamic Data Masking** on `sat_client_profile` columns (client name, LEI, contact details):

```sql
CREATE MASKING POLICY mask_pii
  AS (val STRING) RETURNS STRING ->
    CASE WHEN CURRENT_ROLE() IN ('ROLE_COMPLIANCE', 'ROLE_ADMIN') THEN val
         ELSE '***MASKED***'
    END;
```

COMPLIANCE and ADMIN roles see plain text; TRADER and CLIENT roles see masked values. MiFID II Article 26 transaction reporting fields (LEI, counterparty identifiers) are exempt from masking for COMPLIANCE users.

---

## 7. Transformation Layer — dbt on Snowflake

### 7.1 dbt Profile Change

The migration from PostgreSQL to Snowflake requires a single `profiles.yml` change and a `dbt_project.yml` adapter switch from `dbt-postgres` to `dbt-snowflake`. All model SQL, macros, and tests are ANSI-compatible and require no changes beyond:

- Replace `EXTRACT(HOUR FROM ...)` with `DATE_PART('hour', ...)` (Snowflake preference).
- Replace `generate_surrogate_key` arguments — `dbt_utils` handles both dialects identically.
- Remove `TimescaleDB`-specific hypertable comments (irrelevant on Snowflake; time-series is handled by clustering).

### 7.2 dbt Cloud Multi-Environment Setup

```
Environments:
  dev       → VAULT_DB_DEV,  WH_TRANSFORM_DEV   (developer sandbox)
  ci        → VAULT_DB_CI,   WH_TRANSFORM_CI    (PR validation, zero-copy clone of prod)
  staging   → VAULT_DB_STG,  WH_TRANSFORM_STG   (UAT, Tableau acceptance)
  prod      → VAULT_DB_PROD, WH_TRANSFORM_PROD  (live)

Jobs:
  ingest_staging    → runs every 15 min (dbt build --select staging.*)
  raw_vault_morning → runs 07:30 CET Mon–Fri (dbt build --select raw_vault.*)
  biz_vault_eod     → runs 18:00 CET Mon–Fri (dbt build --select biz_vault.*)
  marts_eod         → runs 18:30 CET Mon–Fri (dbt build --select marts.*)
  full_refresh_wkly → runs Sat 03:00 CET    (dbt build --full-refresh)
```

dbt Cloud's **slim CI** (state-based comparison) only runs models whose upstream sources or SQL changed in the PR, keeping CI build times under 3 minutes even on a large project.

### 7.3 Multi-Entity dbt Projects

Each legal entity is a **separate dbt project** sharing a common `packages` library (`dbt_privatebank_core`) published as a private dbt Hub package. This provides:

- Shared macros (`generate_surrogate_key`, `almgren_chriss_impact`, `mifid_waiver_classify`)
- Shared tests (counterparty isolation assertions)
- Entity-specific overrides via `vars` (currency, trading hours, regulatory scope)

```yaml
# dbt_project.yml for pb-uk
vars:
  legal_entity: "PB_UK"
  base_currency: "GBP"
  trading_hours_start: 8
  trading_hours_end: 16
  mifid_jurisdiction: "UK_MIFIR"
```

---

## 8. Analytics & ML Layer

### 8.1 Snowpark Python (replaces Docker-based analytics engine)

The PoC's `analytics/engine.py` and 10 TCA modules run as Snowpark Python **stored procedures** and **UDFs** inside Snowflake. Computation runs on the `WH_ANALYTICS` warehouse, co-located with the data — no data leaves Snowflake, no serialisation overhead, no network round-trips.

```python
# analytics/modules/cost_decomposition_sp.py
import snowflake.snowpark as snowpark
from snowflake.snowpark.functions import udf, col

@udf(name="almgren_chriss_impact", is_permanent=True,
     stage_location="@analytics_stage", replace=True)
def almgren_chriss_impact(quantity: float, adv: float,
                          sigma: float, eta: float) -> float:
    if adv == 0:
        return 0.0
    return eta * sigma * (quantity / adv) ** 0.5 * 10_000
```

Snowpark procedures eliminate the separate analytics container, reducing operational surface and cost.

### 8.2 ML Pipeline (replaces joblib/sklearn pkl files)

The Execution Quality Predictor (`GradientBoostingRegressor` per instrument class) is promoted to a **Snowflake Model Registry** artifact:

- Model training runs as a Snowpark ML job on `WH_ANALYTICS` (XL) triggered by the weekly Airflow DAG.
- Model artifacts are stored in the Snowflake Model Registry (not on the filesystem).
- Inference runs as a vectorised UDF called directly from dbt models (`bv_pre_trade_estimate`).
- No separate model-serving infrastructure; inference is embedded in the SQL pipeline.

For more complex models (transformer-based anomaly detection), **SageMaker** or **Vertex AI** endpoints are called from a dedicated FastAPI ML microservice in Kubernetes, with results written back to Snowflake via batch INSERT.

---

## 9. API Layer

### 9.1 FastAPI on Kubernetes

FastAPI is containerised unchanged from the PoC (Python 3.11, same router structure). In production it runs as a Kubernetes `Deployment` with Horizontal Pod Autoscaler (HPA):

```yaml
minReplicas: 3
maxReplicas: 20
metrics:
  - type: Resource
    resource:
      name: cpu
      target: averageUtilization: 65
```

Requests hit an **AWS Application Load Balancer** → nginx ingress → FastAPI pods. Each pod holds a Snowflake connection pool (max 5 connections per pod, `snowflake-connector-python` with `arrow` serialisation for sub-100ms mart queries).

### 9.2 Authentication — Okta → JWT

Custom bcrypt auth is replaced by **Okta OIDC**:

1. Angular redirects to Okta for login (PKCE flow).
2. Okta returns an `id_token` (OIDC) with custom claims: `privatebank_role`, `counterparty_id`, `legal_entity`.
3. Angular exchanges the Okta token at FastAPI `POST /auth/token/exchange` for an internal RS256 JWT (8 h) maintaining backward compatibility with the existing interceptor/guard pattern.
4. Snowflake SCIM integration auto-provisions Snowflake users and maps Okta groups to Snowflake roles. No manual role management.

This preserves the Angular RBAC model (authGuard, roleGuard, NgRx auth state) with zero frontend changes.

---

## 10. Frontend & Tableau Integration

### 10.1 Angular SPA — Production Deployment

The Angular SPA is built to static assets (CI pipeline) and served from **Amazon CloudFront** (CDN) backed by **S3**. Nginx is eliminated from the deployment path; CloudFront handles routing, HTTPS termination, and cache headers. API calls proxy to the ALB via CloudFront behaviour rules.

Benefits: global sub-50ms asset delivery, automatic HTTPS, no nginx container to maintain.

### 10.2 Tableau Embedded Analytics

Tableau replaces the hand-rolled Angular chart components for complex analytical views. The Angular SPA embeds Tableau dashboards using the **Tableau Embedding API v3** (Web Component):

```html
<!-- Angular component template -->
<tableau-viz
  id="tableau-tca-dashboard"
  src="https://tableau.privatebank.com/views/TCA/OrderSlippage"
  [token]="tableauJwt"
  toolbar="hidden"
  hide-tabs>
</tableau-viz>
```

**Connected Tableau Dashboards (7 views):**


| View                     | Replaces Angular Component     | Audience           |
| ------------------------ | ------------------------------ | ------------------ |
| Daily TCA Summary        | DashboardComponent KPI cards   | All internal roles |
| Order Cost Decomposition | OrderTcaComponent detail panel | TRADER+            |
| Algo League Table        | AlgoPerfComponent              | TRADER+            |
| Alpha Decay Curves       | AlphaDecayComponent            | TRADER+            |
| Venue / SOR Scorecard    | VenueSorComponent              | TRADER+            |
| MiFID RTS 27/28 Export   | MifidComponent                 | COMPLIANCE         |
| Client Portfolio View    | ClientViewComponent            | CLIENT             |


### 10.3 Tableau Security — JWT Connected Apps

Tableau Server (or Tableau Cloud) is configured with a **Connected App** (JWT-based). The Angular backend generates a short-lived Tableau JWT embedding token at dashboard load time:

```python
# FastAPI: POST /tableau/embed-token
import jwt, time, uuid

def generate_tableau_jwt(user_email: str, role: str) -> str:
    payload = {
        "iss": TABLEAU_CONNECTED_APP_CLIENT_ID,
        "exp": time.time() + 600,           # 10-minute embedding token
        "jti": str(uuid.uuid4()),
        "aud": "tableau",
        "sub": user_email,
        "scp": ["tableau:views:embed"],
        "https://tableau.com/oda": True,
    }
    return jwt.encode(payload, TABLEAU_CONNECTED_APP_SECRET, algorithm="HS256")
```

Snowflake row access policies enforce data isolation **at the database layer**, so Tableau cannot return data the user is not entitled to regardless of what SQL the dashboard executes. Tableau's own user filters provide a second presentation-layer guard (defence in depth).

### 10.4 Tableau to Snowflake — Live Connection

Tableau uses a **live connection** (not extract) to the Snowflake `WH_BI` warehouse. Key configuration:

- **OAuth** service account (not username/password) registered in Tableau Server.
- **Initial SQL**: `ALTER SESSION SET QUERY_TAG = 'tableau_user=<USER>'` — all Tableau queries appear in Snowflake Query History tagged by end-user for cost allocation.
- **Result caching**: Snowflake caches identical query results for 24 hours. Dashboard filters that different users apply to the same underlying query hit the cache, reducing BI warehouse credits.

---

## 11. Orchestration

### 11.1 Astronomer (Airflow on Kubernetes)

Airflow LocalExecutor (single-process Docker) is replaced by **Astronomer** (managed Airflow on Kubernetes). Benefits:

- **KubernetesExecutor**: each task runs in an isolated pod; no shared state, auto-scaling, pod-level resource limits.
- **Astronomer Cloud**: managed control plane (no Airflow metadata DB to maintain), built-in log storage, alerting, RBAC, Okta SSO.
- **Separate task queues** per DAG type: `ingest` queue (S pod), `transform` queue (L pod), `ml` queue (XL pod with GPU if needed).

### 11.2 DAG Inventory (Production)


| DAG                     | Schedule                  | What it does                                                                    |
| ----------------------- | ------------------------- | ------------------------------------------------------------------------------- |
| `tca_ingest_realtime`   | Continuous (Kafka sensor) | Monitors Kafka lag; triggers dbt `staging.`* micro-refresh when lag > 1000 msgs |
| `tca_raw_vault_morning` | 07:30 CET Mon–Fri         | dbt raw_vault full run + schema tests; alerts on failure                        |
| `tca_biz_vault_eod`     | 18:00 CET Mon–Fri         | dbt biz_vault + Snowpark analytics stored procs                                 |
| `tca_marts_eod`         | 18:30 CET Mon–Fri         | dbt marts + Tableau extract refresh trigger                                     |
| `tca_mifid_rts27_eod`   | 18:45 CET Mon–Fri         | Snowflake → S3 export, PGP sign, SFTP to ESMA/FCA                               |
| `tca_ml_train_weekly`   | Sat 04:00 CET             | Retrain Snowpark ML models; push to Snowflake Model Registry                    |
| `tca_backfill_ondemand` | Manual trigger            | Parametrised backfill: entity, date range, layers                               |
| `tca_weekly_reports`    | Mon 07:00 CET             | Algo/trader/venue digest CSVs → SharePoint / S3                                 |
| `tca_data_quality`      | Every 4 h                 | Monte Carlo / Great Expectations checks; write to `obs.obs_warnings`            |


---

## 12. Assessment by Dimension

### 12.1 Scalability

**Storage**: Snowflake storage is object-based (S3) and scales infinitely. 24 M records/month at ~2 KB average = ~50 GB/month of raw data, negligible at Snowflake's $23/TB/month storage rate.

**Compute**: Virtual warehouses scale independently. A warehouse can go from S (1 server) to 6XL (128 servers) in ~10 seconds. Multi-cluster warehouses automatically add clusters when concurrency spikes (e.g., 200 analysts running Tableau at market close).

**Kafka**: Confluent Cloud scales partition count and broker capacity with no downtime. Partitioning by `instrument_class` ensures parallel ingestion across the 4 asset classes.

**FastAPI**: Kubernetes HPA scales pods from 3 to 20 based on CPU/memory, handling peak load (market open, post-trade windows) without manual intervention.

**Data Vault 2.0**: The append-only vault model scales linearly. Adding a new source system (e.g., a new venue) requires adding one Hub, one or two Links, and one Satellite — the existing tables are untouched.

**Backfill at scale**: Snowflake `COPY INTO` with 100+ parallel files achieves 200 M+ rows/hour on an XL warehouse. The 24 M/month volume is ingested comfortably in a daily batch window.

---

### 12.2 Extensibility

**New legal entity**: Add a Snowflake account, configure Kafka topics for the entity prefix, set the dbt `legal_entity` var, add the account to the `pb-global` data share. Estimated effort: 1 sprint.

**New asset class**: Add rows to `dim_instrument`, extend `sat_instrument_ref`, add a dbt var for the class, add a Kafka topic partition. All existing models continue unchanged.

**New TCA module**: Add a Snowpark stored procedure, a dbt model in `biz_vault`, and a FastAPI endpoint. No schema changes to hubs or links.

**New regulatory requirement (e.g., DORA, EMIR)**: Add a satellite in biz_vault for the required fields, a dbt model in marts, and a report DAG. The immutable Raw Vault means historical reprocessing is always possible.

**New dashboard**: Connect Tableau to the existing Snowflake mart. No API changes needed; Tableau's semantic layer handles the presentation.

---

### 12.3 Maintainability

**Infrastructure as Code (Terraform)**: Every Snowflake object (warehouses, databases, schemas, roles, policies) is defined in Terraform. `terraform plan` shows the diff before any change is applied. State is stored in Terraform Cloud (or S3 backend).

```
terraform/
  ├── snowflake/
  │   ├── accounts.tf       # one resource per legal entity account
  │   ├── warehouses.tf     # WH_INGEST, WH_TRANSFORM, WH_API, WH_BI
  │   ├── databases.tf      # RAW_DB, VAULT_DB, MART_DB per entity
  │   ├── roles.tf          # ROLE_ADMIN, ROLE_TRADER, etc.
  │   └── policies.tf       # row access + masking policies
  ├── kubernetes/
  │   ├── fastapi/          # Deployment, Service, HPA, Ingress
  │   └── astronomer/       # Astronomer Helm values
  └── confluent/
      └── topics.tf         # Kafka topics per entity
```

**dbt project structure**: Each logical layer (staging, raw_vault, biz_vault, marts) is a separate dbt subfolder with its own `schema.yml`. Junior analysts can add marts without touching the vault. Senior engineers own the vault layer.

**Dependency management**: `requirements.txt` → `pyproject.toml` with `uv` or `Poetry` for locked, reproducible Python environments. dbt packages pinned in `packages.yml`.

**Runbooks**: Every DAG has a linked Confluence runbook. Airflow task failures include a `doc_md` link to the runbook in the task definition.

---

### 12.4 Security

**Authentication**: Okta OIDC/SAML replaces custom bcrypt. MFA enforced for all internal users. CLIENT counterparties use Okta Customer Identity (CIAM) with separate tenant.

**Authorisation — 7 layers in production** (up from 5 in PoC):


| Layer    | Component                   | Mechanism                                        |
| -------- | --------------------------- | ------------------------------------------------ |
| IdP      | Okta                        | MFA, device trust, IP allowlisting               |
| Token    | RS256 JWT                   | `role`, `counterparty_id`, `legal_entity` claims |
| API      | FastAPI RBAC                | `require_role()` on every endpoint               |
| Database | Snowflake row access policy | `counterparty_id = current_user_attribute()`     |
| Database | Snowflake column masking    | PII masked for non-COMPLIANCE roles              |
| BI       | Tableau user filter         | Presentation-layer counterparty filter           |
| Network  | Snowflake Private Link      | No traffic over public internet                  |


**Network security**: Snowflake accounts are accessed exclusively via AWS PrivateLink (VPC endpoint). FastAPI pods connect to Snowflake via the private endpoint. No Snowflake credentials travel over the public internet.

**Secrets management**: AWS Secrets Manager (or HashiCorp Vault) stores Snowflake credentials, Kafka API keys, Okta client secrets, and PGP keys. Kubernetes pods access secrets via the Secrets Store CSI Driver. No secrets in environment variables or Docker images.

**Encryption**: All Snowflake data encrypted at rest (AES-256, Snowflake-managed keys). **Tri-Secret Secure** option available (customer-managed keys in AWS KMS) for maximum control over key lifecycle. TLS 1.3 for all in-transit communication.

**Vulnerability scanning**: Container images scanned by Trivy in CI. Snowflake connector dependencies audited via `pip-audit` in the GitHub Actions workflow.

---

### 12.5 Reliability

**Snowflake SLA**: 99.99% uptime. Multi-cluster warehouses survive a node failure with no query interruption. Data stored in S3 with 11-nines durability.

**Time Travel (90 days)**: Any table can be restored to any point in the last 90 days with `CREATE TABLE ... CLONE ... AT (TIMESTAMP => ...)`. Critical for regulatory corrections and backfill rollback.

**Fail-Safe (7 days)**: After Time Travel expiry, Snowflake retains data for 7 additional days in a non-queryable Fail-Safe zone, recoverable by Snowflake Support. This provides 97 total days of data recovery window.

**Kafka**: Confluent Cloud's multi-AZ replication factor of 3 ensures no message loss even if an AZ fails. Consumer group offset management means Snowpipe can replay from any offset on failure.

**FastAPI**: Kubernetes liveness and readiness probes restart unhealthy pods automatically. ALB health checks remove failed pods from rotation within 30 seconds. `PodDisruptionBudget` ensures at least 2 replicas remain available during node maintenance.

**Database connection pooling**: `snowflake-connector-python` connection pool per pod (max 5) with exponential backoff retry on transient Snowflake errors (warehouse suspension, concurrency limits).

**Circuit breaker**: Envoy sidecar (via Istio service mesh) implements circuit breaker for FastAPI → Snowflake calls. If Snowflake is unreachable, the circuit opens and FastAPI returns `503` with `Retry-After` rather than queuing indefinitely.

---

### 12.6 Auditability

**Snowflake Account Usage schema**: Every query executed against any Snowflake object is recorded in `SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY` for 365 days. Includes user, warehouse, role, bytes scanned, execution time, and full SQL text. This is the primary MiFID II audit trail for data access.

**Immutable audit log table**: A dedicated `AUDIT_DB.ACCESS_LOG` table (append-only, no DELETE privilege granted to any role) records every FastAPI request carrying financial data: timestamp, user, endpoint, `counterparty_id`, `legal_entity`, and HTTP status.

```sql
-- Enforced via Snowflake governance
GRANT INSERT ON AUDIT_DB.ACCESS_LOG TO ROLE API_WRITER;
-- No DELETE, no UPDATE, no TRUNCATE granted to any operational role
```

**MiFID II Article 25 record-keeping**: All trade reports are hashed (SHA-256) before S3 delivery. The hash and delivery timestamp are written to `AUDIT_DB.MIFID_SUBMISSIONS`. Regulators can verify report integrity by re-hashing the S3 object.

**Data lineage**: dbt generates a full lineage graph (source → staging → raw_vault → biz_vault → mart) exported to JSON by `dbt docs generate` and ingested into **OpenMetadata** or **Datahub** for business-level lineage exploration. Any field in a regulatory report is traceable to its raw source column.

**Change Data Capture**: The Data Vault 2.0 satellite pattern preserves every historical state of every fact. `sat_fill_execution` records every version of a fill (original + corrections) with `_loaded_at` and `_valid_from` timestamps. Point-in-time reconstruction is possible for any past regulatory reporting date.

---

### 12.7 Observability & Testing

#### Observability Stack


| Layer              | Tool                                | What it monitors                                                                         |
| ------------------ | ----------------------------------- | ---------------------------------------------------------------------------------------- |
| Infrastructure     | Datadog / CloudWatch                | CPU, memory, network for K8s pods, Kafka brokers                                         |
| Application        | Datadog APM (OpenTelemetry traces)  | FastAPI request latency, error rate, Snowflake query time per endpoint                   |
| Data quality       | Monte Carlo                         | Table freshness, row count anomalies, null rate drift, schema changes                    |
| Data quality (dbt) | dbt tests + dbt-expectations        | Schema tests on every model; singular tests for business rules                           |
| Snowflake          | Account Usage dashboards in Tableau | Warehouse credit consumption, query spill, long-running queries                          |
| Alerting           | PagerDuty                           | P1: pipeline failure before market open; P2: data quality breach; P3: API latency >500ms |


#### Testing Pyramid

**Unit tests (pytest)**

- All analytics modules tested with in-memory DataFrames (Polars or pandas).
- Almgren-Chriss formula, alpha decay regime classification, MiFID waiver logic.
- 80% minimum line coverage enforced in CI.

**Integration tests**

- Run against Snowflake `dev` environment (zero-copy clone of prod schema, synthetic data).
- FastAPI test client (`httpx.AsyncClient`) with a live Snowflake `dev` connection.
- Counterparty isolation verified: CLIENT token cannot retrieve another counterparty's orders (expect 404).
- Role escalation verified: TRADER cannot call `/mifid/export` (expect 403).

**dbt tests**

- `unique` + `not_null` on every primary key (enforced via `dbt_project.yml` global config).
- `relationships` on every foreign key.
- `accepted_values` on `instrument_class`, `side`, `vol_regime`, `execution_quality`.
- Singular test: `assert_no_counterparty_leakage.sql` — verifies no CLIENT can see another counterparty's `fact_order_execution` rows via the row access policy.
- Source freshness: error if `stg_raw.orders` is >4 hours stale on a trading day.

**Contract tests (Pact)**

- Angular → FastAPI contracts published to a Pact Broker.
- FastAPI PR CI verifies the new API response still satisfies all Angular consumer contracts before merge.
- Prevents breaking API changes from reaching the Angular SPA.

**Load tests (k6)**

- Run weekly against the staging environment.
- 200 concurrent Tableau sessions on `WH_BI` (S): p99 query latency < 2 s.
- 50 concurrent FastAPI requests: p99 < 150 ms.

---

### 12.8 Portability

**Warehouse portability**: dbt's adapter pattern means the entire transformation layer can run on BigQuery or Databricks by changing the `profiles.yml` adapter. The Data Vault SQL is ANSI-compatible with minor function substitutions (abstracted into macros). Tested migration path: Snowflake → BigQuery requires ~2 weeks of macro adaptation.

**Kafka portability**: Kafka topics are consumed by Snowpipe today. Replacing Snowpipe with a Databricks Auto Loader or BigQuery Storage Write API connector requires only a Kafka Consumer code change — the topics and producers are unchanged.

**Container portability**: FastAPI Docker images are platform-agnostic (linux/amd64). Kubernetes manifests deploy unchanged to EKS (AWS), GKE (GCP), or AKS (Azure). Helm charts parameterise cloud-specific values (load balancer type, storage class).

**Tableau portability**: If Tableau is replaced by Power BI or Looker, the Snowflake marts are the stable interface. No mart SQL changes are required; only the BI tool's connection and dashboard configuration changes.

---

### 12.9 CI/CD & Deployment

#### Pipeline Overview

```
Developer push to feature branch
  │
  ├── GitHub Actions: lint (ruff, sqlfluff), unit tests (pytest), type check (mypy)
  │
  ├── dbt Cloud: slim CI job (only changed models + tests)
  │
  ├── Pact: publish consumer contracts
  │
  └── Docker: build + push image to ECR (tagged with git SHA)

PR merged to main
  │
  ├── GitHub Actions: integration tests against Snowflake dev (zero-copy clone)
  │
  ├── Terraform: plan against staging (human approval required for prod)
  │
  ├── ArgoCD: deploy to staging (Kubernetes rolling update, 0 downtime)
  │
  └── k6: smoke load test against staging

Release tag pushed (vX.Y.Z)
  │
  ├── ArgoCD: promote staging image to prod (blue/green or canary)
  │
  ├── dbt Cloud: run prod dbt job against VAULT_DB_PROD
  │
  └── PagerDuty: post-deploy health check (synthetic monitor)
```

#### Key Practices

**Blue/green deployments**: Two identical K8s Deployments (`blue` and `green`) share the ALB. Traffic shifts atomically via ALB target group weight. Rollback is a weight change — zero rebuild required.

**dbt state-based CI**: `dbt build --select state:modified+` only rebuilds models affected by the PR. A PR touching only `bv_tca_costs.sql` rebuilds that model and its mart descendants — not the entire vault.

**Snowflake zero-copy clone for CI**: CI environments clone production schemas in under 1 second with no storage cost. Each PR gets a dedicated schema `VAULT_DB_CI_PR_<number>` that is dropped after the CI job completes.

**Feature flags**: **LaunchDarkly** controls rollout of new API endpoints and Tableau dashboards to subsets of users (e.g., new pre-trade ML model exposed first to HEAD_OF_TRADING only).

**Semantic versioning of the API**: FastAPI versioned routes (`/api/v1/`, `/api/v2/`) allow breaking changes without immediate client migration. dbt exposes mart versions via `config(alias='fact_order_execution_v2')`.

---

### 12.10 Cost Model

#### Monthly cost estimate (steady-state, all 3 legal entities)


| Component                             | Unit                                      | Volume                                     | Estimated Monthly Cost |
| ------------------------------------- | ----------------------------------------- | ------------------------------------------ | ---------------------- |
| Snowflake storage                     | $23/TB/month                              | ~150 GB (raw + vault + marts × 3 entities) | ~$3.50                 |
| Snowflake compute — WH_TRANSFORM      | L warehouse, 2 h/day × 22 days            | ~44 credits                                | ~$110                  |
| Snowflake compute — WH_API            | S warehouse, ~8 h/day active              | ~22 credits                                | ~$55                   |
| Snowflake compute — WH_BI             | M warehouse, ~6 h/day                     | ~33 credits                                | ~$83                   |
| Snowflake compute — WH_INGEST         | S warehouse, 24 h/day (Snowpipe)          | ~66 credits                                | ~$165                  |
| Snowflake compute — WH_ANALYTICS (ML) | M warehouse, 4 h/week                     | ~8 credits                                 | ~$20                   |
| Confluent Cloud Kafka                 | ~500 MB/day ingest                        | ~15 GB/month                               | ~$150                  |
| Kubernetes (EKS)                      | 3-node cluster (m5.xlarge) + HPA burst    | —                                          | ~$300                  |
| Astronomer                            | Managed Airflow                           | —                                          | ~$400                  |
| dbt Cloud                             | Team plan (3 entities, unlimited jobs)    | —                                          | ~$500                  |
| Tableau Cloud                         | Per-user licences (20 Creator, 50 Viewer) | —                                          | ~$2,000                |
| Okta                                  | Per-user (100 internal + 500 CLIENT)      | —                                          | ~$500                  |
| **Total**                             |                                           |                                            | **~$4,300/month**      |


> Enterprise Snowflake contracts typically reduce credit price by 30–50% from list price. Tableau and Okta are negotiated annually. Real total cost at PrivateBank's scale: likely **$3,000–3,500/month** for the data platform components, excluding Tableau licences which are a separate commercial agreement.

#### Cost control mechanisms

- **Warehouse auto-suspend**: zero compute cost during off-market hours (weekends, overnight).
- **Tableau result caching**: repeated dashboard refreshes hit Snowflake's result cache (free) rather than re-executing warehouse queries.
- **Snowflake resource monitors**: alerts and suspends warehouses if monthly credit consumption exceeds threshold (prevents runaway queries).
- **Data retention tuning**: `stg_raw` transient tables (0-day Fail-Safe) vs. vault permanent tables (90-day Time Travel) — optimal balance of recovery capability vs. storage cost.

---

## 13. Migration Path from PoC

The PoC is designed for migration. Each layer can be upgraded independently:


| Phase                        | Duration | What changes                                                                                      | What stays                                                      |
| ---------------------------- | -------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| **1 — Snowflake foundation** | 2 weeks  | Provision Snowflake accounts (Terraform), set up Okta SCIM, change dbt profile                    | dbt SQL, mart schemas, all Angular code                         |
| **2 — Streaming**            | 2 weeks  | Replace Redis with Confluent Kafka + Snowpipe connector                                           | Kafka producer code (FIX adapter) inherits Redis producer logic |
| **3 — Kubernetes**           | 2 weeks  | Containerise FastAPI into K8s Deployment, set up ALB ingress                                      | FastAPI code unchanged                                          |
| **4 — dbt Cloud + CI**       | 1 week   | Point dbt profiles to Snowflake, set up dbt Cloud jobs, configure slim CI                         | All dbt models                                                  |
| **5 — Tableau**              | 3 weeks  | Build 7 Tableau dashboards connected to Snowflake; embed via JS API                               | Angular shell (embedding replaces individual chart components)  |
| **6 — Snowpark ML**          | 2 weeks  | Migrate `execution_quality_predictor.py` to Snowpark stored procedure, register in Model Registry | Model logic (same sklearn model, Snowpark wrapper)              |
| **7 — Observability**        | 1 week   | Deploy Monte Carlo, configure Datadog APM, wire PagerDuty                                         | Existing anomaly_detector logic (Monte Carlo wraps it)          |


**Total estimated migration effort: 13 weeks (3 engineers).**

The PoC's deliberate use of dbt (warehouse-agnostic), standard JWT RBAC, and containerised FastAPI means migration is a configuration and tooling change, not a rewrite.

---

## 14. Decision Register


| Decision                     | Chosen                      | Rejected alternative                   | Rationale                                                                                                                |
| ---------------------------- | --------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Data warehouse               | Snowflake                   | BigQuery, Databricks                   | Native Data Vault support, Snowpipe, best-in-class Tableau connector, familiar SQL, strongest Tri-Secret Secure offering |
| One account per legal entity | Separate accounts           | Single account, multiple databases     | Data residency isolation required by GDPR (EU/UK); SEC data may not reside in EU datacentres                             |
| Streaming                    | Confluent Kafka             | AWS Kinesis, Azure Event Hubs          | Kafka is OMS-vendor-standard (FIX connectors); Confluent's Snowflake Sink Connector is production-grade                  |
| Orchestration                | Astronomer                  | dbt Cloud workflows, Prefect, Dagster  | Airflow operator familiarity, KubernetesExecutor, Astronomer's managed control plane eliminates meta-DB ops              |
| BI layer                     | Tableau                     | Power BI, Looker, Metabase             | Industry standard in sell-side/buy-side TCA; existing PrivateBank licences; best Snowflake connector with OAuth            |
| Auth                         | Okta OIDC + internal JWT    | Azure AD, Ping Identity                | Okta's CIAM product handles both internal staff (SAML) and external CLIENT counterparties (CIAM) in one IdP              |
| Data Vault version           | DV 2.0 (preserved from PoC) | Anchor Modelling, raw star schema      | DV 2.0 append-only satellites are optimal for Snowflake's micro-partition storage; no performance penalty                |
| ML serving                   | Snowpark stored procedure   | SageMaker endpoint, FastAPI ML service | Co-location with data eliminates serialisation overhead; simpler ops for the prediction volume (~1 M calls/day)          |
| Container registry           | Amazon ECR                  | Docker Hub, GCR                        | Co-located with EKS; IAM-based auth; private scanning integration                                                        |
| IaC                          | Terraform                   | Pulumi, AWS CDK                        | Terraform providers for Snowflake, Confluent, and Kubernetes are mature; widest team familiarity                         |


