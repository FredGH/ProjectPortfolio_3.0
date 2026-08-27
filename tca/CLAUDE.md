# CLAUDE.md — PrivateBank TCA Platform

Transaction Cost Analysis platform for PrivateBank's pan-European equities institutional broker business.
MiFID II / MiFIR compliant. PoC — synthetic data, fully containerised, local-only deployment.

Requirements: [`docs/privatebank_tca_requirements.docx`](docs/privatebank_tca_requirements.docx)
Architecture: [`docs/privatebank_tca_architecture.docx`](docs/privatebank_tca_architecture.docx)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL DATA SOURCES                             │
│   OMS/EMS (FIX 4.4)   Market Data (REST)   Ref Data   Fixed Income      │
└─────────┬──────────────────┬──────────┬──────────────────┬──────────────┘
          │                  │          │                  │
┌─────────▼──────────────────▼──────────▼──────────────────▼──────────────┐
│              A. DATA EXTRACTION LAYER                                    │
│   Batch: dlt (dlthub)          Real-time: FastAPI Mock + Redis Streams   │
│   ┌──────────────────┐         ┌──────────────────────────────────────┐  │
│   │ dlt pipelines    │         │ POST /mock/fill  → pb:fills stream  │  │
│   │ oms_source       │         │ POST /mock/order → pb:orders stream │  │
│   │ market_data_src  │         │ POST /mock/tick  → pb:market_ticks  │  │
│   │ ref_data_source  │         │ Redis Consumer: XREADGROUP → DB      │  │
│   │ fi_pricing_src   │         └──────────────────────────────────────┘  │
│   │ eurex_source     │                         │                         │
│   └──────────────────┘                         │                         │
└─────────────────────────┬──────────────────────┘                         │
                          │  stg_raw schema (PostgreSQL landing zone)       │
┌─────────────────────────▼──────────────────────────────────────────────┐ │
│              B. DATA VAULT 2.0  (dbt on PostgreSQL)                    │ │
│                                                                        │ │
│  models/staging/   →   models/raw_vault/    →   models/biz_vault/     │ │
│  stg_* views           Hubs / Links / Sats       Derived satellites    │ │
│                         (raw_vault schema)        PIT / Bridge tables  │ │
│                                                   (biz_vault schema)   │ │
│                                    │                                   │ │
│  ┌─────────────────────────────────▼─────────────────────────────────┐ │ │
│  │  INFORMATION MARTS  (star schemas per domain)                     │ │ │
│  │  mart_trading_risk  |  mart_market_data  |  mart_corporate        │ │ │
│  │  mart_consolidated  (entity schemas: pb_de / pb_uk / bcm_us)   │ │ │
│  └───────────────────────────────────────────────────────────────────┘ │ │
└─────────────────────┬───────────────────┬──────────────────────────────┘ │
                      │                   │                                 │
┌─────────────────────▼──┐   ┌────────────▼──────┐   ┌────────────────────▼┐
│  C. OBSERVABILITY      │   │  D. LINEAGE &     │   │  E. ORCHESTRATION   │
│  Anomaly detection     │   │     CATALOG       │   │  Airflow 2.x DAGs   │
│  Quarantine queue      │   │  dbt docs + store │   │  6 DAGs (see §G)    │
│  obs_warnings table    │   └───────────────────┘   └─────────────────────┘
└────────────────────────┘
┌──────────────────────────────┐   ┌──────────────────────────────────────┐
│  F. ANGULAR 17 SPA           │   │  G. FASTAPI EXTERNAL API             │
│  Role-based dashboards       │   │  JWT RS256 auth                      │
│  Counterparty-scoped views   │   │  Row-level security (counterparty)   │
│  NgRx state · HttpInterceptor│   │  Pydantic response models            │
│  port 4200                   │   │  port 8000                           │
└──────────────────────────────┘   └──────────────────────────────────────┘
                    │                              │
                    └──────────────────────────────┘
                              Docker Compose
                          (local-only PoC · no git push)
