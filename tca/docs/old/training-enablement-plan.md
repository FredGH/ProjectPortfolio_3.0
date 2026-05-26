# TCA Platform: Training & Team Enablement Plan

**Effective Date**: 2026-04-22  
**Target Audience**: Data Engineering Team (3 engineers), Data Analysts (2), DevOps (1)  
**Total Seats**: 6  
**Duration**: 3 weeks ( intensive , prior to Phase 1 kickoff)

---

## Training Philosophy

Given team has **no Kubernetes/Trino/Iceberg experience**, training must be:
1. **Hands-on-first** — every concept accompanied by labs
2. **Production-focused** — skip advanced topics not needed for TCA
3. **Progressive** — build from basics to advanced in 3 weeks
4. **Certification-aligned** — prepare for Starburst/AWS certifications (optional)

**Training Goal**: By end of Week 3, team can:
- Deploy and operate Starburst cluster on EKS
- Troubleshoot Iceberg table issues (compaction, snapshots)
- Optimize dbt models for Iceberg performance
- Own production backfill pipeline
- Respond to P1 incidents (cluster down, query timeouts)

---

## Week 1: Cloud Infrastructure & Kubernetes

### Day 1-2: AWS Fundamentals for Data Engineers (16 hours)

**Instructor**: AWS Digital Training (free online) + internal AWS-certified engineer (if available)

**Topics**:
1. VPC Networking (4h)
   - VPC, subnets (public/private), route tables, NAT gateways, security groups, NACLs
   - Hands-on: Create VPC with 3 private subnets (matching Terraform)
   - Lab: Deploy EC2 in private subnet, access via Session Manager (no SSH)

2. IAM & Security (4h)
   - IAM users/groups/roles, policies (JSON), MFA
   - Service-linked roles (EKS needs these)
   - KMS encryption basics (symmetric keys, key policies)
   - Lab: Create IAM role for EKS cluster, attach S3/KMS policies

3. S3 & Storage (4h)
   - Bucket policies, SSE-S3 vs SSE-KMS, versioning, lifecycle
   - Storage classes (Standard, IA, Glacier Deep Archive)
   - Requester Pays, VPC endpoints (private S3 access)
   - Lab: Create bucket with KMS encryption, upload 1GB file, verify encryption

4. EKS Introduction (4h)
   - What is Kubernetes? Pods, Deployments, Services, ConfigMaps, Secrets
   - EKS architecture: Control plane (AWS managed) + worker nodes (your EC2)
   - eksctl CLI vs Terraform
   - Lab: `eksctl create cluster --name test-cluster --region eu-central-1`

**Assessment**: Quiz (20 questions, 80% passing). Labs must complete successfully.

**Deliverable**: Each engineer has AWS account with test VPC/S3/EKS cluster they created.

---

### Day 3: Kubernetes Core Concepts (8 hours)

**Instructor**: Internal DevOps lead or external K8s trainer

**Topics**:
1. Pods & Containers (2h)
   - pod spec, containers, initContainers, lifecycle hooks
   - Lab: Deploy Nginx pod, exec into it, view logs

2. Deployments & ReplicaSets (2h)
   - Rolling updates, rollbacks, replica count, pod disruption budgets
   - Lab: Deploy Trino coordinator as Deployment, scale to 3 replicas

3. Services & Ingress (2h)
   - ClusterIP, NodePort, LoadBalancer; Ingress controllers
   - Lab: Expose Trino coordinator via LoadBalancer service, test connectivity

4. ConfigMaps & Secrets (1h)
   - Mount as env vars or volumes
   - Lab: Store Starburst license in K8s secret, mount to pod

5. Helm Package Manager (1h)
   - Charts, values.yaml, template syntax
   - Lab: Install Starburst via Helm, customize values

**Hands-on Lab**: Deploy sample Trino cluster (coor + 1 worker) on test EKS cluster using Helm.

**Outcome**: Engineers can `kubectl get pods`, `kubectl logs`, `kubectl scale`, `helm install/upgrade`.

---

### Day 4-5: Starburst Enterprise Deep Dive (16 hours)

**Instructor**: Starburst University (official, requires license purchase)

**Format**: 3-day official Starburst Enterprise course condensed into 2 days internal training

