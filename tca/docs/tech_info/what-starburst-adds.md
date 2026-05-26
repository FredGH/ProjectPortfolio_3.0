# What Starburst Enterprise Gives You (Plain English)

**Question**: "I'm already paying for AWS. What extra do I get from Starburst?"

**Simple Answer**: AWS gives you the **car** (EC2 servers, S3 storage). Starburst gives you a **professional driver + built-in GPS + insurance + 24/7 roadside assistance**.

You could drive the car yourself (use open-source Trino), but Starburst makes it safer, faster, and worry-free.

---

## What You Pay AWS For

AWS provides the **raw ingredients**:
- **Servers** (EC2 instances) — compute power
- **Storage** (S3 buckets) — where your Parquet files live
- **Networking** (VPC, subnets) —Private network for security
- **Kubernetes** (EKS) — orchestration to run containers
- **Security basics** (IAM, KMS encryption) — access control and encryption

**Cost**: ~$25K-40K/year for 4-node setup

**What AWS does NOT provide**:
- ❌ A working query engine (you install Trino yourself)
- ❌ Help when your SQL queries are slow
- ❌ Fixes when Trino crashes
- ❌ Nightly security patches for Trino
- ❌ Fine-grained security (row-level access control)
- ❌ Audit logs showing who queried what
- ❌ Query result caching (so same report runs faster)
- ❌ Expert support when production is down

---

## What Exactly Gets Installed, Configured, and Upgraded?

**Starburst Enterprise manages the Trino query engine software stack**. Specifically:

### The Software Stack (What Starburst Provides)

```
Starburst Enterprise Software Stack:
├── Trino Core (SQL query engine)
│   ├── Parser & analyzer
│   ├── Query planner & optimizer
│   ├── Execution engine (distributed processing)
│   └── Connector framework
│
├── Enterprise Connectors (proprietary, better than OSS)
│   ├── Iceberg connector (enhanced)
│   ├── Hive connector (for Glue metastore)
│   ├── PostgreSQL connector (for dimension lookup joins)
│   ├── Redis connector (cache lookup)
│   └── S3 connector (with optimizations)
│
├── Security Plugins (closed-source Starburst code)
│   ├── Fine-grained access control engine
│   ├── Row/column masking engine
│   ├── Dynamic filter processor
│   └── Attribute-based access control (ABAC)
│
├── Governance Plugins
│   ├── Query lineage capture
│   ├── Data catalog integration
│   ├── Usage reporting & chargeback
│   └── Audit log processor
│
├── Performance Enhancements
│   ├── Advanced cost-based optimizer (CBO)
│   ├── Statistics collector (auto)
│   ├── Distributed query result cache
│   └── Predicate pushdown enhancements
│
├── Operational Tools
│   ├── Starburst Console (web UI for cluster management)
│   ├── Health checks & self-healing
│   ├── Coordinator failover logic
│   └── Metrics exporters (Prometheus format)
│
└── Management Scripts
    ├── Upgrade orchestrator
    ├── Configuration validator
    ├── Backup/restore utilities
    └── Diagnostics collection (support bundles)
```

**Total**: ~50 Java JAR files + configuration templates + management scripts + web UI.

---

## What "Installation" Means

### Without Starburst (Open-Source Trino)

**You install**:
1. **Trino server** (download ~200MB JAR from Maven)
2. **JVM** (OpenJDK 17 or 21 — you manage updates)
3. **Connectors** (Iceberg, Hive, S3 — separate JARs, version matching)
4. **Configuration files** (create from scratch):
   - `config.properties` (JVM, HTTP port, threads)
   - `catalog/hive.properties` (Glue connection)
   - `catalog/iceberg.properties` (Iceberg settings)
   - `etc/access-control.properties` (security — basic)
   - `log4j2.properties` (logging)
5. **Kubernetes manifests** (write YAML for Deployment, Service, ConfigMap, Secret)
6. **Helm chart** (if using Helm — create templates)
7. **Monitoring exporters** (JMX exporter, custom metrics)
8. **Log aggregation** (Fluentd/Logstash config)

**Effort**: 3-5 days of platform engineering work per cluster.

**Risk**: Misconfiguration causes crashes, security holes, poor performance.

---

### With Starburst Enterprise