```

### Instrument Coverage (4 classes — Spot FX EXCLUDED per MiFID II scope)

| Class | Code | TCA Depth | Notes |
|---|---|---|---|
| Cash Equities & ETFs | `equity` | Full (all modules) | Pan-European, lit venues |
| Listed Equity Derivatives | `equity_future` | Full | Eurex, EDSP benchmark |
| Fixed Income | `fixed_income` | Partial | DV01-adjusted, yield slippage |
| FX Derivatives | `fx_derivative` | Partial | Forward points, tenor-adjusted spread |

> Spot FX permanently excluded. Requirements doc `§2` supersedes original CLAUDE.md note.
> Synthetic data: **100 orders × 4 asset classes = 400 orders total**; generated via Faker + NumPy.

---

## Build SOP

Build phases in order. Verify each phase compiles and passes `dbt build` / `coverage run -m unittest discover` before starting the next.

### Phase 1 — Project Scaffold & Storage

1. `docker-compose.yml` — services: postgres (TimescaleDB), redis, app, mock-server, airflow-*, angular
2. `Dockerfile` (python:3.11-slim, port 8000) · `Dockerfile.angular` (node:20 build → nginx)
3. `requirements.txt` / `setup.py` — all dependencies pinned (dlt, faker, python-jose, etc.)
4. `.env.example` — all env vars including JWT_PRIVATE_KEY path
5. `init.sql` — create all schemas + TimescaleDB hypertable on `stg_raw.tick_bars` +
   obs/catalog/auth tables (dlt creates stg_raw tables; dbt creates vault/mart tables)
6. Verify: `docker compose up postgres` → schema loads cleanly

### Phase 2 — dlt Ingestion + Mock Server + Redis Consumer

7. `ingestion/sources/oms_source.py` — `@dlt.source`: orders + fills (all 4 asset classes, Faker + NumPy)
8. `ingestion/sources/market_data_source.py` — 30s OHLCV bars per symbol (Faker + NumPy GBM)
9. `ingestion/sources/ref_data_source.py` — instruments, clients, venues, algos, traders (Faker)
10. `ingestion/sources/fi_pricing_source.py` — bond evaluated prices (NumPy yield simulation)
11. `ingestion/sources/eurex_source.py` — EDSP settlements (synthetic CSV-style)
12. `ingestion/pipelines/run_all.py` — dlt pipeline runner for all sources → `stg_raw` schema
13. `ingestion/mock/mock_server.py` — FastAPI mock (POST /mock/fill|order|tick, GET /mock/seed)
    Uses Faker to generate payloads, publishes to Redis Streams (pb:fills, pb:orders, pb:market_ticks)
14. `ingestion/mock/redis_consumer.py` — XREADGROUP consumer → writes to `stg_raw.rt_fills`
15. `ingestion/seed.py` — runs all dlt pipelines once (bootstraps synthetic data for the PoC)
16. Verify: `python ingestion/seed.py` → 400 orders + fills in `stg_raw`

### Phase 3 — dbt Data Vault 2.0

17. `dbt_project.yml` · `profiles.yml` · `packages.yml` (dbt_utils + dbt_expectations)
18. `macros/generate_schema_name.sql` — exact schema names (no prefix)
19. **Staging** (views on `stg_raw.*`): stg_orders, stg_fills, stg_tick_bars, stg_clients,
    stg_instruments, stg_bond_prices
20. **Raw Vault** (incremental, `raw_vault` schema):
    - Hubs: hub_order, hub_fill, hub_instrument, hub_client, hub_venue, hub_trader, hub_algo, hub_legal_entity
    - Links: lnk_order_fill, lnk_order_client, lnk_order_instrument, lnk_fill_venue, lnk_order_algo, lnk_order_entity
    - Satellites: sat_order_details, sat_fill_execution, sat_price_tick (TimescaleDB), sat_instrument_ref,
      sat_client_profile, sat_venue_detail, sat_algo_version
21. **Business Vault** (incremental, `biz_vault` schema):
    bv_order_enriched, bv_tca_costs, bv_alpha_decay, bv_adverse_selection,
    pit_order_snapshot, bv_trader_attribution, bv_peer_benchmark, bv_mifid_fields
22. **Information Marts** (table, domain schemas):
    - `mart_trading_risk`: fact_order_execution + dim_algo/trader/venue/mifid
    - `mart_market_data`: fact_price_benchmark + dim_instrument/date
    - `mart_corporate`: fact_client_activity + dim_client/legal_entity
    - `mart_consolidated`: entity_pb_de / entity_pb_uk / entity_bcm_us
23. Verify: `dbt build` — all models green, all tests pass

### Phase 4 — Analytics Engine + Observability

24. `analytics/engine.py` — orchestrates modules; reads from `biz_vault.bv_order_enriched`
25. `analytics/modules/` — 10 modules: cost_decomposition, adverse_selection, venue_sor,
    fill_pattern, peer_benchmarking, eurex_derivatives, fixed_income_tca, fx_derivatives_tca,
    mifid_compliance, pre_trade
26. `analytics/attribution.py` — trader_cost / algo_cost / market_cost decomposition
27. `analytics/alpha_decay.py` — regime-tagged alpha curves (Low / Medium / High vol)
28. `analytics/observability/anomaly_detector.py` — Z-score checks (window 30d), volume checks
29. `analytics/observability/quarantine.py` — routes FAIL records to `obs.quarantine_queue`
30. Verify: engine processes 400 orders in <30 seconds

### Phase 5 — FastAPI External API (JWT RS256)

31. `api/auth/jwt_handler.py` — issue/verify JWT (RS256, 8h expiry); generate key pair on startup
32. `api/auth/dependencies.py` — `get_current_user()` → `UserClaims` dataclass
33. `api/auth/rbac.py` — `require_role()` dependency factory
34. `api/routers/auth.py` — POST /auth/token · POST /auth/refresh
35. `api/routers/tca.py` — GET /tca/order/{id} · /tca/summary · /tca/algo-performance · /tca/alpha-decay · /tca/peer-benchmark
36. `api/routers/orders.py` — GET /orders (counterparty-scoped)
37. `api/routers/reports.py` — GET /reports/warning
38. `api/routers/mifid.py` — GET /mifid/export (COMPLY only)
39. `api/routers/pipeline.py` — POST /pipeline/run (ADMIN only, triggers Airflow DAG)
40. `api/services/tca_service.py` — queries marts; **always** injects `counterparty_id` WHERE clause
41. `api/schemas/` — Pydantic response models (TCAResult, OrderSummary, AlgoPerf, etc.)
42. Verify: GET /tca/order/{id} responds in <200ms; CLIENT role cannot see other counterparty data

### Phase 6 — Angular 17 SPA

43. `frontend/package.json` — Angular 17, NgRx 17, jwt-decode
44. `frontend/src/app/core/auth/` — AuthService (JWT decode → NgRx), AuthGuard, RoleGuard
45. `frontend/src/app/core/interceptors/auth.interceptor.ts` — appends Bearer token to all requests
46. `frontend/src/app/core/services/api.service.ts` — wraps all FastAPI calls
47. `frontend/src/app/store/` — NgRx auth state (token, user claims, loading)
48. `frontend/src/app/features/dashboard/` — role-aware landing page
49. `frontend/src/app/features/order-tca/` — order drilldown with slippage decomposition
50. `frontend/src/app/features/algo-perf/` — algo performance (TRADER+ only)
51. `frontend/src/app/features/alpha-decay/` — alpha curves by regime
52. `frontend/src/app/features/venue-sor/` — venue scorecard
53. `frontend/src/app/features/mifid/` — RTS export (COMPLIANCE only)
54. `frontend/src/app/features/client-view/` — counterparty-scoped view (CLIENT role)
55. Verify: `docker compose up angular` → SPA reachable at http://localhost:4200

### Phase 7 — Airflow DAGs + Reports + Tests

56. `dags/dag_ingest_batch.py` — 06:45 CET: dlt run → dbt staging → obs → catalog
57. `dags/dag_raw_vault.py` — 07:15 CET: dbt raw_vault → obs → catalog
58. `dags/dag_biz_vault_eod.py` — 17:30 CET: dlt fi+eurex → dbt biz_vault → obs
59. `dags/dag_marts_eod.py` — 18:15 CET: dbt marts → obs → catalog → mifid export
60. `dags/dag_rt_consumer.py` — continuous: Redis sensor (30s) → stg_raw.rt_fills → micro-dbt
61. `dags/dag_weekly_reports.py` — Monday 07:00 CET: algo digest + trader attribution + venue scorecard
62. `reports/` — order_tca_report.py, trader_digest.py, algo_digest.py, mifid_export.py
63. `tests/` — integration tests (real DB): ingestion, analytics modules, API routes

---

## Tech Stack

| Layer | Technology | Version |
|---|---|---|
| Language | Python | 3.11 (not 3.12/3.13) |
| Batch EL | dlt (dlthub) | 1.5.0 |
| Synthetic data | Faker + NumPy | 25.x + 1.26.x |
| Transform | dbt-postgres + dbt_utils + dbt_expectations | 1.8.x |
| Vault model | Data Vault 2.0 (Hubs / Links / Satellites) | — |
| Storage | PostgreSQL 16 + TimescaleDB | latest-pg16 |
| RT message bus | Redis Streams (XADD / XREADGROUP) | 7-alpine |
| Analytics | pandas + scipy (Almgren-Chriss, alpha decay) | 2.2.x / 1.13.x |
| API | FastAPI + python-jose[cryptography] | 0.111.x / 3.3.x |
| Auth | JWT RS256, 8h expiry, `counterparty_id` claim | — |
| Frontend | Angular 17 SPA + NgRx | 17.x |
| Orchestration | Apache Airflow 2.9 (LocalExecutor) | 2.9.3 |
| Observability | dbt-expectations + custom Python anomaly detector | — |
| Containerisation | Docker Compose (local-only) | v2 |

---

## File Structure

```
tca/
├── docker-compose.yml              # postgres, redis, app, mock-server, airflow-*, angular
├── Dockerfile                      # python:3.11-slim, port 8000 (FastAPI + analytics)
├── Dockerfile.angular              # node:20 build → nginx:alpine, port 80
├── .env.example
├── requirements.txt
├── setup.py
├── init.sql                        # schemas + obs/catalog/auth tables + TimescaleDB ext
├── app.py                          # FastAPI entry point (external API)
├── config.py
├── db.py
│
├── ingestion/
│   ├── sources/
│   │   ├── oms_source.py           # dlt source: orders + fills (all 4 classes, Faker+NumPy)
│   │   ├── market_data_source.py   # dlt source: 30s OHLCV bars (GBM simulation)
│   │   ├── ref_data_source.py      # dlt source: instruments, clients, venues, algos
│   │   ├── fi_pricing_source.py    # dlt source: bond evaluated prices + DV01
│   │   └── eurex_source.py         # dlt source: EDSP settlements
│   ├── pipelines/
│   │   └── run_all.py              # dlt pipeline runner → stg_raw schema
│   ├── mock/
│   │   ├── mock_server.py          # FastAPI mock (port 8001): generates + publishes to Redis
│   │   └── redis_consumer.py       # XREADGROUP consumer → stg_raw.rt_fills
│   └── seed.py                     # Bootstrap: run all dlt pipelines once
│
├── models/                         # dbt Data Vault 2.0
│   ├── sources.yml
│   ├── staging/                    # stg_* views on stg_raw.*
│   ├── raw_vault/
│   │   ├── hubs/                   # hub_order, hub_fill, hub_instrument, hub_client ...
│   │   ├── links/                  # lnk_order_fill, lnk_order_client ...
│   │   └── satellites/             # sat_order_details, sat_fill_execution, sat_price_tick ...
│   ├── biz_vault/                  # bv_order_enriched, bv_tca_costs, bv_alpha_decay ...
│   └── marts/
│       ├── trading_risk/           # fact_order_execution + dims
│       ├── market_data/            # fact_price_benchmark + dims
│       ├── corporate/              # fact_client_activity + dims
│       └── consolidated/           # entity_pb_de / entity_pb_uk / entity_bcm_us
│
├── macros/
│   └── generate_schema_name.sql    # exact schema names (no dbt prefix)
│
├── analytics/
│   ├── engine.py
│   ├── attribution.py
│   ├── alpha_decay.py
│   ├── modules/                    # 10 TCA modules
│   └── observability/
│       ├── anomaly_detector.py     # Z-score + volume checks
│       └── quarantine.py           # obs.quarantine_queue writer
│
├── api/
│   ├── main.py
│   ├── auth/
│   │   ├── jwt_handler.py          # RS256 issue/verify
│   │   ├── dependencies.py         # get_current_user() → UserClaims
│   │   └── rbac.py                 # require_role() factory
│   ├── routers/
│   │   ├── auth.py                 # POST /auth/token · /auth/refresh
│   │   ├── tca.py                  # GET /tca/*
│   │   ├── orders.py               # GET /orders (counterparty-scoped)
│   │   ├── reports.py              # GET /reports/warning
│   │   ├── mifid.py                # GET /mifid/export (COMPLY only)
│   │   └── pipeline.py             # POST /pipeline/run (ADMIN only)
│   ├── services/
│   │   └── tca_service.py          # mart queries + mandatory counterparty_id filter
│   ├── db/
│   │   └── session.py              # SQLAlchemy session
│   └── schemas/                    # Pydantic response models
│
├── frontend/                       # Angular 17 SPA
│   ├── package.json
│   ├── angular.json
│   ├── tsconfig.json
│   ├── nginx.conf                  # SPA routing + /api proxy
│   └── src/
│       ├── main.ts
│       └── app/
│           ├── app.config.ts
│           ├── app.routes.ts
│           ├── core/
│           │   ├── auth/           # AuthService, AuthGuard, RoleGuard
│           │   ├── interceptors/   # auth.interceptor.ts
│           │   └── services/       # api.service.ts
│           ├── store/              # NgRx: auth state
│           ├── shared/             # DataTable, Chart, FilterBar components
│           └── features/
│               ├── dashboard/      # role-aware landing
│               ├── order-tca/      # drilldown: slippage decomposition
│               ├── algo-perf/      # TRADER+ only
│               ├── alpha-decay/    # regime curves
│               ├── venue-sor/      # venue scorecard
│               ├── mifid/          # COMPLIANCE only
│               └── client-view/    # CLIENT role: own orders only
│
├── dags/
│   ├── dag_ingest_batch.py         # 06:45 CET
│   ├── dag_raw_vault.py            # 07:15 CET
│   ├── dag_biz_vault_eod.py        # 17:30 CET
│   ├── dag_marts_eod.py            # 18:15 CET
│   ├── dag_rt_consumer.py          # continuous, 30s sensor
│   └── dag_weekly_reports.py       # Monday 07:00 CET
│
├── reports/
│   ├── order_tca_report.py
│   ├── trader_digest.py
│   ├── algo_digest.py
│   └── mifid_export.py
│
├── tests/
│   ├── ingestion/
│   ├── analytics/
│   └── api/
│
├── dbt_project.yml
├── packages.yml                    # dbt_utils + dbt_expectations
├── profiles.yml
└── docs/
    ├── privatebank_tca_requirements.docx
    └── privatebank_tca_architecture.docx
