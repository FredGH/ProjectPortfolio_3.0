# Vendor Selection Guide: Managed Trino & Cloud Provider

**Last Updated**: 2026-04-22  
**Purpose**: Guide selection of managed Trino provider and cloud platform for production TCA deployment

---

## Executive Summary

**Decision**: Starburst Enterprise on AWS (EU Frankfurt region)

**Alternative**: AWS Trino on EMR (if budget constraints force reduction)

**Budget Impact**: Starburst adds ~$140K-230K/year vs AWS Trino ~$110K/year. Premium justified by:
- 24/7 Premier support (banking production requirement)
- Advanced Iceberg optimizations
- Fine-grained access control (MiFID data segregation)
- Enterprise features (query auditing, lineage)

---

## Managed Trino Provider Comparison

### 1. Starburst Enterprise — ✅ SELECTED

**Provider**: Starburst Data (starburst.io)  
**Deployment Model**: Self-hosted on EKS (BYOL — bring your own license) OR Starburst Galaxy (SaaS)

**Pricing Model**: Credit-hour based ($1.50-2.50/credit-hour)

**Sample Cluster Configuration** (production estimate):
| Resource | Quantity | Specs | Credits/Hour |
|---|---|---|---|
| Coordinator | 1 | 4 vCPU, 32GB RAM | 4 |
| Worker | 3 (min) to 6 (max auto-scale) | 16 vCPU, 128GB RAM | 16 each |
| **Total (3 workers)** | | | **52 credits/hour** |
| **Monthly (720h × 52)** | | | **~3,744 credit-hours** |
| **Monthly Cost @ $2.00/credit** | | | **~$7,488** |
| **Annual** | | | **~$89,856** |

With 3-year commitment (30% discount): **~$63K/year**

**Annual Support (Premier 24/7)**: ~$30K/year (separate from platform license)

**Total First-Year Cost**: License + support = ~$93K  
**Subsequent Years**: ~$93K/year (recurring license + support)

**Key Features**:
- ✅ Native Iceberg support (best-in-class)
- ✅ Cost-based optimizer (CBO) with table statistics
- ✅ Fine-grained access control (row/column level security)
- ✅ Query audit logging (who queried what, when, how many rows)
- ✅ Resource groups (quotas per team/user)
- ✅ Automatic cluster recovery
- ✅ Enterprise SLA: 99.9% uptime, 1-hour P1 response

**Enterprise Add-Ons** (may be required for banking):
- Starburst Security: Integration with LDAP/Active Directory, SAML SSO — +15% to license
- Starburst Governance: Data lineage, catalog integration, policy engine — +20% to license

**With security + governance**: +35% → **~$125K/year**

**EU Data Residency**: Deployable in Frankfurt, Ireland, London, Paris (all EU regions)

**Contract Minimum**: 1-year license term (discounts for 3-year)

**Implementation Effort**:
- Starburst handles software installation via Helm
- Customer provides EKS cluster and networking
- Integration time: 2-3 days for basic cluster, 1 week for full security config

**Vendor Reputation**: Market leader, founded by original Presto creators. Used by large banks (Goldman Sachs, JPMorgan, ING).

---

### 2. AWS Trino on EMR — ❌ ALTERNATIVE (Budget Option)

**Provider**: Amazon Web Services (EMR = Elastic MapReduce)  
**Deployment Model**: Managed service (EMR on EC2, serverless option available)

**Pricing Model**: Per vCPU-hour ($0.20-0.42/vCPU-hour depending on region/instance)

**Sample Cluster Configuration**:
| Resource | Quantity | Specs | vCPU-Hour | Monthly vCPU-Hours | Cost |
|---|---|---|---|---|---|
| Master (coordinator) | 1 | r5.4xlarge (16 vCPU) | $0.20 | 16 × 720h = 11,520 | $2,304 |
| Worker | 3 | r5.4xlarge (16 vCPU) | $0.20 | 48 × 720h = 34,560 | $6,912 |
| **Total** | | | | **46,080 vCPU-hr** | **$9,216** |

Annual compute: ~$110K

**Additional EMR charges**: ~$0.015/GB for HDFS (negligible for Iceberg on S3) + $0.10/GB for EMRFS (also minimal)

**Total Annual**: ~$115K (compute only)

**Support**: AWS Business Support included (or Enterprise for additional fee)

**Key Features**:
- ✅ Iceberg support via AWS SDK (EMR 6.15+)
- ✅ Glue Data Catalog integration (native)
- ✅ Auto-scaling policies (scale workers based on queue)
- ❌ No fine-grained access control (must integrate with Ranger manually if needed)
- ❌ No query lineage/auditing out-of-box
- ❌ Community support (not enterprise Trino vendor)