**Starburst installs**:
1. **Starburst Enterprise binary** (single Helm chart containing all JARs, configs, UI)
2. **All connectors pre-bundled and tested** (Iceberg, Hive, S3, PostgreSQL, Redis)
3. **Enterprise plugins pre-compiled** (security, governance, cache)
4. **Validated configuration templates** (tested on 1000+ production clusters)
5. **Starburst Console** (web management UI, includes query editor, history, lineage)
6. **Metrics adapters** (pre-configured Prometheus endpoints)
7. **Health checks** (liveness/readiness probes configured)

**Effort**: You run **one command**:
```bash
helm install starburst starburst/starburst \
  --namespace starburst \
  --values starburst-values.yaml
```

**Result**: Cluster ready in 10 minutes. All components tested together. No version conflicts.

**Plain English**: Starburst gives you a **single package** with everything inside, properly configured. Open-source is **a la carte** — you assemble 20 pieces yourself.

---

## What "Configuration" Means

### Without Starburst

You write **dozens of configuration files** by hand, guessing values:

**config.properties** (Trino core):
```properties
# You must determine these values:
coordinator=true
node-scheduler.include-coordinator=false
query.max-memory-per-node=8GB  # ? too high? too low?
query.max-memory=50GB  # ? calculated from workers?
http-server.http.port=8080  # OK this one is easy
discovery-server.enabled=true  # OK
# ... 20+ more properties
```

**catalog/hive.properties** (Glue metastore):
```properties
connector.name=hive-hadoop2
hive.metastore.uri=glue  # AWS Glue specific — read docs
hive.metastore=glue  # is this right? try and error
hive.region=eu-central-1
hive.aws-access-key=???  # use IAM role or keys?
hive.aws-secret-key=???
# 15+ properties, many optional, many interact
```

**catalog/iceberg.properties**:
```properties
connector.name=iceberg
iceberg.catalog.name=hive  # ???
iceberg.catalog-type=HIVE  # or AWS? or REST?
iceberg.file-format=PARQUET  # default OK
iceberg.compression-codec=ZSTD  # ZSTD or GZIP? level?
iceberg.max-partitions-per-writer=1000  # ? what's optimal?
# ... 10+ more
```

**access-control.properties** (security — basic only):
```properties
# Only supports simple catalog/schema permissions
# No row-level security without custom plugin
user.analyst1=ANALYST
catalog=tca.analyst1=SELECT
# Cannot say "analyst1 only sees their own trades"
```

**resource-groups.properties** (resource mgmt):
```properties
# Complex JSON-like syntax
# No GUI, no validation, easy to break
# Trial-and-error to get quotas right
```

**log4j2.properties**:
```properties
# 50+ lines of log level configuration
# Which logger for which component? (org.apache.trino, com.starburst)
# Learn by trial-and-error when debug needed
```

**Plus**: Kubernetes Deployment YAML (replicas, resources, env vars), Service, ConfigMap mounts, Secret mounts.

**Total config files**: 10-15 files, 500+ lines total. All hand-written, all manually synced across environments.

**Troubleshooting**: If query fails, check 5 different log files across pods. No central UI.

---

### With Starburst Enterprise

You write **ONE values file** for Helm (YAML, ~100 lines):

```yaml
# starburst-values.yaml
clusterName: tca-starburst

# Coordinator config (memory, CPU — Starburst provides recommended values based on instance type)
coordinator:
  memory:
    heap: "8g"  # Starburst recommended for r5.4xlarge
    direct: "2g"
  resources:
    cpu: 4
    memory: "12Gi"

# Worker config (auto-scaling, resource groups)
worker:
  replicas: 3
  autoscale:
    enabled: true
    minReplicas: 2
    maxReplicas: 12
    targetCPUUtilizationPercentage: 70
  memory:
    heap: "24g"
    direct: "8g"
  resources:
    cpu: 16
    memory: "40Gi"

# Catalogs — just list them, Starburst provides the connector config
catalog:
  hive:
    metastore: glue
    metastore-type: glue
    connection:
      aws-region: eu-central-1

connectors:
  iceberg:
    enabled: true
    hive-catalog-name: glue

# Security — simple declarative policies
authentication:
  type: password
  password-authenticator:
    file:
      path: /etc/starburst/users.properties

authorization:
  type: file
  file:
    path: /etc/starburst/access-control.properties

# Resource groups — high-level abstractions
resourceGroups:
  - name: "etl"
    cpuQuotaPerTask: 2
    maxRunning: 50
  - name: "analysts"
    cpuQuotaPerTask: 1
    maxRunning: 10

# License (provided as K8s secret)
license:
  existingSecret: starburst-license
  key: license.json
```

