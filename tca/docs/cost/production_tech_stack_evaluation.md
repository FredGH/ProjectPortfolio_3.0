# Production Tech Stack Evaluation — PrivateBank TCA Platform

## Context

This evaluation targets a **banking-grade, fully managed production deployment** of the PrivateBank TCA platform as described in the Verbose Component Interaction Story. The platform implements Data Vault 2.0 with an initial load of **2 billion records** that will expand significantly due to the DV2.0 schema multiplier effect (Hubs, Links, Satellites create 8–15× row amplification from source records — projecting 16–30B+ rows in the vault layer at steady state). The deployment must be pan-European, MiFID II / MiFIR compliant, with isolation across legal entities (pb_de, pb_uk, bcm_us).

The PoC uses **PostgreSQL 16 + TimescaleDB** as a single-engine solution covering ingestion landing, Data Vault 2.0, information marts, API auth, and observability. This evaluation first establishes whether PostgreSQL remains the right database engine for production, compares it against Snowflake, MSSQL, and Oracle, then rebuilds the full technology stack recommendation around the winning engine, and finally assesses whether ClickHouse Cloud could play a role.

**PoC component → Production layer mapping:**

| PoC Layer | Production Equivalent |
|---|---|
| PostgreSQL 16 + TimescaleDB (all schemas) | Cloud OLAP warehouse (vault + marts) + managed OLTP DB (auth, obs) |
| dlt pipelines (batch) | Managed ingestion |
| Redis Streams (real-time) | Managed streaming / Kafka |
| dbt Data Vault 2.0 | dbt Cloud |
| Airflow DAGs | Managed orchestration |
| FastAPI + JWT RBAC | API Gateway + managed container runtime (auth, ML predict, pipeline trigger endpoints only — reporting reads move to Tableau direct) |
| Angular 17 SPA | **Tableau Cloud** (fully managed SaaS — replaces the Angular frontend) |
| Observability (obs schema) | Managed monitoring / audit platform |

**Data volume projection:**

| DV2.0 Layer | Amplification | Estimated Rows |
|---|---|---|
| Source (stg_raw) | 1× | 2,000,000,000 |
| Raw Vault — Hubs (8) | ~0.3× (BK dedup) | ~600,000,000 |
| Raw Vault — Links (6) | ~1× | ~2,000,000,000 |
| Raw Vault — Satellites (7, with history) | ~3–5× | ~6B–10B |
| Business Vault (derived sats + PIT) | ~2× | ~4,000,000,000 |
| Information Marts (4 domains) | ~0.5× | ~1B per mart |
| **Total vault + mart storage** | **~8–15×** | **~16B–30B rows** |

---

## Evaluation Criteria

| # | Criterion | Weight | Description |
|---|---|---|---|
| 1 | Managed infrastructure | Must-have | Zero on-prem; provider SLA covers all infra |
| 2 | Security (network + encryption) | Critical | TLS in-transit, AES-256 at-rest, VPC/private endpoints |
| 3 | SSO | Critical | SAML 2.0 / OIDC; integrates with enterprise IdP (Okta, Entra ID) |
| 4 | RBAC (row-level security) | Critical | `counterparty_id` filter at storage layer, not only API |
| 5 | PII security (column-level) | Critical | Dynamic data masking or equivalent column policies |
| 6 | Portability | High | Open formats; vendor lock-in risk |
| 7 | Auditability | Critical | Immutable query logs; MiFID II regulatory audit trail |
| 8 | Observability | High | Pipeline monitoring, data quality, SLA alerting |
| 9 | Estimated cost per year | High | At 2B source / 20B+ vault rows |
| 10 | Reliability | Critical | SLA %, HA, multi-AZ |
| 11 | DR support | Critical | RPO / RTO; cross-region failover |
| 12 | Legal Entity Isolation | Critical | pb_de / pb_uk / bcm_us data boundary enforcement |
| 13 | Schema change resistance | High | DV2.0 satellite schema evolution without maintenance windows |
| 14 | CI/CD ease | High | dbt deploys, IaC, environment promotion |
| 15 | DV2.0 fit | Critical | Columnar storage, append-only patterns, hash_diff, PIT queries |

---

---

# Part I — Database Platform Selection

## Why This Matters

The PoC uses PostgreSQL 16 for everything: the dlt landing zone (`stg_raw`), all 21 DV2.0 raw vault models, 8 business vault models, 4 mart domains, and the operational schemas (`auth`, `obs`). At 400 synthetic orders this works perfectly. At 16–30 billion vault rows with fan-out satellite joins and daily PIT snapshot generation, the engine choice becomes the single most consequential architectural decision.

The evaluation covers the four candidates most relevant to a European banking context:

| Database | Managed Options | Storage Model | Primary Strength |
|---|---|---|---|
| **PostgreSQL 16** | AWS RDS, Aurora, Azure DB for PostgreSQL, Cloud SQL | Row-store + TimescaleDB columnar | Open source, versatile, low cost |
| **Snowflake** | Snowflake (native cloud, AWS / Azure / GCP) | Columnar, proprietary | Analytics, compliance, governance |
| **Microsoft SQL Server** | Azure SQL Managed Instance, RDS SQL Server | Row-store (columnstore index optional) | Enterprise banking, Microsoft ecosystem |
| **Oracle** | Oracle Autonomous Database (ADW/ATP), Exadata Cloud | Row-store + columnar (Exadata HCC) | Maximum enterprise compliance |

---

## PostgreSQL 16 — Assessment

### Fit for the TCA Platform

PostgreSQL is the right engine for the **operational schemas** (`auth`, `obs`) — these are low-volume OLTP workloads (hundreds of thousands of rows, heavy on reads and point lookups) that PostgreSQL handles flawlessly. The issue is whether it can serve the **analytical schemas** (stg_raw, raw_vault, biz_vault, mart_*) at 16–30B rows.

| Criterion | Rating | Notes |
|---|---|---|
| Managed infra | ✅ 9/10 | AWS Aurora PostgreSQL Serverless v2, Azure Database Flexible Server — both fully managed |
| Security | ✅ 8/10 | TDE via managed storage encryption; SSL/TLS enforced; `pgaudit` for query logging |
| SSO | ⚠️ 6/10 | IAM authentication (RDS) or Entra ID (Azure) — works but requires custom JWT bridge for the API layer |
| RBAC (row-level) | ⚠️ 6/10 | `pg_policies` (RLS) work but are table-level predicates with no native role hierarchy; complex to maintain across 21 DV2.0 models |
| PII column masking | ❌ 4/10 | No native Dynamic Data Masking — must use views or application-layer masking; hard to audit and certify |
| Portability | ✅ 10/10 | Standard SQL, open source, no lock-in |
| Auditability | ⚠️ 7/10 | `pgaudit` logs all queries but logs are mutable (can be deleted by privileged user); not suitable for immutable MiFID II evidence packs without additional tooling (CloudWatch Logs / Log Analytics with immutable retention) |
| Observability | ✅ 8/10 | pg_stat_statements, CloudWatch/Azure Monitor; mature ecosystem |
| Cost/year | ✅ 9/10 | Aurora PostgreSQL Serverless v2: £60K–150K/year at this scale |
| Reliability | ✅ 9/10 | Aurora Multi-AZ 99.99% SLA |
| DR support | ✅ 8/10 | Aurora Global Database (cross-region, RPO <1s, RTO <1min); Azure Flexible Server geo-redundant backup |
| Legal Entity Isolation | ⚠️ 6/10 | Separate databases per legal entity work but cross-entity joins (mart_consolidated) require FDW or data movement |
| Schema change resistance | ✅ 9/10 | `ALTER TABLE ADD COLUMN` is near-instant; DV2.0 satellite evolution is clean |
| CI/CD ease | ✅ 9/10 | dbt-postgres mature; Flyway/Liquibase for schema migrations |
| **DV2.0 at 30B rows** | ❌ 3/10 | **Critical gap**: row-store with MVCC is fundamentally wrong for 30B-row satellite history scans; Parallel Query helps but does not close the gap; TimescaleDB columnstore extension helps for tick_bars only |

### Verdict on PostgreSQL

**Retain for operational schemas only (auth, obs).** Replace with a columnar engine for all analytical schemas (stg_raw landing, raw_vault, biz_vault, mart_*). PostgreSQL cannot sustain PIT snapshot generation, satellite history joins, or mart aggregations at 16–30B rows without extreme vertical scaling that becomes more expensive than switching to a purpose-built columnar warehouse.

**Estimated cost to force PostgreSQL into production at scale:** Aurora PostgreSQL r7g.16xlarge (Multi-AZ) ~£250K–400K/year with no guarantee of acceptable query SLAs on complex DV2.0 fan-out joins. Not recommended.

---

## Microsoft SQL Server (Azure SQL Managed Instance) — Assessment

| Criterion | Rating | Notes |
|---|---|---|
| Managed infra | ✅ 9/10 | Azure SQL Managed Instance (Business Critical tier) is fully managed |
| Security | ✅ 9/10 | TDE, Always Encrypted (column-level encryption at rest + transit), Private Link |
| SSO | ✅ 10/10 | Entra ID native; SSPI; AD group membership mapped to SQL Server roles |
| RBAC (row-level) | ✅ 8/10 | Native RLS via security predicates (inline table-valued functions); mature and auditable |
| PII column masking | ✅ 8/10 | Dynamic Data Masking (DDM): `partial()`, `default()`, `email()` mask functions per column; role-based unmask |
| Portability | ⚠️ 5/10 | Proprietary T-SQL; significant re-engineering to migrate off; BACPAC export available |
| Auditability | ✅ 9/10 | SQL Server Audit (server + database audit specs); audit log shipped to Azure Storage (immutable blob) or Log Analytics; column access auditing available |
| Observability | ✅ 8/10 | Azure Monitor, Query Store, DMVs; well-understood in banking ops teams |
| Cost/year | ⚠️ 6/10 | SQL MI Business Critical (16 vCores, 2-node replica): ~£180K–280K/year; additional licensing if not using Entra |
| Reliability | ✅ 9/10 | Business Critical tier: built-in Always On AG, 99.99% SLA |
| DR support | ✅ 9/10 | Auto-failover groups for cross-region DR; RPO <5s, RTO <30s |
| Legal Entity Isolation | ✅ 8/10 | Separate SQL MI instances per legal entity (high cost) or separate databases with cross-database security (complex) |
| Schema change resistance | ⚠️ 6/10 | `ALTER TABLE ADD COLUMN` is online in MI; but column type changes require rebuild; DV2.0 satellite evolution manageable but requires careful DDL scripting |
| CI/CD ease | ✅ 8/10 | dbt-sqlserver adapter mature; DACPAC/Flyway for DDL; Terraform azurerm provider |
| **DV2.0 at 30B rows** | ❌ 4/10 | Row-store by default; columnstore indexes exist but DV2.0's wide satellite joins are not well-served; no Time Travel equivalent for satellite history; PIT queries require full materialisation |

### Verdict on MSSQL

Strong for the operational layer and for banks already deep in the Microsoft ecosystem (Entra ID, Azure). The DDM and audit capabilities are genuinely enterprise-grade. **Not suitable as the primary analytical warehouse** for DV2.0 at 30B rows without Azure Synapse Analytics as the companion OLAP engine — which was ranked last in the overall stack evaluation. Retain as a candidate for the `auth` + `obs` operational layer if Azure is the chosen cloud.

---

## Oracle Autonomous Database (ADW) — Assessment

| Criterion | Rating | Notes |
|---|---|---|
| Managed infra | ✅ 10/10 | Oracle Autonomous Data Warehouse (ADW) on Exadata Cloud Infrastructure — fully managed, self-tuning |
| Security | ✅ 10/10 | Oracle Database Vault (privileged user control), TDE, network encryption, private endpoints |
| SSO | ✅ 9/10 | SAML 2.0 / OIDC via Oracle Identity Cloud; Okta/Entra ID federation supported |
| RBAC (row-level) | ✅ 10/10 | Virtual Private Database (VPD): row-level policy functions applied transparently to every query on a table; industry gold standard |
| PII column masking | ✅ 10/10 | Oracle Data Masking and Subsetting; Redaction Policies (DBMS_REDACT): full/partial/random masking per column per context; the most mature implementation in the market |
| Portability | ❌ 2/10 | Deepest lock-in of any option; PL/SQL, proprietary optimizer hints, Oracle-specific types; migration cost is extreme |
| Auditability | ✅ 10/10 | Unified Auditing: immutable audit records written to a protected tablespace; AUDIT_TRAIL captures object access, column access, DML, privilege use; longest track record in banking compliance |
| Observability | ✅ 9/10 | Oracle Cloud Infrastructure Monitoring; SQL Monitor; AWR/ADDM (self-tuning) |
| Cost/year | ❌ 2/10 | ADW — ECPU-based: **£400K–900K/year** at this scale (compute + storage + support); Oracle licensing is the most expensive in the market; multi-AZ and DR add significant cost |
| Reliability | ✅ 10/10 | Exadata 99.995% SLA; Autonomous operation means self-healing |
| DR support | ✅ 10/10 | Active Data Guard: synchronous standby, RPO 0, RTO <30s; Autonomous Disaster Recovery |
| Legal Entity Isolation | ✅ 9/10 | Oracle Multitenant (Pluggable Databases): each legal entity is a PDB with full isolation within one CDB |
| Schema change resistance | ✅ 9/10 | Online DDL for column additions; ADW auto-indexes; DV2.0 satellites add columns cleanly |
| CI/CD ease | ⚠️ 5/10 | dbt-oracle adapter exists but is community-maintained; Terraform OCI provider; Liquibase for Oracle DDL; significantly more complex than dbt-snowflake |
| **DV2.0 at 30B rows** | ✅ 8/10 | Exadata Smart Scan columnar offload; Hybrid Columnar Compression (HCC); handles large analytical queries better than vanilla row-store; but not as elegantly as native columnar engines |

### Verdict on Oracle

Technically the most capable for banking compliance and regulatory requirements. Oracle's VPD and DBMS_REDACT are 20 years ahead of any competitor in maturity. The Unified Auditing trail is accepted by every financial regulator globally. However, **the cost and lock-in make it non-viable for a greenfield cloud-native deployment** unless the bank already has an Oracle enterprise agreement. The CI/CD story (dbt-oracle community adapter) is the weakest of all options. Recommended only if the bank is an existing Oracle house with an EA that absorbs the licensing cost.

---

## Snowflake — Assessment

