# PrivateBank TCA Platform

Transaction Cost Analysis platform for PrivateBank's pan-European institutional equities business. MiFID II / MiFIR compliant. Fully containerised, local-only PoC with 400 synthetic orders across four asset classes.

---

## Architecture overview

```mermaid
flowchart TB
    subgraph EXT["External / Synthetic Sources"]
        OMS["OMS/EMS\n400 synthetic orders\n(Faker + NumPy GBM)"]
        MD["Market Data\n1 020 × 30s OHLCV bars\nper instrument"]
        REF["Reference Data\ninstruments · clients\nvenues · algos · traders"]
        FI["Fixed Income Pricing\nbond prices · DV01"]
        EU["Eurex EDSP\nfutures settlements"]
        MOCK["Mock Server\n:8001\nFastAPI"]
    end

    subgraph INGEST["Ingestion Layer"]
        DLT["dlt 1.5.0 pipelines\noms_source\nmarket_data_source\nref_data_source\nfi_pricing_source\neurex_source"]
        REDIS_S["Redis Streams\pb:fills\pb:orders\pb:market_ticks"]
        CONS["Redis Consumer\nXREADGROUP → stg_raw.rt_fills"]
    end

    subgraph STORE["Storage — PostgreSQL 16 + TimescaleDB"]
        STG["stg_raw\nlanding zone\n(dlt-managed)"]
        RV["raw_vault\nHubs · Links · Satellites"]
        BV["biz_vault\nDerived Sats · PIT · Bridge"]
        MART["Information Marts\nmart_trading_risk\nmart_market_data\nmart_corporate\nmart_consolidated"]
        OBS["obs\nwarnings · quarantine"]
        AUTH["auth\napi_clients · refresh_tokens"]
    end

    subgraph DBT["dbt Data Vault 2.0"]
        STG_M["Staging views\nstg_orders · stg_fills\nstg_tick_bars · stg_clients\nstg_instruments · stg_bond_prices"]
        RV_M["Raw Vault models\n8 Hubs · 6 Links · 7 Satellites"]
        BV_M["Business Vault models\nbv_order_enriched · bv_tca_costs\nbv_alpha_decay · bv_adverse_selection\nbv_peer_benchmark · bv_mifid_fields\nbv_trader_attribution · pit_order_snapshot"]
        MART_M["Mart models\nfact_order_execution · fact_price_benchmark\nfact_client_activity · dim_* tables\nentity_pb_de/uk · entity_bcm_us"]
    end

    subgraph ANALYTICS["Analytics Engine"]
        ENG["engine.py\norchestrator"]
        MOD["10 TCA Modules\ncost_decomposition · adverse_selection\nvenue_sor · fill_pattern · peer_benchmarking\neurex_derivatives · fixed_income_tca\nfx_derivatives_tca · mifid_compliance · pre_trade"]
        ATTR["attribution.py\nmarket/algo/trader cost split"]
        ALPHA["alpha_decay.py\nLOW / MEDIUM / HIGH vol regimes"]
        ANOM["anomaly_detector.py\nZ-score · volume checks"]
        QUAR["quarantine.py\nobs.quarantine_queue writer"]
    end

    subgraph API["FastAPI External API :8088"]
        AUTH_R["POST /auth/token\nPOST /auth/refresh\nJWT RS256"]
        TCA_R["GET /tca/summary\nGET /tca/order/{id}\nGET /tca/algo-performance\nGET /tca/alpha-decay\nGET /tca/peer-benchmark/{id}"]
        ORD_R["GET /orders"]
        MIF_R["GET /mifid/export\nCSV (COMPLIANCE only)"]
        RPT_R["GET /reports/warning"]
        PIP_R["POST /pipeline/run\n(ADMIN only)"]
        SVC["tca_service.py\ncounterparty_id filter\nin every query"]
        RBAC["rbac.py\nADMIN ≥ HEAD_OF_TRADING\n≥ COMPLIANCE ≥ TRADER ≥ CLIENT"]
    end

    subgraph SPA["Angular 17 SPA :4200"]
        LOGIN["Login\nclient_id + secret"]
        DASH["Dashboard\nrole-aware KPIs + warnings"]
        OTCA["Order TCA\ndrilldown: cost decomposition"]
        ALGO["Algo Performance\nleague table (TRADER+)"]
        ADEC["Alpha Decay\nby vol regime (TRADER+)"]
        VSOR["Venue / SOR\nscorecard (TRADER+)"]
        MIFID["MiFID Export\nRTS 27 CSV (COMPLIANCE+)"]
        CV["Client View\nown orders only (CLIENT)"]
        NGRX["NgRx Store\nauth state · JWT claims"]
        INTER["Auth Interceptor\nBearer token on all requests"]
        GUARD["AuthGuard + RoleGuard\nfunctional guards"]
    end

    subgraph AIRFLOW["Airflow 2.9 :8088"]
        DAG1["dag_ingest_batch\n06:45 CET Mon–Fri"]
        DAG2["dag_raw_vault\n07:15 CET Mon–Fri"]
        DAG3["dag_biz_vault_eod\n17:30 CET Mon–Fri"]
        DAG4["dag_marts_eod\n18:15 CET Mon–Fri"]
        DAG5["dag_rt_consumer\n30 s continuous"]
        DAG6["dag_weekly_reports\nMon 07:00 CET"]
    end

    subgraph REPORTS["Reports"]
        R1["order_tca_report.py\nper-order CSV + venue scorecard"]
        R2["trader_digest.py\nweekly attribution CSV"]
        R3["algo_digest.py\nweekly league table CSV"]
        R4["mifid_export.py\nRTS 27/28 regulatory CSV"]
    end

    OMS & MD & REF & FI & EU --> DLT --> STG
    MOCK --> REDIS_S --> CONS --> STG
    STG --> STG_M --> RV_M --> RV
    RV_M --> BV_M --> BV
    BV_M --> MART_M --> MART
    BV --> ENG --> MOD & ATTR & ALPHA & ANOM
    ANOM --> QUAR --> OBS
    MART --> SVC --> TCA_R & ORD_R & MIF_R
    AUTH_R --> RBAC --> SVC
    SVC --- AUTH["auth schema"]
    API --> SPA
    AIRFLOW --> DLT
    AIRFLOW --> DBT
    AIRFLOW --> ANALYTICS
    AIRFLOW --> REPORTS
    MART --> REPORTS
```



---

### Simplified Architecture Overview

For a high-level view, the PrivateBank TCA platform follows a layered architecture with data flowing from external sources through processing layers to the user interface. Here's a simplified text-based diagram:

```
External Data Sources
  (OMS/EMS Feeds, Market Data, Reference Data)
        ↓
Ingestion Layer
  (dlt Pipelines, Redis Streams for real-time)
        ↓
Storage Layer
  (PostgreSQL + TimescaleDB, Data Vault 2.0)
        ↓
Analytics Layer
  (TCA Modules, Anomaly Detection, Attribution)
        ↓
API Layer
  (FastAPI Endpoints, JWT Authentication)
        ↓
Frontend Layer
  (Angular SPA, Embedded Tableau Dashboards)
```

- **External Data Sources**: Raw inputs like order/fill feeds and market data.
- **Ingestion Layer**: ETL processes to load and validate data.
- **Storage Layer**: Relational database with time-series and vault structures.
- **Analytics Layer**: Core TCA calculations and monitoring.
- **API Layer**: RESTful services with security.
- **Frontend Layer**: User interfaces for viewing reports and dashboards.

This text-based overview abstracts the complex interactions for quick understanding, focusing on the sequential data flow.

---

## Component interactions

```mermaid
sequenceDiagram
    participant Browser
    participant Angular
    participant FastAPI
    participant PostgreSQL
    participant Redis
    participant Airflow

    Note over Airflow,PostgreSQL: 06:45 CET — Batch ingest
    Airflow->>PostgreSQL: dlt pipelines → stg_raw.*
    Airflow->>PostgreSQL: dbt staging → raw_vault → biz_vault → marts

    Note over Redis,PostgreSQL: Continuous — Real-time fills
    Browser->>Angular: POST /mock/fill (synthetic)
    Angular->>FastAPI: (via nginx proxy /api/)
    FastAPI->>Redis: XADD pb:fills
    Redis-->>PostgreSQL: XREADGROUP consumer → stg_raw.rt_fills

    Note over Browser,PostgreSQL: User session
    Browser->>Angular: Enter client_id + secret
    Angular->>FastAPI: POST /api/auth/token
    FastAPI->>PostgreSQL: bcrypt verify auth.api_clients
    FastAPI-->>Angular: {access_token, refresh_token} (RS256 JWT)
    Angular->>Angular: NgRx store + localStorage

    Browser->>Angular: Navigate to /dashboard
    Angular->>FastAPI: GET /api/tca/summary (Bearer token)
    FastAPI->>FastAPI: Decode JWT → UserClaims (counterparty_id)
    FastAPI->>PostgreSQL: SELECT ... WHERE counterparty_id = :cp
    FastAPI-->>Angular: []TCAResult
     Angular-->>Browser: Render KPIs + table
```