**Enterprise Gaps vs Starburst**:
- Missing Starburst's CBO optimizations (uses open-source Trino CBO, less mature)
- Fine-grained access via Ranger (complex setup, separate open-source project)
- No built-in query result caching (Starburst has distributed cache)

**EU Data Residency**: Frankfurt, Ireland, London, Paris, Stockholm, Milan

**Contract Minimum**: None (pay-as-you-go)

**Implementation Effort**:
- EMR cluster creation: 1-2 hours via console/CLI
- Iceberg connector configuration: 1 day
- Security hardening: 3-5 days (VPC endpoints, IAM roles, Kerberos if required)
- Total: 1 week for basic, 2 weeks for hardened

**When to Choose AWS Trino**:
- Budget < $120K/year for compute (compliance team OK with open-source)
- Team already skilled in Trino (no need for Starburst training)
- Do NOT need fine-grained row/column security or query lineage
- Have internal tooling to build what Starburst provides out-of-box

---

### 3. Starburst Galaxy (SaaS) — ❌ ALTERNATIVE (Fully-Managed)

**Provider**: Starburst Data (hosted SaaS)  
**Deployment Model**: Fully-managed service (no K8s, no EKS)

**Pricing Model**: Per terabyte scanned + compute credits (similar to Snowflake model)

**Estimated Cost**:
- Storage processed: 10TB × ~10 scans/month (analytics + backfills) = 100TB scanned/month
- Compute: 52 credits/month (same as self-hosted)
- Price: ~$2.50/credit-hour + $5/TB scanned
- Compute: 52 × $2.50 × 720h? Wait — Galaxy SaaS is per-session, not 24/7

**Galaxy Pricing Actually**:
```bash
# Per query session (typically 1-4 hours)
1 session (4 credits/hour × 4 hours × $2.50) = $40 per session
Monthly sessions:
  - Daily EOD: 30 sessions = $1,200
  - Monthly backfill: 1 session (4h) = $40
  - Ad-hoc analyst queries: 100 sessions = $4,000
  Total/month: ~$5,240
Total/year: ~$62,880
+ Storage scan fees: 100TB × $5 = $500/month = $6,000/year
+ Support: $20K/year
Total: ~$89K/year
```

Wait — Starburst Galaxy is actually **cheaper** than self-hosted if usage is not 24/7.

**Correction**: Galaxy more cost-effective for intermittent workloads. For 24/7 EOD window (8 hours daily), cost is higher.

**Revised Galaxy Estimate for 8h/day EOD + backfills**:
```bash
Daily EOD: 8 hours × 52 credits/day × $2.50/credit × 30 days = $31,200/month
Backfills: 12 × 4h × 52 credits × $2.50 = $6,240/month
Ad-hoc: 100 sessions × 4h × 52 × $2.50 = $52,000/month
Total: ~$89,440/month = ~$1.07M/year ❌ TOO EXPENSIVE
```

**Verdict**: Only viable for light/occasional use. Not suitable for production TCA with daily EOD + monthly backfills.

---

### 4. Self-Hosted Trino (Open Source) — ❌ NOT RECOMMENDED

**Provider**: Self-managed on EKS (open-source Trino)  
**Deployment Model**: BYOK — bring your own Kubernetes

**Pricing**: Zero license cost. Only EC2/EKS costs (~$40K/year as computed earlier)