| Criterion | Rating | Notes |
|---|---|---|
| Managed infra | ✅ 10/10 | 100% serverless; virtual warehouses auto-suspend; zero capacity planning for compute |
| Security | ✅ 10/10 | Tri-Secret Secure (customer + Snowflake + cloud KMS); Private Link; Business Critical edition adds HIPAA/PCI |
| SSO | ✅ 10/10 | Native SAML 2.0 / OIDC; Okta, Entra ID, OneLogin with SCIM group provisioning; MFA enforced at account level |
| RBAC (row-level) | ✅ 10/10 | Row Access Policies: `USING (counterparty_id = CURRENT_CONTEXT_COUNTERPARTY_ID())` — declarative, version-controllable, auditable |
| PII column masking | ✅ 10/10 | Dynamic Data Masking (DDM): expressions like `CASE WHEN IS_ROLE_IN_SESSION('COMPLIANCE') THEN val ELSE SHA2(val) END` applied per column per role; Tokenization policies for PCI data |
| Portability | ⚠️ 5/10 | Proprietary columnar storage (cannot read files externally); data export to Parquet/CSV possible; Iceberg integration (External Tables) provides partial relief |
| Auditability | ✅ 10/10 | ACCESS_HISTORY view: immutable record of every query, every object accessed, every column read — the closest thing to Oracle Unified Auditing in the cloud; ideal for MiFID II column access proofs |
| Observability | ✅ 9/10 | QUERY_HISTORY, WAREHOUSE_METERING, STORAGE_USAGE system tables; Monte Carlo / Soda / dbt Cloud for data quality |
| Cost/year | ⚠️ 6/10 | Snowflake Enterprise on Azure/AWS: **£520K–£850K/year** at 30B vault rows (DV2.0 write-heavy incremental runs consume substantial credits) |
| Reliability | ✅ 10/10 | 99.99% SLA; Snowflake's multi-cluster architecture means zero contention between pipelines and dashboard reads |
| DR support | ✅ 10/10 | Replication Groups with automated cross-region failover; RPO ~1 minute; RTO <5 minutes with Snowflake Failover |
| Legal Entity Isolation | ✅ 10/10 | Snowflake Databases map 1:1 to legal entities (DB_PB_DE, DB_PB_UK, DB_BCM_US); Snowflake Data Sharing allows cross-entity `mart_consolidated` without data movement |
| Schema change resistance | ✅ 9/10 | Zero-copy clone for testing; Time Travel (90 days, Enterprise) for satellite point-in-time reconstruction; `ALTER TABLE ADD COLUMN` is instant |
| CI/CD ease | ✅ 10/10 | dbt-snowflake adapter is the most mature dbt adapter; SchemaChange for DDL migrations; Terraform snowflake provider; native integration with GitHub Actions / GitLab CI |
| **DV2.0 at 30B rows** | ✅ 10/10 | Native columnar; micro-partition pruning on `_loaded_at` watermarks; multi-cluster for parallel vault + mart builds; Time Travel for PIT backdated queries; Fail-safe for 7-day recovery beyond Time Travel |

### Verdict on Snowflake

**The strongest fit** for the TCA platform's analytical schemas at 2B+ source / 30B+ vault rows. Every compliance requirement maps to a native Snowflake feature with minimal custom engineering: DDM for PII, Row Access Policies for `counterparty_id` RBAC, ACCESS_HISTORY for MiFID II audit, Snowflake Databases for legal entity isolation, and Fail-safe + Replication Groups for DR. The dbt-snowflake adapter is production-grade and used by tier-1 financial institutions globally. The primary disadvantage is cost at DV2.0 scale.

---

## Database Comparison Summary Table

| Criterion | PostgreSQL | MSSQL (SQL MI) | Oracle ADW | **Snowflake** |
|---|---|---|---|---|
| Managed infra | ✅ 9 | ✅ 9 | ✅ 10 | ✅ **10** |
| Security | ✅ 8 | ✅ 9 | ✅ 10 | ✅ **10** |
| SSO | ⚠️ 6 | ✅ 10 | ✅ 9 | ✅ **10** |
| RBAC (row-level) | ⚠️ 6 | ✅ 8 | ✅ 10 | ✅ **10** |
| PII column masking | ❌ 4 | ✅ 8 | ✅ 10 | ✅ **10** |
| Portability | ✅ 10 | ⚠️ 5 | ❌ 2 | ⚠️ 5 |
| Auditability | ⚠️ 7 | ✅ 9 | ✅ 10 | ✅ **10** |
| Observability | ✅ 8 | ✅ 8 | ✅ 9 | ✅ 9 |
| Cost/year | ✅ **9** | ⚠️ 6 | ❌ 2 | ⚠️ 6 |
| Reliability | ✅ 9 | ✅ 9 | ✅ 10 | ✅ **10** |
| DR support | ✅ 8 | ✅ 9 | ✅ 10 | ✅ **10** |
| Legal entity isolation | ⚠️ 6 | ✅ 8 | ✅ 9 | ✅ **10** |
| Schema change resistance | ✅ 9 | ⚠️ 6 | ✅ 9 | ✅ 9 |
| CI/CD ease | ✅ 9 | ✅ 8 | ⚠️ 5 | ✅ **10** |
| DV2.0 at 30B rows | ❌ 3 | ❌ 4 | ✅ 8 | ✅ **10** |
| **Total (/150)** | **111** | **116** | **123** | **139** |
| **Recommended use** | Auth + Obs only | Auth + Obs (Azure) | Existing Oracle EA only | **Primary analytical DB** |

---

## Database Recommendation

> **Snowflake as the primary analytical database** (raw_vault, biz_vault, mart_*, stg_raw landing) + **managed PostgreSQL** (AWS Aurora or Azure Database for PostgreSQL Flexible Server) for operational schemas (`auth`, `obs`).

This hybrid follows a clean split that mirrors the existing schema separation in the PoC:

| Schema | Engine | Rationale |
|---|---|---|
| `stg_raw` (landing zone) | Snowflake — Transient Tables | Zero storage cost; auto-expiry; dlt Snowflake connector is native |
| `raw_vault` (Hubs, Links, Sats) | Snowflake — permanent tables | Columnar; micro-partition pruning on `_loaded_at`; Time Travel for satellite backdating |
| `biz_vault` (derived sats, PIT) | Snowflake — permanent tables | PIT queries use Time Travel; dynamic data masking on sensitive columns |
| `mart_*` (4 domains) | Snowflake — permanent tables | Row Access Policies enforce `counterparty_id` at storage; multi-cluster for concurrent dashboards |
| `auth` (api_clients, refresh_tokens) | PostgreSQL (managed) | OLTP point lookups; bcrypt verify is CPU-bound not I/O-bound; Snowflake latency too high for auth hot path |
| `obs` (quarantine_queue, obs_warnings) | PostgreSQL (managed) | Low volume; OLTP insert/select; anomaly detector writes infrequently |

Oracle is ruled out due to cost and lock-in. MSSQL is ruled out due to DV2.0 analytical performance. PostgreSQL is retained for operational schemas but replaced by Snowflake for the analytical layer.

---

---

# Part II — Full Stack Analysis: Snowflake-Centric vs Databricks+Delta Lake

With Snowflake chosen as the database, the full production stack is assembled and then directly compared against the strongest alternative (Databricks + Delta Lake + Unity Catalog on Azure, which ranked first in the original cloud-provider comparison).

---

## Stack A — Snowflake-Centric (Recommended)

### Architecture

```
External Sources (OMS, MD, REF, FI, Eurex)
    │
    ├── Batch: dlt 1.5.0 → Snowflake Transient Tables (stg_raw)
    │          Orchestrated by: Astronomer Cloud (Airflow)
    │
    ├── Real-time: Confluent Cloud (Kafka) → Snowflake Kafka Connector → stg_raw.rt_fills
    │             (replaces Redis Streams → PostgreSQL consumer)
    │
    └── dbt Cloud → Snowflake
           stg_raw (views) → raw_vault (Hubs/Links/Sats, incremental)
                           → biz_vault (derived sats, PIT, incremental)
                           → mart_trading_risk / mart_market_data / mart_corporate / mart_consolidated (table)

Snowflake security layer:
    Row Access Policies (counterparty_id per role)
    Dynamic Data Masking (sat_client_profile PII columns)
    ACCESS_HISTORY (MiFID II audit)
    Snowflake Databases (legal entity isolation: DB_PB_DE, DB_PB_UK, DB_BCM_US)
    Data Sharing (cross-entity mart_consolidated — zero copy)

Operational layer (auth, obs):
    Aurora PostgreSQL Serverless v2 (AWS) or Azure DB Flexible Server
    FastAPI (ECS Fargate / AKS) — auth hot path → PostgreSQL
    FastAPI — TCA read path → Snowflake (Snowpark Python connector)

Authentication / SSO:
    Okta (SAML 2.0) → Snowflake SSO + FastAPI JWT (RS256)

Orchestration:
    Astronomer Cloud (managed Airflow 2.9) — preserves all existing DAG code

Observability:
    dbt Cloud Observability (pipeline runs, test failures)
    Snowflake QUERY_HISTORY + WAREHOUSE_METERING
    Monte Carlo (data quality SLAs on vault and mart tables)
    DataDog (infrastructure, FastAPI API latency, JWT error rates)

DR:
    Snowflake Replication Group → secondary region (EU-West failover)
    Aurora Global Database (cross-region, <1s RPO)
    Astronomer Cloud: HA by design

API / reporting:
    AWS API Gateway → ECS Fargate (FastAPI) — retained for: auth/token, /pipeline/run, /predict/slippage, /regime/*
    FastAPI reporting endpoints (/tca/summary, /tca/order/{id}, /orders, etc.) still exist but Tableau bypasses them via direct Snowflake Live Connection
    Tableau Cloud (SaaS) → Snowflake OAuth per-user → Row Access Policies enforce counterparty_id natively
```

### Component Map

| PoC Component | Production Component | Provider |
|---|---|---|
| PostgreSQL (vault + marts) | **Snowflake Enterprise** | Snowflake |
| PostgreSQL (auth + obs) | Aurora PostgreSQL Serverless v2 | AWS |
| dlt batch pipelines | dlt 1.5.0 → Snowflake (native connector) | dlthub |
| Redis Streams + consumer | **Confluent Cloud** (Kafka) + Snowflake Kafka Connector | Confluent / Snowflake |
| dbt + Airflow | **dbt Cloud Enterprise** + **Astronomer Cloud** | dbt Labs / Astronomer |
| FastAPI (RS256 JWT) | **ECS Fargate** + **AWS API Gateway** (auth, ML, pipeline endpoints only) | AWS |
| Angular 17 SPA (replaced) | **Tableau Cloud** (fully managed SaaS — direct Snowflake OAuth Live Connection) | Tableau |
| SSO / Auth | **Okta** (SAML → Snowflake SSO + Tableau Cloud SSO + FastAPI JWT exchange) | Okta |
| Observability | **Monte Carlo** + DataDog + Snowflake system tables | Monte Carlo / DataDog |
| Audit | **Snowflake ACCESS_HISTORY** (immutable, 365-day retention) | Snowflake |
| DR | **Snowflake Replication Groups** + Aurora Global | Snowflake / AWS |

### Criteria Assessment

| Criterion | Rating | Implementation |
|---|---|---|
| Managed infra | ✅ 10/10 | Every component is fully managed; zero server provisioning |
| Security | ✅ 10/10 | Snowflake Tri-Secret Secure; Aurora encryption; Confluent encryption; VPC/PrivateLink everywhere |
| SSO | ✅ 10/10 | Okta SAML 2.0 → Snowflake SSO; Okta OIDC → FastAPI JWT token exchange; SCIM group sync |
| RBAC (row-level) | ✅ 10/10 | Snowflake Row Access Policies on mart_* tables; `counterparty_id = CURRENT_COUNTERPARTY()` context function |
| PII column masking | ✅ 10/10 | DDM policies on `sat_client_profile` (name, address, account_number); role-aware: COMPLIANCE+ sees plaintext, TRADER sees SHA2 hash, CLIENT sees null |
| Portability | ⚠️ 5/10 | Snowflake proprietary storage; Iceberg External Tables partially mitigates; operational PostgreSQL is portable |
| Auditability | ✅ 10/10 | ACCESS_HISTORY: every column read by every user logged with query text; shipped to S3 for 7-year immutable MiFID II retention via Snowflake data share → S3 export |
| Observability | ✅ 10/10 | Monte Carlo: DQ SLAs on `hub_order` freshness, satellite row count anomalies; DataDog: API p99, error rates; dbt Cloud: test pass/fail history |
| Cost/year | ⚠️ 6/10 | See breakdown below — £520K–£850K; higher than Databricks |
| Reliability | ✅ 10/10 | Snowflake 99.99% SLA; Confluent 99.99%; Astronomer Cloud HA; Aurora Multi-AZ |
| DR support | ✅ 10/10 | Snowflake automated failover (RPO 1min, RTO <5min); Aurora Global DB (<1s RPO); Confluent cross-region replication |
| Legal Entity Isolation | ✅ 10/10 | Separate Snowflake databases per legal entity; cross-entity Data Share for mart_consolidated (read-only, no data copy) |
| Schema change resistance | ✅ 9/10 | Satellite column additions: instant ALTER TABLE; zero-copy clone for pre-production testing; Time Travel for rollback |
| CI/CD ease | ✅ 10/10 | dbt Cloud + GitHub Actions: branch deploys → dev, PR → CI run, merge → prod; SchemaChange for DDL; Terraform for Snowflake resources |
| DV2.0 at 30B rows | ✅ 10/10 | Micro-partition pruning on `_loaded_at`; multi-cluster for parallel incremental runs; Time Travel for PIT reconstruction |
| **Total** | **144/150** | |

### Cost Breakdown — Stack A (Snowflake-Centric)

