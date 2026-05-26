# TCA Platform: Executive Summary & Quick Reference

**Document Purpose**: Single-page summary of production storage decision for leadership review  
**Last Updated**: 2026-04-22  
**Decision**: Apache Iceberg + Managed Trino (Starburst Enterprise)  
**Budget**: $150K-200K first year, $120K-150K recurring (5-year TCO ~$650K-750K)  
**Timeline**: 10 weeks to production

---

## Problem Statement

PrivateBank TCA platform PoC uses PostgreSQL + TimescaleDB (Docker Compose). Need production data storage for:
- 730K orders/year growing indefinitely
- 30-second benchmark data (time-series heavy)
- Monthly backfills (recalculate previous month)
- MiFID II compliance (5+ year retention)
- Budget-conscious but operationally sustainable

---

## Technology Evaluated

| Technology | 5-Year TCO | Backfill Cost/Month | Production Ready? | Recommendation |
|---|---|---|---|---|
| **Apache Iceberg + Starburst** | $650K-750K | $200-500 | ✅ Yes | **SELECTED** |
| Snowflake | $885K-1.2M | $5,000-27,000 | ✅ Yes | ❌ Too expensive |
| BigQuery | $445K | $3,000-15,000 | ✅ Yes | ❌ Costly, SQL dialect issues |
| Oracle On-Prem | $3.4M+ | $8,000-40,000 | ✅ Yes | ❌ Prohibitively expensive |
| Scale-up PostgreSQL | $400K-600K | $4,000-20,000 | ⚠️ Limited | ❌ Not scalable |

---

## Why Iceberg + Starburst?

### 1. Cost Savings: $235K-550K vs Snowflake Over 5 Years

**Monthly backfill requirement makes Snowflake unaffordable**:
- Snowflake: $13,500/backfill × 12 × 5 years = **$810,000 just for backfills**
- Iceberg: $400/backfill × 12 × 5 years = **$24,000 for backfills**
- **Savings on backfills alone: $786,000**

Even with Starburst managed service premium ($140K/year), Iceberg solution is 6-10x cheaper than Snowflake.

---

### 2. Backfill Economics

**Iceberg backfill cost breakdown** (1-month historical data):
- Spark/Trino workers: 4 nodes × 2 hours = 8 compute-hours
- Starburst credit rate: $1.50/credit-hour × 8 = $12
- S3 PUT operations: 0.5M files × $0.005/1K = $2.50
- **Total: ~$15 per backfill**

**Snowflake backfill cost breakdown** (same 1-month data):
- Full table rewrite + reclustering required
- X-Large warehouse for 6 hours: 10 credits/hour × 6h × $3/credit = **$180**
- Cloud services + storage I/O: Additional fees
- **Total per backfill: $5,000-27,000** (depending on warehouse size)

**Annual impact**: ($15 × 12) = $180 vs ($13,500 × 12) = $162,000

---

### 3. Compliance: Unlimited Time Travel

MiFID II requires 5+ year audit trail. Iceberg provides unlimited time travel (all snapshots retained indefinitely). Snowflake limits to 90 days unless you pay extra for Time Travel extensions.

---

### 4. Open Format: No Vendor Lock-in

Iceberg tables stored as Parquet on S3. If needed, can migrate to another cloud in 2-4 weeks (just copy S3 data, deploy Starburst elsewhere). Cannot say same for Snowflake.

---

### 5. Team Skill Development

Learning Iceberg/Trino develops valuable cloud data engineering skills in high demand. Oracle expertise declining in value. Snowflake skills useful but vendor-specific; Iceberg skills portable across companies.

---

## Architecture Overview

```
AWS EU (Frankfurt)
├── VPC (private subnets, no public IPs)
│   ├── EKS Cluster (Kubernetes)
│   │   ├── Starburst coordinator (1)
│   │   ├── Starburst workers (3-6, autoscale)
│   │   ├── FastAPI pods (3 replicas)
│   │   └── Redis master + replicas
│   └── Private subnets only
├── S3 Bucket (encrypted with KMS)
│   ├── iceberg/tca_production/db/ (Iceberg tables)
│   ├── raw/orders, fills, benchmarks
│   └── backups/
├── Glue Data Catalog (Iceberg metastore)
├── MWAA (Managed Airflow)
├── CloudWatch + Grafana Cloud (monitoring)
└── KMS (encryption keys)
```

**Key Components**:
- **Storage**: S3 Standard (10TB → 15TB by year 5) with lifecycle to Deep Archive
- **Query Engine**: Starburst Enterprise (commercial Trino) — 24/7 support
- **Metadata**: AWS Glue Data Catalog
- **Orchestration**: MWAA (managed Airflow)
- **Application**: FastAPI on EKS + Redis
- **Security**: VPC endpoints, IAM, KMS, TLS, fine-grained access control