**What You Lose vs Starburst**:
- No enterprise support (rely on community Slack/GitHub issues)
- No Starburst-specific Iceberg optimizations (partition handling, file statistics)
- No fine-grained access control (use open-source Ranger integration — complex)
- No query auditing lineage (must ship logs to external system)
- No CBO tuning (Trino's open-source CBO less mature)
- No professional services for tuning
- Self-upgrade burden (quarterly Trino releases)
- Self-monitoring/alerting setup (not pre-built dashboards)

**Team Capability Gap**: No K8s experience → 0.5-1.0 FTE senior DevOps hire at $150K/year. **Total TCO similar to Starburst but with higher operational risk**.

**When to Consider**: If budget extremely tight and team already has strong Trino/K8s expertise.

---

## Cloud Provider Selection

All three major clouds support Iceberg + Trino equally well from a **capability** perspective. Decision factors:

### AWS (Frankfurt/London) — ✅ SELECTED

**Pros**:
- Most mature Iceberg ecosystem (S3, Glue Catalog, EMR, EKS, MWAA)
- Best-in-class IAM and security tooling (KMS, CloudTrail, Security Hub)
- Largest talent pool (AWS-certified engineers easiest to hire)
- Cost management tools mature (Budgets, Cost Explorer, rightsizing recommendations)
- Frankfurt region available (EU compliance)
- Existing enterprise agreement? If yes, further discount.

**Cons**:
- Slightly more expensive storage than Azure ($23/TB vs $18/TB)
- AWS pricing complexity (requires专人 to manage cost optimization)

**Storage Cost (5 years)**: $23/TB × 10TB = $230/year = $1,150/5yr  
**With 30% growth/year**: Year 5 storage ~$4,600/year

---

### Azure (Frankfurt/France Central) — ❌ ALTERNATIVE

**Pros**:
- Slightly cheaper storage: $18/TB/month Hot tier
- Microsoft enterprise agreements common in German corporate environment
- Azure Active Directory integration (if PrivateBank uses AD)
- Azure Data Lake Storage Gen2 (ADLS Gen2) fully compatible with Iceberg

**Cons**:
- Smaller Iceberg community; fewer blog posts/tutorials
- Azure's managed Trino (HDInsight) less mature than AWS EMR
- Fewer Azure data engineers available in market (harder to hire)

**Storage Cost (5 years)**: $18/TB × 10TB = $180/year; Year 5 ~$3,600/year with growth

**Verdict**: Choose Azure if:
- Existing Microsoft Enterprise Agreement provides Azure credits (discount >20%)
- IT department standardizes on Microsoft stack
- AD integration critical requirement

---

### GCP (Frankfurt/Zurich) — ❌ ALTERNATIVE

**Pros**:
- Most cost-effective compute (Dataproc ~$0.15/vCPU-hour — 25% cheaper than AWS)
- Zurich region available (Swiss privacy laws even stricter than EU)
- BigQuery integration if hybrid workloads later

**Cons**:
- Smallest market share, hardest to hire GCP data engineers
- GCP's managed Trino (Dataproc) requires more configuration vs Starburst
- Tooling less mature (no direct Glue equivalent)

**Storage Cost**: $20/TB/month = $200/year; Year 5 ~$4,000/year

**Compute Savings vs AWS**: Dataproc 25% cheaper → Starburst worker nodes on GCP might get discount? Unclear.

**Verdict**: Choose GCP if:
- Zurich region required for Swiss-German legal data residency
- Already GCP-shop (existing GCP commitments)
- Cost optimization paramount (compute cheaper)

---

## Multi-Cloud Consideration

**Should you multi-cloud?** No. Iceberg is portable, but management complexity skyrockets.

- Managing EKS on AWS + GKE on GCP + AKS on Azure = 3x operational burden
- Security policies must be replicated 3x
- Networking/VPC peering complexity
- Training team on 3 cloud platforms unrealistic

**Cloud lock-in risk with Iceberg**: Low. Even if you choose AWS today, you can migrate to Azure/GCP in 2-4 weeks by:
1. Copy S3 data to ADLS (Azure Data Factory) or GCS (Storage Transfer Service)
2. Deploy Starburst in new region
3. Update dbt profiles
4. Redirect FastAPI connection strings

Iceberg manifest files are cloud-agnostic.

---

## Vendor Evaluation Rubric

Score each vendor 1-5 (1=poor, 5=excellent)

| Criterion | Weight | Starburst Enterprise | AWS Trino | Self-Hosted |
|---|---|---|---|---|
| **5-year TCO** | 25% | 3 (premium) | 5 (cheapest) | 4 (low license) |
| **Enterprise support SLA** | 20% | 5 (24/7 Premier) | 3 (AWS Business) | 1 (community) |
| **Iceberg feature completeness** | 15% | 5 (native) | 4 (good) | 3 (OSS) |
| **Security (fine-grained access)** | 15% | 5 (built-in) | 2 (manual Ranger) | 2 (manual) |
| **Team skills required** | 10% | 4 (managed) | 3 (K8s+Trino) | 1 (experts needed) |
| **Implementation time** | 10% | 4 (1 week) | 3 (2 weeks) | 2 (1 month) |
| **Banking compliance features** | 5% | 5 (audit, lineage) | 2 (custom) | 2 (custom) |
| **Weighted Score** | 100% | **4.25** | **3.45** | **2.15** |

**Starburst wins decisively** on features critical to banking (support, security, compliance). AWS Trino competitive on cost but behind on enterprise readiness.

---

## Negotiation Tips with Starburst

### Tip 1: Annual Commitment Discount
- Standard: 30% off list price for 3-year term
- Push for: 40% discount if also signing 5-year enterprise agreement
- Target: $120K/year → $72K/year after 40% discount

### Tip 2: Bundle with Training & Services
- Include 5 days of Starburst consulting in license cost (normally $25K)
- Ask for free Starburst University seats (3-5 seats, $5K each)

### Tip 3: Cloud Commitment Transfers
- If AWS enterprise agreement exists, Starburst may credit AWS spend toward license
- Ask: "Can we use AWS RI credits for Starburst Marketplace?"

### Tip 4: Growth Cap
- Negotiate fixed annual fee for 3 years despite data growth
- Example: $120K/year for 3 years regardless of TB growth (predictable budgeting)

### Tip 5: Performance Guarantee
- SLAs: 99.9% uptime, <5s query P99 for 90th percentile queries
- Penalties: Service credits if SLA missed (typical 5-10% refund)

**Expected Final Price**: With negotiation, expect **$100K-115K/year** for Starburst Enterprise + Security + Governance bundles, 3-year term.

---

## Decision Recommendation Matrix

| Decision Factor | Starburst Enterprise | AWS Trino | Recommendation |
|---|---|---|---|
| **Use Starburst if**: | | | |
| Budget > $120K/year | ✅ | ⚠️ Overkill | ✅ Starburst |
| Need 24/7 enterprise support | ✅ | ⚠️ Limited | ✅ Starburst |
| Fine-grained row/column security required | ✅ | ❌ Complex | ✅ Starburst |
| Team lacks Trino/K8s expertise | ✅ (managed software) | ❌ (still need K8s skills) | ✅ Starburst |
| Audit/compliance requires query lineage | ✅ | ❌ Build yourself | ✅ Starburst |
| **Use AWS Trino if**: | | | |
| Budget < $100K/year | ❌ Expensive | ✅ Cheaper | ❌ AWS |
| Already have AWS Enterprise Support | ✅ Yes, but extra cost | ✅ Included | ⚠️ Trade-off |
| Team has strong Trino open-source experience | ✅ Yes | ✅ Yes | ⚠️ Tie |
| Willing to self-support (community) | ❌ Waste of license | ✅ OK | ⚠️ AWS |
| **Final Verdict** | **✅ CHOOSE STARBURST** | | |

**Even with 30% higher cost, Starburst delivers 3-5x enterprise value** for banking production.

---

## Implementation Timeline Impact

| Phase | Starburst Timeline | AWS Trino Timeline | Delta |
|---|---|---|---|
| Procurement | 2 weeks (contract negotiation) | 1 week (AWS account exists) | +1 week |
| Cluster deployment | 2 days (Helm install) | 1 day (EMR cluster) | +1 day |
| Security config | 3 days (Ranger not needed) | 5 days (Ranger setup) | -2 days |
| Training | 3 days (Starburst University) | 5 days (self-study Trino internals) | -2 days |
| Performance tuning | 2 days (CBO auto) | 5 days (manual stats, hints) | -3 days |
| Production hardening | 3 days (built-in features) | 7 days (build monitoring/alerting) | -4 days |
| **Total** | **2.5 weeks** | **4 weeks** | **-1.5 weeks faster** |

Starburst actually **speeds implementation** despite higher cost due to enterprise tooling.

---

## Next Steps

1. **Week 0 Actions**:
   - [ ] Contact Starburst sales: request quote for 4-node cluster, Premier support, Security + Governance bundles, 3-year term
   - [ ] Parallel: Set up AWS account (if not existing) in Frankfurt region
   - [ ] Schedule technical deep-dive with Starburst solutions architect (free)
   - [ ] Book Starburst University training seats (3 people) — dates in Week 1-2

2. **Week 1**:
   - [ ] Sign Starburst Enterprise Agreement
   - [ ] Provision AWS infrastructure (Terraform, VPC, S3, EKS, IAM)
   - [ ] Run AWS training (online) concurrently

3. **Week 2**:
   - [ ] Install Starburst on EKS
   - [ ] Configure Glue Data Catalog
   - [ ] Team attends Starburst University (3-day course)

4. **Week 3**:
   - [ ] Configure security: IAM roles, VPC endpoints, KMS
   - [ ] Load test data, validate Iceberg write performance
   - [ ] Begin dbt adapter migration

---

## Appendix: Cost Comparison Table (5 Years)

| Cost Item | Starburst | AWS Trino | Self-Hosted (w/ hire) |
|---|---|---|---|
| License/subscription | $450K (3yr × $125K/yr) | Included in compute | $0 |
| Compute (workers × 3yr) | Included in license | $345K (3yr × $115K/yr) | $120K (EKS EC2) |
| EKS cluster management | $54K (3yr × $18K/yr) | $54K | $54K |
| Support (Premier vs Business) | Included in license | Included (AWS Business) | Internal team: $450K |
| Training/consulting | $27K (one-time) | $45K (deeper Trino training) | $80K (hire senior) |
| DevOps FTEs (maintenance) | $45K/year × 3yr = $135K (part-time) | $90K/year × 3yr = $270K (full-time needed) | $450K (2 FTEs) |
| **Total 3-year** | **$666K** | **$714K** | **$754K** |
| **Total 5-year** | **~$1.1M** | **~$1.2M** | **~$1.4M+** |

**Even with salary costs included, Starburst remains 10-20% cheaper than self-hosting** when full-time DevOps expertise required. AWS Trino cheapest but missing critical enterprise features.

---

## Document Control

**Version**: 1.0  
**Approved By**: [Pending — Data Engineering Leadership]  
**Next Review**: After 6 months production operation