| Component | Annual Cost (GBP) |
|---|---|
| Snowflake Enterprise on AWS (multi-cluster, ~3,000 credits/month) | £290,000–£480,000 |
| Snowflake Storage (30TB compressed vault + marts) | £28,000–£40,000 |
| Aurora PostgreSQL Serverless v2 (auth + obs, ~4 ACU avg) | £12,000–£18,000 |
| Confluent Cloud (Kafka, 10 CKUs, multi-zone) | £38,000–£55,000 |
| Astronomer Cloud (managed Airflow, Business tier) | £28,000–£38,000 |
| dbt Cloud Enterprise (≤10 seats) | £28,000–£35,000 |
| Okta Workforce Identity (SSO + MFA + SCIM, 500 users) | £18,000–£25,000 |
| Monte Carlo (data observability, vault + mart coverage) | £32,000–£45,000 |
| DataDog (infrastructure + APM, 10 hosts) | £18,000–£25,000 |
| AWS ECS Fargate (FastAPI, 2× tasks — auth + ML endpoints only, smaller footprint) | £8,000–£12,000 |
| AWS API Gateway (API only — no SPA hosting) | £4,000–£6,000 |
| **Tableau Cloud Enterprise** (see license breakdown below) | £80,000–£175,000 |
| Networking (PrivateLink, Transit Gateway, egress EU) | £18,000–£28,000 |
| **Total** | **£596,000–£952,000** |

**Tableau Cloud license breakdown (user count drives cost):**

| Role | Tableau License | Qty (typical trading desk) | Unit cost/yr | Subtotal |
|---|---|---|---|---|
| Data / IT team | Creator | 5 | £680 | £3,400 |
| Traders, HOT, Compliance | Explorer | 40 | £475 | £19,000 |
| Clients, junior viewers | Viewer | 200 | £145 | £29,000 |
| **Tableau Cloud subtotal** | | **245 users** | | **~£51,400** |
| Enterprise site license (500+ users, negotiated) | — | — | — | £120,000–£175,000 |

> Tableau adds £80K–£175K/year on top of the Snowflake-centric stack, depending on whether per-user or enterprise site licensing is negotiated. This replaces £8K–£12K of Angular CDN hosting, making the net Tableau premium **£70K–£165K/year**. However, Tableau also removes the need for several FastAPI TCA read endpoints, reducing Fargate compute and simplifying the API surface. Snowflake credit consumption also decreases slightly because Tableau Extracts (scheduled, not live) reduce real-time warehouse queries — but Live Connection (recommended for MiFID II data currency) maintains live warehouse load.

---

## Stack B — Databricks + Azure (Alternative, for direct comparison)

This was ranked first in the original cloud-provider comparison due to superior portability and cost. It uses Delta Lake instead of Snowflake as the analytical engine.

### Key Differences vs Stack A

| Dimension | Stack A (Snowflake) | Stack B (Databricks + Delta Lake) |
|---|---|---|
| **Analytical engine** | Snowflake (proprietary columnar) | Delta Lake on ADLS Gen2 (open Parquet + Delta) |
| **Governance** | Snowflake DDM + Row Access Policies | Unity Catalog Row Filters + Column Masks |
| **Audit** | ACCESS_HISTORY (column-level, immutable) | Unity Catalog system tables + Azure Purview |
| **DV2.0 transform** | dbt Cloud (dbt-snowflake adapter) | dbt Cloud (dbt-spark adapter) |
| **Real-time** | Confluent Cloud → Snowflake Kafka Connector | Azure Event Hubs → Databricks Structured Streaming |
| **Orchestration** | Astronomer Cloud (Airflow) | Databricks Workflows or Astronomer Cloud |
| **Auth / Obs DB** | Aurora PostgreSQL | Azure Database for PostgreSQL Flexible Server |
| **SSO** | Okta | Azure Entra ID (native) |
| **Frontend / reporting** | Tableau Cloud (Okta SAML → Tableau → Snowflake OAuth) | Tableau Cloud (Entra ID SAML → Tableau → Databricks OAuth) |
| **Storage format** | Proprietary (Snowflake) | Open (Parquet + Delta — any engine can read) |
| **Cost/year** | £596K–£952K | £440K–£680K |
| **Operational expertise** | SQL + dbt | Spark / PySpark + dbt |

### Head-to-Head: Snowflake vs Databricks+Delta Lake

| Criterion | Snowflake (Stack A) | Databricks+Delta Lake (Stack B) | Winner |
|---|---|---|---|
| Managed infra | ✅ 10 | ✅ 10 | Draw |
| Security | ✅ 10 | ✅ 10 | Draw |
| SSO | ✅ 10 | ✅ 10 | Draw |
| RBAC (row-level) | ✅ 10 (Row Access Policies) | ✅ 10 (Unity Catalog Row Filters) | Draw |
| PII column masking | ✅ 10 (DDM — declarative) | ✅ 9 (Unity Catalog Masks — declarative) | **Snowflake** (DDM more auditable) |
| Portability | ⚠️ 5 (proprietary) | ✅ 9 (open Delta/Parquet) | **Databricks** |
| Auditability | ✅ 10 (ACCESS_HISTORY, column-level) | ✅ 9 (system tables, Purview) | **Snowflake** (column-level read log) |
| Observability | ✅ 9 | ✅ 9 | Draw |
| Cost/year | ⚠️ £532K–£819K | ✅ £358K–£500K | **Databricks** (30–40% cheaper) |
| Reliability | ✅ 10 | ✅ 9 | **Snowflake** (higher SLA) |
| DR support | ✅ 10 (automated failover) | ✅ 9 (ADLS GRS + metastore replication) | **Snowflake** (cleaner failover) |
| Legal entity isolation | ✅ 10 (Snowflake DBs + Data Sharing) | ✅ 10 (Unity Catalog catalogs) | Draw |
| Schema change resistance | ✅ 9 | ✅ 10 (Delta schema evolution) | **Databricks** (Delta handles column add/rename mid-stream) |
| CI/CD ease | ✅ 10 (dbt-snowflake mature) | ✅ 9 (dbt-spark + Asset Bundles) | **Snowflake** (less config) |
| DV2.0 at 30B rows | ✅ 10 | ✅ 10 | Draw |
| **Team expertise required** | SQL + dbt | Spark/PySpark + dbt | **Snowflake** (lower barrier) |
| **Compliance doc available** | ✅ Very mature | ✅ Mature | **Snowflake** (more banking refs) |

### When to Choose Snowflake over Databricks

Choose Snowflake (Stack A) when:
- **Regulatory audit requirements are paramount**: ACCESS_HISTORY's column-level read logging satisfies regulators (FCA, BaFin, ESMA) without custom tooling; Databricks system tables require additional assembly
- **Team is SQL-first**: The existing TCA platform is 100% SQL + dbt; Databricks Spark jobs require PySpark knowledge for complex streaming and custom analytics (the `engine.py`, `anomaly_detector.py` etc. would need Spark wrappers)
- **Data Sharing across legal entities**: Snowflake Data Sharing for `mart_consolidated` is zero-copy and zero-compute on the sender side; Delta Lake cross-catalog sharing requires Unity Catalog Federation (less mature)
- **Time-to-compliance is a constraint**: Snowflake's compliance package (Business Critical edition) is pre-certified for GDPR, MiFID II, DORA; Databricks requires more configuration to achieve equivalent certification evidence

Choose Databricks (Stack B) when:
- **Cost is the primary driver** and the team has Spark expertise
- **Open format is a regulatory or contractual requirement** (some banks mandate open storage formats to avoid lock-in)
- **ML workloads are significant**: The TCA platform already has two ML models (`execution_quality_predictor`, `regime_detector`) — in production these would benefit from Databricks MLflow and Databricks Model Serving (more mature than Snowflake ML)
- **Azure is the mandated cloud**: Entra ID SSO is native; no Okta cost; Azure EA pricing applies to Databricks

---

---

# Part III — ClickHouse Cloud: Fully Managed Solution Assessment

## What is ClickHouse Cloud?

ClickHouse Cloud is the fully managed SaaS offering of ClickHouse, the open-source column-oriented OLAP database developed at Yandex and now maintained by ClickHouse Inc. It is available on AWS, Azure (preview), and GCP. The engine is purpose-built for high-throughput analytical queries on append-heavy workloads — which makes it superficially attractive for DV2.0's append-only vault patterns.

**Core engine characteristics relevant to TCA/DV2.0:**

| ClickHouse Engine Feature | DV2.0 Mapping | Suitability |
|---|---|---|
| `MergeTree` | Hub tables (append-only, dedup by BK) | ✅ Excellent |
| `ReplacingMergeTree` | Satellite hash_diff deduplication | ⚠️ Works but eventual consistency |
| `CollapsingMergeTree` | Satellite change tracking (sign column) | ⚠️ Complex, non-standard DV2.0 |
| `AggregatingMergeTree` | Pre-aggregated business vault metrics | ✅ Excellent |
| Materialized Views | Derived metrics (bv_tca_costs, bv_alpha_decay) | ✅ Excellent |
| Kafka table engine | Real-time fill ingestion (pb:fills) | ✅ Excellent |
| `FINAL` keyword | Consistent reads from ReplacingMergeTree | ⚠️ Performance penalty on large tables |

---

## Scenario 1 — ClickHouse Cloud as a Complete Replacement

This scenario replaces the entire Snowflake analytical layer (stg_raw, raw_vault, biz_vault, mart_*) with ClickHouse Cloud.

### Assessment

| Criterion | Rating | Notes |
|---|---|---|
| Managed infra | ✅ 9/10 | ClickHouse Cloud is fully managed; auto-scaling compute |
| Security | ⚠️ 6/10 | TLS + AES-256 at-rest; IP allowlisting; Private Link (AWS only, Azure in preview); adequate but less mature than Snowflake |
| SSO | ⚠️ 6/10 | SAML 2.0 supported in ClickHouse Cloud Enterprise; Okta/Entra ID integration documented but thin; SCIM not yet available |
| RBAC (row-level) | ⚠️ 5/10 | **Critical gap**: ClickHouse row policies exist (`CREATE ROW POLICY`) but are attached to tables not roles; no equivalent to Snowflake's context-function-based Row Access Policies; `counterparty_id` enforcement must be partially delegated to application layer |
| PII column masking | ❌ 3/10 | **No native Dynamic Data Masking**: ClickHouse has no DDM equivalent; column masking must be implemented via views or application-layer middleware; this means masking is not auditable at the DB layer — a significant compliance gap |
| Portability | ✅ 9/10 | Open source; Parquet export; ClickHouse format is open; can migrate off at low cost |
| Auditability | ⚠️ 5/10 | `system.query_log` captures all queries but **is mutable** (can be rotated/deleted by privileged users); no column-level access log; insufficient for MiFID II immutable audit requirements without shipping logs to immutable external storage with additional tooling |
| Observability | ✅ 8/10 | `system.query_log`, `system.part_log`, DataDog integration; Grafana dashboards available |
| Cost/year | ✅ 9/10 | **£100K–£220K/year** at 30B row analytical workload — 3–5× cheaper than Snowflake |
| Reliability | ✅ 8/10 | ClickHouse Cloud 99.9% SLA; HA replicas within a region; cross-region DR not as mature as Snowflake |
| DR support | ⚠️ 6/10 | ClickHouse Cloud has within-region HA; cross-region DR requires manual configuration or ClickHouse replication setup; no automated failover equivalent to Snowflake Replication Groups |
| Legal entity isolation | ⚠️ 6/10 | Separate ClickHouse Cloud services per legal entity (separate clusters); cross-cluster querying requires materialised data copies; no Data Sharing equivalent |
| Schema change resistance | ✅ 9/10 | `ALTER TABLE ADD COLUMN` is lightweight in ClickHouse; wide table additions are near-instant; however, changing column types requires `ALTER TABLE MODIFY COLUMN` which can be slow on large tables |
| CI/CD ease | ⚠️ 6/10 | `dbt-clickhouse` adapter exists (community-maintained, not dbt Labs official); less mature than dbt-snowflake; missing some incremental strategies; Terraform clickhouse provider available but young |
| DV2.0 at 30B rows | ⚠️ 6/10 | See detailed analysis below |
| **Total** | **107/150** | |

### DV2.0 on ClickHouse — Technical Deep Dive

**Hub tables** (append-only, dedup by business key):
`ReplacingMergeTree(loaded_at)` works correctly. Deduplication is asynchronous (background merge) — `SELECT FINAL` forces synchronous dedup but incurs 2–5× performance penalty. For hubs this is acceptable since they are queried rarely (mostly for joins).

**Link tables** (append-only, relationship pairs):
`MergeTree` is ideal — no dedup needed; pure append. Hub-to-link joins perform extremely well in ClickHouse.

**Satellite tables** (hash_diff change detection, history tracking):
This is the critical weakness. Standard DV2.0 satellite pattern (insert new row when `hash_diff` changes, keep all history) works with `MergeTree` but creates the same challenge as PostgreSQL: querying "latest satellite row per hub BK" requires either:
- `ROW_NUMBER() OVER (PARTITION BY hub_key ORDER BY loaded_at DESC)` — ClickHouse window functions are supported but the performance on 10B-row satellite scans is inconsistent.
- `ReplacingMergeTree` (keep latest) — loses history, breaking DV2.0's immutability principle.
- `CollapsingMergeTree` (sign column) — accurate but requires re-engineering the dbt satellite models significantly; no standard dbt DV2.0 macro supports this.

**PIT tables** (`pit_order_snapshot`):
PIT generation in ClickHouse requires complex join patterns that ClickHouse handles slower than expected due to its columnar-but-not-hash-join-optimised architecture. Snowflake's query optimizer handles DV2.0 PIT joins significantly better.

**dbt on ClickHouse**:
The `dbt-clickhouse` community adapter supports `incremental` materialization but lacks:
- Native `unique_key` dedup on ClickHouse (handled via `ReplacingMergeTree` workaround)
- `merge` incremental strategy (uses `insert_overwrite` instead)
- Several `dbt_utils` macros that the PoC uses

This means the existing 35+ dbt DV2.0 model files would require significant modification — adding implementation risk and extending the migration timeline.

### Verdict — Standalone ClickHouse

**Not recommended as a standalone replacement for the banking TCA production system.** The three disqualifying gaps for MiFID II compliance are:
1. No native Dynamic Data Masking (PII column security requires application-layer workaround that is unauditable at the DB layer)
2. Mutable `system.query_log` (MiFID II requires immutable audit of data access)
3. Incomplete SSO/SCIM integration (manual provisioning creates operational risk and audit gaps)

These gaps are not fundamental to ClickHouse's architecture — they are product maturity gaps that may close in future releases. For a 2026+ greenfield deployment to a non-banking context, ClickHouse Cloud would be highly competitive.

---

## Scenario 2 — ClickHouse Cloud as a High-Speed Serving Layer

This is the architecturally sound use of ClickHouse: not replacing the vault, but replacing the mart **serving layer** for dashboard reads. The full DV2.0 vault remains in Snowflake; ClickHouse hosts replicated copies of `mart_trading_risk`, `mart_market_data`, `mart_corporate` for the FastAPI TCA query path.