**Starburst provides**:
- Pre-written ConfigMaps for `users.properties`, `access-control.properties` (templates)
- Validated `catalog/*.properties` files (tested with your cloud provider)
- JVM options optimized for your instance type
- Default log configuration that works
- Environment-specific overrides (dev vs prod just change replica count)

**Effort**: Copy Starburst's example values.yaml, change 10 values (cluster name, region, replica counts). **1 hour**.

**Validation**: Starburst chart includes `helm lint` and `helm test` — fails fast if config invalid.

---

## What "Upgrades" Mean

### Without Starburst (Open-Source Trino)

**Upgrade process** (from Trino 407 → 408):

**Week 1: Research**
- Read release notes (200 commits, find breaking changes)
- Identify connector API changes (Iceberg connector changed class names?)
- Check config property deprecations (`query.pushdown-aggregation-enabled` renamed?)
- Browse GitHub issues: "Anyone upgraded to 408 in production?" (mixed reports)

**Week 2: Test Environment Setup**
- Clone production cluster (cost: $2K extra EC2 for 1 week)
- Apply upgrade: download new JARs, replace in Docker image
- Update config: fix deprecated properties
- Run compatibility tests: 100 queries from dbt suite
- Find 5 queries failing (optimizer changed join order)
- Tune queries (add hints, rewrite)
- Document workarounds

**Week 3: Staging Rollout**
- Schedule maintenance window (Friday 8 PM)
- Backup current config (git commit)
- Apply upgrade to staging cluster
- Smoke test: SELECT 1 works, but 2 queries fail
- Roll back to previous version (1 hour downtime)
- Debug: Found JVM version mismatch (need Java 21, staging still Java 17)
- Upgrade Java, retry — still failures
- Open GitHub issue: "Trino 408 fails with Iceberg 1.5.0" — wait 3 days for response
- Meanwhile, production stuck on old version (security vulnerability unpatched)

**Week 4: Production Rollout**
- Fix compatibility issue (downgrade Iceberg connector or upgrade Trino differently)
- Reschedule maintenance (another Friday night)
- Team on-call (6 people, $3K in overtime)
- Upgrade at 10 PM (plan: 1 hour)
  - Coordinator fails to start (missing dependency)
  - Debug: 30 min (classpath issue)
  - Fix and restart: success at 11 PM
- Workers upgrade: rolling restart, 1 worker dies (OOM)
- Scale up memory, restart worker: midnight
- Smoke tests pass at 12:30 AM
- Team stays until 2 AM monitoring
- Weekend: 3 queries slower, need retuning

**Total cost**:
- **Engineering time**: 120 hours × $100/hour = **$12,000**
- **Infrastructure for testing**: $2,000 EC2
- **Team overtime**: $3,000
- **Production risk**: 4 hours downtime, missed EOD SLA = **$50K+ business impact**
- **Duration**: 4 weeks of stress

**Upgrade frequency**: Quarterly → **$48K/year** in upgrade costs + risk.

---

### With Starburst Enterprise

**Upgrade process** (Trino 407 → 408):

**Day 1 (Advance notice)**:
- Starburst support email: "Trino 408 available. Auto-upgrade scheduled for Apr 28, 2:00 AM UTC."
- You review release notes (Starburst summarizes breaking changes relevant to you)
- No action needed unless you want to test early (optional)

**Upgrade day**:
- 2:00 AM: Starburst initiates rolling upgrade
  - Coordinator: drain connections, restart pod with new image (5 min)
  - Workers: 1 by 1, drain queries, restart, rejoin cluster
  - Zero downtime — queries run on remaining workers
- 2:15 AM: All pods upgraded
- You get Slack notification: "Upgrade complete. Cluster on Trino 408."

**Post-upgrade** (optional):
- Starburst TAM sends: "We recommend setting `query.pushdown-aggregation-enabled=true` for 10% faster TCA aggregations."
- You add to values.yaml, `helm upgrade` (non-disruptive)

**Total cost**:
- **Your time**: 30 minutes (read email, maybe apply config tweak)
- **Starburst time**: They did all work (included in license)
- **Downtime**: 0 minutes
- **Business impact**: $0

**Upgrade frequency**: Quarterly, **$0 incremental cost**.

**Plain English**: Starburst upgrades are like **iPhone auto-updates** — happen overnight, you wake up to new version, no effort.

---

## What "Patches" Mean (Security Fixes)

### Without Starburst

**CVE-2025-1234**: Trino remote code execution vulnerability (critical, CVSS 9.8)