### Verbose Component Interaction Story

The PrivateBank TCA platform operates as a multi-tiered system with intricate interactions across external sources, ingestion layers, storage, analytics, API, frontend, and orchestration components. Below is a detailed narrative of how components interact, grounded in the sequence diagram and expanded with technical specifics from the architecture. This story traces data flow, authentication, real-time processing, and batch operations, highlighting dependencies, protocols, and data transformations.

#### 1. **Batch Ingest Phase (06:45 CET Mon–Fri)**

The day begins with Airflow's `dag_ingest_batch` DAG, which orchestrates the initial data landing into PostgreSQL. Airflow, running as a scheduler on port 8088 (shared with FastAPI via nginx reverse proxy), triggers dlt pipelines in the ingestion layer. The dlt sources (oms_source.py, market_data_source.py, etc.) connect directly to PostgreSQL via SQLAlchemy, using the connection string from `DATABASE_URL` (e.g., `postgresql://tca_user:tca_password@postgres:5432/tca_db`).

- **Airflow → PostgreSQL**: The DAG executes Python tasks that run `run_all.py`, which sequentially loads data into the `stg_raw` schema. This includes 400 synthetic orders (generated via Faker and NumPy GBM with seed 42 for reproducibility), 1,020 × 30-second OHLCV bars per instrument, reference data (instruments, clients, venues), bond prices with DV01, and Eurex EDSP settlements. Each dlt pipeline uses UPSERT logic to handle incremental loads, appending to hypertables like `tick_bars` in TimescaleDB for efficient time-series partitioning. No real-time processing yet; this is pure batch ETL, completing within minutes to prepare the data vault.

This phase ensures the raw landing zone (`stg_raw`) is populated, with dbt staging views applied on top for light transformations (renaming, type casting). Airflow monitors task success via ExternalTaskSensor for downstream DAGs.