### Architecture

```
Snowflake (vault + source of truth)
    │
    │  Scheduled Snowflake → ClickHouse replication
    │  (Snowflake Kafka Connector → Confluent → ClickHouse Kafka Table Engine)
    │  or: dbt materialise marts in BOTH Snowflake and ClickHouse (dual-write)
    │
    ▼
ClickHouse Cloud (serving layer — marts only)
    │
    ├── mart_trading_risk.fact_order_execution  (counterparty_id enforced via ClickHouse row policy)
    ├── mart_market_data.fact_price_benchmark
    ├── mart_corporate.fact_client_activity
    └── mart_consolidated (aggregated)
    │
    ▼
FastAPI tca_service.py  →  ClickHouse (reads)
                        →  Snowflake (fallback + audit)
                        →  PostgreSQL (auth.api_clients — JWT verify)
```

### What This Achieves

| Metric | Snowflake-only | Snowflake + ClickHouse serving |
|---|---|---|
| Dashboard query p50 latency | ~800ms–2s | **~50ms–200ms** |
| Dashboard query p99 latency | ~3s–8s | **~200ms–500ms** |
| Concurrent dashboard users (cost-neutral) | ~20–50 | **200–500** |
| Snowflake compute cost reduction | baseline | **~25–35% reduction** (mart reads offloaded) |
| Operational complexity | Low | **Medium-High** (two systems to maintain) |
| Data freshness lag | Real-time | **~5–15 min** (mart replication cadence) |

### When the Serving Layer Pattern Makes Sense

The TCA dashboard (`GET /tca/summary`, `GET /tca/order/{id}`) is the highest-frequency API path — every analyst, trader, and client hitting the dashboard triggers a mart query. At 500+ concurrent users, Snowflake warehouse contention becomes a real cost and latency issue even with multi-cluster. ClickHouse handles 500 concurrent simple analytical queries at <200ms without breaking a sweat.

This pattern is used in production by financial platforms (e.g., Cloudflare, Adyen) where a source-of-truth warehouse (Snowflake/BigQuery) feeds a ClickHouse serving cluster for user-facing API paths.

### Serving Layer Assessment

| Criterion | Stack A (Snowflake only) | Stack A + ClickHouse serving |
|---|---|---|
| Managed infra | ✅ Full | ✅ Full |
| Security (serving path) | ✅ Full Snowflake guarantees | ⚠️ ClickHouse row policies (less mature) |
| PII on serving path | ✅ DDM in Snowflake | ⚠️ View-based masking in ClickHouse (or pre-mask before replication) |
| Auditability | ✅ ACCESS_HISTORY (vault + mart) | ✅ ACCESS_HISTORY (vault) + ClickHouse query_log (mart reads) — gap on column-level mart reads |
| Dashboard p50 latency | ~800ms | **~100ms** |
| Cost/year | £532K–£819K | £532K–£819K + £60K–£100K (ClickHouse) **minus** £80K–£150K (Snowflake compute savings) = **net neutral to £50K cheaper** |
| Operational complexity | Low | **Medium-High** — two analytical systems, replication pipeline, dual data freshness SLAs |
| Data freshness | Real-time | **~5–15min lag** for mart data |

### Verdict — ClickHouse as Serving Layer

**Conditionally recommended** as a Phase 2 optimisation after the core Snowflake stack is stable. The condition is that PII masking is applied in Snowflake **before** replication to ClickHouse (mart tables contain pre-masked values, not raw PII), preserving the compliance posture. The audit gap (no column-level read log on the serving path) must be documented in the MiFID II compliance statement and mitigated by shipping ClickHouse `query_log` to immutable cold storage.

Trigger criteria for adopting this pattern:
- Concurrent dashboard users > 200
- TCA API p99 latency > 2 seconds on Snowflake
- Snowflake credit spend > £600K/year (serving ClickHouse makes the economics compelling)

---

## ClickHouse Summary

| Use Case | Recommended? | Rationale |
|---|---|---|
| **Full standalone replacement (vault + marts)** | ❌ No | Compliance gaps: no native DDM, mutable audit log, immature SSO/SCIM |
| **Raw vault + biz vault only** | ❌ No | DV2.0 satellite history + PIT patterns require significant ClickHouse-specific re-engineering |
| **Information marts serving layer** | ✅ Yes (Phase 2) | Excellent fit: append-heavy marts, fast point queries, pre-masked data, significant latency gains |
| **Real-time tick_bars storage** | ✅ Yes | MergeTree is superior to TimescaleDB for 30-second OHLCV bars at scale; sub-millisecond OHLCV reads |

---

---

# Part IV — Final Consolidated Recommendation

