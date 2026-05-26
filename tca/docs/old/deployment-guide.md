# TCA Platform: Production Deployment Guide

**Version**: 1.0  
**Target Environment**: AWS EU (Frankfurt)  
**Timeline**: 10 weeks  
**Team**: 1 DevOps (FTE), 1 Data Engineer, 1 Backend Engineer (shared 0.5), 1 Security (0.25)  
**Prerequisites**: All team members completed 3-week training program

---

## Quick Start (TL;DR)

1 Week 0: Vendor contracts + AWS account setup  
2 Week 1-2: Terraform infrastructure (VPC, S3, EKS, Glue)  
3 Week 3-5: Migrate dbt to Iceberg, backfill 5 years data  
4 Week 6-7: FastAPI + Airflow migration to Starburst  
5 Week 8-10: Monitoring, security, DR drill → go-live

**Total**: 10 weeks, ~$150K-200K first-year OPEX

---

## Phase 0: Pre-Implementation (Week 0)

### Task 0.1: Procurement & Vendor Onboarding

**Owner**: Engineering Manager + Procurement  
**Duration**: 3-5 business days

#### Subtask 0.1.1: Starburst Enterprise Contract
- [ ] Contact Starburst sales (sales@starburst.io) — CC engineering manager
- [ ] Request quote for:
  - 4-node cluster license (3 workers + 1 coordinator)
  - Premier 24/7 Support (Enterprise tier)
  - Security bundle (fine-grained access control)
  - Governance bundle (query lineage, audit)
  - 3-year term with 40% discount target
  - Annual commitment (not perpetual)
- [ ] Include training: 3 Starburst University seats (value $15K)
- [ ] Include professional services: 5 days implementation advisory (value $20K)
- [ ] Review legal: Indemnification, liability caps, data processing agreement (GDPR)
- [ ] Sign contract → receive license key (JSON file)

**Deliverable**: Starburst Enterprise license.json + support contract number

---

#### Subtask 0.1.2: AWS Account & Enterprise Agreement

**Owner**: DevOps Engineer  
**Duration**: 2 days

If PrivateBank has existing AWS Enterprise Agreement:
1. Contact AWS account manager, add TCA project to agreement
2. Request AWS credits (~$50K initial) to offset first year
3. Enable AWS Organizations SCP allowing only EU regions (Frankfurt, Ireland, London)
4. Set up AWS SSO for team (MFA required)

If new AWS account:
1. Register aws.amazon.com with company email
2. Verify via phone, provide legal entity documentation
3. Open support ticket: Request Enterprise Agreement tier
4. Create IAM admin user (disable root), store credentials in 1Password vault

**AWS Services to Enable**:
- [ ] Amazon S3
- [ ] Amazon EKS
- [ ] AWS Glue
- [ ] Amazon EMR (optional backup)
- [ ] AWS Key Management Service (KMS)
- [ ] AWS CloudTrail
- [ ] Amazon CloudWatch
- [ ] AWS Budgets
- [ ] Amazon Managed Service for Prometheus (optional)

**Billing Setup**:
```bash
# Create budget alert: $5K soft limit, $8K hard limit
aws budgets create-budget \
  --account-id $AWS_ACCOUNT_ID \
  --budget file://budget.json
```

**Deliverable**: AWS account active, IAM users created, billing alerts configured.

---

#### Subtask 0.1.3: Project Infrastructure-as-Code Repo

**Owner**: DevOps Engineer  
**Duration**: 1 day

```bash
mkdir -p ~/projects/tca-prod
cd ~/projects/tca-prod
git init

# Create directory structure
mkdir -p {terraform/{modules/{vpc,s3,eks,iam,kms,glue,monitoring,budgets},environments/prod},kubernetes/{starburst,fastapi,redis,monitoring},dags,dbt,scripts,docs}

# Terraform backend (S3 for state)
cat > terraform/backend.tf <<'EOF'
terraform {
  backend "s3" {
    bucket         = "tca-terraform-state-xxx"  # unique name
    key            = "prod/terraform.tfstate"
    region         = "eu-central-1"
    encrypt        = true
    kms_key_id     = "alias/tca-terraform-state"
  }
}
EOF

# .gitignore
cat > .gitignore <<'EOF'
.terraform/
.terraform.lock.hcl
*.tfstate
*.tfstate.backup
.secrets/
*.pem
__pycache__/
*.pyc
.env
.venv/
EOF

git add .
git commit -m "Initial commit: Terraform backend setup"
git remote add origin git@github.com:PrivateBank/tca-prod-infra.git
git push -u origin main
```

**Deliverable**: Git repository initialized, CI/CD pipeline ready (GitHub Actions or similar).

---

### Task 0.2: Terraform Bootstrap (Infrastructure Foundation)

**Owner**: DevOps Engineer  
**Duration**: 2 days

#### Step 1: Terraform Root Module

```bash
cd terraform/environments/prod
cat > main.tf <<'EOF'
terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.1"
    }
  }
}

provider "aws" {
  region = var.aws_region  # eu-central-1
}

module "vpc" {
  source = "../../modules/vpc"
  vpc_cidr = "10.0.0.0/16"
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  azs = ["eu-central-1a", "eu-central-1b", "eu-central-1c"]
  environment = "production"
  project = "tca"
}

module "s3" {
  source = "../../modules/s3"
  bucket_name = "tca-production-data-${random_id.bucket_suffix.hex}"
  kms_key_arn = module.kms.key_arn
  environment = "production"
}

module "kms" {
  source = "../../modules/kms"
  key_name = "tca-production-key"
  description = "TCA production data encryption"
  deletion_window_in_days = 30
  enable_key_rotation = true
}

module "eks" {
  source = "../../modules/eks"
  cluster_name = "tca-cluster"
  vpc_id = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  worker_instance_type = "r5.4xlarge"
  worker_desired_size = 3
  worker_max_size = 6
  worker_min_size = 2
  depends_on = [module.kms]
}

module "iam" {
  source = "../../modules/iam"
  eks_cluster_name = module.eks.cluster_name
  eks_oidc_provider = module.eks.oidc_provider
  s3_bucket_arn = module.s3.bucket_arn
  kms_key_arn = module.kms.key_arn
}

module "glue" {
  source = "../../modules/glue"
  database_name = "tca_production"
}

module "monitoring" {
  source = "../../modules/monitoring"
  cluster_name = module.eks.cluster_name
  slack_webhook_url = var.slack_webhook_url  # from vault/SSM
}

module "budgets" {
  source = "../../modules/budgets"
  budget_amount = 8000  # monthly USD
  notification_email = "tca-alerts@privatebank.de"
}
EOF
```

#### Step 2: Terraform Apply

```bash
# Initialize
terraform init

# Plan (review changes)
terraform plan -out=tfplan

# Apply (approve changes)
terraform apply tfplan
```

**Expected Output** (after 15-20 minutes):
```
Outputs:
vpc_id = "vpc-0a1b2c3d4e5f6g7h8"
private_subnet_ids = ["subnet-aaa", "subnet-bbb", "subnet-ccc"]
s3_bucket_name = "tca-production-data-a1b2c3d4"
eks_cluster_endpoint = "https://xxx.gr7.eu-central-1.eks.amazonaws.com"
eks_cluster_name = "tca-cluster"
```

**Validation**:
```bash
# Test AWS connection
aws sts get-caller-identity

# Check VPC created
aws ec2 describe-vpcs --vpc-ids $VPC_ID

# Check S3 bucket
aws s3 ls s3://tca-production-data-xxx/

# Check EKS cluster
aws eks describe-cluster --name tca-cluster --region eu-central-1
```

**Success Criteria**:
- [ ] VPC with 3 private subnets in separate AZs
- [ ] S3 bucket with SSE-KMS encryption, versioning enabled
- [ ] EKS cluster running (status: ACTIVE)
- [ ] IAM roles created for EKS cluster & workers
- [ ] Glue Data Catalog database created
- [ ] CloudWatch/Budgets alerts configured

**Time Estimate**: 2 days (Terraform apply can take 30-45 min for full stack).

---

## Phase 1: Foundation (Weeks 1-2)

### Task 1.1: EKS Cluster Verification

**Owner**: DevOps Engineer  
**Duration**: 4 hours

1. Configure kubectl:
```bash
aws eks update-kubeconfig \
  --region eu-central-1 \
  --name tca-cluster

kubectl get nodes
# Expected: 3 nodes in READY state (after initial node group creation)
```

2. Install cluster add-ons:
```bash
# Storage class for persistent volumes (GP3 SSD)
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/aws-ebs-csi-driver/master/docs/example/StorageClass.yaml

# Metrics server for HPA
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# AWS Load Balancer Controller (if using LoadBalancer services)
kubectl apply -k github.com/aws/eks-charts/stable/aws-load-balancer-controller//crds?ref=master
helm repo add eks https://aws.github.io/eks-charts
helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName=tca-cluster \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller
```

3. Verify networking:
```bash
# Deploy test pod
kubectl run test-pod --image=nginx --restart=Never
kubectl exec test-pod -- curl -s https://aws.amazon.com  # should succeed
kubectl delete pod test-pod
```

---

### Task 1.2: Managed Trino Installation (Starburst)

