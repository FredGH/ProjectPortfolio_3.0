# Managed Trino Services: What They Are & What They Offer

**Document Type**: Technical Reference  
**Last Updated**: 2026-04-22  
**Audience**: Engineering leadership, platform architects, DevOps teams  
**Related Decision**: TCA production storage selection — Apache Iceberg + Starburst Enterprise

---

## TL;DR

**Managed Trino Service** = You pay a vendor (Starburst) to operate the Trino query engine software for you. You still pay **AWS** (or Azure/GCP) for the underlying infrastructure (servers, storage, networking).

**Analogy**: AWS provides the **kitchen** (ovens, refrigeration, building). Starburst provides the **chef** (expert at cooking with Trino). You pay both.

---

## Core Concept: Two-Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2: Query Engine (Software)                          │
│  Provider: Starburst (or AWS if using EMR)                  │
│  What you get: Trino binary, enterprise features, support   │
│  You pay: License fee ($110K-180K/year)                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ runs ON
┌───────────────────────────▼─────────────────────────────────┐
│  LAYER 1: Infrastructure (Hardware + Orchestration)        │
│  Provider: AWS / Azure / GCP                               │
│  What you get: EC2 instances, S3 storage, EKS, VPC, IAM    │
│  You pay: Cloud bill (~$25-40K/year)                        │
└─────────────────────────────────────────────────────────────┘
```

**Managed Trino only manages Layer 2**. You are responsible for Layer 1 (unless you choose fully-managed SaaS like Starburst Galaxy).

---

## What Managed Trino Service ACTUALLY Manages

### ✅ Included in Managed Service

| Category | Specific Items | Who Operates |
|---|---|---|
| **Software lifecycle** | Trino version upgrades, security patches, bug fixes | Starburst |
| **Enterprise features** | Fine-grained access control, query lineage, CBO optimizer, cache | Starburst (license unlocks) |
| **Support** | 24/7 incident response, query optimization advice, TAM access | Starburst |
| **Best practices** | Configuration templates, schema design reviews, performance tuning guidance | Starburst |
| **HA/availability** | Coordinator failover, rolling upgrades with zero downtime | Starburst (software) + You (infrastructure) |
| **Monitoring dashboards** | Pre-built Grafana dashboards for cluster health | Starburst (templates) + You (deploy) |

### ❌ NOT Included (Your Responsibilities)

| Category | Specific Items | Who Operates |
|---|---|---|
| **Infrastructure provisioning** | Create VPC, EKS cluster, S3 buckets, IAM roles | You (Terraform) |
| **Cluster lifecycle** | Create/delete K8s cluster, scale node groups, patch OS | You (DevOps) |
| **Cost management** | Set budgets, monitor cloud spend, optimize instance types | You (FinOps) |
| **Data ingestion** | Build pipelines to load raw data into S3/iceberg | You (Data Engineering) |
| **SQL development** | Write dbt models, optimize queries, schema design | You (Analysts/Engineers) |
| **Application layer** | FastAPI backend, BI tools, reports | You (Backend/BI Team) |
| **Disaster recovery** | Cross-region replication, backup testing, run DR drills | You (SRE) |
| **Security hardening** | VPC endpoints, security groups, KMS key rotation policies | You (Security) |
| **User provisioning** | Add/remove users from LDAP, manage group membership | You (IT/Identity Team) |

---

## Managed Service Deployment Models

### Model A: Enterprise License on Your Infrastructure (Most Common for Banks)

**Example**: Starburst Enterprise on your AWS EKS cluster

**What you get from Starburst**:
- Software license (Trino + enterprise plugins)
- 24/7 support SLA
- Access to Starburst University training
- Starburst Console (web UI for cluster management)
- Query lineage and auditing features
- Fine-grained access control engine

**What you provide**:
- AWS account + billing
- EKS cluster (Kubernetes)
- EC2 worker nodes (r5.4xlarge instances)
- S3 buckets for data
- Glue Data Catalog for Iceberg metastore
- Networking (VPC, subnets, security groups)

**Cost structure**:
- Starburst: $110K-150K/year (license + support)
- AWS: $25K-40K/year (infrastructure)
- **Total**: $135K-190K/year

**Pros**: Full control over infrastructure, can use existing AWS enterprise agreements, portable (can move to Azure/GCP with same license).

**Cons**: Need K8s skills to operate EKS (or hire DevOps).

---

### Model B: Fully-Managed SaaS (Starburst Galaxy)

**Example**: Starburst Galaxy — Starburst hosts everything in their cloud

**What you get from Starburst**:
- Everything (infrastructure + software)
- JDBC/HTTPS endpoint to connect
- Pay-per-query or per-compute-hour pricing
- No AWS bill (Starburst includes infra in their price)

**What you provide**:
- SQL queries (dbt, BI tools)
- Data modeling decisions
- Cost monitoring (your queries drive cost)

**Cost structure**:
- Starburst: $180K-280K/year (consumption-based, includes infra)
- **No separate AWS bill**

**Pros**: Zero infrastructure ops, fastest time-to-value, truly serverless.

**Cons**: More expensive (~30-40% premium), less control over infrastructure, vendor lock-in more severe.

**Use case**: Companies with zero platform engineering team, budget >$250K/year.

---

### Model C: Cloud Provider's Managed Trino (AWS EMR, Azure HDInsight)

**Example**: AWS Trino on EMR — AWS manages the cluster, uses open-source Trino

**What you get from AWS**:
- EMR provisions EC2 instances, installs Trino
- Auto-scaling (based on YARN queue)
- Integration with S3, IAM, CloudWatch

**What you get from Trino**:
- Nothing — you use open-source Trino (no additional license)
- Community support only (Slack/GitHub)

**Cost structure**:
- AWS: $110K/year (EC2 + EMR management fee)
- **No separate software license**
- **Total**: ~$110K/year (cheapest option)

**Pros**: Cheapest, native AWS integration, simple billing.

**Cons**: No enterprise features (no fine-grained security, no lineage, basic CBO), AWS support only (not Trino experts), community-based troubleshooting.

**Use case**: PoC, startups, teams with strong Trino expertise willing to self-support.

---

## What Starburst Enterprise Specifically Adds (vs Open-Source Trino)

These are the **enterprise features** you pay the license fee for:

### 1. Fine-Grained Access Control
**Open-source Trino**: Only basic role-based permissions (user can SELECT on schema.table)

**Starburst Enterprise**:
```
Policy example:
  Role: analyst
  Table: tca_results
  Row filter: trader_id = current_user()  -- each analyst sees only their own rows
  Column mask: credit_card_number → mask('XXXX-XXXX-XXXX-%', last4)  -- PII hidden
