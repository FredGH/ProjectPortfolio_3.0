# PrivateBank TCA — Managed Cloud Cost Estimation
## Snowflake · Managed Airflow · Tableau Cloud · Batch Only

> **Scope**: Batch pipeline only (no real-time Kafka/streaming). No Kubernetes, no FastAPI infrastructure.
> Architecture reference: `docs/proposed_production_architecture.md` — Verbose Component Interaction Story.
> Pricing as of Q2 2026. All figures in USD.

---

## 1. Executive Summary

| Item | Annual Cost (USD) |
|---|---|
| Snowflake — storage (HOT + COOL, ~1.75 TB) | $234 |
| Snowflake — compute (virtual warehouses) | $21,321 |
| Snowflake — enterprise support | $2,156 |
| Managed Airflow (AWS MWAA) | $4,500 |
| Tableau Cloud — 50 users | $17,460 |
| dbt Cloud — Team plan | $1,500 |
| S3 object storage (staging + MiFID archive) | $100 |
| Contingency (5%) | $2,364 |
| **Total — Year 1** | **~$49,600/year** |
| **Total — Year 2 onwards (data growth +27M records/year)** | **~$50,000–51,000/year** |

> Year-on-year cost increase from data growth is minimal (~$600/year) because 27M new records/year adds ~25 GB of compressed Snowflake storage across all Data Vault layers, with compute rising by fewer than 75 credits/year once the pipeline is stable. The dominant costs — Tableau licences and Airflow hosting — are fixed.

---

## 2. Scope and Constraints

### In scope
- Managed Snowflake (Enterprise edition, AWS `eu-central-1`, single account)
- dbt transformations (Data Vault 2.0: staging → raw_vault → biz_vault → marts)
- Managed Airflow batch orchestration (6 scheduled DAGs, no real-time sensor)
- Tableau Cloud (50 users, mixed licence tiers)
- S3 for staging files and immutable MiFID II archive (7-year retention)
- HOT storage tier: last **2 years** of data
- COOL storage tier: older **8 years** of data

### Explicitly excluded
| Excluded component | Why excluded |
|---|---|
| Confluent Cloud Kafka / Snowpipe streaming | Real-time path excluded per scope |
| Kubernetes / FastAPI / EKS cluster | No API infrastructure in this estimate |
| Okta / identity management | Separate corporate contract |
| Monte Carlo / Datadog | Observability tooling separate |
| CI/CD tooling (GitHub Actions, ArgoCD) | DevOps budget |
| Snowflake multi-account (3 legal entities) | Single account assumed for this estimate |

---

## 3. Assumptions

### 3.1 Data volumes

| Parameter | Value | Source / Rationale |
|---|---|---|
| Total records (existing, 10 years) | 2,000,000,000 | Stated requirement |
| Annual growth (new records/year) | 27,000,000 | Stated requirement |
| Average monthly ingestion | ~2,250,000 records/month | 27M ÷ 12 |
| Average daily ingestion | ~108,000 records/day | 27M ÷ 250 trading days |
| Record types | Orders, fills, market data bars, reference data | Architecture doc |
| Average uncompressed source record size | ~500 bytes | Weighted average: trade records ~1 KB, market data bars ~200 bytes |
| Snowflake columnar compression ratio | 5× | Typical for financial numeric data (many repetitive fields) |
| Compressed bytes per source record | ~100 bytes | 500 bytes ÷ 5 |

### 3.1.1 Data Vault 2.0 Storage Fan-out

A single source record (e.g. one order + fill) generates rows across multiple DV2.0 tables. Each row carries hash-key columns and DV metadata overhead in addition to business payload.

**DV2.0 table inventory:**

| Layer | Tables | Row count per source record |
|---|---|---|
| `stg_raw` landing | stg_orders, stg_fills, stg_tick_bars, stg_clients, stg_instruments, stg_bond_prices | ~1 row per source record |
| `raw_vault` hubs | hub_order, hub_fill, hub_instrument, hub_client, hub_venue, hub_trader, hub_algo, hub_legal_entity | 1 row per unique entity (low churn) |
| `raw_vault` links | lnk_order_fill, lnk_order_client, lnk_order_instrument, lnk_fill_venue, lnk_order_algo, lnk_order_entity | 1 row per relationship (~5 link rows per order) |
| `raw_vault` satellites | sat_order_details, sat_fill_execution, sat_price_tick, sat_instrument_ref, sat_client_profile, sat_venue_detail, sat_algo_version | 1 row per record per satellite; sat_price_tick is the largest (one row per 30s bar per instrument) |
| `biz_vault` | bv_order_enriched, bv_tca_costs, bv_alpha_decay, bv_adverse_selection, pit_order_snapshot, bv_trader_attribution, bv_peer_benchmark, bv_mifid_fields | 1–2 rows per order across derived satellites + PIT |
| `marts` | fact_order_execution + dims | ~1 denormalised row per order |