**Day 4 - Starburst Architecture & Administration**:
- Cluster topology: coordinator vs worker
- Connectors: Iceberg, Hive, PostgreSQL, S3
- Memory management (query.max-memory-per-node)
- Resource groups & query queues
- **Lab**: Install Starburst on EKS, connect to Iceberg catalog (S3)

**Day 5 - Starburst Performance & Security**:
- Cost-based optimizer (statistics, joins, ordering)
- Connector pushdown (filter pushdown to Iceberg)
- Fine-grained access control (Role-based, row/column filters)
- Query auditing (system.runtime.queries, system.runtime.nodes)
- **Lab**: 
  1. Create resource group for analysts with CPU quota
  2. Set up row filter: analysts only see their own trader_id
  3. Run EXPLAIN on query, read plan
  4. Query system tables to audit who ran what

**Assessment**: Starburst Enterprise certification exam (optional, ~$200/exam)

**Deliverable**: Starburst cluster running on EKS, team can explain query plan, set resource limits.

---

## Week 2: Apache Iceberg & dbt Integration

### Day 1-2: Iceberg Table Format Deep Dive (16 hours)

**Instructor**: Internal (engineer who completed Iceberg training) or third-party consultant

**Topics**:
1. Iceberg Architecture (4h)
   - Metadata layers: metadata file → manifest list → manifest file → data files (Parquet)
   - Snapshots and time travel (versioning)
   - How Iceberg achieves atomicity without Hive transactions
   - **Lab**: Create Iceberg table, insert 100 rows, query `table$snapshots`

2. Partitioning Strategies (4h)
   - Partition by date + asset_class vs identity partitioning
   - Partition evolution (add/remove partitions without rewrite)
   - Hidden partitioning (partition column generated from data)
   - **Lab**: Create two tables with different partition strategies, query Pruning effectiveness

3. Z-Ordering & File Layout (4h)
   - Why file-level locality matters (avoid scanning all files)
   - Z-ordering vs sorting within files
   - Target file size (512MB-1GB optimal, Iceberg compaction helps)
   - **Lab**: Write unsorted data, query performance poor; Z-order, query fast

4. Compaction & Maintenance (4h)
   - Small files problem: thousands of 10MB files vs few 1GB files
   - `CALL system.merge_iceberg_files(...)` procedure
   - Expiring old snapshots (retain 30 days vs unlimited for MiFID)
   - **Lab**: Write 10,000 small files (1MB each), trigger compaction, verify merged

**Hands-on Project**: Load 1 month of TCA data to Iceberg, optimize layout, benchmark query performance before/after compaction.

**Outcome**: Engineers can design Iceberg schema, tune partitions, schedule compaction via Airflow.

---

### Day 3-4: dbt + Iceberg Integration (16 hours)

**Instructor**: dbt Expert (internal or dbt Labs certified)

**Topics**:
1. dbt-iceberg Adapter (4h)
   - Adapter vs native Trino: dbt abstracts SQL generation
   - Profile configuration: catalog=iceberg vs catalog=hive
   - Materializations: `table` vs `incremental` strategies
   - **Lab**: Convert 1 PoC model (stg_orders) to Iceberg, run `dbt build`

2. Performance Best Practices (4h)
   - Predicate pushdown: ensure WHERE clauses on partition columns
   - Avoiding full scans: always filter by `date` in models
   - Incremental strategies: `insert_overwrite` vs `append`
   - **Lab**: Test incremental build on large mart, compare runtime vs full-refresh

3. Testing & Documentation (4h)
   - Schema tests (unique, not_null, relationships) — Iceberg supports all
   - Data tests (dbt test)
   - Generating documentation site
   - **Lab**: Add tests to fact tables, run `dbt test`, generate docs

4. Advanced: Custom Macros & Hooks (4h)
   - Post-hook to compact table after build
   - Macro to generate partition expressions automatically
   - **Lab**: Write macro `iceberg_optimize()` called at end of each mart model

**Hands-on Project**: Migrate entire PoC dbt pipeline (20+ models) to Iceberg, validate row counts match PostgreSQL baseline.

**Assessment**: `dbt build --target prod` completes without errors in <30 minutes on 1-month data subset.

**Deliverable**: dbt project fully Iceberg-compatible, CI pipeline updated to test against Starburst.