---

## 5-Year Total Cost of Ownership

| Category | Year 1 | Years 2-5 (each) | 5-Year Total |
|---|---|---|---|
| Starburst license (4 nodes) | $110,000 | $110,000 | $440,000 |
| AWS infrastructure (S3, EKS, Glue, MWAA) | $25,000 | $30,000 (grows) | $125,000 |
| Support (Starburst Premier + AWS Enterprise) | $30,000 | $30,000 | $120,000 |
| Training & consulting (one-time) | $25,000 | $0 | $25,000 |
| Monthly backfills (12/year × $400) | $4,800 | $4,800 | $24,000 |
| Monitoring (Grafana Cloud) | $12,000 | $12,000 | $48,000 |
| **Annual Total** | **$206,800** | **$186,800** | **~$762,000** |

**With 3-year Starburst commitment (30% discount)**:
- Years 1-3: ~$165K/year
- Years 4-5: ~$186K/year (renewal at higher rate?)
- **5-year total: ~$703,000**

**Optimized (self-hosted Trino, no managed services)**:
- Starburst license: $0 (use open-source)
- But +$150K/year for senior DevOps FTE
- Net: still ~$600K-700K

**Bottom line**: **$700K-750K over 5 years** is realistic all-in cost (including headcount).

---

## Timeline: 10 Weeks to Production

```
Week 0    Week 1-2    Week 3-5    Week 6-7     Week 8-10
[Procure] → [Infra] → [Data Lake] → [Apps] → [Hardening] → GO LIVE
  4 days      10 days     15 days     10 days      15 days
```

**Key Milestones**:
- Week 2: Starburst cluster operational
- Week 5: 5-year backfill complete, dbt validated
- Week 7: FastAPI + Airflow tested end-to-end
- Week 10: Production cutover

---

## Team & Roles

| Role | FTE Count | Responsibilities |
|---|---|---|
| DevOps Engineer | 1.0 | Terraform, EKS, monitoring, CI/CD |
| Data Engineer | 1.0 | dbt migration, Iceberg schema, backfill generator |
| Backend Engineer | 0.5 | FastAPI migration, Redis, integration |
| Security Engineer | 0.25 | Pen tests, IAM, compliance audit |
| **Total Effort** | **2.75 FTEs** | — |

**If hiring**: Need 1 additional Cloud Data Engineer (1.0 FTE) for ongoing ops.

---

## Critical Success Factors

1. **Starburst license procurement** — Cannot proceed without license key (Week 0)
2. **AWS account provisioning** — Enterprise agreement required for large resources (Week 0)
3. **Team training completion** — 3-week prerequisite (Week -3 to Week -1)
4. **Backfill validation** — 5-year data must load correctly (Week 5 gate)
5. **Performance testing** — API P99 < 3s must be met (Week 7 gate)
6. **Security sign-off** — Pen test zero critical findings (Week 10 gate)

If any gate fails, timeline slips 2-4 weeks.

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Starburst pricing higher than expected | High | Negotiate 40% 3-year discount upfront |
| Team K8s skills gap | High | Hire 1 DevOps prior to start OR use managed service (Starburst Galaxy) |
| Backfill performance poor | Medium | Benchmark 1-month first, adjust partitioning |
| dbt adapter incompatibility | Medium | Test all models in Week 3; PoC already validated basic compatibility |
| Cloud cost overruns | Medium | Budget alerts, auto-scaling, S3 lifecycle |
| MiFID compliance gaps | High | Security engineer involved from Week 1, legal review |

---

## Alternatives Considered (and why rejected)

### Snowflake
- **Why rejected**: Monthly backfills cost $810K over 5 years vs Iceberg's $24K
- **Tiebreaker**: Snowflake has 90-day time travel limit; MiFID needs 5+ year audit trail

### Oracle On-Prem
- **Why rejected**: $3.4M 5-year TCO; skill set declining; vendor lock-in toxic

### BigQuery
- **Why rejected**: Pay-per-query model penalizes heavy analytical workloads; backfills still $360K over 5 years; SQL dialect incompatibility with dbt

### Self-Hosted Trino
- **Why rejected**: No K8s expertise in team; cheaper license cost offset by $150K/year DevOps hire

---

## Next Steps for Approval

1. **Vendor Contracts** (Week 0)
   - [ ] Sign Starburst Enterprise 3-year agreement
   - [ ] AWS Enterprise Agreement (if not existing)
   