**Per-row overhead in DV2.0:**

| Overhead type | Size per row | Notes |
|---|---|---|
| Hub/link hash keys (MD5 or SHA-256) | 32–64 bytes per key column | Links carry 2–3 hash key FKs |
| DV metadata columns (load_date, record_source, load_end_date) | ~40–60 bytes | Every hub, link, sat row |
| Business columns (satellite body) | 200–500 bytes | Varies by satellite width |

**Effective fan-out:**

| Fan-out element | Multiplier | Rationale |
|---|---|---|
| Raw vault row proliferation (hubs + links + sats per source record) | ~7–8× | One order generates ~7–8 rows across raw vault tables |
| Hash key + metadata overhead per DV row | ~1.3× | ~50 bytes metadata on ~200 byte avg satellite body |
| Biz vault derived satellites + PIT | +1.5× | bv_* tables add ~1–2 further copies of enriched attributes |
| Information mart fact rows | +1× | Denormalised wide rows, but highly compressible |
| **Total uncompressed fan-out vs. source record** | **~8–10×** | |
| After Snowflake 5× columnar compression | **~1.6–2×** | Compressed effective multiplier |

> The `sat_price_tick` satellite (30-second OHLCV bars, all instruments, 10 years) is likely the single largest table in the raw vault. With >200 symbols × 10 years × ~13,000 30s bars/trading day, this table alone could reach 500 GB+ compressed.

### 3.2 Storage sizing

| Tier | Source records | Effective compressed storage (DV2.0 fan-out ~1.8× avg) | Time Travel overhead (+25%) |
|---|---|---|---|
| **HOT** (last 2 years at 27M/yr) | 54,000,000 | ~38 GB | ~48 GB |
| **COOL** (years 3–10) | 1,946,000,000 | ~1,362 GB | ~1,703 GB |
| **Total** | 2,000,000,000 | ~1,400 GB | **~1.75 TB** |

> Time Travel (90-day Enterprise) adds ~25% storage overhead. Fail-Safe (7 days) is included in that buffer.

### 3.3 Snowflake pricing (Enterprise, AWS eu-central-1, Capacity contract)

| Item | Rate |
|---|---|
| Compute credits | $3.00 / credit |
| HOT storage | $23 / TB / month |
| COOL storage | $6 / TB / month (≈4× cheaper than HOT) |
| Lifecycle policy serverless compute | ~$0.01 / GB transitioned |

> Enterprise edition is required for: row access policies (counterparty isolation), dynamic data masking (PII fields), 90-day Time Travel (MiFID II audit), and multi-cluster warehouses.

### 3.4 Warehouse sizing and usage

| Warehouse | Size | Credits/hr | Daily active hours | Trading days | Annual credits |
|---|---|---|---|---|---|
| `WH_INGEST` | XS | 1 | 0.25 h (15 min batch load) | 250 | 63 |
| `WH_TRANSFORM` | L | 8 | 1.0 h (dbt daily batch — 39+ DV2.0 tables) | 250 | 2,000 |
| `WH_TRANSFORM` | L | 8 | 3.5 h (dbt weekly full-refresh across all vault layers) | 52 | 1,456 |
| `WH_BI` | M | 4 | 3 h active (Tableau, 50 users, result cache ~40%) | 250 | 3,000 |
| `WH_BI` | M | 4 | 1 h light (weekends/pre-market) | 115 | 460 |
| `WH_BACKFILL` | XL | 16 | 2 h (on-demand, ~4 runs/year) | — | 128 |
| **Total** | | | | | **7,107 credits/year** |

> `WH_TRANSFORM` is sized for a full DV2.0 pipeline: 8 hubs + 6 links + 7 satellites + 8 biz vault + 10 mart models = 39+ tables per execution. Daily incremental runs take ~60 minutes; weekly full-refreshes (all satellites re-derived + PIT/Bridge rebuilt from scratch) take ~3.5 hours.
>
> `WH_BI` is the joint-largest cost driver (3,460 credits/year). Snowflake result caching (identical query results cached for 24 h) is assumed to halve theoretical credit consumption vs. fully uncached Tableau usage.