**Your response**:
1. Learn about CVE (news article, internal security scan)
2. Assess impact: "Is our Trino cluster vulnerable?" — read CVE details
3. Determine affected version: Trino 407 vulnerable, 408 patched
4. Decision: Must upgrade immediately (P0 emergency)
5. But upgrade process takes 4 weeks (see above) — too slow!
6. Try hotfix: Backport patch to 407 (hire Java developer, 3 days)
7. Test hotfix in dev (may introduce regressions)
8. Deploy to prod with reduced functionality (disable affected feature)
9. Still running vulnerable version for 3 weeks until full upgrade

**Cost**: $20K emergency engineering + production risk.

---

### With Starburst

**Same CVE**:

1. Starburst security team: "Critical CVE published. Fix in patch 408.1, deploying within 24h."
2. 6 hours later: Starburst pushes hotfix to all customer clusters (automated)
3. Your cluster automatically upgraded to patched version (coordinator + workers rolled)
4. You receive: "Security patch applied. No action needed."
5. Security team satisfied: Vulnerability remediated in **<24 hours**.

**Cost**: $0. Included in support contract (SLA: critical patches within 48h).

---

## What "Monitoring" Means

### Without Starburst

You must build entire observability stack:

**Step 1: Metrics collection**
- Trino exposes JMX metrics (JVM memory, threads, query counts)
- Write Prometheus JMX exporter config (discover MBean names)
- Deploy as sidecar container in each Trino pod
- Configure Prometheus to scrape `/metrics` endpoint
- Set up recording rules (5-minute aggregates)

**Step 2: Dashboards**
- Import generic Trino dashboard from Grafana.com (outdated)
- Customize panels for your cluster size (adjust thresholds)
- Add business metrics (queries/hour, avg latency)
- Create SLA dashboard (EOD completion time)

**Step 3: Alerts**
- Write Alertmanager rules (YAML):
  ```yaml
  - alert: TrinoWorkerDown
    expr: up{job="trino-worker"} == 0
  - alert: QueryQueueGrowing
    expr: trino_queued_queries > 100
  ```
- Test alerts (fire fake alerts)
- Configure notification routes (Slack, PagerDuty)

**Step 4: Log aggregation**
- Deploy Fluent Bit daemonset on EKS
- Configure Trino JSON log format
- Ship to CloudWatch Logs or Elasticsearch
- Create index patterns, Kibana dashboards

**Step 5: Tracing** (optional, advanced)
- Install OpenTelemetry agent
- Instrument Trino (requires Java agent)
- Export traces to Jaeger

**Total**: 2-3 weeks of DevOps work, ongoing maintenance (log retention costs, dashboard updates).

---

### With Starburst

**Starburst provides**:
1. **Starburst Console** — built-in web UI:
   - Query history (all queries, searchable)
   - Real-time cluster health (CPU, memory, network)
   - Worker distribution map
   - Query analysis (EXPLAIN visualizer)

2. **Pre-built Grafana dashboards**:
   - Import JSON with 1 click
   - Panels: Query latency P50/P99, bytes scanned, worker utilization
   - SLA tracking (EOD completion time)

3. **Pre-configured alerts**:
   - Worker down
   - Queue saturation
   - Coordinator memory pressure
   - Just hook into your Alertmanager/PagerDuty

4. **Metrics API**:
   - `/metrics` endpoint already exposing Prometheus format
   - No JMX exporter needed

**Setup time**: 1 hour (import dashboards, configure alert routes).

**Ongoing**: Starburst adds new dashboards with each release.

---

## What "Support" Actually Covers

### Without Starburst (Community)

**How you get help**:
1. **Trino Slack** (trinodb.slack.com) — free, 12K members
   - Post question: "Iceberg connector slow on S3"
   - Wait 4-8 hours for response (timezone dependent)
   - Response often: "Have you tried increasing `task.writer-count`?"
   - Might not solve your specific issue (your config is unique)

2. **GitHub Issues** (github.com/trinodb/trino)
   - File issue: "Query fails with NPE in IcebergMetadataManager"
   - Wait 2-7 days for triage
   - May get PR from community in 2 weeks
   - No SLA, no guarantees

3. **Stack Overflow**
   - Search existing questions (few Trino questions)
   - Post new question: may get answer in days, never

4. **Hire a consultant**
   - Find Trino expert on LinkedIn (rare)
   - Contract rate: $200-300/hour
   - Availability: 2-3 weeks lead time
   - Cost per incident: $5K-15K

**Production incident at 2 AM**:
- Your on-call engineer wakes up
- Scours logs, tries fixes
- Posts on Slack: "URGENT: cluster down"
- Waits 6 hours for community response
- Meanwhile, business impact accumulates ($10K/hour)