```

---

## Data Vault 2.0 Schema Layout

```
stg_raw       ← dlt landing zone (immutable raw tables)
raw_vault     ← Hubs, Links, Satellites (no business rules)
biz_vault     ← Derived Satellites, PIT, Bridge (soft rules, versioned)
mart_trading_risk   ← fact_order_execution + dim_algo/trader/venue/mifid
mart_market_data    ← fact_price_benchmark + dim_instrument/date
mart_corporate      ← fact_client_activity + dim_client/legal_entity
mart_consolidated   ← UNION ALL across domains + legal entity schemas
obs           ← quarantine_queue, obs_warnings
catalog       ← datasets metadata store
auth          ← refresh_tokens, api_clients
airflow_db    ← Airflow metadata (separate database)
```

---

## Counterparty Isolation — 5 Layers (architecture doc §J)

| Layer | Component | Mechanism |
|---|---|---|
| Data model | `lnk_order_client` | Every order linked to exactly one `counterparty_id` — non-nullable |
| Mart | `fact_order_execution` | `counterparty_id` denormalised into all fact tables |
| API | `tca_service.py` | `AND counterparty_id = :user.counterparty_id` injected into **every** query |
| JWT | RS256 token | `counterparty_id` embedded in signed JWT claim at login |
| Angular | `RoleGuard` | CLIENT role → `/client-view` only; internal routes blocked |

CLIENT queries for another counterparty's order return **HTTP 404**, not 403 (no existence leakage).

---

## Rules

| File type | Style | Testing |
|---|---|---|
| `.py` | [`.claude/rules/python-style.md`](.claude/rules/python-style.md) | [`.claude/rules/python-testing.md`](.claude/rules/python-testing.md) |
| `.sql` / dbt | [`.claude/rules/sql-style.md`](.claude/rules/sql-style.md) | [`.claude/rules/sql-testing.md`](.claude/rules/sql-testing.md) |
| API endpoints | [`.claude/commands/api-review.md`](.claude/commands/api-review.md) | Same as `.py` |

---

## Agents

| Agent | When to use |
|---|---|
| `python-reviewer` | dlt sources, analytics modules, API routes |
| `python-security-auditor` | JWT handler, tca_service.py (RLS queries), Redis consumer |
| `sql-reviewer` | All dbt DV2 models — grain, fan-out, hash key correctness |
| `sql-security-auditor` | Satellites with PII (sat_client_profile), mart counterparty columns |
| `security-auditor` | Full OWASP pass: auth flows, counterparty isolation, Airflow DAGs |

---

## Key Commands

```bash
# Start full stack
docker compose up --build