**Owner**: DevOps Engineer + Data Engineer  
**Duration**: 2 days

#### Option A: Starburst on EKS (recommended)

**Step 1: Create license secret**:
```bash
kubectl create namespace starburst

# Starburst license received as license.json from sales
kubectl create secret generic starburst-license \
  --namespace starburst \
  --from-file=license.json=~/Downloads/starburst-license.json
```

**Step 2: Install Starburst via Helm**:
```bash
helm repo add starburst https://starburstdata.github.io/starburst-charts
helm repo update

cat > starburst-values.yaml <<'EOF'
# Cluster-wide settings
clusterName: tca-starburst

# Runtime config
starburst:
  coordinator:
    memory:
      heap: "8g"
      direct: "2g"
    resources:
      cpu: 4
      memory: "12Gi"
      
  worker:
    memory:
      heap: "24g"
      direct: "8g"
    resources:
      cpu: 16
      memory: "40Gi"
    replicas: 3
    autoscale:
      enabled: true
      minReplicas: 2
      maxReplicas: 12
      targetCPUUtilizationPercentage: 70

# Connect to Iceberg (Hive metastore = AWS Glue)
catalog:
  hive:
    metastore: glue
    metastore-type: glue
    connection:
      aws-access-key: ${AWS_ACCESS_KEY_ID}    # via IRSA, not hardcoded
      aws-secret-key: ${AWS_SECRET_ACCESS_KEY}
      aws-region: eu-central-1
      s3.endpoint: s3.eu-central-1.amazonaws.com

# Iceberg connector
connectors:
  iceberg:
    enabled: true
    hive-catalog-name: glue
    hive.metastore.uri: glue
    hive.region: eu-central-1

# Security
authentication:
  type: password  # for now, integrate with LDAP later
  password-authenticator:
    file:
      path: /etc/starburst/users.properties

authorization:
  type: file
  file:
    path: /etc/starburst/access-control.properties

# JVM options
jvm:
  extraArgs: ["-Dlog4j2.formatMsgNoLookups=true"]

# Resource groups
resourceGroups:
  - name: "tca_etl"
    cpuQuotaPerTask: 2
    memoryPerTaskLimit: "4GB"
    softMemoryLimit: "80%"
    maxQueued: 100
    maxRunning: 50
    schedulingPolicy: "fair"
  - name: "tca_analysts"
    cpuQuotaPerTask: 1
    memoryPerTaskLimit: "2GB"
    softMemoryLimit: "70%"
    maxQueued: 50
    maxRunning: 25
    schedulingPolicy: "fair"

license:
  existingSecret: starburst-license
  key: license.json
EOF

helm install starburst starburst/starbust \
  --namespace starburst \
  --values starburst-values.yaml \
  --timeout 15m
```

Wait for pods:
```bash
kubectl get pods -n starburst -w
# All pods should reach READY state (~5-10 min)
```

**Step 3: Verify Starburst Health**:
```bash
# Port-forward coordinator
kubectl port-forward -n starburst svc/starburst-coordinator 8080:8080 &
sleep 2

# Test connectivity
curl http://localhost:8080/v1/info
# Expected: {"environment":"prod","nodeId":"...","nodeType":"coordinator",...}

# Trino CLI (install locally)
curl -O https://repo1.maven.org/maven2/io/trino/trino-cli/403/trino-cli-403-executable.jar
chmod +x trino-cli-403-executable.jar
./trino-cli-403-executable.jar --server localhost:8080 --catalog iceberg --schema tca_production -e "SELECT 1"
# Should return: 1
```

**Step 4: Configure Hive Metastore (Glue)**:
Validate S3 access:
```sql
-- In Trino CLI
SHOW CATALOGS;
-- Should see: iceberg, system, information_schema

SHOW SCHEMAS FROM iceberg;
-- Initially empty, but should not error
```

If errors: Check IAM roles for EKS worker nodes have S3 read/write access.

**Success Criteria**:
- [ ] Starburst coordinator + 3 workers running
- [ ] Trino CLI can connect, run `SELECT 1`
- [ ] Iceberg catalog visible in SHOW CATALOGS
- [ ] Glue Data Catalog access working (can create table)

---

### Task 1.3: S3 Bucket Structure & Permissions

**Owner**: DevOps Engineer  
**Duration**: 1 day

#### Iceberg Directory Layout

```
s3://tca-production-data-xxx/
├── iceberg/
│   ├── tca_production/
│   │   ├── db/
│   │   │   ├── tca_results/
│   │   │   │   ├── data/
│   │   │   │   │   ├── date=2025-04-01/asset_class=equity/part-00000-xxx.parquet
│   │   │   │   │   └── ...
│   │   │   │   ├── metadata/
│   │   │   │   │   └── metadata file (manifest)
│   │   │   │   └── snapshots/
│   │   │   ├── fct_orders/
│   │   │   └── mart_tca_input/
│   │   └── hive/
│   │       └── tca_production.db/
├── raw/
│   ├── orders/
│   ├── fills/
│   └── benchmarks/
├── dbt/
│   └── target/  # dbt compiled models (optional)
└── backups/
    └── iceberg-snapshots-archive/
```

**Terraform creates initial S3 bucket**. Now create subfolders and IAM policies:

```bash
# Create diretory structure (S3 folders are just prefixes)
aws s3 cp --recursive scripts/create-s3-folders.sh s3://tca-production-data-xxx/

cat > scripts/create-s3-folders.sh <<'SCRIPT'
#!/bin/bash
BUCKET="tca-production-data-xxx"
aws s3api put-object --bucket $BUCKET --key iceberg/
aws s3api put-object --bucket $BUCKET --key iceberg/tca_production/
aws s3api put-object --bucket $BUCKET --key iceberg/tca_production/db/
aws s3api put-object --bucket $BUCKET --key raw/orders/
aws s3api put-object --bucket $BUCKET --key raw/fills/
aws s3api put-object --bucket $BUCKET --key raw/benchmarks/
aws s3api put-object --bucket $BUCKET --key backups/
echo "S3 folder structure created"
SCRIPT

chmod +x scripts/create-s3-folders.sh
./scripts/create-s3-folders.sh

# Verify
aws s3 ls s3://tca-production-data-xxx/iceberg/tca_production/db/ --recursive
```

**IAM Policy for Starburst Workers** (least privilege):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "IcebergReadWrite",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::tca-production-data-xxx",
        "arn:aws:s3:::tca-production-data-xxx/*"
      ]
    },
    {
      "Sid": "GlueCatalogAccess",
      "Effect": "Allow",
      "Action": [
        "glue:GetDatabase",
        "glue:GetDatabases",
        "glue:CreateTable",
        "glue:UpdateTable",
        "glue:GetTable",
        "glue:GetTableVersions",
        "glue:GetPartitions",
        "glue:CreatePartition",
        "glue:BatchCreatePartition"
      ],
      "Resource": "*"
    },
    {
      "Sid": "KMSTouch",
      "Effect": "Allow",
      "Action": [
        "kms:Encrypt",
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:DescribeKey"
      ],
      "Resource": [aws_kms_key.tca.arn]
    }
  ]
}
```

Apply this policy to EKS worker node IAM role created by Terraform module.

**Success Criteria**:
- [ ] S3 bucket directories created
- [ ] IAM policy attached to worker role
- [ ] Test: Starburst worker pod can write to S3 (run `SELECT * FROM iceberg.tca_production.db.mytable LIMIT 1` after creating test table)

---

### Task 1.4: Starburst Security Configuration

**Owner**: Security Engineer + DevOps  
**Duration**: 2 days

#### 1. Fine-Grained Access Control

Create `access-control.properties` ConfigMap:

```yaml
# kubernetes/starburst/configmap-access-control.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: starburst-access-control
  namespace: starburst
data:
  access-control.properties: |
    # Resource groups
    resource-groups=file:/etc/starburst/resource-groups.properties
    
    # Role-based access
    authorizer=file
    file.authorizer.config-path=/etc/starburst/authorizer
```

Create authorizer rules:
```yaml
# kubernetes/starburst/authorizer/rules.json
{
  "catalog": {
    "tca_production": {
      "users": {
        "analyst1": ["SELECT"],
        "analyst2": ["SELECT"]
      },
      "roles": {
        "tca_etl": ["INSERT", "UPDATE", "DELETE", "CREATE", "DROP"]
      }
    }
  },
  "schemas": {
    "tca_production": {
      "tca_results": {
        "users": {
          "analyst1": {
            "columns": ["order_id", "trader_id", "cost_total"],  # column-level restriction
            "filter": "trader_id = 'TRADER_A'"  # row-level security
          }
        }
      }
    }
  }
}
```

Mount as volume to Starburst coordinator pod:
```yaml
# starburst-values.yaml addition
coordinator:
  configOverrides:
    config.properties: |
      access-control.config-path=/etc/starburst/access-control.properties
  volumes:
    - name: access-control
      configMap:
        name: starburst-access-control
  volumeMounts:
    - name: access-control
      mountPath: /etc/starburst
      readOnly: true
```

Upgrade Helm release:
```bash
helm upgrade starburst starburst/starburst \
  --namespace starburst \
  --values starburst-values.yaml