**Note**: The data landing in the "Batch Ingest Phase" (via Airflow's `dag_ingest_batch`) is an **incremental load**, not a full load. Here's the detailed explanation based on the platform's design:

- **DLT Pipelines**: The ingestion uses dlt (Data Load Tool) 1.5.0, which is built for incremental loading by default. Each pipeline (e.g., `oms_source`, `market_data_source`) employs UPSERT logic (INSERT ON CONFLICT UPDATE) to merge new or updated records into PostgreSQL's `stg_raw` schema. This avoids duplicating existing data and handles changes efficiently.
- **Synthetic Data Context**: In the PoC, data is generated synthetically (e.g., 400 orders via Faker + NumPy GBM). The initial seed (`ingestion/seed.py`) runs once to bootstrap, but subsequent DAG runs (daily at 06:45 CET) incrementally append or update records. For example:
  - Orders and fills are landed with timestamps; new days add fresh data without reloading history.
  - Market data bars (1,020 × 30s per instrument) are appended incrementally.
- **No Full Reload**: Unlike a full load (which would truncate and reload all data), this phase preserves existing `stg_raw` data and only processes deltas. TimescaleDB hypertables (e.g., for `tick_bars`) optimize for time-series appends.
- **Downstream Handling**: After landing, dbt staging views apply light transformations, and the raw vault uses `_loaded_at` watermarks for incremental processing in `dag_raw_vault`.

If you need to inspect the dlt pipeline code (e.g., `ingestion/sources/oms_source.py`) or the DAG logic in `dags/dag_ingest_batch.py`, let me know for further verification.

**Note 2: Handling Late Arriving Data**  
Late arriving data in the PrivateBank TCA platform would be handled through a combination of incremental ingestion, Data Vault 2.0 patterns, and reprocessing mechanisms to ensure data integrity, auditability, and minimal impact on downstream analytics. Below is a detailed breakdown based on the platform's architecture and components.

### 3. **Definition of Late Arriving Data**

Late arriving data refers to records (e.g., fills, market data bars, or reference updates) that arrive after their expected timestamp or processing window. Examples include:

- A fill executed at 16:00 CET but received by the system at 16:30 CET (post-EOD analytics run).
- Market data corrections from vendors arriving days after the trade date.
- Reference data updates (e.g., new instruments) arriving mid-session.

In the PoC, this is simulated via the mock server, but production would handle real vendor feeds.

### 4. **Ingestion Layer Handling**

- **DLT Pipelines (Incremental UPSERT)**: All ingestion sources (`oms_source`, `market_data_source`, etc.) use dlt's built-in incremental loading with UPSERT logic (INSERT ON CONFLICT UPDATE). When late data arrives:
  - It merges into `stg_raw` based on primary keys (e.g., order_id + timestamp for fills).
  - No truncation or full reload occurs; existing records are updated, and new ones appended.
  - TimescaleDB hypertables (e.g., `tick_bars`) efficiently handle time-series inserts/updates without performance degradation.
- **Batch vs. Real-Time**:
  - Batch ingest (`dag_ingest_batch` at 06:45 CET) processes daily deltas, including any late arrivals from the previous day.
  - Real-time consumer (`dag_rt_consumer`) polls Redis Streams (`pb:fills`) continuously, capturing intra-day late arrivals and triggering micro-refreshes of biz_vault models (e.g., `bv_order_enriched`).

### 5. **Storage Layer (Data Vault 2.0) Handling**

- **Raw Vault (Append-Only)**:
  - **Hubs**: Business keys (e.g., order_id) are immutable; late data doesn't affect them.
  - **Links**: Relationships (e.g., `lnk_order_fill`) are append-only; new links are added if late data introduces connections.
  - **Satellites**: These capture descriptive attributes with `hash_diff` for change detection. Late arriving data creates new satellite rows with updated `_loaded_at` timestamps, preserving full history. For example:
    - A late fill update adds a new row in `sat_fill_execution` with the corrected price, flagged as "late" if needed.
  - `_loaded_at` watermarks in dbt models ensure incremental processing: only records newer than the last load are processed.
- **Business Vault (Derived Metrics)**:
  - Models like `bv_tca_costs` recalculate on updates (e.g., slippage vs. benchmarks). Late data triggers re-computation via dbt's incremental materialization.
  - **PIT Tables** (e.g., `pit_order_snapshot`): These maintain point-in-time views, allowing reconstruction of TCA metrics as if the late data was present originally. This supports audit trails and backdated reports.
- **Marts (Consumption Layer)**: Fact tables (e.g., `fact_order_execution` in `mart_trading_risk`) denormalize data with counterparty_id. Updates propagate via dbt refreshes, ensuring dashboards reflect corrections.

### 6. **Analytics Engine Handling**

- **Reprocessing Triggers**: The analytics engine (`engine.py`) runs EOD (`dag_biz_vault_eod` at 17:30 CET), but late data can trigger on-demand re-runs via `/pipeline/run` (ADMIN only). It processes deltas:
  - Modules like `cost_decomposition` recalculate Almgren-Chriss impact for updated fills.
  - `anomaly_detector.py` checks for inconsistencies (e.g., Z-score on slippage_bps) and quarantines outliers to `obs.quarantine_queue`.
- **Attribution and Decay**: `attribution.py` re-splits costs (market/algo/trader); `alpha_decay.py` reclassifies vol regimes if benchmarks change.
- **MiFID Compliance**: RTS fields in `bv_mifid_fields` update automatically, ensuring regulatory reports reflect corrections.

### 7. **API and Frontend Handling**

- **Query-Time Isolation**: FastAPI services (`tca_service.py`) query marts with `AND counterparty_id = :cp`. Late updates are reflected in real-time via NgRx store refreshes in Angular components (e.g., OrderTcaComponent re-renders cost decomposition).
- **Caching and Performance**: Redis caches ticks; late data invalidates caches to prevent stale KPIs.
- **User Notifications**: Warnings from `obs.obs_warnings` alert users to late data impacts via `/reports/warning` endpoint, displayed in DashboardComponent.

### 8. **Orchestration and Monitoring**

- **Airflow DAGs**: `dag_raw_vault` and `dag_biz_vault_eod` have ExternalTaskSensor; late data in upstream tasks triggers downstream re-runs. `dag_marts_eod` updates star schemas.
- **Error Handling**: Pipeline runs log latency/errors in `obs.obs_warnings`. Quarantined records require manual review (e.g., via `anomaly_detector.py`).
- **Audit Trail**: All changes are traceable via S3 archives (raw messages) and immutable PostgreSQL logs, meeting MiFID II requirements.

### 9. **Potential Challenges and Mitigations**

- **Performance Impact**: Frequent updates could strain PostgreSQL; mitigated by TimescaleDB partitioning and dbt's incremental strategies.
- **Consistency**: PIT tables ensure historical accuracy; no "rewriting history" without audit logs.
- **Production Scaling**: In production (100,000+ orders/day), real-time streams and micro-batches handle volume; the PoC simulates this via mock server.

In summary, late arriving data is seamlessly integrated via incremental UPSERTs, Data Vault change tracking, and reprocessing, maintaining data freshness and compliance without disrupting operations. If you need code examples (e.g., from `dags/dag_raw_vault.py`), let me know.

---

### Component Interactions with Embedded Tableau (Alternative Version)

```mermaid
sequenceDiagram
    participant Browser
    participant Angular
    participant Okta
    participant Tableau
    participant FastAPI
    participant PostgreSQL
    participant Redis
    participant Airflow

    Note over Airflow,PostgreSQL: 06:45 CET — Batch ingest
    Airflow->>PostgreSQL: dlt pipelines → stg_raw (e.g., orders, fills)
    Airflow->>PostgreSQL: dbt Data Vault → marts (e.g., fact_order_execution)

    Note over Redis,PostgreSQL: Continuous — Real-time fills
    Browser->>Angular: POST /mock/fill (synthetic)
    Angular->>FastAPI: (via nginx proxy /api/)
    FastAPI->>Redis: XADD pb:fills
    Redis-->>PostgreSQL: XREADGROUP consumer → stg_raw.rt_fills

    Note over Browser,Okta: User session with Okta SSO
    Browser->>Angular: Click login
    Angular->>Okta: Redirect to Okta SSO (SAML/OIDC)
    Okta->>Browser: Prompt for PrivateBank credentials
    Browser->>Okta: Authenticate
    Okta->>Angular: Redirect with SAML assertion / OIDC tokens
    Angular->>FastAPI: Exchange Okta tokens for platform JWT
    FastAPI->>Okta: Validate tokens (optional)
    FastAPI-->>Angular: {access_token, refresh_token} (RS256 JWT)
    Angular->>Angular: NgRx store + localStorage

    Browser->>Angular: Navigate to /dashboard
    Angular->>Tableau: Load embedded dashboard (via JS API, passing Okta tokens for SSO)
    Tableau->>Okta: Validate SSO tokens
    Okta-->>Tableau: Confirm auth + user claims
    Tableau->>PostgreSQL: Query marts (e.g., SELECT ... WHERE counterparty_id = :cp from claims)
    PostgreSQL-->>Tableau: TCA results (e.g., slippage, benchmarks)
    Tableau-->>Angular: Render interactive charts (e.g., cost decomposition)
    Angular-->>Browser: Display embedded Tableau viz

    Note over Tableau: Security
    Tableau->>Okta: SSO validation for row-level security (RLS)
    Okta-->>Tableau: User claims (CLIENT sees own data only)
```



This alternative version incorporates Okta SSO for unified authentication across PrivateBank's ecosystem, with Tableau embedded in the Angular SPA using Okta tokens for secure access. It connects directly to PostgreSQL for TCA data visualization, maintaining RBAC via Okta claims and data isolation. Embedding uses Tableau's JS API with Okta SSO for seamless integration.

---

## Data Vault 2.0 lineage

```mermaid
flowchart LR
    subgraph stg_raw
        SO[stg_orders]
        SF[stg_fills]
        ST[stg_tick_bars]
        SC[stg_clients]
        SI[stg_instruments]
    end

    subgraph raw_vault
        HO[hub_order] & HF[hub_fill] & HI[hub_instrument]
        HC[hub_client] & HV[hub_venue] & HT[hub_trader]
        HA[hub_algo] & HLE[hub_legal_entity]

        LK1[lnk_order_fill] & LK2[lnk_order_client]
        LK3[lnk_order_instrument] & LK4[lnk_fill_venue]
        LK5[lnk_order_algo] & LK6[lnk_order_entity]

        SAT1[sat_order_details] & SAT2[sat_fill_execution]
        SAT3[sat_price_tick] & SAT4[sat_instrument_ref]
        SAT5[sat_client_profile] & SAT6[sat_venue_detail]
        SAT7[sat_algo_version]
    end

    subgraph biz_vault
        BVE[bv_order_enriched]
        BVC[bv_tca_costs]
        BVA[bv_alpha_decay]
        BVP[bv_peer_benchmark]
        BVM[bv_mifid_fields]
        BVT[bv_trader_attribution]
        PIT[pit_order_snapshot]
    end

    subgraph marts
        FOE[fact_order_execution\nmart_trading_risk]
        FPB[fact_price_benchmark\nmart_market_data]
        FCA[fact_client_activity\nmart_corporate]
        ENT[entity_pb_de/uk\nentity_bcm_us\nmart_consolidated]
    end

    SO --> HO & LK1 & LK2 & LK3 & SAT1
    SF --> HF & LK1 & LK4 & SAT2
    ST --> SAT3
    SC --> HC & SAT5
    SI --> HI & SAT4

    HO & SAT1 & LK2 --> BVE
    BVE & SAT2 & SAT3 --> BVC
    BVE & SAT3 --> BVA & BVP
    BVE --> BVM & BVT & PIT

    BVE & BVC & BVP & BVA & BVM --> FOE
    SAT3 & SAT4 --> FPB
    BVE --> FCA
    FOE --> ENT
```



---

## Counterparty isolation — 5 layers

```mermaid
flowchart LR
    JWT["JWT claim\ncounterparty_id\n(signed RS256)"]
    LINK["lnk_order_client\nnon-nullable FK\nin raw_vault"]
    FACT["fact_order_execution\ncounterparty_id\ndenormalised"]
    SVC["tca_service.py\nAND counterparty_id = :cp\nin every query"]
    GUARD["Angular RoleGuard\nCLIENT → /client-view only\nrouter redirect"]

    JWT --> SVC
    LINK --> FACT --> SVC
    SVC --> GUARD

    note["CLIENT querying another\ncounterparty's order\n→ HTTP 404 (not 403)"]
    SVC -.-> note
```



---

## Components

### Infrastructure


| Component    | Image / Tech                        | Port | Description                                                                                                                                                                 | Depends on |
| ------------ | ----------------------------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| **postgres** | `timescale/timescaledb:latest-pg16` | 5432 | Primary database. 13 logical schemas (stg_raw, raw_vault, biz_vault, mart_*, obs, catalog, auth, airflow_db). Hypertable on `stg_raw.tick_bars`. Initialised by `init.sql`. | —          |
| **redis**    | `redis:7-alpine`                    | 6379 | Message bus for real-time fills. Three streams: `pb:fills`, `pb:orders`, `pb:market_ticks`.                                                                              | —          |


### Ingestion


| Component              | File                                      | Description                                                                                                               | Depends on      |
| ---------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------- |
| **oms_source**         | `ingestion/sources/oms_source.py`         | dlt source generating 400 synthetic orders + fills (100 per asset class) via Faker + NumPy GBM. Deterministic seed 42.    | —               |
| **market_data_source** | `ingestion/sources/market_data_source.py` | 1 020 × 30-second OHLCV bars per instrument. Vectorised GBM via NumPy.                                                    | —               |
| **ref_data_source**    | `ingestion/sources/ref_data_source.py`    | 6 dlt resources: 50 instruments, clients, venues, algos, traders, legal entities.                                         | —               |
| **fi_pricing_source**  | `ingestion/sources/fi_pricing_source.py`  | Bond evaluated prices (PV formula) + DV01 (modified duration). EOD run.                                                   | —               |
| **eurex_source**       | `ingestion/sources/eurex_source.py`       | Synthetic EDSP settlements for FUTS-001 to FUTS-010.                                                                      | —               |
| **run_all.py**         | `ingestion/pipelines/run_all.py`          | Sequential dlt pipeline runner → `stg_raw` schema. One shared pipeline instance.                                          | postgres        |
| **seed.py**            | `ingestion/seed.py`                       | Bootstrap: hashes client secrets via bcrypt, then runs all dlt pipelines once.                                            | postgres        |
| **mock-server**        | `ingestion/mock/mock_server.py`           | FastAPI app on :8001. `POST /mock/fill                                                                                    | order           |
| **redis_consumer**     | `ingestion/mock/redis_consumer.py`        | `XREADGROUP` consumer (group `tca-consumers`). Writes to `stg_raw.rt_fills` via SQLAlchemy INSERT ON CONFLICT DO NOTHING. | redis, postgres |


### dbt Data Vault 2.0


| Layer                      | Models                                                                                                                                                               | Schema              | Materialisation | Description                                                                                                |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | --------------- | ---------------------------------------------------------------------------------------------------------- |
| **Staging**                | `stg_orders`, `stg_fills`, `stg_tick_bars`, `stg_clients`, `stg_instruments`, `stg_bond_prices`                                                                      | `stg_raw`           | View            | Light renaming + type casting over dlt landing tables. No business logic.                                  |
| **Raw Vault — Hubs**       | `hub_order`, `hub_fill`, `hub_instrument`, `hub_client`, `hub_venue`, `hub_trader`, `hub_algo`, `hub_legal_entity`                                                   | `raw_vault`         | Incremental     | Business key + surrogate hash key. Append-only. `_loaded_at` watermark for incremental filter.             |
| **Raw Vault — Links**      | `lnk_order_fill`, `lnk_order_client`, `lnk_order_instrument`, `lnk_fill_venue`, `lnk_order_algo`, `lnk_order_entity`                                                 | `raw_vault`         | Incremental     | Relationships between hubs. `lnk_order_client` enforces non-nullable `counterparty_id`.                    |
| **Raw Vault — Satellites** | `sat_order_details`, `sat_fill_execution`, `sat_price_tick`, `sat_instrument_ref`, `sat_client_profile`, `sat_venue_detail`, `sat_algo_version`                      | `raw_vault`         | Incremental     | Descriptive attributes + `hash_diff` for change detection. `sat_price_tick` is a TimescaleDB hypertable.   |
| **Business Vault**         | `bv_order_enriched`, `bv_tca_costs`, `bv_alpha_decay`, `bv_adverse_selection`, `bv_peer_benchmark`, `bv_mifid_fields`, `bv_trader_attribution`, `pit_order_snapshot` | `biz_vault`         | Incremental     | Derived metrics: arrival slippage, Almgren-Chriss impact, alpha decay curves, MiFID waiver classification. |
| **Marts — Trading Risk**   | `fact_order_execution`, `dim_algo`, `dim_trader`, `dim_venue`, `dim_mifid`                                                                                           | `mart_trading_risk` | Table           | Primary consumption layer. `counterparty_id` denormalised.                                                 |
| **Marts — Market Data**    | `fact_price_benchmark`, `dim_instrument`, `dim_date`                                                                                                                 | `mart_market_data`  | Table           | OHLCV benchmarks + instrument dimension.                                                                   |
| **Marts — Corporate**      | `fact_client_activity`, `dim_client`, `dim_legal_entity`                                                                                                             | `mart_corporate`    | Table           | Aggregated activity by counterparty, date, asset class.                                                    |
| **Marts — Consolidated**   | `entity_pb_de`, `entity_pb_uk`, `entity_bcm_us`                                                                                                                    | `mart_consolidated` | Table           | Legal entity views over `fact_order_execution`.                                                            |
| **Macro**                  | `macros/generate_schema_name.sql`                                                                                                                                    | —                   | —               | Overrides dbt default to return schema names as-is (prevents `stg_raw_raw_vault` prefix).                  |


### Analytics Engine


| Component              | File                                          | Description                                                                                                             | Depends on |
| ---------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ---------- |
| **engine.py**          | `analytics/engine.py`                         | Orchestrator: loads data from `biz_vault` + `raw_vault` + `mart_market_data`, runs all 10 modules, writes warnings.     | postgres   |
| **cost_decomposition** | `analytics/modules/cost_decomposition.py`     | Almgren-Chriss permanent impact (`η × σ × √(X/ADV) × 10 000 bps`). Timing, spread, commission decomposition.            | —          |
| **adverse_selection**  | `analytics/modules/adverse_selection.py`      | Slippage vs session VWAP; flags fills > 10 bps adverse.                                                                 | —          |
| **venue_sor**          | `analytics/modules/venue_sor.py`              | Venue scorecard ranked by avg slippage vs VWAP per instrument class.                                                    | —          |
| **fill_pattern**       | `analytics/modules/fill_pattern.py`           | Intraday fill distribution; participation rate profiling.                                                               | —          |
| **peer_benchmarking**  | `analytics/modules/peer_benchmarking.py`      | Algo league table vs VWAP / TWAP / arrival / close benchmarks.                                                          | —          |
| **eurex_derivatives**  | `analytics/modules/eurex_derivatives.py`      | Futures TCA vs EDSP; basis risk.                                                                                        | —          |
| **fixed_income_tca**   | `analytics/modules/fixed_income_tca.py`       | DV01-adjusted slippage; yield-space cost decomposition.                                                                 | —          |
| **fx_derivatives_tca** | `analytics/modules/fx_derivatives_tca.py`     | Forward point decomposition; tenor-adjusted spread.                                                                     | —          |
| **mifid_compliance**   | `analytics/modules/mifid_compliance.py`       | Waiver flag validation; LRGS deferral check (> €50 M notional).                                                         | —          |
| **pre_trade**          | `analytics/modules/pre_trade.py`              | Pre-trade cost estimates using historical slippage distributions.                                                       | —          |
| **execution_quality_predictor** | `analytics/modules/execution_quality_predictor.py` | GradientBoostingRegressor per instrument class trained on `fact_order_execution`. Predicts `arrival_slippage_bps` with IQR confidence interval and feature importance. Serialised to `analytics/models/slippage_<class>.pkl`. | — |
| **regime_detector**    | `analytics/modules/regime_detection/regime_detector.py` | KMeans(k=3) + StandardScaler on 30-second bar features: `intraday_vol`, `volume_ratio`, `momentum`. Detects intraday vol regime (LOW / MEDIUM / HIGH) per tick with cluster confidence score. Serialised to `analytics/models/regime_kmeans.pkl`. | — |
| **attribution.py**     | `analytics/attribution.py`                    | Splits total cost into market_cost / algo_cost / trader_cost with percentage attribution.                               | —          |
| **alpha_decay.py**     | `analytics/alpha_decay.py`                    | Classifies vol regime (LOW < 80 bps, MEDIUM 80–150, HIGH > 150). Aggregated decay curves per regime + instrument class. | —          |
| **anomaly_detector**   | `analytics/observability/anomaly_detector.py` | Z-score (threshold 3.0) on slippage_bps and fill_quantity; requires 10-row minimum history.                             | —          |
| **quarantine**         | `analytics/observability/quarantine.py`       | Routes FAIL records to `obs.quarantine_queue` and `obs.obs_warnings`.                                                   | postgres   |


### FastAPI External API


| Component           | File                          | Description                                                                                                                                                 | Depends on            |
| ------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| **app**             | `api/main.py`                 | FastAPI app with lifespan (loads JWT keys). CORS for :4200. 6 routers registered.                                                                           | —                     |
| **jwt_handler**     | `api/auth/jwt_handler.py`     | Issues + verifies RS256 JWTs. Generates key pair at startup if env paths absent. 8 h access / 7 d refresh.                                                  | —                     |
| **dependencies**    | `api/auth/dependencies.py`    | `get_current_user()` → `UserClaims(client_id, role, counterparty_id, legal_entity)`. Validates `token_type == "access"`.                                    | jwt_handler           |
| **rbac**            | `api/auth/rbac.py`            | `require_role(*allowed)` and `require_min_role(min)`. Hierarchy: ADMIN(5) > HEAD_OF_TRADING(4) > COMPLIANCE(3) > TRADER(2) > CLIENT(1).                     | dependencies          |
| **router/auth**     | `api/routers/auth.py`         | `POST /auth/token` (form: client_id + client_secret → bcrypt verify → JWT issue). `POST /auth/refresh`.                                                     | jwt_handler, postgres |
| **router/tca**      | `api/routers/tca.py`          | `GET /tca/summary`, `/tca/order/{id}`, `/tca/algo-performance`, `/tca/alpha-decay`, `/tca/peer-benchmark/{id}`. CLIENT blocked from algo-performance (403). | tca_service           |
| **router/orders**   | `api/routers/orders.py`       | `GET /orders` — counterparty-scoped order list.                                                                                                             | tca_service           |
| **router/reports**  | `api/routers/reports.py`      | `GET /reports/warning` — observability warnings from `obs.obs_warnings`.                                                                                    | tca_service           |
| **router/mifid**    | `api/routers/mifid.py`        | `GET /mifid/export` — `StreamingResponse` CSV. COMPLIANCE + ADMIN only.                                                                                     | tca_service           |
| **router/pipeline** | `api/routers/pipeline.py`     | `POST /pipeline/run` — triggers Airflow DAG via httpx. ADMIN only.                                                                                          | —                     |
| **tca_service**     | `api/services/tca_service.py` | All mart queries. Injects `AND counterparty_id = :cp` into every query. CLIENT request for another counterparty's data → `None` → HTTP 404.                 | postgres              |
| **router/predict**  | `api/routers/predict.py`      | `POST /predict/slippage` — GBT arrival slippage estimate (all authenticated roles). `POST /predict/train` — fit per-class models (ADMIN only). `GET /predict/status` — model readiness + training row counts per class. | execution_quality_predictor |
| **router/regime**   | `api/routers/regime.py`       | `GET /regime/status`. `POST /regime/train` (ADMIN). `GET /regime/summary?trade_date=`. `GET /regime/detect?trade_date=&sample_size=`. `GET /regime/timeline?trade_date=&instrument_id=`. | regime_detector |
| **models**          | `api/schemas/models.py`       | Pydantic response models: `TokenResponse`, `TCAResult`, `OrderSummary`, `AlgoPerformance`, `AlphaDecayCurve`, `PeerBenchmark`, `ObsWarning`, `MifidRow`.    | —                     |


### Angular 17 SPA


| Component               | File                                            | Description                                                                                                                                                | Depends on                                                           |
| ----------------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| **app.config.ts**       | `src/app/app.config.ts`                         | Wires `provideStore`, `provideEffects`, `provideHttpClient(withInterceptors([authInterceptor]))`.                                                          | —                                                                    |
| **app.routes.ts**       | `src/app/app.routes.ts`                         | Lazy-loaded routes with `authGuard` + `roleGuard([...])`.                                                                                                  | —                                                                    |
| **AuthService**         | `core/auth/auth.service.ts`                     | Angular signal: `currentUser = signal<UserClaims                                                                                                           | null>`. Decodes JWT via` jwt-decode`. Stores tokens in localStorage. |
| **authGuard**           | `core/auth/auth.guard.ts`                       | Functional guard: `auth.isAuthenticated() ? true : redirect('/login')`.                                                                                    | AuthService                                                          |
| **roleGuard**           | `core/auth/role.guard.ts`                       | Factory `roleGuard(allowedRoles[])`: checks `auth.hasRole(...allowedRoles)`, redirects to `/dashboard`.                                                    | AuthService                                                          |
| **authInterceptor**     | `core/interceptors/auth.interceptor.ts`         | `HttpInterceptorFn`: appends `Authorization: Bearer <token>` to every outgoing request.                                                                    | AuthService                                                          |
| **ApiService**          | `core/services/api.service.ts`                  | Wraps all FastAPI endpoints. Login sends `application/x-www-form-urlencoded`.                                                                              | HttpClient                                                           |
| **auth.actions**        | `store/auth.actions.ts`                         | NgRx `createActionGroup`: Login, LoginSuccess, LoginFailure, Logout, RefreshToken, RefreshTokenSuccess, RefreshTokenFailure.                               | —                                                                    |
| **auth.reducer**        | `store/auth.reducer.ts`                         | `AuthState { user, loading, error }`.                                                                                                                      | auth.actions                                                         |
| **auth.effects**        | `store/auth.effects.ts`                         | `login$` → API call. `loginSuccess$` → `storeTokens` + role-based redirect (CLIENT → `/client-view`, others → `/dashboard`). `logout$` → clear + redirect. | auth.actions, ApiService, AuthService                                |
| **auth.selectors**      | `store/auth.selectors.ts`                       | `selectAuthLoading`, `selectAuthError`, `selectCurrentUser`, `selectIsAuthenticated`, `selectUserRole`.                                                    | —                                                                    |
| **LoginComponent**      | `features/login/login.component.ts`             | Standalone form. Dispatches `AuthActions.login`. Displays NgRx error + loading state.                                                                      | auth.actions, auth.selectors                                         |
| **DashboardComponent**  | `features/dashboard/dashboard.component.ts`     | Role-aware sidebar. KPI grid (warnings, orders, avg slippage). Warning table. All roles except CLIENT.                                                     | ApiService, AuthService                                              |
| **OrderTcaComponent**   | `features/order-tca/order-tca.component.ts`     | Order ID lookup. Cost decomposition grid: arrival slippage, market impact, timing, commission, VWAP. MiFID panel. All authenticated roles.                 | ApiService                                                           |
| **AlgoPerfComponent**   | `features/algo-perf/algo-perf.component.ts`     | Algo league table with date + asset-class filter. TRADER, HEAD_OF_TRADING, COMPLIANCE, ADMIN.                                                              | ApiService                                                           |
| **AlphaDecayComponent** | `features/alpha-decay/alpha-decay.component.ts` | Alpha decay curves grouped by LOW / MEDIUM / HIGH vol regime. TRADER+.                                                                                     | ApiService                                                           |
| **VenueSorComponent**   | `features/venue-sor/venue-sor.component.ts`     | Venue scorecard ranked by avg VWAP slippage. Computed client-side from `/tca/summary`. TRADER+.                                                            | ApiService                                                           |
| **MifidComponent**      | `features/mifid/mifid.component.ts`             | Preview + client-side CSV export of RTS 27 data. COMPLIANCE + ADMIN only.                                                                                  | ApiService                                                           |
| **ClientViewComponent** | `features/client-view/client-view.component.ts` | Counterparty-scoped order list + summary KPIs. CLIENT role only.                                                                                           | ApiService, AuthService                                              |
| **PreTradeComponent** | `features/pre-trade/pre-trade.component.ts` | Pre-trade slippage estimate form. Model status chips with per-class training row count. Prediction panel: arrival slippage bps, IQR confidence interval, interpretation badge, CSS feature importance bars for top 5 drivers. TRADER+. | ApiService, AuthService |
| **RegimeDetectionComponent** | `features/regime-detection/regime-detection.component.ts` | Three regime KPI cards (% of session, avg vol / volume-Z / momentum / confidence, animated progress bar). Intraday timeline strip (1 020 color-coded 30-second slices). CSS scatter plot (bar price range × volume z-score). Centroid comparison table. Feature separation bars. ML vs legacy comparison note. TRADER+. | ApiService, AuthService |


### Airflow DAGs


| DAG                    | File                         | Schedule          | Description                                                                                            | Depends on        |
| ---------------------- | ---------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------ | ----------------- |
| **tca_ingest_batch**   | `dags/dag_ingest_batch.py`   | 06:45 CET Mon–Fri | seed_auth → dlt all sources → dbt staging → source freshness check.                                    | postgres          |
| **tca_raw_vault**      | `dags/dag_raw_vault.py`      | 07:15 CET Mon–Fri | ExternalTaskSensor waits for ingest → dbt hubs → links → satellites → tests.                           | tca_ingest_batch  |
| **tca_biz_vault_eod**  | `dags/dag_biz_vault_eod.py`  | 17:30 CET Mon–Fri | dlt FI pricing + Eurex EDSP → dbt biz_vault → tests → analytics engine.                                | postgres          |
| **tca_marts_eod**      | `dags/dag_marts_eod.py`      | 18:15 CET Mon–Fri | ExternalTaskSensor waits for biz_vault → dbt marts → tests → MiFID export → catalog update.            | tca_biz_vault_eod |
| **tca_rt_consumer**    | `dags/dag_rt_consumer.py`    | 30 s continuous   | `RedisStreamSensor` polls `pb:fills` → dbt micro-refresh of `bv_order_enriched`. `max_active_runs=1`. | redis, postgres   |
| **tca_weekly_reports** | `dags/dag_weekly_reports.py` | Mon 07:00 CET     | algo_digest + trader_digest + venue_scorecard CSV generation (parallel tasks).                         | postgres          |


### Reports


| Report               | File                          | Output             | Description                                                                                |
| -------------------- | ----------------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| **order_tca_report** | `reports/order_tca_report.py` | CSV per trade date | Full `fact_order_execution` export + venue scorecard aggregated over the week.             |
| **trader_digest**    | `reports/trader_digest.py`    | CSV per week       | Trader attribution ranked by avg arrival slippage per instrument class.                    |
| **algo_digest**      | `reports/algo_digest.py`      | CSV per week       | Algo league table: slippage, VWAP, market impact, participation rate per instrument class. |
| **mifid_export**     | `reports/mifid_export.py`     | CSV per trade date | RTS 27/28 regulatory columns. Scoped by `counterparty_id` when called for CLIENT role.     |


---

## Machine Learning

Two ML modules extend the platform beyond parametric analytics into predictive and unsupervised modelling. Both are trained on data that already flows through the Data Vault, expose predictions via FastAPI endpoints, and render results in dedicated Angular views.

---

### Pre-Trade Slippage Estimate

**Module:** `analytics/modules/execution_quality_predictor.py`  
**UI route:** `/pre-trade` — TRADER, HEAD_OF_TRADING, COMPLIANCE, ADMIN  
**API:** `POST /predict/slippage` · `POST /predict/train` (ADMIN) · `GET /predict/status`

#### Problem

The Almgren-Chriss formula in `pre_trade.py` estimates market impact using a fixed parametric form. It cannot learn non-linear interactions — for example, the combined effect of a high participation rate, a HIGH vol regime, and a late-session time-of-day window. A gradient boosting model learns these interactions directly from historical fills.

#### Model

One `GradientBoostingRegressor` (scikit-learn, `n_estimators=300`, `max_depth=4`, `learning_rate=0.05`, `subsample=0.8`) is trained per instrument class. Each model is cross-validated (up to 5-fold, skipped when fewer than 40 samples) and serialised via joblib.

| Attribute | Value |
|---|---|
| **Features** | `side`, `vol_regime`, `algo_id`, `venue_id`, `quantity`, `hour_of_day`, `day_of_week` |
| **Target** | `arrival_slippage_bps` |
| **Data source** | `mart_trading_risk.fact_order_execution` |
| **Min. samples** | 100 rows per instrument class |
| **Persistence** | `analytics/models/slippage_<instrument_class>.pkl` |
| **Output** | `predicted_slippage_bps`, `ci_low_bps`, `ci_high_bps` (IQR-based), `feature_importance` |

Categorical inputs (`side`, `vol_regime`, `algo_id`, `venue_id`) are label-encoded per class; unseen values at inference time fall back to the first known label. The IQR of training residuals forms the confidence interval shown in the UI.

#### Training and inference

```bash
# Train all classes via API (ADMIN only)
POST /predict/train

# Predict arrival slippage for a planned order
POST /predict/slippage
{
  "instrument_class": "equity",
  "side": "BUY",
  "quantity": 50000,
  "vol_regime": "HIGH",
  "algo_id": "VWAP",
  "venue_id": "XLON",
  "order_hour": 9,
  "order_dow": 2
}
# → { "predicted_slippage_bps": -8.42, "ci_low_bps": -12.1, "ci_high_bps": -5.3,
#      "trained_on": 400, "feature_importance": { "vol_regime": 0.31, ... } }

# Check model status
GET /predict/status
# → { "equity": { "ready": true, "trained_on": 400, "top_features": [...] }, ... }
```

#### UI — `/pre-trade`

| Panel | Description |
|---|---|
| Model status bar | Per-class readiness chip (green dot + training row count). Train / Retrain button (ADMIN only). Cross-validation R² displayed after training. |
| Input form | Instrument class, side, quantity, vol_regime, algo_id, venue_id, planned execution hour (CET), day of week. |
| Prediction | Large arrival slippage display in bps. IQR confidence interval (`ci_low` → `ci_high`). Interpretation badge: High cost / Moderate cost / Favourable fill. CSS horizontal bars showing top-5 feature importance. |

---

### Regime Detection

**Module:** `analytics/modules/regime_detection/regime_detector.py`  
**UI route:** `/regime-detection` — TRADER, HEAD_OF_TRADING, COMPLIANCE, ADMIN  
**API:** `GET /regime/status` · `POST /regime/train` (ADMIN) · `GET /regime/summary` · `GET /regime/detect` · `GET /regime/timeline`

#### Problem

The TCA system assigns orders a `vol_regime` label (LOW / MEDIUM / HIGH) using a fixed daily volatility threshold applied once per order. This has two structural weaknesses:

1. It uses **daily** realised volatility — regime changes within a single session are invisible.
2. It uses a **fixed threshold** — it ignores the interaction between volatility, volume, and directional momentum that together define market microstructure conditions.

The ML regime detector runs on **30-second OHLCV bars** from `stg_raw.tick_bars` and detects intraday regime transitions in real time. A transient HIGH-regime cluster mid-session (e.g., an unexpected macro print) is captured immediately, whereas the legacy daily threshold would see the same order as MEDIUM all day.

#### Model

`KMeans(k=3)` + `StandardScaler` (scikit-learn) is trained on the full tick history available in `stg_raw.tick_bars`. Clusters are mapped to LOW / MEDIUM / HIGH by ascending average `intraday_vol` so the labels are always interpretable — the tightest-range cluster is always LOW.

| Attribute | Value |
|---|---|
| **Features** | `intraday_vol` = (high − low) / close · `volume_ratio` = z-scored volume per instrument × day · `momentum` = (close − open) / open |
| **Data source** | `stg_raw.tick_bars` (TimescaleDB hypertable, 30-second OHLCV bars) |
| **Output per bar** | `regime` (LOW / MEDIUM / HIGH), `cluster_id`, `cluster_confidence` |
| **Persistence** | `analytics/models/regime_kmeans.pkl` |

All features are winsorised at the 1st/99th percentile before fitting. The `StandardScaler` is serialised inside the artifact and applied identically at inference time.

#### Confidence score

```
cluster_confidence = 1 / (1 + ‖x_scaled − nearest_centroid_scaled‖₂)
```

A score near 1 means the bar is a prototypical member of its regime; a score near 0 means it sits close to a cluster boundary.

#### Training and inference

```bash
# Train via API (ADMIN only)
POST /regime/train
# → { "status": "trained", "trained_on": 51000, "inertia": 142873.4,
#      "centroids": [ { "regime": "LOW", "avg_intraday_vol": 0.000842, ... }, ... ] }

# Session regime distribution
GET /regime/summary?trade_date=2025-01-15
# → [ { "regime": "LOW", "tick_count": 18360, "pct_of_session": 36.0,
#         "avg_intraday_vol": 0.000842, "avg_volume_ratio": -0.12,
#         "avg_momentum": 0.000031, "avg_confidence": 0.81 }, ... ]

# Sampled bars with features — for scatter plot
GET /regime/detect?trade_date=2025-01-15&sample_size=300

# Full sorted regime sequence for one instrument — for timeline strip
GET /regime/timeline?trade_date=2025-01-15&instrument_id=EQTY-001
# → [ { "ts": "07:00", "regime": "LOW", "confidence": 0.84 }, ... ]
```

#### UI — `/regime-detection`

| Panel | Description |
|---|---|
| Model status bar | Training readiness indicator showing bar count, cluster inertia, and feature list. Train / Retrain button (ADMIN only). |
| Regime KPI cards | Three cards (LOW · MEDIUM · HIGH), color-coded green / amber / red. Each shows session %, animated progress bar, avg price-range vol in bps, volume z-score, momentum in bps, and avg confidence score. |
| Intraday timeline strip | 1 020 color-coded slices — one per 30-second bar — forming a continuous heatmap across the European session (07:00–15:30 UTC). Intraday regime transitions are visible at a glance. Hovering a slice shows the exact timestamp, regime, and confidence. Instrument selector and date picker above. |
| CSS scatter plot | Up to 300 sampled bars positioned at `intraday_vol` (x-axis) × `volume_ratio` (y-axis). Each dot colored by regime. Hovering shows instrument, feature values, and regime. Grid lines at 25% / 50% / 75% intervals. |
| Centroid comparison table | Cluster centroids as feature means per regime — shows what separates the clusters in feature space. |
| Feature separation bars | Relative contribution of each feature to inter-regime spread (range of centroid means), expressed as a percentage. |
| ML vs legacy note | Explains why intraday microstructure-based detection outperforms the legacy daily-vol threshold. |

#### Value to the platform

The ML regime label is a **feature for every other model**. The Pre-Trade Slippage Estimate (`POST /predict/slippage`) accepts a `vol_regime` parameter — replacing the user-supplied legacy label with the ML-detected intraday regime improves prediction accuracy, particularly for orders placed during transient stress events that the daily threshold misclassifies.

---

## Role permissions matrix


| Endpoint / Feature               | ADMIN | HEAD_OF_TRADING | COMPLIANCE | TRADER | CLIENT      |
| -------------------------------- | ----- | --------------- | ---------- | ------ | ----------- |
| `/auth/token`                    | ✓     | ✓               | ✓          | ✓      | ✓           |
| `/tca/summary`                   | ✓     | ✓               | ✓          | ✓      | own CP only |
| `/tca/order/{id}`                | ✓     | ✓               | ✓          | ✓      | own CP only |
| `/tca/algo-performance`          | ✓     | ✓               | ✓          | ✓      | 403         |
| `/tca/alpha-decay`               | ✓     | ✓               | ✓          | ✓      | 403         |
| `/tca/peer-benchmark/{id}`       | ✓     | ✓               | ✓          | ✓      | 403         |
| `/orders`                        | ✓     | ✓               | ✓          | ✓      | own CP only |
| `/mifid/export`                  | ✓     | —               | ✓          | —      | —           |
| `/pipeline/run`                  | ✓     | —               | —          | —      | —           |
| `/reports/warning`               | ✓     | ✓               | ✓          | ✓      | —           |
| `/predict/slippage` (POST)       | ✓     | ✓               | ✓          | ✓      | —           |
| `/predict/train` (POST)          | ✓     | —               | —          | —      | —           |
| `/predict/status` (GET)          | ✓     | ✓               | ✓          | ✓      | —           |
| `/regime/summary` (GET)          | ✓     | ✓               | ✓          | ✓      | —           |
| `/regime/detect` (GET)           | ✓     | ✓               | ✓          | ✓      | —           |
| `/regime/timeline` (GET)         | ✓     | ✓               | ✓          | ✓      | —           |
| `/regime/train` (POST)           | ✓     | —               | —          | —      | —           |
| Angular `/dashboard`             | ✓     | ✓               | ✓          | ✓      | —           |
| Angular `/pre-trade`             | ✓     | ✓               | ✓          | ✓      | —           |
| Angular `/regime-detection`      | ✓     | ✓               | ✓          | ✓      | —           |
| Angular `/client-view`           | —     | —               | —          | —      | ✓           |


### User Roles and Credentials


| Role            | Client ID     | Password | Notes                                                                      |
| --------------- | ------------- | -------- | -------------------------------------------------------------------------- |
| ADMIN           | admin_01      | changeme | Full access; can trigger pipelines, view all data, MiFID exports.          |
| HEAD_OF_TRADING | head_trading  | changeme | Access to trading desk features; algo performance, alpha decay, venue SOR. |
| COMPLIANCE      | compliance_01 | changeme | Regulatory access; MiFID exports, RTS reports, warnings.                   |
| TRADER          | trader_01     | changeme | Execution-focused; order TCA, algo perf, alpha decay, venue SOR.           |
| CLIENT          | client_cp_a   | changeme | Limited to own counterparty (CP_ABCD); order summaries, client view only.  |
| CLIENT          | client_cp_b   | changeme | Limited to own counterparty (CP_EFGH); order summaries, client view only.  |


---

*Passwords default to "changeme" (hashed with bcrypt); override via env vars (e.g., TRADER_01_SECRET) before seeding.*

---

## User Session and Authorization

The PrivateBank TCA platform implements secure user sessions and authorization using JWT-based authentication and role-based access control (RBAC) to protect sensitive TCA data and ensure compliance with MiFID II. This section details the flow, components, and security measures.

### Authentication Flow

- **Login Process**:
  - Users enter `client_id` (e.g., "trader_01") and `client_secret` (default: "changeme") in the Angular SPA's LoginComponent.
  - Angular sends a POST request to FastAPI's `/auth/token` endpoint with `application/x-www-form-urlencoded` data.
  - FastAPI validates credentials against the `auth.api_clients` table in PostgreSQL using bcrypt hashing.
  - On success, FastAPI issues RS256-signed JWTs: access token (8 hours) and refresh token (7 days), generated via `jwt_handler.py` with auto-created RSA keys.
  - Tokens are returned to Angular, stored in localStorage, and managed by NgRx for state handling.
- **Session Management**:
  - JWTs enable stateless sessions—no server-side storage.
  - The `authInterceptor` in Angular appends `Authorization: Bearer <token>` to all API requests.
  - Tokens are refreshed automatically via `/auth/refresh` before expiry.
- **Logout**:
  - Angular clears localStorage and dispatches NgRx logout actions, redirecting to login.

### Authorization and Access Control

- **RBAC Hierarchy**: ADMIN(5) > HEAD_OF_TRADING(4) > COMPLIANCE(3) > TRADER(2) > CLIENT(1). Enforced via `rbac.py` with `require_role()` and `require_min_role()` decorators on FastAPI endpoints.
- **Data Isolation**: All queries inject `AND counterparty_id = :cp` (from JWT claims) to restrict CLIENT users to their own data (e.g., `fact_order_execution`). Unauthorized access returns HTTP 404 (not 403) for security.
- **Guards in Angular**: `authGuard` checks login; `roleGuard` enforces UI-level restrictions (e.g., CLIENT sees only `/client-view`).
- **API Security**: Endpoints like `/tca/algo-performance` require TRADER+; `/mifid/export` requires COMPLIANCE+. Pydantic models validate inputs; bcrypt secures passwords.

### Security Measures

- **JWT Security**: RS256 signatures prevent tampering; claims include `client_id`, `role`, `counterparty_id`.
- **Encryption**: Passwords hashed with bcrypt; optional HTTPS for production.
- **Auditability**: All auth attempts logged; tokens traceable via refresh mechanism.
- **Testing**: Covered in `tests/api/test_auth.py` (e.g., invalid tokens → 401, role blocks → 403/404).

This setup ensures secure, scalable access for TCA users while maintaining regulatory compliance.

---

## Error Handling and Observability

The PrivateBank TCA platform includes robust error handling and observability mechanisms to ensure reliability, detect anomalies, and maintain data quality in a high-stakes financial environment. These features support real-time monitoring, automated quarantining, and user-facing alerts.

### Anomaly Detection

- **Component**: `analytics/observability/anomaly_detector.py`
- **Function**: Uses Z-score analysis (threshold: 3.0) on metrics like slippage_bps and fill_quantity. Requires a minimum history (10 rows) to avoid false positives.
- **Triggers**: Flags outliers (e.g., excessive slippage) during analytics runs. Normal cases pass without alerts; extreme cases quarantine data.
- **Purpose**: Identifies data inconsistencies, such as erroneous fills or market data glitches, ensuring TCA accuracy.

### Quarantine Process

- **Component**: `analytics/observability/quarantine.py`
- **Function**: Routes FAIL records (e.g., validation errors, anomalies) to `obs.quarantine_queue` and `obs.obs_warnings` tables in PostgreSQL.
- **Triggers**: Activated by anomaly detection or ETL validation failures (e.g., missing benchmarks).
- **Purpose**: Isolates problematic data for manual review, preventing downstream issues in marts or reports. Maintains auditability without halting pipelines.

### Monitoring and Alerts

- **API Endpoint**: GET `/reports/warning` (returns obs_warnings for ADMIN/TRADER/COMPLIANCE roles).
- **Frontend**: DashboardComponent displays warnings (e.g., late data impacts, quarantine counts).
- **Orchestration**: Airflow DAGs log errors/latency in `obs.obs_warnings`; ExternalTaskSensors monitor dependencies.
- **Purpose**: Provides real-time visibility into system health, data quality, and operational issues. Supports proactive fixes (e.g., reprocessing late data).

### Error Handling in Pipelines

- **ETL Validation**: Rules in dlt pipelines (e.g., soft warns for missing fees, hard rejects for invalid timestamps) ensure data integrity.
- **Retry Logic**: Airflow handles transient failures; persistent errors quarantine records.
- **Logging**: All components log to console/files; PostgreSQL immutable logs support MiFID II audits.

This observability layer ensures the platform detects and mitigates issues like late arriving data or anomalies, maintaining compliance and user trust.

---

## Running on Docker

### Prerequisites

- Docker Desktop ≥ 4.x with Compose V2
- 6 GB RAM allocated to Docker (Airflow + TimescaleDB + Redis + 3 Python services)

### Step 1 — Environment

```bash
cp .env.example .env
# Edit .env if you want non-default credentials
```

### Step 2 — Start storage layer first

```bash
docker compose up -d postgres redis
# Wait for health checks to pass (~15 s)
docker compose ps   # both should show "healthy"
```

### Step 3 — Bootstrap database schemas and seed data

```bash
# Run seed inside the app container (schemas are created by init.sql on first postgres start)
docker compose run --rm app python ingestion/seed.py
```

This runs `init.sql` (done automatically by PostgreSQL on first start), then seeds 400 synthetic orders via all dlt pipelines and hashes the auth client secrets.

### Step 4 — Run dbt to build the Data Vault and marts

```bash
docker compose run --rm app bash -c "cd /app && dbt deps && dbt build --target docker"
```

`dbt deps` fetches `dbt_utils`, `dbt_expectations`, and `dbt_date`. `dbt build` runs staging → raw_vault → biz_vault → marts in dependency order and executes all schema tests.

### Step 5 — Start the Python services

```bash
docker compose up -d app mock-server
```


| Service     | URL                                                      | Description               |
| ----------- | -------------------------------------------------------- | ------------------------- |
| FastAPI     | [http://localhost:8088/docs](http://localhost:8088/docs) | External API + Swagger UI |
| Mock server | [http://localhost:8001/docs](http://localhost:8001/docs) | Synthetic event generator |


### Step 6 — Start Airflow

```bash
docker compose up -d airflow-init
# Wait for airflow-init to complete (status: exited 0)
docker compose up -d airflow-webserver airflow-scheduler
```

Airflow UI: [http://localhost:8088](http://localhost:8088) — default credentials `admin / admin`.

Unpause DAGs from the UI or via CLI:

```bash
docker compose exec airflow-scheduler airflow dags unpause tca_ingest_batch
docker compose exec airflow-scheduler airflow dags unpause tca_raw_vault
docker compose exec airflow-scheduler airflow dags unpause tca_biz_vault_eod
docker compose exec airflow-scheduler airflow dags unpause tca_marts_eod
docker compose exec airflow-scheduler airflow dags unpause tca_rt_consumer
docker compose exec airflow-scheduler airflow dags unpause tca_weekly_reports
```

#### Manual first-run — trigger order

DAGs are paused at creation. For the initial bootstrap, trigger them in dependency order (do not trigger the next DAG until the previous one has completed successfully):


| Step | DAG                  | What it does                                                                     |
| ---- | -------------------- | -------------------------------------------------------------------------------- |
| 1    | `tca_ingest_batch`   | Lands raw data into `stg_raw` via dlt; builds staging views                      |
| 2    | `tca_raw_vault`      | Builds all hubs, links, and satellites in `raw_vault`                            |
| 3    | `tca_biz_vault_eod`  | Derives business vault metrics + PIT snapshot in `biz_vault`                     |
| 4    | `tca_marts_eod`      | Builds all mart star schemas; runs MiFID export + catalog update                 |
| 5    | `tca_rt_consumer`    | Start last — polls Redis for real-time fills (needs `stg_raw.rt_fills` to exist) |
| 6    | `tca_weekly_reports` | Run any time after step 4; generates algo/trader/venue CSVs                      |


```bash
# Trigger each in order and wait for green before the next
docker compose exec airflow-scheduler airflow dags trigger tca_ingest_batch
docker compose exec airflow-scheduler airflow dags trigger tca_raw_vault
docker compose exec airflow-scheduler airflow dags trigger tca_biz_vault_eod
docker compose exec airflow-scheduler airflow dags trigger tca_marts_eod
docker compose exec airflow-scheduler airflow dags trigger tca_rt_consumer
docker compose exec airflow-scheduler airflow dags trigger tca_weekly_reports
```

### Step 7 — Start the Angular SPA

```bash
docker compose up -d angular
```

SPA: [http://localhost:4200](http://localhost:4200)

### Start everything at once (after first seed)

```bash
docker compose up --build
```

### Full startup order summary

```
postgres ──► redis ──► app ──► mock-server
                  │
                  └──► airflow-init ──► airflow-webserver
                                   └──► airflow-scheduler
                  │
                  └──► angular (depends on app)
```

### Useful commands

```bash
# Tail API logs
docker compose logs -f app

# Trigger a DAG run manually
docker compose exec airflow-scheduler airflow dags trigger tca_ingest_batch

# Run analytics engine directly
docker compose exec app python analytics/engine.py --date 2025-01-15

# Generate a MiFID export
docker compose exec app python -c "
from reports.mifid_export import generate_mifid_rts27
from datetime import date
print(generate_mifid_rts27(trade_date=date(2025, 1, 15)))
"

# Rebuild only the SPA
docker compose build angular && docker compose up -d angular

# Reset everything (data loss)
docker compose down -v
```

### Quick Start
# Rebuild all
docker compose up
# Airfflow
http://localhost:8080/home
# UI
http://localhost:4200/dashboard

---

## Running the tests

Tests require a live PostgreSQL connection. Run them inside the `app` container, or locally with the `.env` file sourced.

### Inside Docker (recommended)

```bash
# All tests
docker compose exec app bash -c "cd /app && coverage run -m unittest discover -s tests && coverage report -m"

# By layer
docker compose exec app coverage run -m unittest discover -s tests/ingestion
docker compose exec app coverage run -m unittest discover -s tests/analytics
docker compose exec app coverage run -m unittest discover -s tests/api

# Single test class
docker compose exec app python -m unittest tests.analytics.test_cost_decomposition.TestAlmgrenChrissImpact -v
```

### Locally (with venv)

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Source environment (assumes postgres is reachable on localhost:5432)
source .env

# Run all
coverage run -m unittest discover -s tests
coverage report -m
coverage html   # opens htmlcov/index.html
```

### dbt tests

```bash
# Inside Docker
docker compose exec app bash -c "cd /app && dbt test --target docker"

# Specific model
docker compose exec app bash -c "cd /app && dbt test --select hub_order --target docker"

# Source freshness
docker compose exec app bash -c "cd /app && dbt source freshness --target docker"
```

### Test inventory


| Suite                                        | File                                 | What it covers                                                                                                                                   |
| -------------------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `tests/ingestion/test_oms_source.py`         | OMS source                           | 400 orders, 4 asset classes × 100, no spot FX, required fields, reproducibility, side validity                                                   |
| `tests/ingestion/test_market_data_source.py` | Market data                          | Bar count, OHLCV field presence, H ≥ L, close within range, positive prices, chronological order                                                 |
| `tests/analytics/test_cost_decomposition.py` | Almgren-Chriss + alpha decay regimes | Positive impact for BUY/SELL, monotonic with participation + vol, zero-ADV guard, regime boundaries                                              |
| `tests/analytics/test_anomaly_detector.py`   | Anomaly detector                     | Normal no-flag, extreme outlier flag, minimum history guard, Z-score value, negative outlier                                                     |
| `tests/api/test_auth.py`                     | JWT auth + counterparty isolation    | Valid credentials → tokens, invalid → 401, JWT structure, missing field → 422, non-existent order → 404, CLIENT blocked from algo-perf and MiFID |
| `tests/api/test_tca_endpoints.py`            | TCA + orders endpoints               | Summary returns list, counterparty + asset-class filters, missing trade_date → 422, algo-performance for admin, orders field presence            |


---

## Environment variables reference


| Variable                        | Default                                                   | Description                                                  |
| ------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------ |
| `DATABASE_URL`                  | `postgresql://tca_user:tca_password@postgres:5432/tca_db` | SQLAlchemy connection string                                 |
| `REDIS_URL`                     | `redis://redis:6379/0`                                    | Redis connection URL                                         |
| `JWT_PRIVATE_KEY_PATH`          | *(auto-generated)*                                        | Path to RSA private key PEM. Generated at startup if absent. |
| `JWT_PUBLIC_KEY_PATH`           | *(auto-generated)*                                        | Path to RSA public key PEM.                                  |
| `JWT_ALGORITHM`                 | `RS256`                                                   | Must be RS256.                                               |
| `JWT_ACCESS_TOKEN_EXPIRE_HOURS` | `8`                                                       | Access token lifetime.                                       |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7`                                                       | Refresh token lifetime.                                      |
| `AIRFLOW_SECRET_KEY`            | `supersecretkey-change-in-prod`                           | Airflow webserver secret.                                    |
| `REPORT_OUTPUT_DIR`             | `/tmp/tca_reports`                                        | Directory for generated CSV reports.                         |
| `DBT_PROJECT_DIR`               | `/opt/airflow/tca`                                        | Project root path used by Airflow tasks.                     |


---

## Tech stack


| Layer            | Technology                                  | Version         |
| ---------------- | ------------------------------------------- | --------------- |
| Language         | Python                                      | 3.11            |
| Batch EL         | dlt (dlthub)                                | 1.5.0           |
| Synthetic data   | Faker + NumPy                               | 25.x + 1.26.x   |
| Transform        | dbt-postgres + dbt_utils + dbt_expectations | 1.8.x           |
| Storage          | PostgreSQL 16 + TimescaleDB                 | latest-pg16     |
| RT message bus   | Redis Streams                               | 7-alpine        |
| Analytics        | pandas + scipy                              | 2.2.x + 1.13.x  |
| API              | FastAPI + python-jose[cryptography]         | 0.111.x + 3.3.x |
| Frontend         | Angular 1admin_017 + NgRx 17                | 17.3.x          |
| Orchestration    | Apache Airflow                              | 2.9.3           |
| Containerisation | Docker Compose v2                           | —               |