# Seed 400 synthetic orders via dlt
python ingestion/seed.py

# Run dbt DV2 pipeline
dbt build                         # staging → raw_vault → biz_vault → marts
dbt build --select raw_vault      # just the vault layer
dbt test --select hub_order       # single model tests

# Run analytics engine manually
python analytics/engine.py --date 2025-01-15

# Angular dev server (outside Docker)
cd frontend && npm install && npm start

# Tests (real DB — no mocking)
coverage run -m unittest discover && coverage report -m

# Code quality
ruff check . && isort . && black .
```

---

## Development Workflow

1. **Before starting a non-trivial change**, ask once, in a single question: is this a `feat` / `fix` / `chore` / `docs` / `refactor` / `test`, or should it just be made directly without a branch? Skip asking for docs/comment/config-only edits, if already on a non-main branch, or if already answered earlier in this conversation. Branch as `<type>/<slug>` (see the `commit-push` skill).
2. **If `plan/backlog.yml` exists**, use the `jira-log` skill to record the confirmed fix/feature as a Jira ticket.
3. Implement changes following the rules in `.claude/rules/` (see Rules table above)
4. Run `dbt build` and/or `coverage run -m unittest discover` as relevant before committing
5. Use the `commit`, `commit-push`, `pr`, or `commit-push-pr` skills to commit/branch/push/open a PR

---

## Constraints

- **Python 3.11 only** — not 3.12, not 3.13 (dlt 1.5.0 incompatibility with 3.13)
- **Spot FX permanently excluded** — not a MiFID II instrument for PrivateBank
- **dlt == 1.5.0** for all batch EL pipelines
- **Data Vault 2.0** methodology — no shortcutting to star schema; Raw Vault is immutable
- **counterparty_id filter is mandatory** in every mart query — not optional middleware
- **JWT RS256** — not HS256, not simple Bearer token; RS256 key pair generated at startup
- **No DB mocking** — all integration tests use a real Postgres connection
- **If any requirement is ambiguous** — stop and ask; do not guess or invent behaviour


## Context Management

When compacting, preserve: file paths touched, migration/dbt phase reached (see Build SOP), test commands run, and any pending decisions or TODOs.