```

---

#### 2. HTTPS & TLS

Starburst supports HTTPS via self-signed cert or Let's Encrypt:

```bash
# Generate self-signed cert for internal VPC (valid, just not CA-signed)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tca.key -out tca.crt \
  -subj "/CN=starburst.tca.svc.cluster.local"

# Create K8s TLS secret
kubectl create secret tls starburst-tls \
  --cert=tca.crt \
  --key=tca.key \
  --namespace starburst
```

Update Helm values:
```yaml
server:
  https:
    enabled: true
    keyStorePath: /etc/starburst/keystore.jks
    keyStorePassword: ${KEYSTORE_PASSWORD}  # set via secret
```

**Internal VPC**: Self-signed OK (private network). For external access, use Kubernetes Ingress + Let's Encrypt cert.

---

#### 3. Authentication

Phase 1: Password-based (simple). Phase 4: Integrate with LDAP/Active Directory.

Create `users.properties`:
```
analyst1:password123, analyst1@privatebank.de, ANALYST
analyst2:password456, analyst2@privatebank.de, ANALYST
etl_user:etl_pass, etl@privatebank.de, ETL
```

As secret:
```bash
kubectl create secret generic starburst-users \
  --from-file=users.properties=./users.properties \
  --namespace starburst
```

Mount to coordinator pod.

**Next Phase**: Replace with LDAP bind:
```properties
password-authenticator.type=ldap
ldap.url=ldaps://ldap.privatebank.de:636
ldap.user=cn=admin,dc=privatebank,dc=de
ldap.password-file=/etc/starburst/ldap.pwd
```

---

**Week 1-2 Success Criteria**:
- [ ] Starburst cluster running (1 coordinator + 3 workers)
- [ ] HTTPS enabled (self-signed OK)
- [ ] User/password authentication working
- [ ] Fine-grained access control tested (analyst1 cannot see analyst2 data)
- [ ] S3 access via VPC endpoint (no internet egress)
- [ ] Glue Catalog connectivity confirmed
- [ ] Monitoring (Prometheus scraping metrics)

**If any fail**: Debug with Starburst support (included in license).

---

## Phase 2: Data Lakehouse Migration (Weeks 3-5)

### Task 2.1: dbt Adapter Migration

**Owner**: Data Engineer  
**Duration**: 2 days

#### Step 1: Update `dbt_project.yml`

Change nothing substantial; dbt-iceberg adapter uses same Jinja macros.

Add adapter requirement:
```yaml
# requirements.txt
dbt-core==1.8.0
dbt-iceberg==1.5.0
trino[sqlalchemy]==0.327.0
```

Install:
```bash
pip install -r requirements.txt
dbt debug --target prod  # should succeed
```

---

#### Step 2: `profiles.yml` Configuration

```yaml
tca:
  target: prod
  outputs:
    dev:
      type: iceberg
      catalog: hive
      schema: tca_dev
      warehouse: s3://tca-dev-data-xxx/
      presto_host: starburst-coordinator.starburst.svc.cluster.local
      presto_port: 8080
      presto_catalog: iceberg
      presto_schema: tca_dev
      auth: password  # or none if using IAM
    prod:
      type: iceberg
      catalog: hive
      schema: tca_production
      warehouse: s3://tca-production-data-xxx/
      presto_host: starburst-coordinator.starburst.svc.cluster.local
      presto_port: 8080
      presto_catalog: iceberg
      presto_schema: tca_production
      # In-cluster: use service account IAM role (no auth needed)
      use_aws_secret_attribution: true
      # Outside cluster: use password auth
      # auth: password
```

**Note**: `catalog: hive` means use Hive Metastore (Glue). Iceberg is the connector.

---

#### Step 3: Model Compatibility Check

Some PostgreSQL-specific SQL in PoC models may need adjustment:

**Common incompatibilities**:
1. `||` string concatenation → `CONCAT()` or `||` works in Trino
2. `::date` cast → `CAST(x AS DATE)`
3. `COALESCE()` works same
4. Window functions: identical syntax
5. `generate_series()` → Trino equivalent `sequence()` or join to `UNNEST`

Run dbt compile to find errors:
```bash
dbt compile --target prod
# Inspect compiled SQL in target/compiled/
grep -r "ERROR" target/compiled/  # should be none
```

Fix any failing models.

---

#### Step 4: Test with Sample Data

Load 100 synthetic orders (use PoC generator):
```bash
python ingestion/seed.py \
  --synthetic-only \
  --count 100 \
  --output /tmp/sample_data/
```

Upload to S3:
```bash
aws s3 cp /tmp/sample_data/ s3://tca-production-data-xxx/raw/ --recursive
```

Run dbt for 100-order subset:
```bash
dbt run \
  --select stg_orders stg_fills stg_benchmarks \
  --target prod \
  --threads 4
```

**Expected**: ~1 minute for 3 staging models.

Continue building up:
```bash
dbt run --select dim_* --target prod
dbt run --select fct_* --target prod
dbt run --select mart_* --target prod
```

**Validate**:
```bash
# Row counts
dbt run-operation row_counts --target prod

# Metrics sanity check
dbt test --select tca_metrics_correctness --target prod
```

**Success Criteria**:
- [ ] dbt build completes without errors (all 20+ models)
- [ ] Row counts match expectations (hundreds, not millions yet)

---

### Task 2.2: Iceberg Table Optimization

**Owner**: Data Engineer  
**Duration**: 2 days

#### Strategy Session

Review each model's configuration:

**Staging models** (`stg_*`): Raw data, full refresh acceptable
```sql
{{ config(
    materialized='table',
    file_format='parquet',
    format_compression='ZSTD(3)',
    partitioning=['date(_etl_loaded_at)']
) }}
```

**Dimension models** (`dim_*`): Small, rarely change, table materialization
```sql
{{ config(
    materialized='table',
    partition_by={'date': 'date(_etl_loaded_at)'}
) }}
```

**Fact models** (`fct_*`): Large, time-series, incremental
```sql
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    partitioning=['date(event_timestamp)', 'asset_class'],
    file_format='parquet',
    format_compression='ZSTD(3)',
    z_order_cols=['order_id', 'symbol', 'event_timestamp'],
    location='s3://tca-production-data-xxx/iceberg/tca_production/db/fct_orders/'
) }}
```

**Mart models** (`mart_*`): Denormalized for analytics, incremental with overwrite per partition
```sql
{{ config(
    materialized='incremental',
    incremental_strategy='insert_overwrite',
    partitioning=['date(trade_date)'],
    file_format='parquet',
    z_order_cols=['order_id']
) }}
```

**Critical**: Add `date` partition to EVERY large table to enable partition pruning.

---

#### Partition Design Decision

TCA queries typically filter by date range (1 day to 5 years). Partition by:

**Option A**: Date only (daily partitions)
- Pros: Simple, even splits, good for time-range queries
- Cons: 1,825 partitions for 5 years; Queries scanning multiple asset classes hit many small partitions

**Option B**: Date + asset_class (composite partition)
- Pros: ~7,300 partitions — still manageable; Queries for single asset class hit only 1/4 of partitions
- Cons: Slightly more metadata overhead
- **Recommendation**: Use date + asset_class (composite)

**Implementation**:
```sql
{% set partition_cols = ["date(trade_timestamp)", "asset_class"] %}

{{ config(
    materialized='incremental',
    incremental_strategy='append',
    partitioning=partition_cols,
    ...
) }}
```

---

### Task 2.3: 5-Year Historical Data Ingestion

**Owner**: Data Engineer + Backend Engineer  
**Duration**: 3 days

#### Step 1: Generate Historical Data

Modify PoC generator to output 5 years of data:

```python
# ingestion/generate_historical.py (NEW)
import argparse
from datetime import datetime, timedelta
from generators import equity_generator, equity_future_generator, fixed_income_generator, fx_derivative_generator

def generate_range(start_date: str, end_date: str, output_dir: str):
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    while current < end:
        # Generate 1 day of orders (100 orders per class per day? unrealistic)
        # Better: generate full 5-year dataset once with historical random seed
        current += timedelta(days=1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)  # 2020-01-01
    parser.add_argument("--end-date", required=True)    # 2025-01-01
    parser.add_argument("--output", default="/data/historical/")
    args = parser.parse_args()
    generate_range(args.start_date, args.end_date, args.output)