### 3.5 Tableau Cloud licensing (50 users)

| Role | Users | Rationale |
|---|---|---|
| **Creator** | 5 | Data engineers and lead analysts who build and publish workbooks |
| **Explorer** | 15 | Traders, compliance officers, head of trading — interact with views, apply filters |
| **Viewer** | 30 | Managers, CLIENT counterparties — read-only dashboard access |

### 3.6 Airflow usage pattern (batch only)

| DAG | Schedule | Est. runtime |
|---|---|---|
| `tca_ingest_batch` | 06:45 CET Mon–Fri | 10 min |
| `tca_raw_vault_morning` | 07:30 CET Mon–Fri | 15 min |
| `tca_biz_vault_eod` | 18:00 CET Mon–Fri | 20 min |
| `tca_marts_eod` | 18:30 CET Mon–Fri | 15 min |
| `tca_mifid_rts27_eod` | 18:45 CET Mon–Fri | 10 min |
| `tca_weekly_reports` | Mon 07:00 CET | 30 min |

Total active DAG time: ~1.2 hours/day. Very light for a managed Airflow environment.

---

## 4. Cost Breakdown by Component

### 4.1 Snowflake Storage

| Tier | Size | Monthly rate | Monthly cost | Annual cost |
|---|---|---|---|---|
| HOT (last 2 years) | ~48 GB = 0.048 TB | $23 / TB | $1.10 | **$13** |
| COOL (years 3–10) | ~1,703 GB = 1.703 TB | $6 / TB | $10.22 | **$123** |
| Lifecycle policy compute | ~48 GB transitioned/year | $0.01 / GB | — | **$48** |
| Lifecycle policy daily serverless | fixed overhead | — | — | **$50** |
| Time Travel + Fail-Safe overhead | included in sizing above | — | — | $0 |
| **Storage subtotal** | | | | **~$234/year** |

> Storage is negligibly cheap at this data volume. Snowflake's cost model strongly favours compute over storage.

### 4.2 Snowflake Compute

| Warehouse | Annual credits | @ $3.00/credit | Annual cost |
|---|---|---|---|
| `WH_INGEST` (XS) | 63 | $3.00 | $189 |
| `WH_TRANSFORM` (L) — daily + weekly | 3,456 | $3.00 | $10,368 |
| `WH_BI` (M) — Tableau 50 users | 3,460 | $3.00 | $10,380 |
| `WH_BACKFILL` (XL) — on-demand | 128 | $3.00 | $384 |
| **Compute subtotal** | **7,107 credits** | | **~$21,321/year** |

### 4.3 Snowflake Total

| Item | Annual cost |
|---|---|
| Storage (~1.75 TB) | $234 |
| Compute (all warehouses) | $21,321 |
| Enterprise support (10% of contract, included in capacity deal) | $2,156 |
| **Snowflake total** | **~$23,711/year** |

### 4.4 Managed Airflow

Two viable managed options for this workload:

| Option | Description | Annual cost |
|---|---|---|
| **AWS MWAA** (Small environment) | $0.49/environment-hour × 8,760 h + mw1.small workers × active hours | ~$4,800 |
| **Astronomer Cloud** (Entry tier) | Managed K8s Airflow, CI/CD, SSO, log retention | ~$7,200 |

> **Recommendation**: AWS MWAA for cost efficiency given the light batch-only workload. Astronomer is justified if more than one Airflow environment (dev/staging/prod) is needed.

| MWAA line item | Cost |
|---|---|
| Environment fee (Small, 24×7) | $4,292/year |
| Worker instances (mw1.small, 2 workers × ~500 h active/year) | $82/year |
| CloudWatch Logs | $120/year |
| **Airflow total** | **~$4,494 ≈ $4,500/year** |

### 4.5 Tableau Cloud

| Licence tier | Users | Monthly rate | Annual per user | Annual total |
|---|---|---|---|---|
| Creator | 5 | $75 | $900 | $4,500 |
| Explorer | 15 | $42 | $504 | $7,560 |
| Viewer | 30 | $15 | $180 | $5,400 |
| **Tableau total** | **50** | | | **$17,460/year** |

> Tableau list price. Enterprise contracts typically negotiate 20–30% discount → potential reduction to ~$12,200–$14,000/year. The estimate uses list price conservatively.

### 4.6 dbt Cloud — Team Plan

| Item | Monthly | Annual |
|---|---|---|
| dbt Cloud Team (up to 8 developers, unlimited jobs) | $100 | $1,200 |
| Buffer for overage / additional seat | — | $300 |
| **dbt Cloud total** | | **$1,500/year** |