---

## Week 3: Production Operations & Security

### Day 1-2: Production Monitoring & Incident Response (16 hours)

**Instructor**: Senior DevOps / SRE

**Topics**:
1. Prometheus Metrics Collection (4h)
   - Starburst JMX metrics exposed on `:8080/metrics`
   - Trino-specific metrics: queries.queued, queries.running, scan.bytes
   - scrape config in Prometheus
   - **Lab**: Setup Prometheus in EKS, configure scrape job for Starburst, view metrics

2. Grafana Dashboards (4h)
   - Dashboard as code (JSON/YAML)
   - Key panels: Query latency (P50/P99), Worker CPU/Memory, Bytes scanned, Failures
   - **Lab**: Import Starburst dashboard template, customize for TCA

3. Alerting with Alertmanager (4h)
   - Alert rules (YAML), severity levels (critical/warning/info)
   - Notification channels: Slack, PagerDuty, email
   - **Lab**: Create alert "Starburst worker down" → send to #tca-alerts Slack

4. Runbooks & Playbooks (4h)
   - Structured troubleshooting: symptom → check → action → verify
   - Escalation procedures (P1/P2/P3)
   - **Lab**: Simulate incident "EOD enrichment taking 2 hours", follow runbook, identify bottleneck (workers CPU-bound → scale up)

**Hands-on Project**: Implement full monitoring stack in dev cluster, simulate 5 incidents, resolve using runbooks.

**Deliverable**: Grafana dashboards deployed, alert rules configured, runbook documentation complete.

---

### Day 3: Security & Compliance Hardening (8 hours)

**Instructor**: Security Engineer (internal or consultant)

**Topics**:
1. Network Security (2h)
   - VPC endpoints (S3, no internet egress)
   - Security groups (least privilege)
   - Network policies in K8s (Calico/Cilium)
   - **Lab**: Deploy Starburst in private subnet, verify no public IPs

2. Identity & Access Management (2h)
   - IAM roles for service accounts (IRSA) — EKS best practice
   - Trino authentication (LDAP, JWT, password)
   - Authorization: Starburst fine-grained access control
   - **Lab**: Create IAM role for Starburst workers, restrict to specific S3 prefix

3. Encryption (2h)
   - KMS keys, envelope encryption, automatic rotation (90 days)
   - TLS for in-transit (Trino HTTPS, mTLS between coordinator/workers)
   - S3 SSE-KMS verification
   - **Lab**: Enable HTTPS on Starburst, test with `curl https://coordinator:8443`

4. Audit & Compliance (2h)
   - CloudTrail logging all API calls
   - S3 access logs
   - Starburst query audit tables (system.runtime.queries)
   - MiFID requirements: Immutable audit trail, 5+ year retention
   - **Lab**: Run query, verify `system.runtime.queries` has user, query_text, start_time, end_time

**Assessment**: Penetration test checklist completed, zero critical findings.

**Deliverable**: Security hardening report, audit log configuration verified.

---

### Day 4-5: Disaster Recovery & Cost Management (16 hours)

**Day 4 — Disaster Recovery**:
- Backup strategies: Iceberg snapshots + S3 cross-region replication
- Recovery procedures: Point-in-time restore to secondary region
- **Lab**: Failover test — destroy primary cluster in Frankfurt, spin up secondary in Ireland, restore from S3 replica, validate queries work (<2 hours)
- RTO/RPO definitions, test quarterly

**Day 5 — Cost Optimization**:
- AWS Budgets alerts (80% and 100% thresholds)
- S3 Intelligent-Tiering + lifecycle to Glacier Deep Archive
- Starburst auto-scaling (KEDA)
- Spot instance workers (70% discount)
- **Lab**: Set monthly budget $8K in AWS Budgets, configure Slack notification

**Hands-on Project**: Implement cost controls, run cost report script, verify spending visible in Cost Explorer.

**Deliverable**: DR runbook documented and tested, cost alerts live, optimization measures in place.

---

## Certification Path (Optional but Recommended)