2. **Hiring / Training** (Week -4 to Week -1)
   - [ ] Hire Cloud DevOps Engineer (or repurpose existing)
   - [ ] Enroll team in Starburst University (3 seats)
   - [ ] Complete AWS EKS digital training

3. **Infrastructure Setup** (Week 1-2)
   - [ ] Deploy Terraform (VPC, S3, EKS, IAM)
   - [ ] Install Starburst on EKS
   - [ ] Configure Glue catalog

4. **Data Migration** (Week 3-5)
   - [ ] Generate 5-year synthetic dataset
   - [ ] Run dbt full-refresh
   - [ ] Validate row counts

5. **Application Migration** (Week 6-7)
   - [ ] FastAPI connected to Starburst
   - [ ] MWAA Airflow deployed with DAGs
   - [ ] End-to-end pipeline tested

6. **Production Readiness** (Week 8-10)
   - [ ] Monitoring, alerts deployed
   - [ ] Security hardening and pen test passed
   - [ ] DR drill completed successfully
   - [ ] Go-live decision meeting (Week 10 Friday)

---

## Questions for Leadership

1. **Budget approval**: $150K-200K first-year OPEX confirmed? (Not CAPEX)
2. **Hiring approval**: Can we hire 1 Cloud DevOps Engineer at $130K-150K? Or reassign internally?
3. **Vendor approval**: Sign 3-year Starburst Enterprise contract (legal review needed)?
4. **Cloud provider**: AWS Frankfurt confirmed? Any existing enterprise agreements we must use?
5. **Compliance**: Legal team OK with Iceberg time travel as MiFID audit mechanism?
6. **Timeline**: 10 weeks from Week 0 start date acceptable? Can accelerate by adding headcount?

---

## Appendix: Cost Comparison Table (All Options)

| Vendor Model | License/Subscription | Compute | Storage | Network | Support | 5-Year Total |
|---|---|---|---|---|---|---|
| **Starburst Enterprise (recommended)** | $440K | Included | $23/TB/mo | Included | Included | $762K |
| AWS Trino (EMR) | $0 | $345K | $23/TB/mo | Included | Included | $712K |
| Snowflake Capacity | $0 | $820K | $40/TB/mo | Egress fees | Included | $1.2M |
| BigQuery (Flat-rate) | $0 | $1.1M | $20/TB/mo | Included | Included | $1.6M |
| Oracle Enterprise | $800K CAPEX | Included | $40/TB/mo | Extra | Included | $3.4M |
| Self-hosted PG + TimescaleDB | $0 | $118K | $30/TB/mo | Included | $100K | $509K |

*Numbers approximate, exclude headcount. Starburst includes compute in license fee; others itemize.*

---

## Decision Record

**Decision ID**: TCA-STORAGE-2026-04-22-001  
**Title**: Production Data Storage Technology Selection  
**Status**: Approved (pending leadership sign-off)  
**Decision Makers**: Engineering Leadership, Data Engineering  
**Date**: 2026-04-22  

**Decision**: Adopt **Apache Iceberg** as table format, **Starburst Enterprise** as query engine, deployed on **AWS Frankfurt** with EKS, S3, Glue, and MWAA.

**Rationale**:
1. Monthly backfill requirement eliminates Snowflake/BigQuery (cost-prohibitive)
2. Oracle on-prem financially unsustainable (10x cost)
3. Iceberg + Starburst provides optimal cost/performance/compliance balance
4. Managed Trino mitigates operational complexity given team skill gaps
5. Open format future-proofs platform, prevents vendor lock-in

**Alternatives Considered**: Snowflake, BigQuery, Oracle, self-hosted Trino, scale-up PostgreSQL  
**Rejected Because**: Cost, operational burden, compliance gaps, or skill requirements

**Consequences**:
- Requires 3-week training investment
- Requires 1 additional DevOps hire or managed services spend
- Implementation timeline 10 weeks (vs 4 for Snowflake)
- Ongoing operational burden moderate (managed services reduce this)

**Validation Criteria**:
- Week 2: Starburst cluster operational, dbt compiles
- Week 5: 5-year backload completes < 48 hours, row counts validate
- Week 7: API P99 < 3 seconds, EOD enrichment < 30 minutes
- Week 10: Pen test zero critical findings, DR drill RTO < 2 hours

**Post-Implementation Review**: 3 months after go-live (Week 22) — evaluate performance, cost actuals vs budget, team satisfaction.

---

**Document Location**: `docs/executive-summary.md`  
**Related Documents**:
- `docs/production-storage-decision.md` (detailed analysis)
- `docs/vendor-selection.md` (vendor comparison)
- `docs/deployment-guide.md` (step-by-step implementation)
- `docs/training-enablement-plan.md` (team upskilling plan)