---

### With Starburst Enterprise

**Support tiers**:

**Premier (included)**:
- **P1 (production down)**: 1 hour response, 24/7/365
- **P2 (performance degradation)**: 4 hour response
- **P3 (non-critical)**: 1 business day

**How it works**:
1. Incident at 2 AM: On-call engineer opens Starburst support portal (or calls hotline)
2. **15 minutes**: Starburst P1 engineer paged, joins bridge call
3. **30 minutes**: Engineer reviews logs, identifies root cause (misconfigured resource group)
4. **45 minutes**: Provides fix (update Helm values), guides application
5. **1 hour**: Cluster restored, EOD enrichment resumes

**Additional support**:
- **Dedicated TAM** (Technical Account Manager): Quarterly architecture reviews, proactive health checks
- **Support bundles**: `starburst-support gather-logs` creates diagnostic package, uploads to Starburst
- **Knowledge base**: Internal articles not public (performance tuning secrets)
- **Escalation to engineering**: If bug in Trino, Starburst engineers fix it (they wrote the code)

**Plain English**: Starburst support is like having the **Trino engineering team on speed-dial**. They don't just know the software — they built it.

---

## What "Enterprise Features" Actually Are (Code-Level)

Starburst Enterprise is **Trino open-source + proprietary plugins**.

### Open-Source Trino (What You Get Free)
```
Trino OSS:
├── SQL parser & analyzer
├── Distributed query planner
├── Connector interfaces (read-only, basic pushdown)
├── HTTP server
└── Basic auth (password file, LDAP bind)
```
**You code**: Everything else (security, lineage, cache).

---

### Starburst Enterprise (What You Pay For)
```
Starburst Pro adds:
├── Fine-Grained Access Control (com.starburst.data.access)
│   ├── RowFilterEngine (SQL-based row-level security)
│   ├── ColumnMaskingEngine (PII masking functions)
│   ├── DynamicFilterProcessor (filter based on user attributes)
│   └── PolicyEvaluator (ABAC, attribute-based)
│
├── Query Lineage (com.starburst.governance.lineage)
│   ├── QueryGraphBuilder (builds DAG of query dependencies)
│   ├── MetadataCapturer (captures table/column usage per query)
│   ├── LineageStore (persists to catalog)
│   └── Visualizer (web UI graph)
│
├── Advanced CBO (com.starburst.optimizer)
│   ├── StatisticsManager (auto-collects, stores in metastore)
│   ├── CostModel (better I/O cost estimation for Iceberg)
│   ├── JoinGraphOptimizer (smarter multi-way join ordering)
│   └── PredicatePushDown (deeper into Iceberg manifest lists)
│
├── Result Cache (com.starburst.cache)
│   ├── CacheKeyGenerator (SQL + params + session hash)
│   ├── DistributedCache (in-memory across workers)
│   ├── InvalidationListener (listens to Iceberg snapshots)
│   └── CacheStats (hit rate reporting)
│
├── Resource Governance (com.starburst.resource)
│   ├── ResourceGroupManager (per-group CPU/memory quotas)
│   ├── QueryQueue (prioritized scheduling)
│   ├── AdmitCtrl (reject queries when quota exceeded)
│   └── UsageReporter (chargeback metrics)
│
└── Starburst Console (webapp)
    ├── Query editor (web SQL interface)
    ├── History browser (search, filter by user/table)
    ├── Cluster health dashboard
    ├── Cost allocation reports
    └── Lineage graph visualizer
```

**All closed-source, only in Starburst Enterprise binary**.

---

## Real-World Daily Operations Comparison

### Day in the Life Without Starburst

**9:00 AM**: Check cluster health
- SSH to bastion, `kubectl get pods` — all green ✓
- Check Prometheus dashboard — CPU normal ✓

**10:00 AM**: Analyst tickets: "Query slow on tca_results"
- Check query in Trino UI (basic open-source UI): Full table scan
- Explain plan: No partition pruning (query filters wrong column)
- Tell analyst to fix query (close ticket)

**11:00 AM**: Security team: "Add row filter for new trader TRADER_42"
- Write Java plugin to extend `StaticFilter` (2 days dev)
- Build JAR, update Docker image
- Deploy to cluster (rolling restart)
- Test: works ✓
- **Time spent**: 2 days

**2:00 PM**: CVE-2025-4321 announced (Trino remote DoS)
- Research: affects versions 400-408
- We're on 406 → vulnerable!
- Create emergency ticket: "Upgrade to 408 now"
- Start upgrade process (4-week project begins)