| Certification | Provider | Cost | Value |
|---|---|---|---|
| **AWS Certified Data Analytics — Specialty** | AWS | $300 | Demonstrates S3/EMR/Athena expertise |
| **Starburst Enterprise Certified Administrator** | Starburst | $500 | Valuable for production support |
| **Starburst Enterprise Certified Developer** | Starburst | $500 | SQL optimization skills |
| **Certified Kubernetes Administrator (CKA)** | CNCF | $395 | Valuable if operating EKS |
| **dbt Analytics Engineering** | dbt Labs | $200 | dbt best practices |

**Recommendation**: Send 2 engineers to Starburst certifications (admin + developer), 1 engineer to CKA. Budget: ~$1,600.

---

## Training Materials Inventory

### Required Purchases

| Item | Qty | Unit Cost | Total |
|---|---|---|---|
| Starburst University (3-day) attendance | 3 seats | $5,000 | $15,000 |
| Starburst Enterprise Sandbox License (30-day) | 1 | $0 (trial) | $0 |
| AWS Training - Data Analytics (digital) | 6 seats | $500 | $3,000 |
| K8s Bootcamp (A Cloud Guru/Udemy) | 6 seats | $300 | $1,800 |
| dbt-iceberg workshop (consultant) | 1 group | $3,000 | $3,000 |
| **Training Budget Total** | | | **$22,800** |

### Free Resources

- Starburst documentation (docs.starburst.io)
- Apache Iceberg documentation (iceberg.apache.org)
- dbt docs (docs.getdbt.com)
- AWS re:Post (free Q&A)
- Trino Slack community (trinodb.slack.com)
- Kubernetes.io documentation

---

## Post-Training Assessment

**Goal**: Ensure team can operate production independently before go-live.

### Week 3 Friday: Capstone Exercise

**Scenario**: "Production Starburst cluster showing high query latency. EOD enrichment failing SLA."

**Team Tasks** (pair programming, 2 hours):
1. Diagnose root cause (check Grafana dashboards, Starburst query UI)
2. Submit fix (scale workers, kill runaway query, adjust resource group)
3. Document incident in runbook format
4. Present findings to instructor (acting as stakeholder)

**Evaluation Criteria**:
- Identify bottleneck within 15 minutes ✓
- Implement fix within 30 minutes ✓
- Complete runbook entry with root cause and preventive action ✓
- Team demonstrates understanding of Starburst/Iceberg internals ✓

**Pass Threshold**: 3/4 criteria met per team

**Remediation**: If team fails, extend training by 1 week with focused SRE coaching.

---

## Ongoing Enablement (Post-Go-Live)

### Monthly Knowledge Sharing (1 hour)
- Rotate presenters: each engineer presents one topic per month
- Topics: Iceberg compaction results, query optimization case study, incident post-mortem

### Quarterly Refresher Training (half-day)
- Review new Starburst features
- Deep dive on one advanced topic (cost-based optimizer tuning, etc.)
- Hands-on lab with real production queries (anonymized)

### External Conferences (annual budget $10K)
- **Starburst Summit** (usually US/EU, 2 days) — 2 engineers attend
- **AWS re:Invent** (Las Vegas) — 1 engineer attend if budget allows
- **Data Council** (EU) — 1 engineer attend

**Knowledge Sharing**: Attendees present learnings to entire team within 1 week of return.

---

## Team Skill Gap Analysis & Hiring Plan

**Current Team Skills**:
- 3 Data Engineers: Python, dbt, PostgreSQL, basic SQL — **no cloud/K8s**
- 2 Data Analysts: SQL, Excel, PowerBI — **no engineering**
- 0 DevOps engineers

**Critical Gaps**:
1. Cloud infrastructure (AWS/EKS) — **Must hire 1 DevOps engineer OR train existing**
2. Trino cluster operations — **Can be trained (Starburst provides)**
3. Iceberg internals — **Can be trained (third-party consultant)**
4. Production SRE — **Must develop capability in-house**

### Hiring Recommendation

**Priority 1**: Cloud Data Engineer / Platform Engineer (1 FTE)
- Skills: AWS, Kubernetes, Terraform, CI/CD, monitoring
- Salary: $120K-150K/year
- Start date: Week -2 (before training begins) to assist with Terraform and infrastructure provisioning during training

**Alternative**: Upskill existing Data Engineer to Platform Engineer role (6 months training, mentor required).

**Without this hire**: Risk of operational incidents high. Starburst support helps but cannot replace internal DevOps.