## Recommended Production Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  PHASE 1: CORE STACK (Year 1)                                               │
│                                                                              │
│  Sources → dlt → Confluent Cloud (Kafka) → Snowflake (stg_raw transient)   │
│                       ↓                                                      │
│              Astronomer Cloud (Airflow)                                      │
│                       ↓                                                      │
│              dbt Cloud → Snowflake                                           │
│              raw_vault → biz_vault → mart_*                                 │
│                       ↓                                                      │
│              Okta SSO → FastAPI (ECS Fargate) → PostgreSQL (auth, obs)      │
│              FastAPI retained for: /auth/token, /predict/slippage,           │
│              /regime/*, /pipeline/run (ML + ops endpoints only)              │
│                       ↓                                                      │
│              Tableau Cloud (SaaS) — Okta SAML SSO                           │
│              Snowflake OAuth per-user → Row Access Policies (counterparty)   │
│              Live Connection to mart_* (or Extract for performance tier)     │
│                                                                              │
│  Snowflake security: Row Access Policies + DDM + ACCESS_HISTORY             │
│  DR: Snowflake Replication Groups + Aurora Global                           │
└──────────────────────────────────────────────────────────────────────────────┘
                              ↓ (When Snowflake credits > £600K/yr or API consumers > 200 concurrent)
┌──────────────────────────────────────────────────────────────────────────────┐
│  PHASE 2: SERVING LAYER (Year 2 optimisation)                               │
│                                                                              │
│  Note: Tableau Cloud uses its own Hyper extract engine for caching —        │
│  ClickHouse serving layer is NOT needed for Tableau dashboard reads.        │
│                                                                              │
│  ClickHouse serving applies to: FastAPI ML/predict paths, programmatic      │
│  API consumers (Bloomberg terminals, OMS feeds), not Tableau.               │
│                                                                              │
│  Snowflake marts → pre-masked replication → ClickHouse Cloud (API paths)   │
│  Tableau Cloud → Snowflake Live Connection (native; Hyper cache optional)   │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Final Scorecard

| Dimension | Phase 1 (Snowflake + Tableau) | Phase 2 (+ClickHouse for API paths) | Databricks+Azure + Tableau |
|---|---|---|---|
| Database engine | Snowflake Enterprise | Snowflake + ClickHouse Cloud | Delta Lake + Unity Catalog |
| Frontend / reporting | Tableau Cloud | Tableau Cloud | Tableau Cloud |
| DV2.0 fit | ✅ 10/10 | ✅ 10/10 | ✅ 10/10 |
| PII / Column security | ✅ 10/10 (DDM in Snowflake; Tableau OAuth inherits) | ✅ 9/10 (pre-mask before CH replication) | ✅ 9/10 |
| RBAC / Row security | ✅ 10/10 (Snowflake RAP via Tableau OAuth) | ✅ 9/10 | ✅ 10/10 |
| Auditability | ✅ 10/10 (ACCESS_HISTORY captures Tableau queries as Snowflake SQL) | ✅ 9/10 | ✅ 9/10 |
| DR | ✅ 10/10 | ✅ 10/10 | ✅ 9/10 |
| Legal entity isolation | ✅ 10/10 | ✅ 10/10 | ✅ 10/10 |
| Dashboard performance | ✅ Fast (Tableau Hyper extract cache) | ✅ Fast | ⚠️ Similar to Snowflake path |
| Portability | ⚠️ 5/10 | ⚠️ 6/10 | ✅ 9/10 |
| Operational complexity | Medium (Tableau workbook management) | Medium-High | High |
| Annual cost | **£596K–£952K** | **£550K–£880K** (ClickHouse offset Snowflake API credits) | **£440K–£680K** |
| Migration effort from PoC | Medium (Angular rebuild → Tableau workbooks) | Medium + Phase 2 pipeline | High (Spark expertise + Tableau) |
| MiFID II readiness (out of box) | ✅ Highest | ✅ High | ✅ High |
| ML prediction (pre-trade, regime) | ⚠️ Via FastAPI only (Tableau cannot POST) | ⚠️ Via FastAPI only | ⚠️ Via FastAPI only |
| **Overall recommendation** | **Primary recommendation** | **Adopt at API-scale trigger** | **If budget is hard constraint** |

## Database Decision Summary

| Engine | Verdict | Role in Production |
|---|---|---|
| **PostgreSQL** | Retain | `auth`, `obs` operational schemas only (not analytical) |
| **Snowflake** | **Primary recommendation** | All analytical schemas: stg_raw → raw_vault → biz_vault → mart_* |
| **MSSQL (SQL MI)** | Not recommended | Row-store limits DV2.0 OLAP performance at scale |
| **Oracle ADW** | Not recommended | Cost and lock-in prohibitive for greenfield deployment |
| **ClickHouse Cloud** | Phase 2 for API serving paths | mart_* for programmatic API consumers only; Tableau has its own Hyper cache |
| **Tableau Cloud** | **Replaces Angular 17 SPA** | Reporting, dashboards, role-aware views — see Part V |

---

*Evaluation conducted: 2026-05-01. Cost estimates are indicative based on public list pricing at EU-region rates. Enterprise Agreement discounts (AWS, Snowflake, Tableau) can reduce costs by 20–40% and should be negotiated. Snowflake credit consumption projections assume a 60/40 split between DV2.0 incremental build workloads and mart serving workloads.*

---

---

# Part V — Angular 17 SPA → Tableau Cloud: Full Impact Analysis

## Why Tableau Replaces Angular

The Angular 17 SPA in the PoC was purpose-built to demonstrate role-aware TCA dashboards, JWT-based auth state management (NgRx), and counterparty-scoped data isolation. In a production banking environment, maintaining a bespoke Angular application creates ongoing frontend engineering overhead — release cycles, security patching, browser compatibility testing — in an environment where the core value is data, not a custom UI.

Tableau Cloud eliminates this overhead. It is a **fully managed SaaS** BI platform that connects directly to Snowflake, inherits Snowflake's security model via OAuth, and provides a drag-and-drop authoring environment appropriate for a trading desk where the consumers (compliance officers, traders, clients) need reports, not a web application.

---

## Managed Deployment Comparison

| Dimension | Angular 17 SPA (PoC) | Tableau Cloud (Production) |
|---|---|---|
| **Hosting** | Docker container (Nginx) → CloudFront + S3 | Tableau SaaS — zero hosting infra |
| **Build pipeline** | Node 20, Angular CLI, Dockerfile.angular, CI/CD build job | No build pipeline; workbooks published via Tableau Desktop / REST API |
| **Deployment** | `docker compose up angular` → image push → CDN invalidation | Tableau workbook publish (`.twbx` file) via Tableau REST API or Desktop |
| **Infrastructure managed** | CloudFront distribution, S3 bucket, Nginx config, SSL cert | 100% managed by Tableau — zero infra to manage |
| **Scaling** | Manual (ECS service scaling or CDN) | Automatic — Tableau Cloud scales Hyper extract servers |
| **Updates / patching** | Angular CVE patches → rebuild → redeploy | Tableau manages all platform patching |
| **DR** | S3 cross-region replication + CloudFront failover | Built into Tableau Cloud SLA (99.9%) |
| **Browser support testing** | Required with each Angular release | Not required — Tableau Cloud handles it |
| **SLA** | Custom (self-managed) | Tableau Cloud 99.9% SLA |
| **CI/CD integration** | GitHub Actions → Angular build → S3 deploy | Tableau REST API → workbook publish / Tableau Cloud Connected Apps CI |

**Net managed deployment gain:** Tableau eliminates the entire frontend CI/CD pipeline, Nginx configuration, CDN management, and Angular release cycle. The operational burden shifts from ongoing engineering maintenance to Tableau workbook authoring and data source management.

---

## SSO and Authentication Impact

### Angular 17 approach (PoC)
1. User enters `client_id` + `client_secret` in Angular login form
2. Angular POSTs to `FastAPI /auth/token` (form-urlencoded)
3. FastAPI bcrypt-verifies against `auth.api_clients` (PostgreSQL)
4. FastAPI returns RS256 JWT (access + refresh)
5. Angular stores JWT in `localStorage` + NgRx store
6. `AuthInterceptor` appends `Authorization: Bearer` to every subsequent request
7. FastAPI decodes JWT → `UserClaims(client_id, role, counterparty_id)`

### Tableau Cloud approach (Production)
1. User opens Tableau Cloud URL → redirected to Okta SSO (SAML 2.0)
2. Okta authenticates user against enterprise directory (AD group membership → Tableau site role)
3. Okta returns SAML assertion to Tableau Cloud
4. Tableau Cloud maps Okta group → Tableau site role (Creator / Explorer / Viewer)
5. **Snowflake OAuth per-user**: Tableau requests OAuth token from Snowflake on behalf of the user
6. Each Tableau query executes in Snowflake under the user's own Snowflake role
7. Snowflake Row Access Policies (`WHERE counterparty_id = CURRENT_COUNTERPARTY_ID()`) apply natively — no application-layer injection required
8. `FastAPI /auth/token` endpoint **still required** for ML prediction form (Tableau Extensions or separate micro-app)

| Auth aspect | Angular | Tableau Cloud | Change |
|---|---|---|---|
| Login mechanism | client_id + secret (custom) | Okta SAML SSO | Eliminated custom auth form |
| Token management | JWT in localStorage + NgRx | Managed by Tableau + Okta | Eliminated token refresh logic |
| counterparty_id enforcement | FastAPI injects `AND counterparty_id = :cp` in every query | Snowflake Row Access Policy applied per OAuth user | Enforcement moves to DB layer (stronger) |
| `auth.api_clients` table | Required (bcrypt credential store) | No longer the primary auth store (Okta is) | PostgreSQL `auth` schema simplified |
| Refresh token | Custom `/auth/refresh` endpoint | Handled by Okta session management | Endpoint retired for Tableau users |
| MFA | Optional (custom implementation) | Enforced by Okta (banking grade) | Strengthened |

---

## RBAC and Counterparty Isolation Impact

### Angular role enforcement (PoC)
- `RoleGuard` in Angular blocked routes client-side (e.g., CLIENT → `/client-view` only)
- `AuthGuard` checked JWT expiry
- FastAPI `require_role()` / `require_min_role()` enforced server-side
- `tca_service.py` injected `AND counterparty_id = :cp` into every query

### Tableau Cloud role enforcement (Production)
Tableau has two distinct layers for the TCA role hierarchy:

**Layer 1 — Tableau Site Roles** (coarse-grained, matches Tableau's role model):

| PoC Role | Tableau Site Role | Access |
|---|---|---|
| ADMIN | Creator | Full workbook authoring + admin |
| HEAD_OF_TRADING | Explorer | Interact with all dashboards + filters |
| COMPLIANCE | Explorer | Interact with all dashboards; MiFID export via Tableau |
| TRADER | Explorer | Interact with trading dashboards |
| CLIENT | Viewer | Read-only access to specific published views |

**Layer 2 — Snowflake OAuth Row Access Policies** (fine-grained, replaces `counterparty_id` injection):
- Each Tableau user maps to a Snowflake role via Snowflake OAuth
- Snowflake roles carry the `counterparty_id` context: `SET counterparty_context = 'CP_ABCD'`
- Row Access Policies on `fact_order_execution`, `fact_client_activity` enforce isolation transparently
- A CLIENT user querying `mart_trading_risk` via Tableau physically cannot retrieve another counterparty's orders — the policy blocks it at query execution, not at the API layer

**This is architecturally stronger than the Angular approach**: in the PoC, a compromised JWT could theoretically bypass the FastAPI layer and query PostgreSQL directly. With Snowflake OAuth, the policy lives inside the database engine — it applies to every query regardless of client.

| RBAC aspect | Angular | Tableau Cloud |
|---|---|---|
| Route blocking | Angular `RoleGuard` (client-side, bypassable) | Tableau permissions (server-side, not bypassable) |
| Row-level filter | FastAPI `AND counterparty_id = :cp` injection | Snowflake Row Access Policy (DB-layer, not bypassable) |
| Column masking | Application-layer (FastAPI serialiser excludes fields) | Snowflake DDM (transparently masks at query time) |
| Role hierarchy | Custom `require_min_role()` in FastAPI | Tableau site roles + Snowflake role hierarchy |
| Audit of access | FastAPI access logs (application layer) | Snowflake ACCESS_HISTORY (DB layer, column-level) |

---

## Feature-by-Feature Migration Map

| Angular Feature | Tableau Equivalent | Gap / Notes |
|---|---|---|
| **Dashboard (role-aware KPIs + warnings)** | Tableau dashboard with role-based published views | ✅ Full equivalent; filter actions replace NgRx selectors |
| **Order TCA drilldown** (cost decomposition grid) | Tableau detail view with drill-through action | ✅ Full equivalent; richer visualisation options |
| **Algo Performance league table** | Tableau table viz with date + asset-class filter | ✅ Full equivalent |
| **Alpha Decay curves by vol regime** | Tableau line chart with LOW/MEDIUM/HIGH colour encoding | ✅ Full equivalent |
| **Venue SOR scorecard** | Tableau bar chart ranked by avg VWAP slippage | ✅ Full equivalent |
| **MiFID Export (RTS 27 CSV)** | Tableau data download (full data CSV export from any view) | ✅ Equivalent; format customisable; COMPLIANCE permission on the data source controls access |
| **Client View (counterparty-scoped)** | Tableau published view scoped via Row Access Policy | ✅ Stronger than Angular — isolation enforced at DB layer |
| **Observability warnings dashboard** | Tableau connected to `obs.obs_warnings` (PostgreSQL data source) | ✅ Full equivalent |
| **Pre-Trade Slippage Estimate** (`POST /predict/slippage`) | ❌ **Cannot replicate natively** — Tableau cannot POST to ML endpoints | Mitigation: Tableau Extension (JS API) that calls FastAPI, OR pre-compute slippage estimates and publish as a Tableau view |
| **Regime Detection timeline** (1020 colour-coded slices) | Tableau Gantt chart with `timestamp` × `regime` colour | ⚠️ Achievable but requires careful calculated field design; interactive hover detail works via tooltip |
| **Regime scatter plot** | Tableau scatter plot with `intraday_vol` × `volume_ratio` | ✅ Direct equivalent |
| **Real-time fill submission** (`POST /mock/fill`) | ❌ Not applicable in production (mock server feature only) | Not needed in production |
| **JWT login form** | Replaced by Okta SSO redirect | ✅ Better UX; enterprise-grade |
| **NgRx auth state** | Managed by Tableau session | ✅ Eliminated complexity |

**Critical gap — ML prediction interactivity**: The Pre-Trade Slippage Estimate (`/pre-trade` component) required a form with `instrument_class`, `side`, `quantity`, etc. submitted via `POST /predict/slippage`. Tableau cannot submit forms to external APIs natively. **Mitigations in priority order:**
1. **Tableau Extension (preferred)**: A Tableau Dashboard Extension (JS API) renders a React micro-form inside a Tableau dashboard that calls FastAPI `/predict/slippage`. Fully within Tableau, no separate app.
2. **Pre-computed estimates**: Run the GBT model nightly for all (instrument_class × side × quantity_bucket × vol_regime × algo × venue) combinations and publish results as a Tableau view. Loses interactivity but eliminates the dependency.
3. **Separate micro-application**: Retain a minimal React/Vue app for the prediction form only; embed it in the Tableau dashboard via a URL action. Adds a second frontend artefact.

---

## Cost Impact Analysis

### Removed costs (Angular)

| Removed item | Annual saving |
|---|---|
| S3 static hosting + CloudFront CDN | £8,000–£12,000 |
| Angular build CI/CD (GitHub Actions minutes, ECR storage) | £2,000–£4,000 |
| ECS Fargate task reduction (FastAPI now smaller — TCA read endpoints mostly retired) | £4,000–£6,000 |
| Angular development + maintenance (frontend engineering FTE cost — not vendor cost) | Significant (not modelled as infrastructure cost) |
| **Total infrastructure saving** | **£14,000–£22,000/year** |

### Added costs (Tableau Cloud)

| Added item | Annual cost |
|---|---|
| Tableau Cloud Explorer licenses (40 users — traders, compliance, HOT) | £19,000–£22,000 |
| Tableau Cloud Viewer licenses (200 users — clients, read-only) | £29,000–£34,000 |
| Tableau Cloud Creator licenses (5 users — data team, IT) | £3,400–£4,000 |
| Tableau Desktop (included in Creator license, 5 seats) | £0 (bundled) |
| Tableau REST API CI/CD (workbook publish automation) | Minimal (included in Cloud) |
| **Total Tableau Cloud (per-user pricing)** | **£51,400–£60,000/year** |
| **Alternative: Tableau Enterprise Site License (500 users)** | **£120,000–£175,000/year** |

### Net cost change

| Scenario | Angular infrastructure | Tableau Cloud | Net annual change |
|---|---|---|---|
| Small deployment (245 users, per-user) | -£14K–£22K | +£51K–£60K | **+£29K–£46K/year** |
| Large deployment (500+ users, site license) | -£14K–£22K | +£120K–£175K | **+£98K–£161K/year** |

### Impact on Snowflake credits

Tableau's connection mode to Snowflake has a material impact on Snowflake credit consumption:

| Tableau Mode | Behaviour | Snowflake credit impact |
|---|---|---|
| **Live Connection** | Every dashboard interaction → SQL query → Snowflake warehouse | High consumption; each filter, drill-down, page load hits Snowflake. Preferred for MiFID II data currency (always current) |
| **Extract (Hyper)** | Scheduled full or incremental extract → stored in Tableau's Hyper engine | Low consumption during serving; credit spike at extract time. Dashboards serve from Hyper cache — fast, but data has lag (e.g., refreshed hourly or daily) |
| **Hybrid** | Live for key metrics; Extracts for historical analysis | Balanced approach — recommended for TCA |

**Recommendation for TCA**: Use **Live Connection** for `mart_trading_risk` (real-time TCA metrics — regulators expect current data) and **Extracts (hourly refresh)** for `mart_market_data` and historical alpha decay / venue SOR workbooks. This reduces Snowflake credit consumption from Tableau by ~35% vs. all-live.

---

## Deployment Simplification

The Angular SPA required the following CI/CD artifacts in the PoC:
- `Dockerfile.angular` (Node 20 multi-stage build → Nginx)
- `nginx.conf` (SPA routing + `/api` proxy)
- Angular `app.config.ts`, `app.routes.ts`, `auth.interceptor.ts`, `auth.guard.ts`, `role.guard.ts`
- NgRx store (6 files: actions, reducer, effects, selectors)
- 10 Angular feature components (login, dashboard, order-tca, algo-perf, alpha-decay, venue-sor, mifid, client-view, pre-trade, regime-detection)

**All of this is eliminated** and replaced by:
- Tableau workbooks (`.twbx`) published to Tableau Cloud
- Snowflake OAuth configuration (one-time)
- Okta → Tableau SAML SSO group mapping (one-time)
- Tableau REST API publish script (optional CI/CD for workbook versioning)

The `docker-compose.yml` loses the `angular` service entirely. The Nginx proxy that forwarded `/api` calls to FastAPI is no longer needed for reporting traffic.

---

## Tableau Cloud: Managed Infrastructure Assessment

| Criterion | Rating | Notes |
|---|---|---|
| Managed infra | ✅ 10/10 | 100% SaaS; Tableau manages all compute, storage, upgrades |
| Security | ✅ 9/10 | TLS everywhere; IP allowlisting; Tableau Connected Apps for embedding; no data stored outside Tableau (Live Connection) |
| SSO | ✅ 10/10 | Native SAML 2.0 with Okta, Entra ID, OneLogin; MFA delegated to IdP |
| RBAC (row-level) | ✅ 10/10 | Snowflake OAuth per-user → Row Access Policies; stronger than application-layer injection |
| PII column masking | ✅ 10/10 | Snowflake DDM applies to Tableau queries transparently; Tableau users see only what their Snowflake role permits |
| Portability | ✅ 8/10 | Workbooks in `.twbx` / `.hyper` format; can republish to Tableau Server (self-hosted) if needed; standard SQL data sources |
| Auditability | ✅ 9/10 | Snowflake ACCESS_HISTORY captures all Tableau-generated SQL (queries appear as named Tableau user under their Snowflake OAuth token); Tableau Cloud admin audit logs for site activity |
| Observability | ✅ 8/10 | Tableau Cloud admin dashboards for usage, extract failures, connection errors; Tableau Pulse for metric alerts |
| Cost/year | ⚠️ 6/10 | £51K–£175K/year depending on user count and licensing model — significant premium over Angular CDN hosting |
| Reliability | ✅ 9/10 | Tableau Cloud 99.9% SLA; multi-region within same provider (AWS us-east for Cloud, EU compliance region available) |
| DR support | ✅ 8/10 | Tableau Cloud manages its own DR; workbooks backed up; Snowflake remains the source of truth (stateless on the Tableau side for Live Connection) |
| Legal Entity Isolation | ✅ 9/10 | Snowflake separate databases per entity; Tableau data sources scoped per entity DB; Tableau site permissions restrict which data source each user group can access |
| Schema change resistance | ✅ 9/10 | Tableau data sources auto-detect Snowflake column additions; calculated fields continue to work; new satellite columns appear automatically in the field list |
| CI/CD ease | ✅ 8/10 | Tableau REST API allows workbook publish from CI/CD; Tableau Cloud Connected Apps enable embedding; less elegant than dbt Cloud but manageable |

---

## Angular vs Tableau Summary

| Dimension | Angular 17 SPA | Tableau Cloud | Verdict |
|---|---|---|---|
| Managed | ❌ Self-managed (Docker, CDN, Nginx) | ✅ Fully managed SaaS | **Tableau** |
| SSO | Custom JWT + localStorage | Okta SAML (enterprise-grade) | **Tableau** |
| RBAC enforcement | API layer (FastAPI) | DB layer (Snowflake RAP) | **Tableau** (stronger) |
| PII masking | API serialiser exclusion | Snowflake DDM (transparent) | **Tableau** (stronger) |
| Auditability of reads | FastAPI access logs | Snowflake ACCESS_HISTORY (column-level) | **Tableau** |
| Interactive ML prediction | ✅ Full (POST form) | ⚠️ Requires Tableau Extension | **Angular** |
| Real-time data | ✅ NgRx polling | ✅ Live Connection (or Extract) | Draw |
| Build & deploy effort | High (Node, Angular CLI, CI/CD) | Low (workbook publish) | **Tableau** |
| Custom interactivity | ✅ Full (any JS component) | ⚠️ Limited (Extensions for complex forms) | **Angular** |
| Development time (initial) | High (10 components, NgRx) | Low (workbook authoring) | **Tableau** |
| Cost vs Angular hosting | £8K–£12K/year (CDN) | £51K–£175K/year (licenses) | **Angular** (cheaper infra) |
| Cost vs engineering FTE | Add Angular FTE | Use existing data analysts | **Tableau** (lower human cost) |
| Banking adoption | Common (custom portals) | Very common (regulatory reporting) | Draw |

---

## Migration Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **ML prediction interactivity gap** — Tableau cannot POST to `/predict/slippage` | ⚠️ Medium | Build Tableau Dashboard Extension (JS API + React micro-form) in Phase 1; pre-computed slippage grid as fallback if Extension timeline slips |
| **Workbook authoring skill gap** — data team may not have Tableau Desktop experience | ⚠️ Medium | 2–3 Creator licenses for the data/IT team; standard Tableau Desktop training (2–3 days); drag-and-drop is significantly faster than Angular component development |
| **Snowflake OAuth per-user configuration** — more complex than static service account | ⚠️ Medium | One-time setup (~2–3 days engineering); Tableau publishes a step-by-step Snowflake OAuth integration guide; test with 5 pilot users before rollout |
| **License procurement timeline** — Enterprise site license requires procurement cycle | ⚠️ Medium | Engage Tableau sales 8–12 weeks before go-live; per-user licensing can bridge the gap while site license is negotiated; ~20–40% EA discount expected for 500+ users |
| **Tableau Cloud EU data residency** — some PII may not leave EU jurisdiction under GDPR/DORA | ⚠️ Medium | Tableau Cloud EU region available on AWS eu-west-1 (Ireland); verify data residency requirements with DPO before provisioning; Live Connection means PII stays in Snowflake (only query results transit Tableau) |
| **Extract lag for MiFID II data currency** — Extract mode introduces up to 60 min data lag | ⚠️ Low–Medium | Use Live Connection for `mart_trading_risk` (real-time TCA regulatory data); Extracts only for historical workbooks where lag is acceptable; document hybrid approach in the MiFID II compliance statement |
| **Workbook versioning and change management** — `.twbx` files are binary, not diff-friendly | Low | Publish workbooks via Tableau REST API from a Git-controlled directory; tag workbook versions; treat `.twbx` as a build artefact (publish, not commit) |
| **Regime Detection timeline visualisation** — 1,020 colour-coded slices require Gantt design | Low | Achievable in Tableau Gantt chart with calculated fields; requires a dedicated workbook authoring session (~1 day); interactive tooltips replace Angular hover details |

---

## Implementation Timeline — Tableau Migration (Phase 1)

Assumes Snowflake is provisioned and dbt Cloud models are green before Tableau work begins.

| Week | Milestone | Owner |
|---|---|---|
| 1–2 | Provision Tableau Cloud site (EU region, AWS eu-west-1); Okta SAML 2.0 SSO configuration; Snowflake OAuth per-user setup | Platform / Data Eng |
| 3–4 | Build Tableau data sources for `mart_trading_risk`, `mart_market_data`, `mart_corporate`; validate Snowflake OAuth per-user token flow; confirm Row Access Policy enforcement | Data Eng |
| 5–6 | Author core workbooks: Dashboard (role-aware KPIs), Order TCA drilldown, Algo Performance league table, Venue SOR scorecard | Data Analyst / Data Eng |
| 7–8 | Author compliance workbooks: MiFID Export (COMPLIANCE role), Client View (CLIENT Viewer); configure Tableau site permissions per Okta group | Data Analyst / Compliance |
| 9–10 | Build Tableau Dashboard Extension for Pre-Trade Slippage Estimate (JS API → FastAPI `/predict/slippage`); Alpha Decay and Regime Detection workbooks | Data Eng (frontend-capable) |
| 11–12 | UAT with TRADER, HEAD_OF_TRADING, COMPLIANCE, CLIENT user roles; validate `counterparty_id` isolation end-to-end; Snowflake ACCESS_HISTORY audit spot-check | QA / Compliance / Data Eng |
| 13 | Decommission `angular` service from `docker-compose.yml`; retire FastAPI TCA read endpoints (`/tca/summary`, `/tca/order/{id}`, `/orders`, `/tca/algo-performance` etc.); retain auth + ML + pipeline endpoints | Data Eng / DevOps |
| 14 | Tableau Pulse alerting for mart freshness; Tableau admin dashboard review; documentation handoff | Data Eng |

**Total migration effort:** ~10–14 weeks (2 engineers). No Angular frontend skills required after week 10.

---

## Part V Conclusion

Tableau Cloud is the **unambiguous production choice** over Angular 17 for the TCA reporting layer. The decision is driven by four factors that compound:

1. **Security posture**: Row Access Policies enforced at the database engine, not the API layer — a compromised session cannot bypass `counterparty_id` isolation regardless of how the Tableau query is constructed.
2. **Compliance posture**: Snowflake ACCESS_HISTORY logs every Tableau query under the named OAuth user, including column access — this satisfies MiFID II column-level audit requirements without additional tooling.
3. **Operational burden**: Eliminating Angular removes a full frontend engineering lifecycle (Node.js CVE patches, Angular major version upgrades, Nginx config, CDN invalidations, NgRx complexity). The replacement artifact is a `.twbx` workbook published via REST API.
4. **Audience fit**: A trading desk's reporting consumers — compliance officers, traders, clients — need interactive dashboards, not a web application framework. Tableau's drag-and-drop workbooks are authored and iterated by data analysts, not frontend engineers.

The only area where Angular retains an advantage is **interactive ML form submission** (Pre-Trade Slippage Estimate), mitigated by the Tableau Dashboard Extension pattern. This is a known gap that must be addressed in the Phase 1 implementation plan.

**Net cost premium**: +£29K–£46K/year (per-user, 245 users) or +£98K–£161K/year (site license, 500 users) over Angular hosting. This is justified by the elimination of frontend engineering FTE cost and the stronger compliance posture — angular would require a dedicated frontend engineer; Tableau is operated by existing data analysts.

---

---

# Part VI — Final Architecture Decision: Stack A + Real-Time Consumer Redesign

## Confirmed Stack

| Layer | Managed tier | Component | Provider | Replaces |
|---|---|---|---|---|
| **Analytical DB** | ✅ SaaS | Snowflake Enterprise (multi-cluster) | Snowflake | PostgreSQL analytical schemas |
| **Operational DB** | ✅ Fully managed | Aurora PostgreSQL Serverless v2 | AWS | PostgreSQL auth + obs schemas |
| **Batch ingestion** | ✅ Runs inside Astronomer | dlt 1.5.0 pipelines (Python tasks in Airflow) | dlthub | — (retained from PoC) |
| **Real-time broker** | ✅ Fully managed | AWS MSK Serverless | AWS | Redis Streams; **Confluent Cloud removed** |
| **Real-time consumer** | ✅ Serverless | **Lambda + MSK Event Source Mapping** (Python handler) | AWS | ECS Fargate consumer (removed) |
| **DV2.0 transform** | ✅ SaaS | dbt Cloud Enterprise | dbt Labs | dbt-postgres (PoC) |
| **Orchestration** | ✅ SaaS | Astronomer Cloud (managed Airflow 2.9) | Astronomer | Airflow LocalExecutor (PoC) |
| **Reporting** | ✅ SaaS | Tableau Cloud | Tableau | Angular 17 SPA |
| **API** | ✅ Fully managed | **AWS App Runner** + AWS API Gateway | AWS | FastAPI on ECS Fargate — simpler managed runtime |
| **SSO** | ✅ SaaS | Okta (SAML 2.0) | Okta | Custom JWT only |
| **Observability** | ✅ SaaS | Monte Carlo + DataDog + Snowflake system tables | Monte Carlo / DataDog | dbt-expectations + custom obs schema |
| **Audit** | ✅ SaaS | Snowflake ACCESS_HISTORY | Snowflake | pgaudit (PoC) |
| **DR** | ✅ Fully managed | Snowflake Replication Groups + Aurora Global | Snowflake / AWS | None (PoC) |

> **ECS Fargate is removed entirely.** It is managed compute, not managed service — it requires Docker image lifecycle, ECR repo management, task definition JSON, ECS service configuration, and active container health monitoring. App Runner and Lambda are the correct managed-service equivalents for the two custom Python workloads in this stack.

Confluent Cloud (£38K–£55K/year) is **removed** from the original Stack A. The real-time ingestion path is redesigned around Lambda + MSK Event Source Mapping.

---

## Managed Hosting Spectrum

Not all "managed" is equal. The TCA stack spans three tiers and it matters which tier each component falls into:

| Tier | What the provider owns | What you own | Examples in this stack |
|---|---|---|---|
| **SaaS** | Infrastructure, runtime, application platform, upgrades, DR | Configuration + your code/data | Snowflake, Tableau Cloud, dbt Cloud, Astronomer, Okta, Monte Carlo, DataDog |
| **Fully managed service** | Infrastructure, runtime, scaling, HA, patching | Configuration + your application code | Aurora PostgreSQL, MSK Serverless, AWS Lambda, AWS App Runner, API Gateway |
| **Managed compute** | Underlying servers, hypervisor, OS | Container image, task definition, service config, health monitoring, rollout strategy | ECS Fargate ← **this was the previous incorrect choice** |
| **Self-managed** | Nothing | Everything | On-prem, self-hosted Kubernetes |

**ECS Fargate sits in the managed-compute tier, not the fully-managed tier.** Operating ECS Fargate means: maintaining Dockerfiles, pushing images to ECR, writing task definition JSON, managing ECS service auto-scaling rules, setting up CloudWatch Container Insights, writing rollback procedures, and monitoring for container crashes. For a 2-person data platform team whose core skill is data engineering, this is unnecessary operational overhead when genuinely managed alternatives exist for both custom Python workloads.

---

## Real-Time Ingestion: Topic Broker Selection

The PoC uses Redis Streams → `redis_consumer.py` → PostgreSQL. In production, the broker must be durable, managed, and auditable. Four candidates evaluated against the TCA fill-ingestion pattern (fills, orders, market ticks — estimated **< 500K events/day** for a private bank trading desk):

| Broker | Managed? | Protocol | Throughput ceiling | Latency | Cost/year | Ops complexity |
|---|---|---|---|---|---|---|
| **Confluent Cloud** (Stack A default) | ✅ Fully | Kafka | Unlimited | ~10ms | £38K–£55K | Zero (connector only) |
| **AWS MSK Serverless** | ✅ Fully | Kafka | ~200 MB/s | ~10ms | £8K–£15K | Low (no broker sizing) |
| **AWS Kinesis Data Streams** | ✅ Fully | Kinesis SDK | ~1 MB/s/shard | ~70ms | £3K–£8K | Very Low |
| **AWS SQS FIFO** | ✅ Fully | SQS SDK | ~3,000 msg/s per queue | ~100ms | £1K–£3K | Minimal |

### Recommendation: AWS MSK Serverless

**AWS MSK Serverless** is the best fit for the TCA platform:

- **Protocol compatibility**: Standard Kafka protocol (`confluent-kafka` Python library) — the production consumer is a near-direct port of `redis_consumer.py` with minimal rewrite.
- **Cost**: £8K–£15K/year vs £38K–£55K/year for Confluent — saves **£23K–£40K/year**.
- **Managed**: MSK Serverless handles broker provisioning, patching, scaling, and multi-AZ HA automatically; no broker sizing decisions.
- **Security**: IAM-based auth (no credential management for Kafka brokers); VPC-native; encryption in transit enforced; Private Link available.
- **Audit**: MSK broker logs shipped to CloudWatch Logs (immutable retention via CloudWatch Log Group resource policy) — satisfies MiFID II message-level audit for fill events.

**When to prefer Kinesis instead**: If fill volume stays permanently below 100K events/day and the team wants zero Kafka expertise, Kinesis + Lambda enhanced fan-out is simpler — but the Python consumer code requires a rewrite (Kafka vs Kinesis SDK are not compatible). MSK is the lower-risk migration from the PoC.

**Why not SQS**: SQS FIFO is a queue, not a stream — no replay, no consumer-group offsets, no time-ordered fan-out to multiple consumers. Unsuitable for DV2.0 streaming where the consumer must be restartable from a known offset.

---

## Python Consumer: Architecture

The consumer is a **Lambda function triggered by MSK Event Source Mapping** — not a persistent ECS process. The common concern about Lambda and Kafka (consumer group state cannot survive cold starts) does not apply when Lambda Event Source Mapping is used: AWS manages the consumer group, polls MSK on your behalf, batches records, invokes your function, and commits offsets only after a successful execution. You write a stateless handler; AWS owns the streaming protocol.

```
AWS MSK Serverless
  Topics: pb.fills | pb.orders | pb.market_ticks
        │
        │  AWS manages: consumer group, partition polling,
        │  offset commits, retry schedule, DLQ routing
        ▼
Lambda — tca_rt_consumer (Python 3.11, triggered by MSK Event Source Mapping)
  ├── Handler receives: list[KafkaRecord] (batch per partition, up to 10,000 records)
  ├── Per record:
  │     1. Deserialise JSON payload
  │     2. Write to Snowflake stg_raw via Snowpipe Streaming SDK
  ├── On success: AWS commits Kafka offsets automatically
  ├── On failure: Lambda retry (configurable) → on-error SQS DLQ → CloudWatch alarm
  └── Concurrency: 1 invocation per MSK partition (parallelism managed by AWS)
        │
        ▼
Snowflake — stg_raw.rt_fills (Transient table, 7-day auto-expiry)
        │
        ▼
Astronomer Cloud — micro-DAG (sensor: stg_raw.rt_fills new rows, 15-min max wait)
        │
        ▼
dbt Cloud — dbt run --select raw_vault.sat_fill_execution+ (incremental)
```

**What Lambda + MSK Event Source Mapping eliminates vs ECS Fargate:**
- No Dockerfile or Docker image to build and maintain for the consumer
- No ECR repository to manage
- No ECS task definition JSON
- No ECS service auto-scaling configuration
- No container health checks or restart policies
- No CloudWatch Container Insights setup
- No on-call response for "consumer process crashed" — Lambda is stateless and always restarts cleanly
- Deployment is `aws lambda update-function-code` (a single CLI call or CDK deploy), not a Docker build + push + rolling service update

### Write path to DV2.0 raw layer — two approaches

**Approach A (recommended): Consumer → stg_raw → dbt → raw_vault**

The consumer writes raw JSON to `stg_raw.rt_fills` only. All DV2.0 hash key and `hash_diff` computation stays inside dbt models. An Astronomer sensor triggers a micro-dbt-run every 15 minutes.

| Property | Value |
|---|---|
| DV2.0 logic location | dbt only (single source of truth) |
| Latency to raw_vault | 15–30 min (acceptable for TCA — orders are analysed post-execution) |
| Consumer code complexity | ~150 lines (deserialise + Snowpipe SDK call + offset commit) |
| Risk of hash key drift | None — dbt macros are authoritative |
| Schema evolution | dbt satellite column addition propagates automatically |

**Approach B: Consumer → raw_vault directly (Python computes DV2.0 hash keys)**

The consumer computes `hub_key = md5(fill_id)`, `hash_diff = md5(field1 || field2 || ...)` in Python and upserts directly into `hub_fill`, `lnk_order_fill`, `sat_fill_execution`.

| Property | Value |
|---|---|
| DV2.0 logic location | Python consumer **and** dbt (duplicated) |
| Latency to raw_vault | < 1 min |
| Consumer code complexity | ~400–600 lines (hash functions must exactly match dbt macros) |
| Risk of hash key drift | **High** — any dbt macro change must be replicated in Python |
| Schema evolution | Every satellite column addition requires consumer code change |

**Approach A is the clear recommendation** unless sub-minute raw_vault latency is a regulatory or operational requirement. For TCA, fills are enriched against market data post-execution; the 15-min window is well within analytical needs. If real-time raw_vault becomes necessary in a later phase, a shared `dv2_hashing.py` library can be introduced — used by the consumer and imported by dbt Python models — eliminating the drift risk.

### FastAPI: AWS App Runner

FastAPI (`/auth/token`, `/predict/slippage`, `/regime/*`, `/pipeline/run`) moves to **AWS App Runner** instead of ECS Fargate.

App Runner sits in the fully-managed service tier: you push a Docker image to ECR (or point at source code) and App Runner owns load balancing, TLS termination, health checks, auto-scaling, and zero-downtime deployments. There are no ECS task definitions, no ECS service configurations, no Application Load Balancer to provision, and no target group health check rules to maintain.

| Dimension | ECS Fargate (removed) | AWS App Runner (chosen) |
|---|---|---|
| What you manage | Dockerfile, ECR image, task definition JSON, ECS service, ALB target group, health check config, scaling policy | Dockerfile + ECR image only |
| Load balancer | Self-provisioned ALB | Managed by App Runner |
| TLS | ACM cert + ALB listener rule | Managed by App Runner |
| Auto-scaling | ECS Service auto-scaling policy (CPU %) | App Runner built-in (request-based, configurable) |
| Zero-downtime deploy | Rolling update configuration required | Automatic on every deploy |
| Health checks | Custom ALB health check + ECS container health | App Runner default HTTP health check |
| VPC access | ECS task in VPC subnet, security group | App Runner VPC connector (1 config item) |
| Deployment trigger | `ecs update-service` + task definition update | Push to ECR → App Runner auto-deploys |
| Rollback | Previous task definition revision (manual) | App Runner deployment history (one-click) |
| Cost | ~£8K–£12K/year | ~£5K–£9K/year (per-request model, scales to zero when idle) |

**App Runner is banking-appropriate**: AWS-managed, VPC-native, SOC 2 / PCI DSS covered under the AWS compliance umbrella. Private networking to Aurora PostgreSQL is via App Runner VPC Connector — a single Terraform resource, not a subnet/security-group/NLB configuration.

### Consumer Runtime Comparison

| Runtime | Managed tier | Kafka offset management | Deployment | Cost | Verdict |
|---|---|---|---|---|---|
| **Lambda + MSK trigger** | ✅ Fully managed | AWS owns (Event Source Mapping) | Function code only | ~£500–£1K/year | **Chosen** |
| ECS Fargate (persistent) | ⚠️ Managed compute | Application code must commit offsets | Docker image + task def + service | ~£3K–£5K/year | Removed |
| ECS on EC2 | ❌ More ops | Application code must commit offsets | Docker + EC2 + task def | Lower cost, higher ops | Not recommended |
| EKS | ❌ Self-managed cluster | Application code must commit offsets | Kubernetes manifests + cluster | £15K+/year overhead | Not recommended |

The earlier concern that "Lambda cannot maintain Kafka consumer-group state" is only true for self-managed Kafka consumers. With **Lambda MSK Event Source Mapping**, AWS operates the consumer group — your Lambda function is a pure, stateless record processor. This is the intended managed pattern for Kafka on Lambda.

---

## CI/CD and Deployment Complexity

The full production stack has **six distinct deployment surfaces**. Each is assessed independently, then a combined complexity verdict is given.

### Per-Component CI/CD

| Component | Managed tier | Artifact deployed | Deploy mechanism | Rollback | Who triggers |
|---|---|---|---|---|---|
| **Snowflake** | SaaS | — | Snowflake manages platform updates | — | Provider |
| **Tableau Cloud** | SaaS | `.twbx` workbook | `tableau_tools` REST API publish | Republish previous tagged workbook | Git push → GitHub Actions |
| **dbt Cloud** | SaaS | dbt project (SQL + YAML) | dbt Cloud webhook on Git push; slim CI on PR | Revert commit → dbt Cloud re-run; Snowflake Time Travel for data | Git push → dbt Cloud webhook |
| **Astronomer Cloud** | SaaS | Python DAG files | `astro deploy --dags` (~30s, DAG-only) | Revert commit → `astro deploy` | Git push → GitHub Actions → Astro CLI |
| **Lambda (RT consumer)** | Fully managed | Python function code (zip or ECR image) | `aws lambda update-function-code` or CDK deploy | Lambda versioning → `update-alias` to previous version | Git push → GitHub Actions |
| **App Runner (FastAPI)** | Fully managed | Docker image (ECR) | Push to ECR → App Runner auto-deploys | App Runner deployment history (one-click revert in console or CLI) | `docker push ECR` → App Runner (event-driven) |
| **Infrastructure (Terraform)** | IaC | `.tf` files | `terraform plan` on PR; `terraform apply` on merge (manual approval gate) | `terraform apply` previous state | Git push → GitHub Actions → Terraform Cloud |

**Key change from the ECS-based design**: The two previously Docker-heavy pipelines (`app_deploy.yml` for FastAPI, `consumer_deploy.yml` for the consumer) are now fundamentally simpler:
- **FastAPI (App Runner)**: `docker build → docker push ECR` — App Runner handles the rest automatically. No `ecs update-service`, no task definition update, no ALB health check validation.
- **Consumer (Lambda)**: Function code package or container image deploy — no Docker build required if using a pure Python zip deployment. AWS Lambda Powertools handles structured logging, tracing, and error handling as a layer.

### GitHub Actions Pipelines

```
.github/workflows/
├── dbt_ci.yml           # PR: dbt Cloud slim CI via API; merge: dbt Cloud production job
├── astro_deploy.yml     # DAG-only deploy on merge to main (astro deploy --dags)
├── app_deploy.yml       # FastAPI: docker build → ECR push (App Runner auto-deploys)
├── consumer_deploy.yml  # Lambda: package → aws lambda update-function-code
├── tableau_publish.yml  # Workbook publish via Tableau REST API
└── terraform.yml        # Plan on PR (no apply); apply on merge with manual approval gate
```

Six pipelines — same count as before — but `app_deploy.yml` and `consumer_deploy.yml` are now materially simpler. The ECS-era pipelines each had 5–6 steps (build, tag, push, register task definition, update service, wait for stability). The App Runner pipeline stops at ECR push. The Lambda pipeline is a single function-code update call.

### Terraform Simplification: ECS Removal Impact

Removing ECS Fargate from the stack eliminates a large portion of the Terraform surface. The stack now splits cleanly into two groups:

- **SaaS components** (Snowflake, dbt Cloud, Astronomer Cloud / Airflow, Tableau, Okta, Monte Carlo, DataDog) — **zero Terraform footprint**. These are provisioned through provider consoles and deployed via provider CLIs (`astro deploy --dags`, dbt Cloud webhook, Tableau REST API). Terraform has no visibility into them.
- **AWS fully managed services** (App Runner, Lambda, MSK Serverless, Aurora Serverless v2, API Gateway) — still require Terraform for provisioning, but each resource is expressed in far fewer lines than the ECS equivalent and has no operational configuration to maintain.

**Resources removed from Terraform (ECS-era):**

| Resource | Why it existed | Status |
|---|---|---|
| `aws_lb` (ALB) | Load balancer for ECS Fargate FastAPI | Gone — App Runner owns load balancing |
| `aws_lb_listener` + `aws_lb_listener_rule` | ALB HTTPS routing | Gone |
| `aws_lb_target_group` | ECS service target group | Gone |
| `aws_ecs_cluster` | Cluster for consumer + FastAPI | Gone |
| `aws_ecs_task_definition` (×2) | FastAPI + consumer task definitions | Gone |
| `aws_ecs_service` (×2) | FastAPI + consumer ECS services | Gone |
| `aws_appautoscaling_target` + `aws_appautoscaling_policy` (×2) | ECS service auto-scaling rules | Gone — App Runner and Lambda scale natively |
| `aws_ecr_repository` (×2) | One ECR repo per ECS service | Reduced to 1 — App Runner only; Lambda uses zip deploy |
| CloudWatch Container Insights config | ECS container crash monitoring | Gone — replaced by Lambda/App Runner native CloudWatch metrics |

**Remaining Terraform surface (fully managed tier):**

| Resource | What it provisions | Approx. lines |
|---|---|---|
| `aws_apprunner_service` | FastAPI: ECR image source, vCPU/memory, VPC connector, env vars | ~30 |
| `aws_apprunner_vpc_connector` | Private connectivity to Aurora PostgreSQL | ~10 |
| `aws_lambda_function` | RT consumer: zip source, runtime (Python 3.11), IAM role, env vars | ~25 |
| `aws_lambda_event_source_mapping` | MSK → Lambda trigger: topic names, batch size, starting position | ~10 |
| `aws_msk_serverless_cluster` | Broker: VPC config + IAM auth — no broker instance type, no storage-per-broker sizing | ~20 |
| `aws_rds_cluster` (Serverless v2) | Aurora: engine version, serverless scaling config (min/max ACU), subnet group | ~25 |
| `aws_api_gateway_rest_api` + stage | API Gateway in front of App Runner | ~30 |
| `aws_secretsmanager_secret` (×3) | MSK credentials, Snowflake private key, Okta client secret | ~15 |
| VPC, subnets, security groups | Networking baseline — unchanged from ECS design | ~50 |

Estimated total: **~215 lines of Terraform** for all AWS infrastructure. The ECS Fargate equivalent required ~400–500 lines (ALB, two ECS services, auto-scaling policies, container insights, ECR lifecycle policies for two repos). The `terraform.yml` pipeline is structurally unchanged — plan on PR, apply on merge with manual gate — but `terraform plan` output is smaller, which reduces review burden and lowers the chance of drift being missed.

### Environment Promotion

```
Feature branch
    │
    ├── PR open:
    │   ├── dbt slim CI → Snowflake DEV schema (dbt Cloud webhook)
    │   ├── pytest → Lambda function tests (GitHub Actions)
    │   ├── pytest → FastAPI tests (GitHub Actions)
    │   └── terraform plan → no apply
    ▼
main merge:
    ├── dbt Cloud job → Snowflake STAGING schema
    ├── astro deploy --dags → Astronomer staging
    ├── docker push → ECR staging → App Runner staging auto-deploys
    ├── lambda update-function-code → Lambda staging alias
    └── terraform apply → staging infra
    │
    ▼  [Manual approval gate — required for banking production deploys]
    │
production:
    ├── dbt Cloud job → Snowflake PROD schema
    ├── astro deploy --dags → Astronomer prod
    ├── docker push → ECR prod → App Runner prod auto-deploys
    ├── lambda update-function-code → Lambda prod alias (with weighted alias for canary if needed)
    ├── terraform apply → prod infra
    └── Tableau workbook publish → prod site
```

### Complexity Assessment

| Dimension | Rating | Notes |
|---|---|---|
| **Deployment pipelines** | Low–Medium | 6 pipelines; 4 are SaaS push-based (dbt, Astronomer, Tableau, Terraform); App Runner and Lambda pipelines are each ~3 steps |
| **No container orchestration** | Low | Zero ECS task definition management, zero ECS service management, zero ALB configuration. App Runner is the entire container ops surface |
| **Consumer reliability** | High | Lambda is stateless — a crash is just a failed invocation with automatic retry. No "consumer process died overnight" scenario. AWS owns the consumer group state |
| **FastAPI reliability** | High | App Runner manages health checks and restarts transparently. A bad deploy triggers automatic rollback if the health check fails |
| **dbt CI coverage** | High | Slim CI runs only affected models on PR; full run on merge; Snowflake Time Travel for data rollback |
| **Infrastructure drift** | Low–Medium | Terraform state in S3 backend; `terraform plan` on every PR catches drift before apply |
| **Secret management** | Low | AWS Secrets Manager: MSK credentials, Snowflake private key, Okta client secrets. Lambda and App Runner reference secrets via IAM role + Secrets Manager ARN — no secrets in code or environment literals |
| **Tableau workbook versioning** | Medium | `.twbx` files are binary; mitigated by tagging versions in Git as release artefacts and using Tableau REST API versioned publish |
| **On-call burden** | Low–Medium | Lambda error rate alert (CloudWatch → PagerDuty); dbt test failures (Monte Carlo); App Runner service health (built-in CloudWatch metrics). No container crash monitoring. Estimated 0–1 meaningful alert/week at steady state |

**Overall CI/CD complexity: Low–Medium.** Removing ECS from the stack eliminates the single largest source of operational overhead. Every component now either manages its own deployment (dbt Cloud, Astronomer, App Runner auto-deploy) or deploys with a single CLI call (Lambda function code, Astro DAG deploy). A 2-engineer data platform team whose primary skills are data engineering — not DevOps — can own the full deployment lifecycle without a dedicated platform engineer.

---

## SaaS Integration: Astronomer ↔ dbt Cloud

### How the old self-hosted model worked

Terraform provisioned an ECS node running Airflow and an S3 bucket storing dbt artifacts (`manifest.json`, `run_results.json`). Airflow ran dbt as a CLI subprocess inside the ECS container, reading and writing those artifacts to S3. Everything lived in a single AWS account; Terraform was the glue holding Airflow and dbt together.

### How the new model works

Astronomer Cloud and dbt Cloud are managed by different providers and share nothing at the infrastructure level. There is no shared filesystem, no S3 bucket for artifacts, and no ECS node. The integration is a single HTTPS API call:

```
Astronomer Cloud (Airflow DAG)
    └── DbtCloudRunJobOperator
            └── POST https://cloud.getdbt.com/api/v2/accounts/{id}/jobs/{id}/run/
                    └── dbt Cloud executes the dbt job → Snowflake
```

The operator polls the dbt Cloud API for completion and surfaces the result as a standard Airflow task — with logs, retries, and SLA tracking behaving as normal. No dbt CLI is installed on any server the team manages.

### Wiring the two providers together

| Element | Mechanism | Where configured |
|---|---|---|
| dbt Cloud API token | Airflow Connection (`dbt_cloud` type) stored in Astronomer | Astronomer UI or `astro deployment variable create` |
| Snowflake credentials | dbt Cloud project environment settings | dbt Cloud UI |
| dbt job definition | Defined once in dbt Cloud UI; referenced by job ID from DAG | dbt Cloud |
| DAG deployment | `astro deploy --dags` triggered by GitHub Actions on merge | Astronomer |
| dbt model deployment | Git push → dbt Cloud webhook (slim CI on PR, full run on merge) | dbt Cloud |
| dbt artifacts (manifest, run_results, catalog) | Stored internally by dbt Cloud; accessible via dbt Cloud API | dbt Cloud — no S3 needed |

The Airflow provider is `airflow-provider-dbt-cloud`, which ships `DbtCloudRunJobOperator` and `DbtCloudJobRunSensor` as first-class Airflow operators. The connection between the two SaaS platforms is the API token — a single credential stored as an Airflow Connection in Astronomer.

### What disappears vs the self-hosted model

| Old (self-hosted) | New (SaaS) |
|---|---|
| S3 bucket for dbt artifacts | Gone — dbt Cloud stores artifacts internally |
| Terraform provisioning of Airflow ECS node | Gone — Astronomer is SaaS |
| Terraform S3 bucket + IAM policy for dbt artifacts | Gone |
| dbt CLI installed on ECS container image | Gone — dbt Cloud runs dbt |
| Airflow ↔ dbt shared filesystem dependency | Gone — integration is a REST API call |
| ECS node patching, health monitoring, rollout strategy | Gone |

### DAG pattern

A typical batch vault DAG on Astronomer calling dbt Cloud:

```python
from airflow.decorators import dag
from airflow.providers.dbt.cloud.operators.dbt import DbtCloudRunJobOperator
from datetime import datetime

@dag(schedule="15 07 * * 1-5", start_date=datetime(2026, 1, 1), catchup=False)
def dag_raw_vault():
    DbtCloudRunJobOperator(
        task_id="run_raw_vault",
        dbt_cloud_conn_id="dbt_cloud_default",
        job_id="{{ var.value.dbt_raw_vault_job_id }}",
        check_interval=30,
        timeout=3600,
    )

dag_raw_vault()
```

No subprocess calls, no S3 references, no dbt CLI path configuration. The operator handles authentication, job triggering, polling, and error propagation natively.

### Deployment independence

A critical property of this model is that Astronomer and dbt Cloud deploy **independently** and neither deployment touches Terraform:

- A dbt model change (SQL or YAML edit) triggers the dbt Cloud webhook and deploys to dbt Cloud without affecting Astronomer.
- A DAG change (schedule, task order, sensor threshold) triggers `astro deploy --dags` and deploys to Astronomer without affecting dbt Cloud.
- The only Terraform involvement is rotating the dbt Cloud API token in AWS Secrets Manager — which Astronomer reads at runtime via the Airflow Connection. That is a credential rotation operation, not a deployment.

### Release and Rollback Procedures

#### Releasing a DAG change to Astronomer Cloud

```
1. Edit DAG file locally (e.g. dag_raw_vault.py)
2. git push → open PR
3. PR: GitHub Actions runs pytest (DAG import checks, task structure tests)
4. Merge to main → GitHub Actions runs:
       astro deploy --dags
   Astronomer syncs DAG files only — no Docker image rebuild (~30 seconds)
5. Manual approval gate → same command targets the production deployment
```

`astro deploy --dags` is the normal path for any change inside a DAG file. A full `astro deploy` (with Docker image rebuild, ~5–10 minutes) is only needed when adding a new Python package or upgrading an Airflow provider version.

#### Releasing a dbt model change to dbt Cloud

```
1. Edit model or YAML locally (e.g. sat_fill_execution.sql)
2. git push → open PR
3. PR: dbt Cloud slim CI fires automatically via GitHub webhook
       dbt build --select state:modified+
   Only the changed model and its downstream dependants run against Snowflake DEV
4. Merge to main → dbt Cloud production job fires automatically via webhook
       dbt build  (full run against Snowflake PROD)
```

The webhook is configured once in dbt Cloud pointing at the GitHub repo. Opening a PR and merging are the only triggers — no manual CLI calls after initial setup.

#### Release summary

| | Astronomer (DAGs) | dbt Cloud (models) |
|---|---|---|
| Trigger | `git push` → GitHub Actions | `git push` → dbt Cloud webhook |
| PR check | pytest (DAG import / task structure) | dbt slim CI (changed models only, Snowflake DEV) |
| Deploy command | `astro deploy --dags` | Automatic — webhook fires dbt Cloud job |
| Deploy time | ~30 seconds | ~2–15 min depending on model count |
| Terraform involved | No | No |
| Server restart | No | No |

**Coordination rule**: when a DAG change and a dbt model change are related (e.g. a new dbt job and the DAG operator that calls it), merge and deploy the dbt change first so the job exists in dbt Cloud before Astronomer tries to reference it.

#### Rollback: Airflow DAG on Astronomer

Astronomer stores a deploy history per deployment. Two rollback paths exist depending on urgency:

**Fast rollback — Astro CLI (minutes):**
```bash
# List recent deploys to get the deploy ID
astro deployment inspect <deployment-id> --log

# Roll back to the previous DAG deploy
astro deploy --dags --deployment-id <deployment-id> --dag-deploy-id <previous-deploy-id>
```
This re-syncs the DAG files from the previous deploy without a Git revert. Running DAG instances are not interrupted — Airflow completes in-flight task instances using the existing task definition before picking up the new (rolled-back) DAG structure on the next run.

**Standard rollback — Git revert:**
```bash
git revert <commit-sha>   # creates a new revert commit
git push → merge → GitHub Actions → astro deploy --dags
```
Preferred when the bad DAG is in a merged PR and other changes in the branch should be preserved. The revert commit is the audit trail.

**What cannot be rolled back automatically**: task instances that already ran successfully under the bad DAG version. If the bad DAG caused incorrect data to be written (e.g. wrong schedule triggered a duplicate dbt run), that is a data issue — see the dbt rollback path below.

#### Rollback: dbt model on dbt Cloud

dbt Cloud rollback has two independent dimensions: **code** (the model definition) and **data** (what was written to Snowflake).

**Code rollback — Git revert + dbt Cloud re-run:**
```bash
git revert <commit-sha>   # creates a new revert commit
git push → merge → dbt Cloud webhook triggers production job automatically
```
dbt Cloud re-runs the full production job against the reverted model definitions. No manual intervention in dbt Cloud is needed — the webhook fires on merge as normal.

**Data rollback — Snowflake Time Travel:**

If the bad model run wrote incorrect data to Snowflake before the code was reverted, restore the affected table to its pre-run state using Time Travel:

```sql
-- Restore a satellite to its state before the bad run
CREATE OR REPLACE TABLE raw_vault.sat_fill_execution
    CLONE raw_vault.sat_fill_execution
    BEFORE (timestamp => '<run-start-timestamp>'::timestamp_tz);
```

Snowflake Time Travel retention is 90 days on Enterprise tier. For incremental models, identify the `load_dts` of the bad records and delete surgically rather than cloning the full table:

```sql
DELETE FROM raw_vault.sat_fill_execution
WHERE load_dts >= '<run-start-timestamp>';
```

Then re-run the corrected dbt job from dbt Cloud UI (Runs → Re-run) to reprocess the source records cleanly.

#### Rollback decision tree

```
Issue detected in production
        │
        ├── Bad DAG logic (wrong schedule, wrong task order, bad sensor threshold)?
        │       │
        │       ├── Urgent (DAG running now) → astro CLI deploy previous deploy ID
        │       └── Normal             → git revert → merge → astro deploy --dags
        │
        └── Bad dbt model (wrong SQL, wrong grain, bad hash key)?
                │
                ├── Code only (data not yet written) → git revert → merge → webhook fires
                └── Data already written to Snowflake
                        │
                        ├── Incremental model → DELETE bad load_dts rows → re-run dbt job
                        └── Full-refresh model → Time Travel CLONE → re-run dbt job
```

---

## Updated Cost Estimate — Final Stack (Confluent + ECS Replaced)

| Component | Managed tier | Original Stack A | Final Stack |
|---|---|---|---|
| Snowflake Enterprise | SaaS | £290K–£480K | £290K–£480K |
| Snowflake Storage | SaaS | £28K–£40K | £28K–£40K |
| Aurora PostgreSQL Serverless v2 | Fully managed | £12K–£18K | £12K–£18K |
| **Confluent Cloud** | SaaS | **£38K–£55K** | **Removed** |
| **AWS MSK Serverless** | Fully managed | — | **£8K–£15K** |
| **Lambda (RT consumer)** | Fully managed | — | **£0.5K–£1K** |
| Astronomer Cloud | SaaS | £28K–£38K | £28K–£38K |
| dbt Cloud Enterprise | SaaS | £28K–£35K | £28K–£35K |
| Okta Workforce Identity | SaaS | £18K–£25K | £18K–£25K |
| Monte Carlo | SaaS | £32K–£45K | £32K–£45K |
| DataDog | SaaS | £18K–£25K | £18K–£25K |
| **AWS ECS Fargate (FastAPI)** | Managed compute | **£8K–£12K** | **Removed** |
| **AWS App Runner (FastAPI)** | Fully managed | — | **£5K–£9K** |
| AWS API Gateway | Fully managed | £4K–£6K | £4K–£6K |
| Tableau Cloud Enterprise | SaaS | £80K–£175K | £80K–£175K |
| Networking (PrivateLink, egress, MSK VPC) | — | £18K–£28K | £18K–£28K |
| **Total** | | **£596K–£952K** | **£520K–£840K** |

**Saving vs original Stack A: £76K–£112K/year** by removing Confluent Cloud and ECS Fargate entirely and replacing with MSK Serverless + Lambda + App Runner. The cost reduction is secondary to the operational simplification: the two removed components (Confluent, ECS) were also the two highest-overhead components to operate. The replacement services (Lambda, App Runner) require no container or broker operations.

---

## Final Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  CONFIRMED PRODUCTION STACK — PrivateBank TCA Platform                     │
│  All components: SaaS or fully managed service. No self-managed servers.   │
│                                                                             │
│  Sources (OMS/FIX, Market Data, Ref Data, FI, Eurex)                       │
│      │                                                                      │
│      ├── BATCH  [Astronomer Cloud — SaaS orchestration]                    │
│      │   dlt 1.5.0 tasks (Python, run inside Airflow workers)              │
│      │   → Snowflake stg_raw (Transient tables, auto-expire 7d)            │
│      │                                                                      │
│      └── REAL-TIME  [AWS MSK Serverless — fully managed Kafka]             │
│          Topics: pb.fills | pb.orders | pb.market_ticks                    │
│          → Lambda (Python handler, MSK Event Source Mapping)               │
│            AWS manages: consumer group, polling, offsets, retry, DLQ       │
│          → Snowpipe Streaming SDK → stg_raw.rt_fills                       │
│          → SQS DLQ (failed records, CloudWatch alarm)                      │
│                                                                             │
│  DV2.0 TRANSFORM  [dbt Cloud — SaaS; Astronomer — SaaS]                   │
│    Astronomer sensor (15-min) → dbt Cloud API → Snowflake                  │
│    stg_raw (views) → raw_vault (Hubs / Links / Sats, incremental)          │
│                    → biz_vault (derived sats, PIT)                         │
│                    → mart_trading_risk / mart_market_data                   │
│                      mart_corporate / mart_consolidated                     │
│                                                                             │
│  SECURITY  [Snowflake — SaaS, all queries]                                 │
│    Row Access Policies       counterparty_id per Snowflake OAuth role      │
│    Dynamic Data Masking      sat_client_profile PII columns                │
│    ACCESS_HISTORY            MiFID II column-level audit, 7-year retain    │
│    Snowflake Databases       DB_PB_DE | DB_PB_UK | DB_BCM_US              │
│                                                                             │
│  REPORTING  [Tableau Cloud — SaaS]                                         │
│    Okta SAML 2.0 SSO → Tableau Cloud (EU region, AWS eu-west-1)           │
│    Snowflake OAuth per-user → Row Access Policies apply natively           │
│    Live Connection:  mart_trading_risk (MiFID II data currency)            │
│    Extract (hourly): mart_market_data, alpha decay, venue SOR workbooks    │
│                                                                             │
│  API  [AWS App Runner — fully managed; API Gateway — fully managed]        │
│    API Gateway → App Runner → FastAPI (Python 3.11)                        │
│    Retained endpoints: /auth/token | /predict/slippage | /regime/*         │
│                        /pipeline/run                                        │
│    → Aurora PostgreSQL Serverless v2 (auth hot path)                       │
│    → Snowflake Snowpark connector (ML predict, regime queries)             │
│    App Runner: load balancing, TLS, health checks, auto-scale — managed   │
│                                                                             │
│  OBSERVABILITY  [SaaS]                                                     │
│    Monte Carlo  → DQ SLAs on hub_order freshness, satellite anomalies      │
│    DataDog      → App Runner API latency, Lambda error rates               │
│    Snowflake    → QUERY_HISTORY, WAREHOUSE_METERING system tables          │
│    Astronomer   → DAG run history, SLA miss alerts                         │
│                                                                             │
│  DR  [provider-managed]                                                    │
│    Snowflake Replication Groups → EU-West secondary (RPO 1min, RTO <5min) │
│    Aurora Global Database       → cross-region (RPO <1s)                  │
│    MSK Serverless               → multi-AZ built-in                        │
│    App Runner                   → multi-AZ built-in                        │
│    Lambda                       → multi-AZ built-in                        │
└─────────────────────────────────────────────────────────────────────────────┘

DEPLOYMENT — what the team actually operates:
  Git repository (dbt models, DAG files, Lambda function, FastAPI app, Terraform, Tableau workbooks)
  │
  GitHub Actions (6 lightweight pipelines):
  ├── dbt_ci.yml           dbt Cloud webhook (slim CI on PR, prod run on merge)
  ├── astro_deploy.yml     astro deploy --dags  [~30s, DAG-only]
  ├── app_deploy.yml       docker build → ECR push  [App Runner auto-deploys from ECR]
  ├── consumer_deploy.yml  aws lambda update-function-code  [single CLI call]
  ├── tableau_publish.yml  Tableau REST API publishWorkbook
  └── terraform.yml        terraform plan (PR) / terraform apply (merge + manual gate)

NOTHING the team deploys runs on a server they manage.
Every custom workload (FastAPI, Lambda consumer, dlt tasks, dbt models, DAGs)
runs on a provider-managed runtime.

ANNUAL COST: £520K–£840K (EA discounts of 20–40% achievable on Snowflake, Tableau, AWS)
```

---

*Evaluation conducted: 2026-05-01. Revised: 2026-05-01 — Confluent Cloud replaced by MSK Serverless + Lambda; ECS Fargate replaced by App Runner + Lambda; Part VI corrected to reflect fully-managed hosting throughout. Cost estimates based on public EU-region list pricing; all figures GBP.*
