# AWS Deployment — PrivateBank TCA Platform

Infrastructure-as-code for deploying the TCA platform to AWS using Terraform.
The PoC is local-only; this document describes the production-ready AWS target architecture
and the local testing strategy using LocalStack Community + `terraform plan`.

---

## Architecture

```
                          ┌─────────────────────────────────────────┐
                          │              Route 53 (optional)        │
                          └──────────────────┬──────────────────────┘
                                             │
                          ┌──────────────────▼──────────────────────┐
                          │         CloudFront Distribution         │
                          │   Angular SPA (S3) + /api/* → ALB       │
                          └──────┬───────────────────────┬──────────┘
                                 │ static assets         │ /api/*
               ┌─────────────────▼─────────────────────────────────┐
               │              S3 Bucket (Angular SPA)              │
               └───────────────────────────────────────────────────┘
                                                         │
                          ┌──────────────────────────────▼──────────┐
                          │    Application Load Balancer (public)   │
                          │  :80 → /api/* → tca-api:8000            │
                          │  :80 → /airflow/* → airflow-web:8080    │
                          │  :80 → /mock/* → tca-mock:8001          │
                          └──────┬───────────────┬──────────────────┘
                                 │               │
         ┌───────────────────────┤   Private VPC (10.0.0.0/16)
         │                       │
┌────────▼────────┐   ┌──────────▼──────────┐   ┌─────────────────────┐
│  ECS Fargate    │   │   ECS Fargate        │   │   ECS Fargate       │
│  tca-api        │   │   tca-mock-server    │   │   tca-airflow       │
│  0.5 vCPU/1 GB  │   │   0.25 vCPU/0.5 GB  │   │   webserver         │
│  port 8000      │   │   port 8001          │   │   0.5 vCPU/1 GB     │
└────────┬────────┘   └──────────┬──────────┘   │   scheduler         │
         │                       │              │   0.25 vCPU/0.5 GB  │
         └───────────────────────┼──────────────┘
                                 │
         ┌───────────────────────┼──────────────────────────┐
         │                       │                          │
┌────────▼────────┐   ┌──────────▼──────────┐   ┌──────────▼──────────┐
│  RDS            │   │  ElastiCache        │   │  Secrets Manager    │
│  PostgreSQL 16  │   │  Redis 7            │   │  DB creds           │
│  + TimescaleDB  │   │  cache.t3.micro     │   │  JWT RSA keys       │
│  db.t3.micro    │   │  port 6379          │   │  Airflow secret key │
└─────────────────┘   └─────────────────────┘   └─────────────────────┘
         │                       │
┌────────▼───────────────────────▼──────────────────────────────────────┐
│                     ECR (Docker Image Registry)                       │
│   tca-api  |  tca-mock-server  |  tca-airflow  |  tca-angular        │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Service Mapping: Docker Compose → AWS

| Docker Compose Service | AWS Service | Config |
|---|---|---|
| `postgres` (TimescaleDB) | RDS for PostgreSQL 16 | db.t3.micro, TimescaleDB extension, single-AZ |
| `redis` | ElastiCache Redis 7 | cache.t3.micro, single node |
| `app` (FastAPI) | ECS Fargate | 0.5 vCPU / 1 GB, ALB target group |
| `mock-server` | ECS Fargate | 0.25 vCPU / 0.5 GB, ALB target group |
| `airflow-webserver` | ECS Fargate | 0.5 vCPU / 1 GB, ALB target group |
| `airflow-scheduler` | ECS Fargate | 0.25 vCPU / 0.5 GB, no ALB (scheduler only) |
| `angular` (nginx SPA) | S3 + CloudFront | Static website hosting |
| `docker-compose` networking | VPC + Security Groups | Private subnets for all backend services |
| `.env` secrets | Secrets Manager | DB creds, JWT keys, Airflow secret key |
| Local Docker images | ECR | One repository per image |

---

## Terraform Module Structure

```
terraform/
├── main.tf                     # root: calls all modules
├── variables.tf                # input variables (region, env, naming)
├── outputs.tf                  # ALB DNS, CloudFront URL, RDS endpoint
├── providers.tf                # AWS provider + LocalStack override flag
├── locals.tf                   # computed name prefixes, tags
│
├── modules/
│   ├── vpc/                    # VPC, subnets, IGW, NAT, route tables, security groups
│   ├── rds/                    # RDS PostgreSQL + parameter group + subnet group
│   ├── elasticache/            # ElastiCache Redis + subnet group
│   ├── ecr/                    # ECR repositories + lifecycle policies
│   ├── ecs/
│   │   ├── cluster/            # ECS cluster + CloudWatch log group
│   │   ├── api/                # tca-api task definition + service
│   │   ├── mock_server/        # tca-mock-server task definition + service
│   │   ├── airflow_webserver/  # Airflow webserver task definition + service
│   │   └── airflow_scheduler/  # Airflow scheduler task definition (no ALB)
│   ├── alb/                    # ALB + listeners + target groups (api, airflow, mock)
│   ├── cdn/                    # S3 bucket + CloudFront + OAI
│   ├── iam/                    # ECS execution role + task role + policies
│   └── secrets/                # Secrets Manager: DB creds, JWT keys, Airflow key
│
└── environments/
    ├── local/                  # LocalStack-testable modules only (ecr, cdn, iam, secrets)
    │   ├── main.tf
    │   └── terraform.tfvars
    └── prod/                   # full deployment
        ├── main.tf
        └── terraform.tfvars