**3:00 PM**: EOD enrichment failing
- Coordinator OOM killed (heap too small)
- Increase `-Xmx` in config, restart (30 min downtime)
- Re-run EOD (now 1 hour late)

**5:00 PM**: Compliance: "Export all queries on tca_results for last quarter"
- Trino system tables have limited history (only 7 days)
- Need to query application logs (FastAPI) and Trino logs
- Write Python script to merge logs, filter, export CSV (3 hours)
- Incomplete — some queries missing (Trino logs not wire-compatible)

**Takeaway**: **Constant firefighting**, platform work distracts from TCA analytics.

---

### Day in the Life With Starburst

**9:00 AM**: Check Starburst Console
- All green ✓
- No alerts ✓

**10:00 AM**: Analyst: "Query slow"
- Open query in Starburst Console, click "Explain"
- Starburst highlights: "Predicate on non-partition column `trader_name` — use `trader_id` instead"
- Tell analyst to fix (5 min interaction)

**11:00 AM**: Security: "Add filter for TRADER_42"
- Edit `access-control.properties`:
  ```
  analyst42: FILTER "trader_id = 'TRADER_42'"
  ```
- `helm upgrade` (rolling restart, zero downtime, 5 min)
- Done. **Time spent**: 15 minutes.

**2:00 PM**: Starburst support ticket: "CVE-2025-4321 — patch coming tonight"
- You: "OK, notify when deployed"
- 3 AM: Starburst deploys hotfix automatically
- Morning: Cluster on patched version, no action needed

**3:00 PM**: EOD enrichment warning: "Memory pressure on coordinator"
- Starburst TAM pings: "Your coordinator heap too small for workload. Recommend `-Xmx12g`."
- Update values.yaml, `helm upgrade` (5 min)
- Issue resolved before EOD window

**5:00 PM**: Compliance: "Export all queries on sensitive tables last quarter"
- Starburst Console → Audit tab → Filter: `table = tca_results`, `date last 90 days`
- Click "Export CSV" — done in 1 minute
- Complete, auditable, signed by Starburst

**Takeaway**: **Platform "just works"**, team focuses on TCA business logic.

---

## The "Simplification Scorecard"

| What You Do Daily | Without Starburst (Complexity 1-10) | With Starburst (Complexity 1-10) |
|---|---|---|
| Check cluster health | 7 (multiple dashboards, log parsing) | 2 (single Starburst Console) |
| Add new analyst user | 8 (write plugin, rebuild, redeploy) | 2 (LDAP group + config file) |
| Investigate slow query | 9 (manual EXPLAIN, read docs, experiment) | 3 (Starburst engineer tells you) |
| Apply security patch | 10 (emergency upgrade, high risk) | 1 (automatic, no action) |
| Set up monitoring | 8 (Prometheus + Grafana + custom exporters) | 2 (import dashboard) |
| Achieve compliance audit | 9 (manual log collection, incomplete) | 2 (one-click export) |
| Scale cluster for EOD | 6 (edit K8s HPA, test, hope it works) | 3 (resource groups auto-scale) |
| Upgrade Trino version | 9 (4-week project, risky) | 1 (automatic) |
| Add row-level security | 10 (build custom plugin, 2 months) | 2 (edit config file) |
| Troubleshoot crash | 8 (read logs, Google, wait for Slack) | 2 (call support, get answer in 1 hour) |

**Average complexity**: 
- Self-supported: **8.2/10** (very complex, expert needed)
- Starburst: **2.3/10** (simple, routine)

---

## What Exactly Simplifies: The 80/20 Rule

**Without Starburst**, you spend **80% of time** on:
- Platform engineering (K8s, Trino config, upgrades, patches, monitoring)
- Firefighting (incidents, slow queries, crashes)
- Building enterprise features (security, lineage, caching)

**Only 20%** on actual TCA analytics (dbt models, interpreting results).

**With Starburst**, you spend:
- **20%** on platform (point Starburst at S3, tune resource groups)
- **80%** on TCA analytics (where you add business value)

**Starburst's simplification**: **Shift 60% of your team's time from undifferentiated platform work to differentiated analytics work**.

---

## The 10 Things Starburst Turns from Projects into Config