---

## Training Budget Summary

| Category | Cost |
|---|---|
| Starburst University (3 seats) | $15,000 |
| AWS digital training (6 seats) | $3,000 |
| K8s bootcamp (6 seats) | $1,800 |
| dbt-iceberg workshop | $3,000 |
| External trainer (Week 2-3 Iceberg deep-dive) | $15,000 (5 days × $3K/day) |
| Security consultant (Week 3 hardening) | $10,000 (2 days) |
| Starburst certifications (2 people) | $1,600 |
| **Total Training Budget** | **$49,400** |

**Note**: This is **on top of** the $25K initial consulting budget already allocated in main plan. You may combine (hire consultant to deliver both training and implementation).

**Optimization**: Hire one consulting firm (Infostrux or similar) for both implementation AND training at bundled rate ~$60K total (saves $15K).

---

## Training Schedule (3-Week Intensive)

```
Week 1 — Cloud & K8s
Mon-Tue: AWS Fundamentals (VPC, IAM, S3, EKS intro)
Wed: Kubernetes core (pods, deployments, services)
Thu-Fri: Starburst install + basic ops

Week 2 — Iceberg & dbt
Mon-Tue: Iceberg internals (metadata, partitions, compaction)
Wed-Thu: dbt-iceberg adapter migration
Fri: Migration project complete (1-month dataset on Starburst)

Week 3 — Production Ops
Mon-Tue: Monitoring, alerting, incident response
Wed: Security hardening (VPC endpoints, IAM, encryption)
Thu-Fri: DR drill, cost controls, capstone exercise

Week 4 — Kickoff Phase 1
Mon: Begin Terraform infrastructure deployment (real prod)
```

---

## Training Attendance Tracking

| Name | Role | Week 1 | Week 2 | Week 3 | Assessment Score | Certification Target |
|---|---|---|---|---|---|---|
| [Engineer 1] | Data Engineer | ✓ | ✓ | ✓ | TBD | Starburst Admin |
| [Engineer 2] | Data Engineer | ✓ | ✓ | ✓ | TBD | Starburst Dev |
| [Engineer 3] | Data Engineer | ✓ | ✓ | ✓ | TBD | CKA |
| [Analyst 1] | Data Analyst | ✓ | ✓ | Optional | TBD | — |
| [Analyst 2] | Data Analyst | ✓ | ✓ | Optional | TBD | — |
| [DevOps] | Platform Engineer | ✓ | ✓ | ✓ | TBD | AWS Analytics |
| **Minimum Passing** | | 80% attendance | 80% attendance | 80% attendance | 70% exam | 1 per role |

**Analysts**: Can skip Week 3 ops training (not responsible for cluster ops). Focus on dbt + query optimization.

---

## Training Success Metrics

Track weekly:
1. **Lab completion rate**: Target 100% (all labs completed)
2. **Quiz scores**: Target ≥80% average
3. **Capstone exercise**: 3/4 criteria met per team
4. **Certification exams passed**: 2/3 engineers within 30 days post-training
5. **Confidence survey**: Team rates confidence in operating production >4/5

If any metric <70% → add 1 week remediation training before Phase 1 begins.

---

## Appendix: Training Resources

### Online Courses (Free/Low-Cost)
1. **AWS**: 
   - AWS Skill Builder: "Data Analytics Fundamentals" (free)
   - "AWS Certified Data Analytics — Specialty" course (A Cloud Guru, $59/mo)

2. **Kubernetes**:
   - Katacoda interactive K8s tutorials (free)
   - KodeKloud Certified Kubernetes Administrator (CKA) course ($30)

3. **Starburst**:
   - Starburst University (official, included with license)
   - Starburst documentation (free, comprehensive)

4. **Iceberg**:
   - Iceberg ApacheCon talks (YouTube, free)
   - "Lakehouse Fundamentals" course by Databricks ($299)

5. **dbt**:
   - dbt Learn (free, self-paced)
   - "Analytics Engineering" course by dbt Labs ($1,300)

**Recommendation**: Prioritize hands-on labs over video lectures. Team learns by doing.

---

## Document Control

**Version**: 1.0  
**Approved By**: [Pending - Engineering Leadership]  
**Next Review**: After first training cohort completes