> Alternative: run `dbt` CLI from Airflow tasks (zero additional cost). dbt Cloud adds managed scheduling, Slim CI, hosted docs, and alerts — recommended for a production TCA platform.

### 4.7 S3 Object Storage

| Purpose | Volume | Rate | Annual cost |
|---|---|---|---|
| Staging files for dlt/COPY INTO | ~27 GB/year (rolling 7-day retention) | $0.023/GB | $1 |
| MiFID II immutable archive (raw JSON, 7-year S3 Glacier) | ~27 GB/year × 7 years = 189 GB | $0.004/GB | $9 |
| S3 request costs + egress | — | — | $90 |
| **S3 total** | | | **~$100/year** |

---

## 5. Annual Cost Summary

### Year 1

| Component | Annual Cost (USD) | % of total |
|---|---|---|
| Snowflake storage (HOT + COOL + lifecycle) | $234 | 0.4% |
| Snowflake compute (all warehouses) | $21,321 | 38.9% |
| Snowflake enterprise support | $2,156 | 3.9% |
| **Snowflake subtotal** | **$23,711** | **43.3%** |
| Managed Airflow (AWS MWAA) | $4,500 | 8.2% |
| Tableau Cloud (50 users, list price) | $17,460 | 31.9% |
| dbt Cloud (Team) | $1,500 | 2.7% |
| S3 object storage | $100 | 0.2% |
| Contingency (5%) | $2,364 | 4.3% |
| **GRAND TOTAL — Year 1** | **~$49,635** | **~$49,600/year** |

> With Tableau enterprise discount (25%): **~$45,300/year**.

### Year-on-Year Cost Trajectory

| Year | New records/year | Added storage (DV2.0 layers) | Storage cost delta | Compute delta | Approx. total |
|---|---|---|---|---|---|
| Year 1 (baseline) | 27M | — | — | — | **$49,600** |
| Year 2 | 27M | +25 GB compressed | +$2 | +$75 | **$49,700** |
| Year 3 | 27M | +25 GB | +$2 | +$75 | **$49,800** |
| Year 5 | 27M | +25 GB | +$2 | +$75 | **$50,000** |
| Year 10 | 27M | +25 GB | +$2 | +$75 | **$50,400** |

> Data growth cost is essentially flat. The 27M records/year = ~25 GB additional compressed storage across all DV2.0 layers = **$2/year additional storage, ~$75/year additional compute** from marginally longer dbt runs as data volume grows.

---

## 6. Storage Tier Design

```
Trade date → 0 days
        │
        ▼  (2-year boundary)
   ┌─────────────────────────────────┐
   │  HOT TIER  (Standard Storage)  │
   │  Last 24 months of data        │
   │  ~48 GB compressed             │
   │  $23/TB/month                  │
   │  Instant query latency         │
   │  90-day Time Travel            │
   └─────────────────────────────────┘
        │  Lifecycle policy moves data after 730 days
        ▼
   ┌─────────────────────────────────┐
   │  COOL TIER  (Archived Storage) │
   │  Years 3–10 (8 years)          │
   │  ~1,703 GB compressed          │
   │  $6/TB/month (4× cheaper)      │
   │  Instant retrieval             │
   │  90-day minimum retention      │
   └─────────────────────────────────┘
```

**Snowflake lifecycle policy (applied to all satellite and fact tables):**

```sql
CREATE STORAGE LIFECYCLE POLICY tca_cool_policy
  AS (trade_date DATE)
  RETURNS BOOLEAN ->
    trade_date < DATEADD(YEAR, -2, CURRENT_DATE())
  ARCHIVE_TIER = COOL
  ARCHIVE_FOR_DAYS = 2920;  -- 8 years before expiry

ALTER TABLE mart_trading_risk.fact_order_execution
  ADD STORAGE LIFECYCLE POLICY tca_cool_policy ON (trade_date);
```

---

## 7. Virtual Warehouse Configuration

```sql
-- Ingestion warehouse (COPY INTO from S3/dlt)
CREATE WAREHOUSE WH_INGEST
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;

-- dbt transformation warehouse
CREATE WAREHOUSE WH_TRANSFORM
  WAREHOUSE_SIZE = 'LARGE'
  AUTO_SUSPEND = 300
  AUTO_RESUME = TRUE;

-- Tableau BI warehouse (live connection, 50 users)
CREATE WAREHOUSE WH_BI
  WAREHOUSE_SIZE = 'MEDIUM'
  AUTO_SUSPEND = 120
  AUTO_RESUME = TRUE
  MAX_CLUSTER_COUNT = 2;  -- auto-scale if all 50 users hit simultaneously

-- On-demand backfill warehouse
CREATE WAREHOUSE WH_BACKFILL
  WAREHOUSE_SIZE = 'XLARGE'
  AUTO_SUSPEND = 600
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;
```