```

---

## Module Details

### `modules/vpc`

- VPC CIDR: `10.0.0.0/16`
- 2 public subnets (`10.0.1.0/24`, `10.0.2.0/24`) — ALB only
- 2 private subnets (`10.0.10.0/24`, `10.0.11.0/24`) — ECS, RDS, ElastiCache
- 1 Internet Gateway (public subnets)
- 1 NAT Gateway (ECS tasks in private subnets need outbound internet for ECR pulls)
- Security groups:
  - `sg-alb` — inbound 80/443 from 0.0.0.0/0
  - `sg-api` — inbound 8000 from `sg-alb` only
  - `sg-mock` — inbound 8001 from `sg-alb` only
  - `sg-airflow` — inbound 8080 from `sg-alb` only
  - `sg-rds` — inbound 5432 from ECS security groups only
  - `sg-redis` — inbound 6379 from ECS security groups only

### `modules/rds`

- Engine: PostgreSQL 16
- Instance: `db.t3.micro` (free tier eligible, year 1)
- Parameter group: enables `timescaledb` extension
- Subnet group: private subnets
- Storage: 20 GB gp2, encrypted
- Backup retention: 7 days
- Credentials sourced from Secrets Manager
- **Post-deploy step**: run `init.sql` as a one-off ECS task (creates schemas, TimescaleDB hypertable, auth tables)

### `modules/elasticache`

- Engine: Redis 7
- Node type: `cache.t3.micro`
- Single-node cluster (no replication for PoC)
- Subnet group: private subnets
- Transit encryption enabled

### `modules/ecr`

- 4 repositories: `tca-api`, `tca-mock-server`, `tca-airflow`, `tca-angular`
- Lifecycle policy: keep last 10 images per repository
- Image scanning on push enabled

### `modules/ecs/cluster`

- ECS cluster with Container Insights enabled
- CloudWatch log group: `/ecs/tca` (30-day retention)

### `modules/ecs/api`

- Task definition:
  - Image: `ECR/tca-api:<tag>`
  - CPU: 512 / Memory: 1024
  - Port: 8000
  - Environment variables sourced from Secrets Manager (DATABASE_URL, REDIS_URL, JWT keys)
  - Command: `uvicorn app:app --host 0.0.0.0 --port 8000`
- ECS service:
  - Desired count: 1
  - ALB target group on port 8000
  - Health check: `GET /docs` → 200

### `modules/ecs/airflow_webserver`

- Task definition:
  - Image: `ECR/tca-airflow:<tag>`
  - CPU: 512 / Memory: 1024
  - Port: 8080
  - Command: `webserver`
  - Environment: AIRFLOW__CORE__EXECUTOR=LocalExecutor, DB and Redis URLs from Secrets Manager
- ECS service:
  - ALB target group on port 8080
  - Health check: `GET /health` → 200

### `modules/ecs/airflow_scheduler`

- Task definition:
  - CPU: 256 / Memory: 512
  - Command: `scheduler`
  - No ALB (scheduler has no HTTP interface)
- Shares the same image and environment as the webserver

### `modules/alb`

- Internet-facing ALB in public subnets
- HTTP listener (port 80) with path-based routing:
  - `/api/*` → target group: tca-api (port 8000)
  - `/airflow/*` → target group: airflow-webserver (port 8080)
  - `/mock/*` → target group: tca-mock-server (port 8001)
- HTTPS (port 443) can be added with an ACM certificate when a domain is available

### `modules/cdn`

- S3 bucket: private, versioning enabled, no public access
- CloudFront Origin Access Identity (OAI) — only CloudFront can read the bucket
- CloudFront distribution:
  - Default origin: S3 bucket (Angular SPA)
  - `/api/*` behaviour: forwards to ALB
  - Default root object: `index.html`
  - SPA fallback: 404 → `index.html` (handles Angular client-side routing)
  - Price class: PriceClass_100 (US + EU only)

### `modules/iam`

- **ECS execution role** (used by ECS agent, not the container):
  - `AmazonECSTaskExecutionRolePolicy` (ECR pull, CloudWatch logs)
  - `secretsmanager:GetSecretValue` (for injecting secrets as env vars)
- **ECS task role** (used by the container process):
  - `secretsmanager:GetSecretValue`
  - `s3:PutObject` (for report uploads, if needed)

### `modules/secrets`

| Secret Name | Contents |
|---|---|
| `tca/db-credentials` | `{ "username": "...", "password": "...", "host": "...", "dbname": "..." }` |
| `tca/redis-url` | `redis://<elasticache-endpoint>:6379/0` |
| `tca/jwt-private-key` | RSA private key PEM (generated once, stored at bootstrap) |
| `tca/jwt-public-key` | RSA public key PEM |
| `tca/airflow-secret-key` | Airflow webserver secret key |

---

## Airflow Bootstrap Sequence on AWS

The Docker Compose `airflow-init` container (which runs `airflow db migrate` and creates the admin user) maps to a one-off ECS task on AWS. The sequence on first deploy:

```
1. terraform apply              → provisions all infrastructure
2. ECS init task: init.sql      → creates DB schemas + TimescaleDB hypertable
3. ECS init task: seed.py       → loads 400 synthetic orders via dlt
4. ECS init task: dbt build     → builds Raw Vault → Biz Vault → Marts
5. ECS init task: airflow db migrate + create admin user
6. ECS services start           → api, mock-server, airflow-webserver, airflow-scheduler
7. Unpause Airflow DAGs         → via Airflow CLI or UI
```

Steps 2–5 are one-off ECS tasks (run-once, not long-running services). They are triggered manually after `terraform apply` completes.

---

## Local Testing Strategy

Full end-to-end local testing requires no AWS account.

### Layer 1 — Runtime (Docker Compose)

The existing `docker-compose.yml` already provides exact local equivalents of all AWS runtime services. No changes needed.

```bash
docker compose up --build    # full stack locally
```

### Layer 2 — Terraform (LocalStack Community)

LocalStack Community emulates the AWS API for services that don't require compute (S3, ECR, Secrets Manager, IAM, CloudFront). Use `tflocal` to redirect Terraform API calls to LocalStack.

**Setup:**
```bash
pip install localstack awscli-local terraform-local
brew install terraform
```

**Start LocalStack alongside the existing stack:**
```bash
docker compose -f docker-compose.yml -f docker-compose.localstack.yml up -d localstack
```

**Apply LocalStack-testable modules:**
```bash
cd terraform/environments/local
tflocal init
tflocal apply
```

This validates and applies: ECR repositories, S3 bucket + CloudFront config, Secrets Manager secrets, IAM roles and policies.

### Layer 3 — `terraform plan` (VPC, RDS, ECS, ALB, ElastiCache)

For services LocalStack Community cannot emulate, `terraform plan` against the real AWS API validates all resource definitions without creating anything (plan is always free).

```bash
cd terraform/environments/prod

# Requires AWS credentials in ~/.aws/credentials or env vars
# No resources are created; this only calls the AWS planning API
terraform init
terraform plan -var-file=terraform.tfvars
```

Catches: invalid AMIs, IAM policy syntax errors, security group rule conflicts, missing subnet groups, unsupported RDS parameter values.

### Summary

| Module | Local test method | Cost |
|---|---|---|
| `ecr` | `tflocal apply` → LocalStack | $0 |
| `cdn` (S3 + CloudFront) | `tflocal apply` → LocalStack | $0 |
| `iam` | `tflocal apply` → LocalStack | $0 |
| `secrets` | `tflocal apply` → LocalStack | $0 |
| `vpc` | `terraform plan` only | $0 |
| `rds` | `terraform plan` only | $0 |
| `elasticache` | `terraform plan` only | $0 |
| `alb` | `terraform plan` only | $0 |
| `ecs/*` | `terraform plan` only | $0 |
| Full runtime stack | `docker compose up` | $0 |

---

## Monthly AWS Cost Estimate

All costs in USD. Based on `eu-west-1` (Ireland). No $200 promotional credit available.

| Service | Config | Monthly |
|---|---|---|
| RDS PostgreSQL db.t3.micro | Single-AZ, 20 GB gp2 | $0 (free tier yr 1) → $14 |
| ElastiCache cache.t3.micro | Single node | $13 |
| ECS Fargate — tca-api | 0.5 vCPU / 1 GB, always-on | $20 |
| ECS Fargate — tca-mock-server | 0.25 vCPU / 0.5 GB | $10 |
| ECS Farfast — airflow-webserver | 0.5 vCPU / 1 GB | $20 |
| ECS Fargate — airflow-scheduler | 0.25 vCPU / 0.5 GB | $10 |
| ALB | 1 load balancer | $16 |
| NAT Gateway | 1 NAT (private subnet egress) | $32 |
| S3 + CloudFront | Angular SPA, minimal traffic | $2 |
| ECR | 4 repos, ~2 GB storage | $1 |
| Secrets Manager | 5 secrets | $2 |
| CloudWatch Logs | ECS task logs | $2 |
| **Total (year 1, RDS free)** | | **~$128/month** |
| **Total (year 2+)** | | **~$142/month** |

The NAT Gateway ($32) and ALB ($16) are the two largest fixed costs. Both are required: NAT for ECS tasks in private subnets to pull images from ECR, ALB for routing to multiple ECS services.

---

## Prerequisites for Real AWS Deployment

1. AWS account with IAM user having `AdministratorAccess` (for initial Terraform apply)
2. `terraform` CLI installed
3. `aws` CLI configured with credentials
4. Docker images built and pushed to ECR (via CD pipeline or manually)
5. ACM certificate (optional, for HTTPS on the ALB)