| Project (Without Starburst) | Configuration (With Starburst) |
|---|---|
| 1. Build query result cache system | `cache.enabled=true` |
| 2. Implement row-level security for 100 traders | `user.analyst1: FILTER "trader_id='A'"` |
| 3. Create audit log system for compliance | `audit.log.retention=7 years` (built-in) |
| 4. Build query lineage graph | `lineage.enabled=true` (Console shows graph) |
| 5. Set up coordinator HA | `coordinator.count=2` (just deploy 2 replicas) |
| 6. Create resource quota system | `resourceGroups: [etl:80%, analysts:20%]` |
| 7. Implement statistics collector | `statistics.collector.enabled=true` (auto) |
| 8. Build query history UI | Starburst Console (already there) |
| 9. Create alert rules for cluster health | Import Starburst's Alertmanager rules |
| 10. Upgrade Trino across fleet | Starburst automatic upgrade schedule |

**Each "project"** = 1-4 weeks engineer time.  
**Total simplification value**: **6-12 months** of engineering per year.

---

## Bottom Line: What Gets Simplified?

**Starburst simplifies the entire Trino operational lifecycle**:

1. **Installation**: One Helm install vs 3 weeks of YAML writing
2. **Configuration**: 100-line values file vs 500 lines across 15 files
3. **Security**: Config file vs custom Java plugin development
4. **Monitoring**: Pre-built vs 2 weeks of Prometheus/Grafana setup
5. **Upgrades**: Automatic vs 4-week quarterly projects
6. **Patches**: Automatic within 48h vs emergency scramble
7. **Support**: 1-hour P1 vs community Slack (days)
8. **Performance**: Auto-optimizer + expert advice vs manual tuning
9. **Compliance**: One-click export vs custom log aggregation
10. **Scaling**: Resource groups config vs manual queue management

**Result**: Your team spends **80% of time on analytics** (TCA models, reports, insights) instead of **80% on platform operations**.

---

## Related Documents

- [production-storage-decision.md](production-storage-decision.md) — Why Iceberg + Starburst selected
- [vendor-selection.md](vendor-selection.md) — Side-by-side vendor comparison
- [managed-trino-explained.md](managed-trino-explained.md) — Technical architecture
- [deployment-guide.md](deployment-guide.md) — How to install Starburst on AWS

---

## Real Example: "What Happens When Something Goes Wrong?"

### Scenario: EOD enrichment suddenly takes 2 hours (should be 30 min)

**Without Starburst** (self-supported OSS Trino):
1. Engineers notice at 19:00 (SLA missed)
2. Check Trino logs (coordinator pod) — errors?
3. Search Stack Overflow — "Trino query slow" → generic advice
4. Check worker node CPU — all at 100%
5. Try manual worker restart — doesn't help
6. Ask on Trino Slack — "Anyone seen this?" (wait 6 hours for response)
7. Finally diagnose: statistics outdated, CBO making bad plan
8. Manually collect stats: `CALL system.flush_metadata_cache()` then `ANALYZE table`
9. Still slow — realize need to increase worker memory
10. Resize node group (30 min downtime)
11. Query finally fast at 22:00
**Total**: 3 hours downtime, $50K+ in delayed reporting, team stressed

**With Starburst**:
1. Engineers notice at 19:00 (SLA missed)
2. Open Starburst support ticket (P1)
3. **15 min**: Starburst engineer joins call, says "We see stats stale on fct_fills"
4. **30 min**: Starburst engineer runs diagnostic, recommends `ANALYZE fct_fills`
5. **45 min**: Command executed via Trino CLI, query plan improves
6. **19:50**: EOD enrichment completes
**Total**: 1 hour incident, no dollar loss, supported by expert

**Value of support**: $50K+ saved in delayed reporting + team burnout avoided.

---

## Why It's Not "Just a Support Contract"

You might think: "I'll use open-source Trino and buy AWS Enterprise Support for $15K/year instead."

**But AWS Support doesn't know Trino**. Example call:

**You**: "Trino queries are slow, EOD enrichment missing SLA"
**AWS Support**: "I see your EC2 CPU at 95%. I recommend scaling up to r5.8xlarge."
**You**: "Yes, we already did, still slow"
**AWS**: "Have you checked Trino logs?"
**You**: "Yes, but don't understand them. Can you help?"
**AWS**: "I'm not certified in Trino. I'll open a case with Trino community Slack for you."

**Versus Starburst Support**:
**You**: "Trino queries slow"
**Starburst**: "Let me look. I see fct_fills missing statistics. Running ANALYZE now. Also your Z-order column choice suboptimal — here's recommended change."

**Key difference**: Starburst engineers **wrote** Trino (or at least deeply know it). AWS supports the underlying EC2, not the software **on** it.