```

But wait — better to **backfill via dbt incremental strategy** rather than pre-generate raw data.

**Recommended backfill method**:
1. Keep PoC `seed.py` as-is (generates 400 orders — used for dev)
2. For production backfill, modify dbt models to use `full-refresh` on historical period

---

#### Step 2: Backfill Strategy Decision

**Option A: Backfill raw data first, then dbt pipeline**
- Generate raw orders/fills/benchmarks for 5-year period
- Load directly to S3 in partitioned layout
- Run dbt `full-refresh` once
- Pros: Simpler, single full-refresh; Cons: Needs raw data generation script for 5 years

**Option B: Use existing PoC data and expand via sensible distributions**
- Existing PoC data representative of full 5-year dataset
- Duplicate it with date shifts to fill 5 years
- Faster to implement
- Cons: Less realistic timeline

**Recommendation**: Option A — write historical generator (2 days effort).

---

**Historical Data Generator** (`ingestion/backfill_generator.py`):

```python
#!/usr/bin/env python3
"""
Generate 5 years of synthetic TCA data for backfill validation.
Orders: 730K/year × 5 = 3.65M orders
Benchmarks: 30s intervals × 8h trading day × 1,825 days = 5.26M benchmarks
Fills: ~4 fills per order average = ~14.6M fills
Output: Parquet files written directly to S3 partitioned by date
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pyarrow as pa
import pyarrow.parquet as pq
import boto3
from botocore.config import Config
import os
from tqdm import tqdm

# Config
BUCKET = os.environ["S3_BUCKET"]
AWS_REGION = os.environ.get("AWS_REGION", "eu-central-1")
OUTPUT_PREFIX = "raw/"

# Trading hours CET: 09:00-17:30 = 8.5 hours × 3600 / 30 = 1,020 ticks/day
TRADING_SECONDS_PER_DAY = 8.5 * 3600
TICK_INTERVAL = 30
TICKS_PER_DAY = int(TRADING_SECONDS_PER_DAY / TICK_INTERVAL)  # 1,020

def generate_orders(start_date: datetime, end_date: datetime, orders_per_day: int = 200):
    """Generate order flow for date range."""
    orders = []
    fills = []
    current_date = start_date
    
    while current_date < end_date:
        # 200 orders/day × 5 asset classes = ~40 each class
        asset_classes = ['equity', 'equity_future', 'fixed_income', 'fx_derivative']
        
        for i in range(orders_per_day):
            order = {
                'order_id': f"ORD-{current_date.strftime('%Y%m%d')}-{i:06d}",
                'asset_class': np.random.choice(asset_classes),
                'side': np.random.choice(['BUY', 'SELL'], p=[0.55, 0.45]),
                'order_type': np.random.choice(['LIMIT', 'MARKET'], p=[0.3, 0.7]),
                'quantity': np.random.randint(1000, 100000),
                'currency': 'EUR',
                'trader_id': f"TRADER-{np.random.randint(1, 21):02d}",
                'algo_id': f"ALGO-{np.random.choice(['VWAP', 'TWAP', 'ImplementationShortfall'])}",
                'decision_price': round(np.random.uniform(10, 500), 2),
                'submission_time': current_date.replace(
                    hour=np.random.randint(9, 17),
                    minute=np.random.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55])
                ),
                'mifid_class': np.random.choice(['equity', 'equity_derivative', 'bond', 'fx_derivative'])
            }
            orders.append(order)
            
            # Generate 1-5 fills per order
            num_fills = np.random.randint(1, 6)
            for fill_num in range(num_fills):
                fill_time = order['submission_time'] + timedelta(minutes=np.random.exponential(30))
                fill = {
                    'fill_id': f"FILL-{order['order_id']}-{fill_num}",
                    'order_id': order['order_id'],
                    'price': round(order['decision_price'] + np.random.uniform(-0.5, 0.5), 5),
                    'quantity': order['quantity'] // num_fills,
                    'venue': np.random.choice(['XETRA', 'EUREX', 'BGC', 'FXALL']),
                    'fees_bps': np.random.uniform(1, 10),
                    'is_dark': np.random.choice([True, False], p=[0.1, 0.9]),
                    'is_si': np.random.choice([True, False], p=[0.05, 0.95]),
                    'liquidity_flag': np.random.choice(['ADDED', 'REMOVED', 'PASSIVE']),
                    'fill_time': fill_time
                }
                fills.append(fill)
        
        current_date += timedelta(days=1)
    
    return pd.DataFrame(orders), pd.DataFrame(fills)

def generate_benchmarks(start_date: datetime, end_date: datetime):
    """Generate 30-second tick data for all symbols."""
    # For simplicity: single benchmark per asset class per tick
    benchmarks = []
    current_date = start_date
    
    while current_date < end_date:
        for tick in range(TICKS_PER_DAY):
            timestamp = current_date.replace(hour=9, minute=0, second=0) + timedelta(seconds=tick * 30)
            
            for asset_class in ['equity', 'equity_future', 'fixed_income', 'fx_derivative']:
                benchmark = {
                    'symbol': f"SYM-{asset_class[:3].upper()}",
                    'timestamp': timestamp,
                    'bid': round(np.random.uniform(100, 200), 5),
                    'ask': round(np.random.uniform(200.01, 300), 5),
                    'mid': None,  # computed
                    'vwap_cumulative': round(np.random.uniform(150, 250), 5),
                    'edsp': round(np.random.uniform(200, 300), 5) if asset_class == 'equity_future' else None,
                    'yield_mid': round(np.random.uniform(1, 5), 4) if asset_class == 'fixed_income' else None
                }
                benchmark['mid'] = (benchmark['bid'] + benchmark['ask']) / 2
                benchmarks.append(benchmark)
        
        current_date += timedelta(days=1)
    
    return pd.DataFrame(benchmarks)

def write_partitioned_parquet(df: pd.DataFrame, bucket: str, prefix: str, partition_cols: list):
    """Write DataFrame to S3 partitioned by partition_cols."""
    s3_client = boto3.client('s3', region_name=AWS_REGION)
    
    # Group by partitions
    for partition_vals, group in df.groupby(partition_cols):
        if not isinstance(partition_vals, tuple):
            partition_vals = (partition_vals,)
        
        # Build S3 key
        partition_path = "/".join([f"{col}={val}" for col, val in zip(partition_cols, partition_vals)])
        key = f"{prefix}{partition_path}/data.parquet"
        
        # Convert to Arrow and write Parquet
        table = pa.Table.from_pandas(group, preserve_index=False)
        buf = pa.BufferOutputStream()
        pq.write_table(table, buf, compression='zstd')
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=buf.getvalue().to_pybytes(),
            ContentType='application/octet-stream'
        )
        print(f"Written {len(group)} rows to s3://{bucket}/{key}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2025-01-01")
    parser.add_argument("--bucket", default=BUCKET)
    args = parser.parse_args()
    
    start = datetime.strptime(args.start_date, "%Y-%m-%d")
    end = datetime.strptime(args.end_date, "%Y-%m-%d")
    
    print("Generating orders & fills...")
    orders_df, fills_df = generate_orders(start, end)
    print(f"Generated {len(orders_df)} orders, {len(fills_df)} fills")
    
    print("Generating benchmarks...")
    benchmarks_df = generate_benchmarks(start, end)
    print(f"Generated {len(benchmarks_df)} benchmark records")
    
    print("Writing to S3...")
    # Orders partitioned by date(event_date)
    orders_df['event_date'] = pd.to_datetime(orders_df['submission_time']).dt.date
    write_partitioned_parquet(orders_df, args.bucket, "raw/orders/", ['event_date'])
    
    # Fills partitioned by fill_date
    fills_df['fill_date'] = pd.to_datetime(fills_df['fill_time']).dt.date
    write_partitioned_parquet(fills_df, args.bucket, "raw/fills/", ['fill_date'])
    
    # Benchmarks partitioned by date
    benchmarks_df['event_date'] = pd.to_datetime(benchmarks_df['timestamp']).dt.date
    write_partitioned_parquet(benchmarks_df, args.bucket, "raw/benchmarks/", ['event_date', 'symbol'])
    
    print("Backfill generation complete.")
```

---

#### Step 3: Run Backfill Generation

```bash
# Install dependencies
pip install pandas pyarrow boto3 tqdm

# Run (in background, may take several hours for 5 years)
python ingestion/backfill_generator.py \
  --start-date 2020-01-01 \
  --end-date 2025-01-01 \
  --bucket $S3_BUCKET

# Monitor S3 write progress
aws s3 ls s3://tca-production-data-xxx/raw/orders/ --recursive | wc -l
```

**Expected**:
- ~730K order Parquet files (partitioned by 1,825 dates)
- ~14.6M fill Parquet files
- Total: ~5-8 GB compressed (synthetic data is sparse)

**Duration**: 2-4 hours writing 15M rows × 3 datasets in Parquet.

---

#### Step 4: dbt Full-Refresh on Historical Data

```bash
# Drop existing tables if any (Iceberg supports DROP)
dbt run-operation drop_staging --target prod   # custom macro if needed

# Full refresh all models
dbt run --full-refresh --threads 8 --target prod
```

**Thread count**: Iceberg + Starburst can handle 8 concurrent dbt models. Monitor worker CPU, adjust.

**Expected runtime**:
- Staging: 1-2 hours (scanning raw S3 data)
- Dimensions: 15 minutes (small reference tables)
- Facts: 4-6 hours (joins across large tables)
- Marts: 1-2 hours
- **Total**: ~12 hours sequential; with `--threads 8` potentially 4-6 hours

**Monitor via Starburst UI** (port-forward):
```bash
kubectl port-forward -n starburst svc/starburst-coordinator 8080:8080
# Open http://localhost:8080 in browser
# See running queries, stages, workers, bytes processed
```

**If queries are slow**:
1. Check if partitions are being pruned (query should only scan relevant dates)
2. Check if workers have data in cache (warming needed?)
3. Increase workers to 6 temporarily

---

#### Step 5: Validate Row Counts

```bash
# dbt test row counts
dbt test --select test_not_null --target prod  # should pass
dbt test --select test_unique --target prod   # should pass

# Custom validation script
python scripts/validate_backfill.py --target prod
```

`validate_backfill.py`:
```python
#!/usr/bin/env python3
from trino import dbapi
import os

conn = dbapi.connect(
    host=os.environ["TRINO_HOST"],
    port=8080,
    catalog="iceberg",
    schema="tca_production",
    user="analyst1",
    password=os.environ["TRINO_PASSWORD"]
)

cursor = conn.cursor()

# Expected counts
expected = {
    'fct_orders': 3_650_000,
    'fct_fills': 14_600_000,
    'fct_benchmarks': 5_260_000,
    'tca_results': 3_650_000
}

for table, expected_count in expected.items():
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    actual = cursor.fetchone()[0]
    match = "✓" if actual == expected_count else "✗"
    print(f"{match} {table}: {actual:,} (expected {expected_count:,})")
    if actual != expected_count:
        print(f"  ERROR: Count mismatch!")
```

**Success Criteria**:
- [ ] All counts match expectations
- [ ] Spot-check 10 random orders: Metrics in `tca_results` reasonable (costs not null, grades A-F)
- [ ] Starburst not overloaded (workers <80% CPU during backfill)

---

**Phase 2 Success Criteria** (Week 5 end):
- [ ] 5 years of synthetic data loaded into Iceberg (8-10TB total)
- [ ] dbt pipeline runs `full-refresh` in <8 hours
- [ ] All models pass data quality tests
- [ ] Spot-check metrics match PoC baseline (within 1% tolerance)
- [ ] Iceberg snapshots: `CALL system.snapshots('tca_results')` → 1 active snapshot

---

## Phase 3: Application Migration (Weeks 6-7)

### Task 3.1: FastAPI Migration to Trino

**Owner**: Backend Engineer  
**Duration**: 2 days

#### Update Connection Pool

Replace PostgreSQL SQLAlchemy with Trino SQLAlchemy dialect:

```python
# app/db.py (UPDATED)
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from trino.sqlalchemy import URL  # <-- NEW

# Option A: In-cluster (IRSA — no password)
if os.getenv("KUBERNETES_SERVICE_HOST"):
    engine = create_engine(
        URL(
            host=os.environ["TRINO_HOST"],  # starburst-coordinator.starburst.svc.cluster.local
            port=8080,
            catalog="iceberg",
            schema="tca_production",
            http_scheme="https",
            # Auth: use IAM role via IRSA (no credentials in code)
            auth="AWSIAM"
        ),
        isolation_level="AUTOCOMMIT",
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
# Option B: External access (password)
else:
    engine = create_engine(
        URL(
            host=os.environ["TRINO_HOST"],  # external LB DNS
            port=443,
            catalog="iceberg",
            schema="tca_production",
            http_scheme="https",
            auth=BasicAuthentication(
                os.environ["TRINO_USER"],
                os.environ["TRINO_PASSWORD"]
            )
        )
    )

@contextmanager
def get_db() -> Generator:
    with engine.connect() as conn:
        yield conn
```

**Install dependency**:
```bash
pip install trino[sqlalchemy]==0.327.0
```

---

#### Test Connection

```python
# scripts/test_trino_connection.py
from app.db import engine
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execute(text("SELECT 1 AS test"))
    print(result.fetchone()['test'])  # should print 1
```

---

#### Verify Query Compatibility

All queries must be compatible with Trino SQL dialect (mostly standard ANSI).

**Common PostgreSQL→Trino adjustments**:

| PostgreSQL | Trino equivalent | Notes |
|---|---|---|
| `::date` cast | `CAST(x AS DATE)` or `DATE(x)` | Trino supports `::` but prefer explicit |
| `||` string concat | `CONCAT(a, b)` or `||` | `||` works in Trino |
| `INTERVAL '1 day'` | Same | OK |
| `NOW()` | `now()` | lowercase |
| `GENERATE_SERIES()` | Not supported in Trino (use `sequence()` if needed) | TCA likely not used |
| `ARRAY_AGG(DISTINCT x)` | Same | OK |
| `DATE_TRUNC('day', ts)` | `date_trunc('day', ts)` | Same |
| `EXTRACT(EPOCH FROM ts)` | `to_unixtime(ts)` | Different function |

Search codebase for PostgreSQL-specific constructs:
```bash
grep -r "GENERATE_SERIES" app/ analytics/
grep -r "::" app/ analytics/ | grep -v "http"
```

Fix any occurrences.

---

#### Update Query Performance

Iceberg performs best with **partition pruning**. Ensure all API endpoints filter by date:

```python
# app/routes/tca.py
@router.get("/order/{order_id}")
def get_order(order_id: str, db=Depends(get_db)):
    # Existing query fine:
    query = text("SELECT * FROM tca_results WHERE order_id = :oid")
    return db.execute(query, {"oid": order_id}).fetchall()
```

But summary endpoints often query full table:

```python
@router.get("/summary")
def get_summary(start_date: str, end_date: str, db=Depends(get_db)):
    # BAD: Without date filter, query full table (10TB scan, slow)
    # query = "SELECT asset_class, AVG(cost_total) FROM tca_results GROUP BY 1"
    
    # GOOD: Enforce date range
    query = text("""
        SELECT asset_class, AVG(cost_total) AS avg_cost
        FROM tca_results
        WHERE trade_date BETWEEN :start AND :end
        GROUP BY asset_class
    """)
```

If summary endpoints need to aggregate across all dates (rare for TCA), consider materialized view:

```sql
-- In Iceberg, create materialized aggregate table
CREATE TABLE tca_summary_daily (
    trade_date DATE,
    asset_class VARCHAR,
    avg_cost DECIMAL(18,6),
    order_count BIGINT
)
WITH (
    partitioning = ARRAY['date(trade_date)'],
    format = 'PARQUET'
) AS
SELECT
    trade_date,
    asset_class,
    AVG(cost_total) AS avg_cost,
    COUNT(*) AS order_count
FROM tca_results
GROUP BY 1, 2;
```

Refresh nightly via Airflow.

---

#### Deploy FastAPI to EKS

**Dockerfile** (unchanged from PoC):
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and push to ECR:
```bash
# ECR repository
aws ecr create-repository --repository-name tca-api --region eu-central-1

# Get login password
aws ecr get-login-password --region eu-central-1 | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.eu-central-1.amazonaws.com

# Build
docker build -t tca-api:latest .
docker tag tca-api:latest $AWS_ACCOUNT_ID.dkr.ecr.eu-central-1.amazonaws.com/tca-api:latest

# Push
docker push $AWS_ACCOUNT_ID.dkr.ecr.eu-central-1.amazonaws.com/tca-api:latest
```

**K8s Deployment**:
```yaml
# kubernetes/fastapi-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tca-api
  namespace: tca
  labels:
    app: tca-api
spec:
  replicas: 3  # HA
  selector:
    matchLabels:
      app: tca-api
  template:
    metadata:
      labels:
        app: tca-api
    spec:
      containers:
      - name: api
        image: ${AWS_ACCOUNT_ID}.dkr.ecr.eu-central-1.amazonaws.com/tca-api:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL  # deprecated, use TRINO_* instead
          valueFrom:
            secretKeyRef:
              name: tca-db-secret
              key: url
        - name: TRINO_HOST
          value: "starburst-coordinator.starburst.svc.cluster.local"
        - name: TRINO_PORT
          value: "8080"
        - name: REDIS_HOST
          value: "tca-redis-master.tca.svc.cluster.local"
        resources:
          requests:
            cpu: "500m"  # 0.5 vCPU
            memory: "1Gi"
          limits:
            cpu: "1000m"  # 1 vCPU
            memory: "2Gi"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
        envFrom:
        - secretRef:
            name: tca-db-secret  # Contains TRINO credentials if using password auth
---
apiVersion: v1
kind: Service
metadata:
  name: tca-api
  namespace: tca
spec:
  selector:
    app: tca-api
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP  # Ingress will expose externally
```

Apply:
```bash
kubectl apply -f kubernetes/fastapi/deployment.yaml
kubectl get pods -n tca -w
# Wait for 3 pods READY
```

**Test from inside cluster**:
```bash
kubectl run test -n tca --image=curlimages/curl --rm -it -- \
  curl http://tca-api.tca.svc.cluster.local:80/health
# Should return: {"status":"ok"}
```

---

#### External Access via Ingress

```yaml
# kubernetes/fastapi/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tca-api-ingress
  namespace: tca
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internal  # private VPC only
    alb.ingress.kubernetes.io/target-type: ip
spec:
  rules:
  - host: tca-api.internal.privatebank.de  # internal DNS
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: tca-api
            port:
              number: 80
```

For external (corporate) access, use VPN or internal ALB with SSO integration later.

---

### Task 3.2: Airflow Migration to Managed Service

**Owner**: Data Engineer  
**Duration**: 2 days

#### Option: AWS Managed Workflows for Apache Airflow (MWAA)

**Why MWAA**: Fully managed, integrates with IAM, VPC, SCM (Git), AWS Secrets Manager.

**Step 1: Create MWAA Environment**

```bash
# Create S3 bucket for DAGs + plugins
aws s3 mb s3://tca-airflow-dags-xxx --region eu-central-1

# Upload initial DAGs
aws s3 sync dags/ s3://tca-airflow-dags-xxx/dags/
aws s3 sync plugins/ s3://tca-airflow-dags-xxx/plugins/

# Create MWAA environment (CLI)
aws mwaa create-environment \
  --name tca-airflow \
  --execution-role-arn arn:aws:iam::${AWS_ACCOUNT_ID}:role/TCA-Airflow-Execution \
  --network-configuration "SubnetIds=[subnet-xxx,subnet-yyy,subnet-zzz],SecurityGroupIds=[sg-airflow]" \
  --source-bucket-arn arn:aws:s3:::tca-airflow-dags-xxx \
  --dag-s3-path dags/ \
  --plugins-s3-path plugins/ \
  --requirements-file requirements.txt \
  --airflow-version 2.7.2 \
  --logging-configuration "DagProcessingLogs:S3KeyPrefix=logs/dag_processing/,TaskLogs:S3KeyPrefix=logs/task_logs/" \
  --min-workers 2 \
  --max-workers 10 \
  --scheduler-count 1
```

Wait 25-30 minutes for environment provisioning.

**Step 2: Configure Airflow Connections**

Via MWAA UI (Sedente) or through `airflow connections` CLI:

```bash
# Conn ID: trino_default
aws mwaa create-cli-token --name tca-airflow
# Use token with Airflow CLI to add connection

airflow connections add 'trino_default' \
  --conn-uri 'trino://user:pass@starburst-coordinator.starburst.svc.cluster.local:8080/iceberg/tca_production'
```

**Simpler**: Use MWAA Secrets Manager integration. Store Trino credentials in AWS Secrets Manager, reference in MWAA environment variables.

```bash
aws secretsmanager create-secret \
  --name /tca/airflow/trino-credentials \
  --secret-string '{"user":"etl_user","password":"***"}'
```

MWAA automatically mounts secrets as env vars.

**Step 3: Update DAGs for MWAA**

Modify PoC DAGs to use TrinoHook:

```python
# dags/eod_enrichment.py (UPDATED)
from airflow import DAG
from airflow.providers.trino.hooks.trino import TrinoHook
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'tca',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'retries': 2,
    'retry_delay': timedelta(minutes=10),
}

dag = DAG(
    'eod_enrichment',
    default_args=default_args,
    schedule_interval='30 18 * * 1-5',  # 18:30 CET, Mon-Fri
    catchup=False,
    max_active_runs=1,
)

def run_daily_tca(**context):
    trino = TrinoHook(trino_conn_id='trino_default')
    execution_date = context['ds']
    
    # Call TCA engine stored procedure or run analytics script
    trino.run(f"""
        CALL tca_production.run_daily_enrichment(
            date('{execution_date}')
        )
    """)
    
    # Or invoke Spark/Python job on EMR/K8s
    # TODO: Implement as PythonOperator

enrich_task = PythonOperator(
    task_id='run_enrichment',
    python_callable=run_daily_tca,
    provide_context=True,
    dag=dag,
)
```

**Daily enrichment** now calls stored procedure in Trino (or runs Python script on Fargate/EKS).

**Step 4: Add Monthly Backfill DAG**

```python
# dags/monthly_backfill.py
from airflow import DAG
from airflow.operators.bash import BashOperator
from datetime import datetime, timedelta, date

default_args = {
    'owner': 'tca-backfill',
    'depends_on_past': False,
    'start_date': datetime(2025, 1, 1),
    'retries': 1,
    'retry_delay': timedelta(minutes=30),
    'email_on_failure': True,
    'email': ['tca-alerts@privatebank.de']
}

dag = DAG(
    'monthly_tca_backfill',
    default_args=default_args,
    description='Monthly TCA backfill for previous month with corrected data',
    schedule_interval='0 6 1 * *',  # 1st of month at 06:00 CET
    catchup=True,
    max_active_runs=1,
)

backfill_command = """
export PYTHONPATH=/opt/airflow/plugins
cd /opt/airflow/plugins
python analytics/engine.py \
  --start-date {{ macros.ds_add(ds, -30) }} \
  --end-date {{ ds }} \
  --recalculate \
  --output-mode iceberg