---

## 8. Cost Optimisation Levers

| Lever | Potential saving | Effort |
|---|---|---|
| Negotiate Tableau enterprise contract (25% off list) | ~$4,365/year | Low — annual renewal |
| Snowflake Capacity contract (vs. On-Demand) | Already assumed; On-Demand would be ~$7K more | Pre-signed |
| Reduce WH_BI to XS during off-market hours (18:00–08:00) | ~$1,500/year | Low — Airflow task to resize |
| Switch 30 Tableau Viewers to Tableau Embedded Analytics via Snowflake Streamlit | Eliminate 30 Viewer licences ($5,400) | Medium — rebuild those views |
| Replace dbt Cloud with Airflow-native dbt CLI tasks | $1,500/year | Low — remove dbt Cloud |
| Move MiFID archive directly to S3 Glacier instead of Snowflake COOL | $50/year (minimal; already using COOL correctly) | Low |
| Aggressively tune WH_TRANSFORM auto-suspend from 5 min → 2 min | ~$500/year | Low — config change |
| **Total realisable savings (realistic scenario)** | **~$8,000–11,000/year** | |

**Optimised scenario: ~$38,000–42,000/year** after Tableau discount + dbt CLI + BI warehouse schedule.

---

## 9. Risks and Caveats

| Risk | Impact | Mitigation |
|---|---|---|
| **Tableau list price used** — enterprise deals vary widely | ±$5,000/year | Negotiate before sign-off; Viewer users may qualify for lower rate |
| **WH_BI credit estimate assumes 40% result cache hit rate** — if users run highly varied queries (different date ranges, filters), cache rate drops and credits rise | +$3,000–5,000/year | Enforce fixed dashboard filters; use Tableau extracts for some views |
| **Single Snowflake account assumed** — production architecture may require 3 accounts (PB-DE, PB-UK, BCM-US) for data residency | 3× compute costs (pro-rated by entity volume) | Confirm regulatory requirement before provisioning multi-account |
| **Snowflake Enterprise minimum commitment** — some regions/resellers require $25K/year minimum | Minimum spend applies | Verify with Snowflake sales before finalising |
| **COOL tier 90-day minimum retention** — data transitioned to COOL cannot be deleted before 90 days without incurring the full charge | Early exit costs | Design lifecycle policy with correct cut-off dates; do not transition data likely to be corrected |
| **sat_price_tick volume** — market data bars are the largest satellite; actual size depends on instrument universe width | +200–500 GB if >200 symbols tracked | Profile instrument count before sizing; consider archiving inactive symbols sooner |
| **27M records/year assumption** — if volume ramps up (new asset classes, additional counterparties) | Every 10× volume increase ≈ +$2,000/year compute | Monitor via Snowflake resource monitor; alert at 120% of budget |
| **Prices are 2026 list** — cloud pricing changes | ±10% typically year-on-year | Build 10% contingency into budget (already included) |

---

## 10. Appendix — Snowflake Credit Reference

| Warehouse size | Credits/hour | Approximate capacity |
|---|---|---|
| X-Small | 1 | 1 server |
| Small | 2 | 2 servers |
| Medium | 4 | 4 servers |
| Large | 8 | 8 servers |
| X-Large | 16 | 16 servers |
| 2X-Large | 32 | 32 servers |

At enterprise capacity pricing ($3.00/credit):

| Warehouse | Cost if run 8 h/day × 250 days | Cost if run 1 h/day × 250 days |
|---|---|---|
| XS | $1,500/year | $188/year |
| S | $3,000/year | $375/year |
| M | $6,000/year | $750/year |
| L | $12,000/year | $1,500/year |
| XL | $24,000/year | $3,000/year |

> **Key takeaway**: auto-suspend is essential. A forgotten unsuspended L warehouse costs $12,000/year. A Snowflake resource monitor set at 110% of monthly credit budget will alert and optionally suspend.

---

*Compiled 2026-04-27. Prices are indicative and should be validated against current Snowflake, AWS, Tableau, and Astronomer pricing at time of contract negotiation.*
