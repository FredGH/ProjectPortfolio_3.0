# TCA Platform — Observability Improvements Backlog

Items not yet implemented, ordered roughly by effort / value ratio.

---

## AWS Infrastructure

| Item | What it adds | Effort |
|---|---|---|
| **RDS Performance Insights** | Top SQL queries by wait time, query latency by statement — already has `pg_stat_statements` loaded, one Terraform flag away | Very low — `performance_insights_enabled = true` + `performance_insights_retention_period = 7` on `aws_db_instance` |
| **RDS Enhanced Monitoring** | OS-level metrics at 1–60 s granularity (CPU steal, per-process memory, swap) | Very low — `monitoring_interval = 60` + `monitoring_role_arn` on `aws_db_instance` |
| **ElastiCache metrics on CloudWatch dashboard** | Cache hit rate, evictions, connections — data already in CloudWatch, just not displayed | Very low — add 3 widgets to the observability module dashboard |
| **ALB Access Logs → S3** | Full HTTP audit trail: latency, status codes, client IPs, TLS version; queryable with Athena | Low — `access_logs { bucket = ... enabled = true }` block on `aws_lb` + an S3 bucket |
| **VPC Flow Logs** | Network-level audit: which IPs talk to which services, rejected security-group connections | Low — one `aws_flow_log` resource targeting CloudWatch Logs or S3 |
| **CloudTrail** | Immutable audit log of every AWS API call — who deployed what, IAM activity, secret access | Low — one `aws_cloudtrail` resource with S3 destination |
| **CloudWatch Synthetics (canaries)** | Scheduled HTTP checks against Angular SPA + `/api/health` every 5 min; alarm on failure | Medium — needs a canary Lambda written in Node.js/Python, an S3 bucket for artefacts, and a Terraform `aws_synthetics_canary` resource |
| **AWS X-Ray** | End-to-end distributed traces: API → Postgres → Redis, latency per hop, error sampling | Medium — add OTEL/X-Ray SDK to FastAPI, Terraform `aws_xray_sampling_rule`, IAM `xray:PutTelemetryRecords` |

---

## FastAPI

| Item | What it adds | Effort |
|---|---|---|
| **Prometheus `/metrics` endpoint** | Request count, latency histograms, error rates — scrapeable by Prometheus or the CloudWatch agent | Medium — `pip install prometheus-fastapi-instrumentator`, one call in `create_app()`; needs a scraper or push gateway to get data into CloudWatch |
| **OpenTelemetry + X-Ray** | Automatic instrumentation of every SQLAlchemy query and Redis call with trace IDs that appear in X-Ray ServiceMap | Medium — `opentelemetry-instrumentation-fastapi`, OTEL collector sidecar in the ECS task definition |

---

## Airflow

| Item | What it adds | Effort |
|---|---|---|
| **SLA miss notifications** | Airflow fires a callback when a DAG doesn't finish within its declared SLA window | Low — add `sla=timedelta(hours=2)` to each DAG + configure `[smtp]` or an SNS callback in `on_sla_miss` |

---

## dbt / Data Quality

| Item | What it adds | Effort |
|---|---|---|
| **Great Expectations** | Richer data quality assertions with HTML evidence reports; supports row-level samples of failures | Medium–High — separate GX context repo, `GreatExpectationsOperator` in Airflow, S3 bucket for reports |

---

## Frontend (Angular SPA)

| Item | What it adds | Effort |
|---|---|---|
| **AWS RUM (Real User Monitoring)** | Client-side page load time, JS errors, user journeys reported to CloudWatch | Medium — one `aws_rum_app_monitor` Terraform resource + inject the RUM script into `index.html` in the Angular build |
| **Sentry** | Frontend error tracking with stack traces, user context, and release tracking | Low — `npm install @sentry/angular`, ~10 lines of config in `app.module.ts`, free tier available |

---

## Redis / ElastiCache

| Item | What it adds | Effort |
|---|---|---|
| **Redis Slow Log on CloudWatch dashboard** | Commands taking longer than a configurable threshold; surfaced via `SLOWLOG GET` | Low — add a Lambda or Airflow task that polls `SLOWLOG GET` and emits CloudWatch custom metrics |