"""

backfill_task = BashOperator(
    task_id='run_monthly_tca',
    bash_command=backfill_command,
    dag=dag,
)
```

**Deploy DAGs**:
```bash
aws s3 sync dags/ s3://tca-airflow-dags-xxx/dags/ --delete
```

MWAA picks up changes in ~1-2 minutes.

**Step 5: Validation**

Wait for next scheduled DAG run (or trigger manually):
```bash
# From Airflow UI (MWAA provides URL) or CLI
airflow dags trigger eod_enrichment --exec-date 2025-04-21
```

Monitor DAG run:
```bash
airflow tasks list eod_enrichment --tree
airflow tasks state eod_enrichment <task_id> <execution_date>
```

Check logs in MWAA UI → task instance → Log.

**Success Criteria**:
- [ ] eod_enrichment runs successfully on sample date
- [ ] DAG completes <30 minutes (vs PoC 5 min acceptable due to larger data)
- [ ] Monthly backfill DAG added, tested with 1-month backfill

---

### Task 3.3: FastAPI Deploy & Smoke Tests

**Owner**: Backend Engineer  
**Duration**: 1 day

**Step 1**: Deploy FastAPI (already covered in Task 3.1)

**Step 2**: Create K8s Service + Ingress for external access (internal only initially)

```yaml
# kubernetes/fastapi/ingress.yaml (internal ALB)
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: tca-api-alb
  namespace: tca
  annotations:
    kubernetes.io/ingress.class: alb
    alb.ingress.kubernetes.io/scheme: internal  # private VPC
    alb.ingress.kubernetes.io/target-type: ip
spec:
  rules:
  - host: tca-api.tca.internal  # internal DNS
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: tca-api
            port:
              number: 80
```

Create internal DNS entry in Route53 private hosted zone:
```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id /hosted zone/XXX \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "tca-api.tca.internal",
        "Type": "A",
        "AliasTarget": {
          "DNSName": "internal-tca-api-xxx.elb.eu-central-1.amazonaws.com",
          "HostedZoneId": "Z1234567890",
          "EvaluateTargetHealth": false
        }
      }
    }]
  }'
```

**Step 3**: Smoke tests

```bash
# Test from bastion host (or team VPN)
curl http://tca-api.tca.internal/v1/tca/order/ORD-20250421-000001
# Should return JSON (or 404 if not in backfilled data)

# Load test (parallel 10 users)
hey -c 10 -n 100 http://tca-api.tca.internal/v1/tca/summary?start_date=2025-04-01&end_date=2025-04-21

# Expected: P50 < 1s, P99 < 3s (larger than PoC due to Starburst query latency)
```

**Step 4**: Performance tuning

If queries slow (>5s P99):
- Check if Starburst workers saturated (increase from 3→6)
- Check if missing partition filter in query (fix API)
- Add caching: Redis already in PoC configuration — ensure Redis deployed in EKS

**Redis deployment** (unchanged):
```yaml
# kubernetes/redis-deployment.yaml (from PoC)
# Deploy with replicas: 1 master + 2 replicas
# Use Redis cluster mode for HA
```

**Success Criteria**:
- [ ] FastAPI reachable from corporate network (internal DNS)
- [ ] Health endpoint returns 200 OK
- [ ] 5 random orders return TCA data (from backfilled dataset)
- [ ] P99 query latency < 3 seconds
- [ ] All 10 API endpoints accessible

---

**Phase 3 Success Criteria** (Week 7 end):
- [ ] FastAPI deployed in EKS, connected to Starburst
- [ ] Redis cache operational
- [ ] Airflow DAGs migrated, MWAA environment running
- [ ] End-to-end pipeline: ingest new order → dbt build → analytics engine → API → reports
- [ ] Monthly backfill DAG tested and passes

---

## Phase 4: Production Hardening (Weeks 8-10)

### Task 4.1: Monitoring Stack

**Owner**: DevOps Engineer  
**Duration**: 2 days

#### Prometheus + Grafana Cloud

**Option A**: Self-host Prometheus + Alertmanager on EKS
**Option B**: Grafana Cloud (managed, $49-199/month) — recommended

**Choose Option B** (Grafana Cloud) to reduce ops burden.

1. Sign up at grafana.com (free tier included)
2. Create Cloud stack: `tca-production`
3. Add data source: Prometheus remote write endpoint URL
4. Install Grafana Agent on EKS to scrape metrics:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm install grafana-agent grafana/grafana-agent \
  --namespace monitoring \
  --set agents.prometheus.enabled=true \
  --set agents.prometheus.remoteWriteUrl=<GRAFANA_CLOUD_PROM_ENDPOINT> \
  --set agents.prometheus.scrapeInterval=30s
```

Configure scrape config for Starburst:
```yaml
# agents ConfigMap
prometheus:
  global:
    scrape_interval: 30s
  configs:
    - name: starburst
      remote_write:
        - url: <GRAFANA_CLOUD_PROM_ENDPOINT>
      scrape_configs:
        - job_name: 'starburst'
          static_configs:
            - targets: ['starburst-coordinator.starburst.svc.cluster.local:8080']
          metrics_path: /metrics
          scheme: https
          tls_config:
            insecure_skip_verify: true  # self-signed OK in VPC
```

**Dashboards to Import**:
1. Starburst cluster overview (CPU, memory, query count, failures)
2. Iceberg table metrics (file count, snapshot age, partition count)
3. TCA pipeline SLA (EOD completion time, backfill duration)
4. Cost metrics (S3 storage growth, monthly spend)

Grafana provides pre-built dashboard templates.

---

### Task 4.2: Cost Controls & Optimization

**Owner**: DevOps Engineer  
**Duration**: 1 day

#### AWS Budgets Alerts

Already created in Terraform. Verify:

```bash
aws budgets describe-budgets --account-id $AWS_ACCOUNT_ID
```

Ensure two alerts:
- 80% threshold → email to team
- 100% threshold → SNS to PagerDuty

**Test**: Temporarily set budget $1, then trigger alert to verify delivery.

---

#### S3 Lifecycle Policies

Already in Terraform:
- 90 days → S3 Standard-IA (cheaper)
- 180 days → Glacier
- 365 days → Glacier Deep Archive (archival, <1% cost of Standard)

Verify:
```bash
aws s3api get-bucket-lifecycle-configuration --bucket tca-production-data-xxx
```

Should show 3 rules.

---

#### Starburst Auto-Scaling (KEDA)

Starburst Helm chart may support autoscaling via KEDA:

```yaml
# values.yaml
worker:
  autoscale:
    enabled: true
    minReplicas: 2
    maxReplicas: 12
    # Scale based on queue size (pending queries)
    triggers:
      - type: prometheus
        metadata:
          serverAddress: http://prometheus-operated.monitoring.svc.cluster.local:9090
          metricName: trino_queued_queries
          query: sum(trino_queued_queries{cluster="starburst"})
          threshold: "10"  # scale up if >10 queued queries
```

Install KEDA:
```bash
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --namespace keda --create-namespace
```

---

#### Spot Instances for Cost Savings (Advanced)

If budget very tight, switch worker node group to Spot:

```bash
# EKS managed node group with Spot
eksctl create nodegroup \
  --cluster tca-cluster \
  --name starburst-spot \
  --node-type r5.4xlarge \
  --nodes 2 \
  --nodes-min 2 \
  --nodes-max 10 \
  --managed  \
  --spot
```

**Savings**: ~65% discount vs on-demand.

**Risk**: Spot interruption with 2-minute warning. Starburst workers stateless (can terminate safely); jobs retry on other workers. Acceptable risk.

---

### Task 4.3: Security & Compliance

**Owner**: Security Engineer  
**Duration**: 2 days

#### Penetration Test

Engage internal security team or external firm (e.g., Cure53, SecTiger):

**Scope**:
- Starburst coordinator API (8080)
- FastAPI endpoints (8000)
- S3 bucket (perimeter check — should be private)
- EKS worker nodes (should not have public IPs)

**Deliverable**: Report with findings, fix critical within 1 week.

---

#### Encryption Audit

Generate compliance report:

```bash
# Check all S3 objects encrypted
aws s3api head-object --bucket tca-production-data-xxx --key iceberg/tca_production/db/tca_results/data/part-00000.parquet | grep SSEKMSKeyId

# Check KMS key rotation
aws kms describe-key --key-id alias/tca-production-key | grep KeyRotationEnabled

# Check CloudTrail enabled for all regions
aws cloudtrail describe-trails | grep IsMultiRegionTrail
```

Document results in `docs/security-compliance.md`.

---

#### MiFID Audit Trail Validation

Iceberg time travel provides snapshot history. Verify retention:

```sql
-- In Starburst
SELECT * FROM "tca_production"."tca_results$snapshots"
ORDER BY committed_at DESC
LIMIT 10;
```

**Expected**: Snapshots back to first load (no expiration). Iceberg default keeps all snapshots until manually expired.

Set snapshot retention:
```sql
-- Retain all snapshots (do not expire) for MiFID 5-year audit
CALL iceberg.system.set_table_property(
    'tca_production', 
    'tca_results',
    'history.expire.max-retention', 
    '0'  -- 0 = never expire
);
```

**Also needed**: Application-level audit log (FastAPI logs every query with user_id, timestamp, order_id). Ensure logs go to CloudWatch Logs with 7-year retention (immutable).

---

### Task 4.4: Disaster Recovery Drill

**Owner**: DevOps Engineer + Team  
**Duration**: 1 day

#### DR Scenario: Frankfurt Region Outage

**Recovery Objective**:
- RTO: 2 hours
- RPO: < 5 minutes (continuous S3 cross-region replication)

**Procedure**:

1. **Prepare secondary region**: Ireland (eu-west-1) already has:
   - VPC identical to Frankfurt
   - EKS cluster running (warm standby, no workers running to save cost)
   - S3 cross-region replication enabled (bucket → bucket in Ireland)

2. **Simulate outage**:
   - Route53 health check fails on Frankfurt ALB
   - Failover DNS to Ireland endpoint (TTL 60s)
   - Spin up EKS workers in Ireland (scale from 0→3)
   - Start Starburst cluster (already installed, just start workers)

3. **Validate**:
   - Starburst coordinator responds to queries
   - S3 bucket in Ireland has full data (replicated from Frankfurt)
   - Iceberg manifests point to Ireland S3 (update bucket name if different)
   - API responds (update Ingress DNS)

4. **Test query**:
```sql
SELECT COUNT(*) FROM tca_results WHERE trade_date = '2025-04-21';
-- Should return correct count (data from Ireland replica)
```

5. **Fail back**: After 4 hours, switch DNS back to Frankfurt.

**Document**: Complete runbook with step-by-step, timestamps for each step (target: <2 hours total RTO).

**Post-drill**: Retrospective meeting, update runbook with lessons learned.

---

### Task 4.5: Go-Live Readiness Review

**Owner**: Engineering Manager  
**Duration**: 1 day

#### Checklist Review

Go through checklist line by line, all boxes must be checked:

**Infrastructure**:
- [ ] VPC deployed with private subnets, no public IPs on workers
- [ ] S3 bucket with SSE-KMS, versioning, lifecycle policies
- [ ] EKS cluster running with 3 worker nodes (r5.4xlarge)
- [ ] IAM roles follow least privilege
- [ ] CloudTrail enabled for all regions
- [ ] VPC endpoint for S3 (no internet egress)

**Starburst**:
- [ ] Cluster running (1 coordinator + 3 workers)
- [ ] HTTPS enabled (TLS)
- [ ] Authentication configured (password or IAM)
- [ ] Authorization configured (fine-grained access)
- [ ] Resource groups limiting CPU/memory
- [ ] Auto-scaling configured

**Data**:
- [ ] 5 years data backfilled (3.65M orders, 5.26M benchmarks)
- [ ] Row counts validated
- [ ] dbt tests passing
- [ ] Iceberg snapshots retained (no expiration)

**Applications**:
- [ ] FastAPI deployed, health check OK
- [ ] Airflow MWAA environment running
- [ ] eod_enrichment DAG tested successfully
- [ ] monthly_backfill DAG added
- [ ] Redis cache operational

**Monitoring**:
- [ ] Grafana dashboards deployed
- [ ] Alerts configured (Slack, email)
- [ ] PagerDuty integration for P1
- [ ] Query latency P99 < 3s baseline

**Security**:
- [ ] Pen test complete, zero critical findings
- [ ] Encryption audit passed
- [ ] IAM policies reviewed (least privilege)
- [ ] Security groups tightened

**Operations**:
- [ ] Runbooks documented
- [ ] Incident response playbook in place
- [ ] On-call rotation established
- [ ] DR drill completed successfully

**Financial**:
- [ ] Budget alerts configured
- [ ] Cost tracking enabled (Cost Explorer)
- [ ] Monthly cost forecast < $8,000

**If any item outstanding**, delay go-live, resolve before proceeding.

---

## Go-Live (Week 10)

### Go-Live Day Checklist

**Time**: Friday 17:00 CET (low traffic window)

- [ ] 08:00 — Announce maintenance window to stakeholders (1 hour expected)
- [ ] 08:15 — Final backup of PoC PostgreSQL database (just in case)
- [ ] 08:30 — Switch FastAPI load balancer from PoC to production:
  ```bash
  # Update DNS or ALB target group
  aws elbv2 modify-listener --listener-arn $PROD_LISTENER_ARN --default-actions Type=forward,TargetGroupArn=$NEW_TG_ARN
  ```
- [ ] 08:35 — Smoke test: 10 API calls, verify responses
- [ ] 08:45 — Start end-of-day enrichment DAG manually (to process today's data)
- [ ] 09:00 — Monitor enrichment run (should complete by 18:30 CET)
- [ ] 09:00-18:00 — Hyper-care: DevOps + Data Engineer on standby
- [ ] 18:30 — Verify EOD enrichment SLA met
- [ ] 19:00 — Announce production open, close maintenance window

**Success**: Dashboard shows green for 24 hours straight.

---

## Post-Go-Live (Weeks 11-12)

### Monitoring Period

Week 1:
- Daily cost review (ensure under $8K/month)
- Query performance tracking (any queries over 10s need optimization)
- Alert fatigue check (false positives?)

Week 2:
- Backfill dry-run (test monthly backfill on paper production)
- Review Starburst logs for warnings/errors

Week 3:
- Security audit log review (any unauthorized access attempts?)
- Data quality drift detection (do daily metrics look normal?)

**Handover to Operations**: After 3 weeks green, hand over to ops team with runbooks.

---

## Appendix A: Terraform Module Reference

All Terraform modules follow standard structure:

```
terraform/modules/vpc/
├── main.tf
├── variables.tf
├── outputs.tf
├── subnets.tf
├── routing.tf
└── security-groups.tf

terraform/modules/s3/
├── main.tf          # bucket + versioning + encryption
├── lifecycle.tf     # Glacier transition policies
├── bucket-policy.tf # CORS, public access block
└── outputs.tf

terraform/modules/eks/
├── main.tf          # cluster + node groups
├── node-group.tf    # worker definitions
├── addons.tf        # CoreDNS, kube-proxy, VPC CNI
├── iam.tf           # IAM roles for cluster/workers
└── outputs.tf

terraform/modules/iam/
├── roles.tf         # EKS cluster role, worker role
├── policies.tf      # S3, KMS policies
└── service-accounts.tf  # IRSA trust relationships

terraform/modules/glue/
├── database.tf
└── table.tf (manual — dbt creates tables)

terraform/modules/monitoring/
├── prometheus-operator.tf  # kube-prometheus-stack
├── grafana-dashboards.tf
└── alert-rules.tf

terraform/modules/budgets/
├── budget.tf
└── notifications.tf
```

Each module `variables.tf` defines inputs, `outputs.tf` exports values.

---

## Appendix B: kubectl Cheat Sheet

```bash
# Cluster info
kubectl cluster-info
kubectl get nodes -o wide

# Pods
kubectl get pods -A
kubectl get pods -n starburst -w
kubectl describe pod starburst-coordinator-0 -n starburst
kubectl logs -f starburst-coordinator-0 -n starburst

# Deployments
kubectl get deploy -n starburst
kubectl scale deploy starburst-worker --replicas=6 -n starburst

# Services
kubectl get svc -A
kubectl port-forward svc/starburst-coordinator 8080:8080 -n starburst

# ConfigMaps & Secrets
kubectl get configmap -n starburst
kubectl get secret -n starburst
kubectl describe secret starburst-license -n starburst

# Debug
kubectl exec -it starburst-coordinator-0 -n starburst -- bash
# Inside pod:
ls /etc/starburst/
cat /etc/starburst/catalog/hive.properties
```

---

## Appendix C: Trino CLI Reference

```bash
# Connect
trino --server starburst-coordinator.tca.svc.cluster.local:8080 \
      --catalog iceberg \
      --schema tca_production \
      --user analyst1 \
      --password

# Queries
SHOW CATALOGS;
SHOW SCHEMAS FROM iceberg;
SHOW TABLES FROM tca_production;
DESCRIBE tca_production.tca_results;

# Iceberg-specific
CALL iceberg.system.snapshots('tca_production', 'tca_results');
CALL iceberg.system.manifests('tca_production', 'tca_results');

# Query with time travel
SELECT * FROM tca_results FOR SYSTEM_TIME AS OF TIMESTAMP '2025-04-01 00:00:00';

# Expire old snapshots (rarely for MiFID, but good hygiene for old tables)
CALL iceberg.system.expire_snapshots(
    table => 'tca_production.tca_results',
    older_than => DATE '2020-01-01',
    retain_last => 1
);
```

---

## Appendix D: Troubleshooting Guide

### Issue: Starburst worker pods failing to start

**Symptoms**: `kubectl get pods -n starburst` shows CrashLoopBackOff

**Diagnose**:
```bash
kubectl logs starburst-worker-0 -n starburst
# Look for: "Not enough memory" or "Unable to allocate JVM heap"
```

**Fix**: Worker memory spec too large for node type. Reduce `worker.memory.heap` in values.yaml to "16g" instead of "24g", or upgrade to r5.8xlarge instances.

---

### Issue: Query hangs indefinitely

**Symptoms**: Trino CLI shows query running for >5 min, no results

**Diagnose**:
1. Starburst UI → Queries tab → check stages. Is stage 0 (source scan) stuck at 0%?
2. Check worker nodes: `kubectl top pods -n starburst` — high CPU?
3. Check if query scanning entire table (no partition pruning)

**Fix**:
- If scanning full table: Check query WHERE clause includes partition column (date)
- If workers saturated: Scale up worker count temporarily
- If network issue: Check VPC endpoints, S3 connectivity

---

### Issue: Iceberg table write fails with "Access Denied"

**Symptoms**: `INSERT INTO table failed: S3 Access Denied`

**Diagnose**:
```bash
kubectl exec starburst-worker-0 -n starburst -- cat /var/log/starburst/starburst-server.log | grep "AccessDenied"
```

**Fix**: IAM role for worker nodes missing S3 write permission. Attach `AmazonS3FullAccess` or custom policy to worker instance profile. Verify IRSA (if using IAM roles for service accounts) is configured correctly.

---

### Issue: dbt incremental model inserting duplicate rows

**Symptoms**: `dbt run` succeeds, but `SELECT COUNT(*)` > expected

**Diagnose**: Incremental strategy `append` without uniqueness check.

**Fix**:
```sql
-- In model config, add unique_key
{{ config(
    materialized='incremental',
    incremental_strategy='append',
    unique_key='order_id'
) }}
```

Or switch to `insert_overwrite` for idempotency.

---

### Issue: Monthly backfill takes too long (>4 hours)

**Diagnosis**: 
1. Check worker count — not enough parallelism
2. Check if backfill scans entire table instead of date range
3. Check if Starburst worker memory pressure causing GC pauses

**Remediation**:
- Increase workers from 3 → 6 during backfill window (6am-10am)
- Ensure backfill script filters dates tightly: `WHERE trade_date BETWEEN start AND end`
- Add `--threads 16` to dbt run to increase parallelism
- Consider writing backfill as Spark job on EMR (more efficient for large scans), then use Trino only for final write

---

## Appendix E: Common Commands Reference

### Starburst Operations
```bash
# Restart coordinator
kubectl rollout restart deployment/starburst-coordinator -n starburst

# Scale workers
kubectl scale deploy starburst-worker --replicas=6 -n starburst

# View coordinator logs
kubectl logs -f starburst-coordinator-0 -n starburst

# Get query IDs
curl http://starburst-coordinator:8080/v1/query | jq '.nodes[].queryId'

# Kill runaway query
curl -X DELETE http://starburst-coordinator:8080/v1/query/20250421_123456_00001_abcde
```

### Iceberg Maintenance
```sql
-- Compact small files
CALL system.merge_iceberg_files(
    'tca_production', 
    'tca_results',
    target_file_size_in_bytes = '536870912'  -- 512MB
);

-- List snapshots
SELECT * FROM "tca_results$snapshots" ORDER BY committed_at DESC LIMIT 20;

-- Rollback to specific snapshot
CALL system.rollback_to_snapshot('tca_production', 'tca_results', snapshot_id => 5);

-- Clean metadata (expire old snapshots)
CALL iceberg.system.expire_snapshots(
    table => 'tca_production.tca_results',
    older_than => DATE '2022-01-01',
    retain_last => 1
);
```

### S3 Operations
```bash
# Check bucket size
aws s3 ls s3://tca-production-data-xxx/ --recursive --summarize

# List object versions
aws s3api list-object-versions --bucket tca-production-data-xxx --prefix iceberg/

# Restore from Glacier (if needed)
aws s3api restore-object \
  --bucket tca-production-data-xxx \
  --key path/to/object.parquet \
  --restore-request Days=7
```

### Airflow Operations
```bash
# List DAGs
airflow dags list

# Trigger DAG
airflow dags trigger eod_enrichment --exec-date 2025-04-21

# Task instance state
airflow tasks state eod_enrichment task_id 2025-04-21

# Clear failed task and retry
airflow tasks clear eod_enrichment task_id --start-date 2025-04-21 --end-date 2025-04-22
```

---

## Appendix F: Rollback Plan

**If Phase 1-3 fail** (can't get Starburst stable), revert to PoC with scale-up PostgreSQL (temporary measure):

1. Keep PoC PostgreSQL running (TimescaleDB on RDS or managed Postgres)
2. Continue with PoC for 3 months while fixing Starburst issues
3. Timeline slips 3 months but no production downtime

**If Phase 4 fails** (security/compliance issues):
- Do not go live
- Address findings (typically 2-4 weeks remediation)
- Re-run DR drill

---

## Document Control

**Version**: 1.0  
**Approved By**: [Engineering Leadership Signature]  
**Effective Date**: [Go-Live Date]  
**Next Review**: Quarterly (or after major incident)

---

## Related Documents
- `production-storage-decision.md` — Why Iceberg selected
- `vendor-selection.md` — Starburst vs AWS Trino evaluation
- `training-enablement-plan.md` — Team upskilling curriculum
- `CLAUDE.md` — PoC specification (source of truth for requirements)
- `docs/privatebank_tca_requirements.docx` — Original requirements
- `docs/privatebank_tca_architecture.docx` — Architecture diagrams