```
**Value**: MiFID compliance, data segregation, GDPR privacy by design.

---

### 2. Query Lineage & Auditing
**Open-source Trino**: Query history in `system.runtime.queries` table (basic: user, query text, start time)

**Starburst Enterprise**:
- Visual lineage graph: Show how `tca_results` derived from `fct_fills` → `raw.fills`
- Data provenance: Which source system produced each row
- Impact analysis: If `fct_fills` schema changes, what downstream reports break?
- Regulatory audit: Prove who accessed sensitive data and when (retained 7 years)

**Value**: Compliance (MiFID Article 27 — transaction records), data governance, impact analysis.

---

### 3. Advanced Cost-Based Optimizer (CBO)
**Open-source Trino**: Basic statistics (row count, null fraction) optional, manual collection

**Starburst Enterprise**:
- Automatic statistics collection (nightly)
- Advanced join ordering (multi-way join optimization)
- Filter pushdown to Iceberg (only scan relevant Parquet files)
- Join reordering, aggregation pushdown, top-N optimization
- Better query plans → 2-10x faster queries on large tables

**Value**: Query performance, reduced S3 scan costs, better user experience.

---

### 4. Distributed Query Result Cache
**Open-source Trino**: No cache — every query re-reads from S3

**Starburst Enterprise**:
- Cache query results in memory across workers
- Repeated queries (daily reports) hit cache instead of scanning Iceberg
- Cache invalidation on table changes (statistics-aware)
- Typical hit rate: 30-60% for recurring reports

**Value**: Faster reports, lower S3 GET request costs, reduced CPU on workers.

---

### 5. Resource Management & Multi-Tenancy
**Open-source Trino**: Basic query queues (FIFO)

**Starburst Enterprise**:
```
Resource groups:
  Group: etl_pipeline
    CPU quota: 80% of cluster
    Max concurrent queries: 20
    Priority: HIGH (preempt others if idle)
  
  Group: analysts
    CPU quota: 20% of cluster
    Max concurrent queries: 5 per user
    Max runtime: 30 min (kill long queries)
    Priority: LOW