---

## The Starbucks vs Making Coffee at Home Analogy

| | Make Coffee at Home | Starbucks |
|---|---|---|
| **Ingredients** | Beans, milk, equipment ($0.50/cup) | Starbucks buys beans, milk, equipment (fixed cost) |
| **Your effort** | Grind beans, brew, clean (10 min) | Barista does it (0 min) |
| **Consistency** | Sometimes great, sometimes bad | Always same quality |
| **Expert help** | Google "why is my coffee bitter?" | Barista adjusts grind on spot |
| **Convenience** | Clean-up, supplies to buy | Walk in, get coffee |
| **Cost over 5 years** | $0.50/cup × 365 days × 5 = **$913** | $5/cup × 365 × 5 = **$9,125** |

**Starbucks costs 10x more** — but you pay for convenience, consistency, no equipment maintenance.

**Starburst is Starbucks for Trino**:
- Open-source Trino = make coffee at home (free, but you do all work)
- Starburst Enterprise = Starbucks coffee (premium, but zero hassle)

For a bank running production TCA with 730K orders/year, **"coffee" is too critical to brew yourself** when it breaks at 2 AM.

---

## What You Actually Touch Day-to-Day With Starburst

**Your daily job**:
- Write dbt models (SQL)
- Schedule Airflow DAGs
- Investigate failed orders (application logic)
- Answer analyst questions about TCA results

**Starburst's daily job**:
- Monitor cluster health (CPU, memory, query queue)
- Apply security patches to Trino
- Optimize query plans for Iceberg
- Support your P1 incidents
- Upgrade Trino from 407 to 408 without you noticing

**You work on business logic. Starburst handles platform infrastructure.**

---

## What Happens If You Don't Buy Starburst?

You must **replace Starburst's value with your own team's effort**:

| Starburst Feature | DIY Alternative | Cost (in person-hours) | Skills Needed |
|---|---|---|---|
| 24/7 support | Hire 2 senior platform engineers on-call 24/7 | $300K/year each × 2 = $600K | Trino expert + Kubernetes expert |
| Fine-grained security | Build custom plugin, integrate with LDAP | 4 weeks developer time = $40K | Java plugin dev, security |
| Query lineage | Ship Trino logs to data catalog, build lineage graph | 3 months = $90K | Data catalog, ETL, visualization |
| CBO auto-stats | Write nightly stats collection jobs, tune manually | 2 weeks = $20K | Query optimization expertise |
| Result cache | Implement Redis + query hash layer | 3 weeks = $45K | Distributed systems |
| Monitoring dashboards | Prometheus + Grafana setup, custom panels | 1 week = $15K | Observability stack |
| Upgrades/patches | DevOps manual upgrade cycles | 1 week/quarter × 4 = $48K/year | Release management |
| HA/failover | Active-standby coordinator, load balancer | 2 weeks = $30K | K8s, networking |

**Total DIY cost**: ~$888K/year in personnel (2-3 extra FTEs)  
**Starburst cost**: $110K/year

**Saving with Starburst**: ~$778K/year in avoided hiring

---

## Bottom Line: Starburst's Value Proposition

| What You Want | Without Starburst | With Starburst |
|---|---|---|
| **Run queries on Iceberg** | Install Trino yourself (days) | Click install (hours) |
| **Production SLA** | You build HA, monitoring, support | Vendor guarantees it |
| **When something breaks** | You debug, Google, panic | Call 24/7 hotline |
| **Security compliance** | Build custom plugins (weeks) | Configure file (hours) |
| **Query performance** | Manual tuning, guesswork | Automatic optimization |
| **Stay up-to-date** | Manual upgrades (risky) | Automatic patches |
| **Team focus** | Platform engineering (distraction) | Business logic (value) |

**Starburst lets your team focus on TCA analytics** (writing models, interpreting results) instead of **platform engineering** (operating Trino clusters, debugging query engine bugs).

---

## The Bottom Line (One Sentence)

**Starburst Enterprise gives you a production-grade, secure, high-performance Trino query engine with 24/7 expert support, so your team can focus on building TCA analytics instead of operating database infrastructure.**

---

## Related Documents

- [production-storage-decision.md](production-storage-decision.md) — Why Iceberg + Starburst selected
- [vendor-selection.md](vendor-selection.md) — Side-by-side vendor comparison
- [managed-trino-explained.md](managed-trino-explained.md) — Technical deep-dive
- [deployment-guide.md](deployment-guide.md) — How to install Starburst on AWS