```
**Value**: Prevent ETL jobs from starving analysts, enforce SLAs, chargeback/showback.

---

### 6. Enhanced Iceberg Connector
**Open-source Trino Iceberg connector**: Basic read/write support

**Starburst Enterprise Iceberg connector**:
- Faster metadata caching (metadata files cached in memory)
- Better file filtering (skip irrelevant Parquet files more aggressively)
- Z-ordering optimization awareness
- Incremental metadata refresh (only changed manifests)
- Support for Iceberg's branching/ tagging (future)

**Value**: Query performance on Iceberg 2-5x faster than OSS connector.

---

### 7. Security Integrations
**Open-source Trino**: LDAP bind, password file

**Starburst Enterprise**:
- SAML 2.0 SSO (Okta, Azure AD, Auth0)
- Kerberos (for Windows-integrated environments)
- JWT tokens (for REST API)
- OAuth2 with OIDC
- Integration with external policy engines (Open Policy Agent)

**Value**: Single sign-on for analysts, meet corporate security standards.

---

### 8. High Availability & Operations
**Open-source Trino**: Manual coordinator failover, no built-in redundancy

**Starburst Enterprise**:
- Multiple coordinators (active-standby)
- Automatic worker replacement (failed pod → new pod)
- Configuration validation before upgrades
- Rolling restart with zero downtime
- Cluster health checks + self-healing

**Value**: Production SLAs (99.9% uptime), less manual intervention.

---

## Real Cost Comparison: Starburst vs AWS EMR Trino

### Scenario: 4-node cluster, 24/7 operation, 730h/month

| Cost Item | Starburst Enterprise (on EKS) | AWS Trino (EMR) |
|---|---|---|
| **Software license** | $110K/year | $0 |
| **EC2 workers** (3 × r5.4xlarge) | $9,216/mo × 12 = $110K | $9,216/mo × 12 = $110K |
| **EKS control plane** | $73/mo × 12 = $876 | $73/mo × 12 = $876 |
| **EMR management fee** | N/A (you manage EKS) | $0.10/EC2-hr × 3 × 730 = $219 |
| **Support** | Starburst Premier (included) | AWS Business ($100/mo? minimal) |
| **Enterprise features** | ✅ Full suite | ❌ Basic OSS only |
| **Fine-grained security** | ✅ Built-in | ❌ Manual Ranger integration (weeks of work) |
| **Query lineage** | ✅ Built-in | ❌ Must build custom logging |
| **CBO optimizer** | ✅ Advanced | ⚠️ Basic (OSS version) |
| **Support SLA** | 1-hour P1 response | 1-hour P1 (but AWS not Trino experts) |
| **Total Year 1** | **~$221K** | **~$111K** |

**Gap**: $110K/year license premium for Starburst.

**Is it worth it?** For TCA platform with monthly backfills and MiFID compliance:
- Yes: Fine-grained security needed for trader data segregation
- Yes: Audit lineage required for regulators
- Yes: 24/7 Trino-specific support critical for production
- Yes: CBO saves hours of query optimization work

**Net value**: Starburst features save ~$150K/year in DBA/DevOps time, reduce compliance risk, improve performance. Premium is justified.

---

## When to Choose Each Model

### Choose Starburst Enterprise on Your Cloud (Model A) if:
- ✅ Already have AWS/Azure/GCP account and VPC setup
- ✅ Have or willing to hire 1 DevOps for K8s/EKS
- ✅ Need enterprise features (row-level security, lineage, cache)
- ✅ Want 24/7 Trino-specific support
- ✅ Plan to run production for 3+ years
- ✅ Budget $150K-200K/year total

**TCA platform fits here perfectly.**

---

### Choose Starburst Galaxy / Fully-Managed SaaS (Model B) if:
- ✅ No platform engineering team at all
- ✅ Budget > $250K/year (accept 30% premium for zero ops)
- ✅ Want fastest time-to-production (deploy in days, not weeks)
- ✅ Don't need to own infrastructure (don't want AWS bill visibility)
- ✅ Consumption pricing OK (predictable usage, not spiky)

**TCA doesn't fit**: Monthly backfills make consumption expensive; prefer fixed license fee.

---

### Choose AWS EMR Trino (Model C) if:
- ✅ Budget < $120K/year (license premium breaks budget)
- ✅ Have strong Trino expertise internally (can self-support)
- ✅ Don't need fine-grained security or lineage (simple use case)
- ✅ OK with community support (Slack response in hours, not minutes)
- ✅ Prototype/PoC or non-production workload

**TCA doesn't fit**: Regulatory compliance requires enterprise features only Starburst provides.

---

## Frequently Asked Questions

### Q: Can I start with AWS EMR Trino and upgrade to Starburst later?

**A**: Yes, but migration effort significant:
1. Rebuild cluster with Starburst Helm chart (1-2 days)
2. Migrate configuration (users, resource groups) to Starburst format
3. Retrain team on Starburst Console
4. No data migration needed (Iceberg tables remain)
5. Total downtime: 4-8 hours

**Recommendation**: Start with Starburst if you know you'll need enterprise features. Migrating mid-project adds risk.

---

### Q: Does Starburst manage my S3 costs?

**A**: No. Starburst has no control over AWS billing. You pay:
- AWS directly for storage (S3), compute (EC2), network (data transfer)
- Starburst directly for software license

Starburst provides cost monitoring (queries scanned, bytes processed) but cannot reduce your AWS bill.

---

### Q: What happens if Starburst goes out of business?

**A**: You have options:
1. **Continue with open-source Trino**: Starburst Enterprise is Trino + proprietary plugins. You can uninstall Starburst software, run OSS Trino on same EKS cluster. Lose enterprise features but keep data.
2. **Export Iceberg tables**: Your data is in S3 as Parquet files — portable to any query engine (Databricks, DuckDB, even rewrite to Redshift).
3. **License transfer**: Enterprise contracts often allow source code escrow; can continue self-support.

**Iceberg format protects you from vendor lock-in** — cannot say same for Snowflake.

---

### Q: Do I need to manage Kubernetes myself if I use Starburst?

**A**: Yes, for Model A (Enterprise on your cloud). Starburst provides:
- Helm chart for installation
- Configuration templates
- Upgrade scripts

But you:
- Create EKS cluster (or GKE/AKS)
- Monitor node health
- Apply OS security patches
- Scale node groups
- Manage networking (VPC, security groups)

**If you don't want K8s ops**, choose:
- Model B: Starburst Galaxy (fully SaaS) OR
- Model C: AWS EMR (AWS manages K8s/EC2 for you, but less features)

---

### Q: How does support work? Who do I call when something breaks?

**Scenario 1: Starburst coordinator pod crashes**
- **Call**: Starburst support (P1, 1-hour response)
- **They do**: Debug JVM heap, check config, provide fix or workaround
- **You do**: Apply fix to Helm values, restart pod (they guide you)

**Scenario 2: EC2 spot instance terminated, workers lost**
- **Call**: AWS support (if EC2 issue) OR self-heal (K8s auto-replaces)
- **They do**: Replace instances automatically via Auto Scaling Group
- **You do**: Update node group to use on-demand (if spot too risky)

**Scenario 3: Query suddenly slow (P99 30s instead of 2s)**
- **Call**: Starburst TAM (Technical Account Manager) for optimization review
- **They do**: Analyze query plan, suggest Z-order or stats refresh
- **You do**: Implement recommendations in dbt models

**Dual support model**: Starburst for Trino software issues, AWS for EC2/S3/VPC issues. Starburst Premier often includes coordination with AWS enterprise support if needed.

---

### Q: What's the difference between Starburst and Presto/Trino?

**History**:
- **Presto**: Original project at Facebook (open-source)
- **Trino**: Fork of Presto (same creators left Facebook, re-branded)
- **Starburst**: Commercial company founded by Trino creators. Starburst Enterprise = Trino + proprietary plugins + support.

**Starburst Enterprise** is **not a fork** — it's Trino with additional closed-source plugins (fine-grained access control, lineage, cache). Core SQL engine is Trino.

**Open-source Trino** = Starburst without enterprise plugins. You can compile from GitHub, no license needed.

---

## Conclusion

**Managed Trino Service** (specifically Starburst Enterprise) provides:

✅ **Production-grade Trino** with enterprise security, governance, and 24/7 support  
✅ **Software operations handled** by Trino experts (upgrades, patches, HA)  
✅ **You retain control** over infrastructure (AWS), data (S3), and SQL logic (dbt)  
✅ **Cost premium justified** for regulated industries (banking, healthcare) needing fine-grained security and audit trails  

**You do NOT get**:
❌ Infrastructure management (you still pay AWS bill)  
❌ Data engineering (build pipelines yourself)  
❌ Application development (FastAPI is your code)  

**For TCA platform**: Starburst Enterprise on AWS EKS = best balance of cost, control, and operational simplicity given team's K8s skill gap.

---

## Further Reading

- Starburst product page: https://starburst.io/product/
- Starburst Enterprise documentation: https://docs.starburst.io/
- Trino open-source documentation: https://trino.io/docs/current/
- Comparison: Starburst vs AWS EMR Trino: https://starburst.io/resources/white-papers/starburst-vs-emr/
