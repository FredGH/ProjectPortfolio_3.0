# CSTA — Cortex Signal-to-Action

**Snowflake Marketing Intelligence Pipeline**

A production-grade Snowflake-native pipeline for customer voice analytics: sentiment analysis, theme classification, and next-best-action generation powered by Snowflake Cortex and dbt Core.

| | |
|---|---|
| **Tech stack** | Snowflake (compute + storage) · dbt Core + MetricFlow · Snowflake Task DAGs · Terraform · GitHub Actions |
| **Dataset** | Olist Brazilian E-Commerce (Kaggle) + synthetic MMM spend layer |
| **Architecture** | See [ARCHITECTURE.md](ARCHITECTURE.md) — full design, RBAC, observability framework |

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Required Variables & Secrets](#2-required-variables--secrets)
   - [2a. Local bootstrap values](#2a-local-bootstrap)
   - [2b. Terraform variables](#2b-terraform-variables)
   - [2c. GitHub Actions secrets](#2c-github-actions-secrets)
   - [2d. Snowflake Secrets](#2d-snowflake-secrets)
   - [2e. dbt profiles](#2e-dbt-profiles)
   - [2f. SQL bootstrap placeholders](#2f-sql-bootstrap-placeholders)
3. [Deployment](#3-deployment)
   - [3a. Generate RSA key pairs](#3a-generate-rsa-key-pairs-local-one-time)
   - [3b. Bootstrap Snowflake](#3b-bootstrap-snowflake)
   - [3c. Configure Terraform](#3c-configure-terraform)
   - [3d. First terraform apply](#3d-first-terraform-apply)
   - [3e. Configure GitHub Actions secrets](#3e-configure-github-actions-secrets)
   - [3f. Validate CI/CD](#3f-validate-cicd)
4. [Running dbt Locally](#4-running-dbt-locally)
5. [Cost Notes](#5-cost-notes)

---

## 1. Prerequisites

### 1.1 Snowflake account with ACCOUNTADMIN access

**Verify:**
```sql
-- Run in a Snowflake worksheet
SELECT CURRENT_ROLE();
-- Expected: ACCOUNTADMIN
```
If the result is a different role, try switching first:
```sql
USE ROLE ACCOUNTADMIN;
SELECT CURRENT_ROLE();
```

**Fix:** If `USE ROLE ACCOUNTADMIN` fails, ask a Snowflake org admin to grant it:
```sql
GRANT ROLE ACCOUNTADMIN TO USER <your_username>;
```

---

### 1.2 Terraform >= 1.7

**Verify:**
```bash
terraform version
# Expected: Terraform v1.7.x or higher
```

**Fix:** Download and install from [developer.hashicorp.com/terraform/install](https://developer.hashicorp.com/terraform/install), or on macOS:
```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform version
```

---

### 1.3 AWS account and CLI configured

**Verify:**
```bash
aws --version
# Expected: aws-cli/2.x.x or higher

aws sts get-caller-identity
# Expected: JSON with UserId, Account, Arn — no error
```

**Fix (CLI not installed):**
```bash
brew install awscli        # macOS
aws --version
```

**Fix (credentials not configured):**
```bash
aws configure
# Prompts for: AWS Access Key ID, Secret Access Key, region, output format
# The IAM user/role must have s3:CreateBucket, s3:PutObject, dynamodb:CreateTable permissions
```

---

### 1.4 `openssl` on PATH

**Verify:**
```bash
openssl version
# Expected: OpenSSL 3.x.x or higher
```

**Fix:**
```bash
brew install openssl
echo 'export PATH="$(brew --prefix openssl)/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
openssl version
```

---

### 1.5 Snowflake CLI (`snow`) >= 3.x

**Verify:**
```bash
snow --version
# Expected: 3.x.x or higher
```

**Fix — pip (recommended):**
```bash
pip3.11 install snowflake-cli
snow --version
```

If `snow` is not found after install, the pip bin directory is not on your PATH:
```bash
echo 'export PATH="$HOME/Library/Python/3.11/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
snow --version
```

> **Note — Homebrew tap is broken (as of June 2026):** `brew tap snowflakedb/snowflake-cli` completes but `brew install snowflake-cli` fails with:
> ```
> Error: Cask 'snowcli.tmpl' definition is invalid: Token 'snowcli' in header line does not match the file name.
> ```
> This is a malformed cask definition in the upstream tap. Use the pip install above instead.

After installing, register your Snowflake connection.

This project uses RSA key-pair auth. Have the following ready before running the command:

**Account identifier** — read it directly from your Snowflake URL:
```
https://app.snowflake.com/<region>/<account-locator>/
                           ──────── ───────────────
```
Combine them as `<account-locator>.<region>`. For example:
```
https://app.snowflake.com/europe-west2.gcp/ab12345/
→ --account ab12345.europe-west2.gcp
```

**Username** — the username you use to log into Snowflake (visible top-right in the UI or under Admin → Users).

**Private key path** — the `.p8` file generated in [step 3a](#3a-generate-rsa-key-pairs-local-one-time). The path below is a placeholder; use the actual filename you chose.

```bash
snow connection add \
  --connection-name csta-dev \
  --account <account-locator>.<region> \
  --user <your-snowflake-username> \
  --authenticator SNOWFLAKE_JWT \
  --private-key ~/.ssh/<your-key-name>.p8 \
  --role ACCOUNTADMIN \
  --default

snow connection test --connection csta-dev
# Expected: Connection csta-dev is valid
```

---

### 1.6 Python 3.11

**Verify:**
```bash
python3.11 --version
# Expected: Python 3.11.x
```

**Fix:**
```bash
brew install python@3.11
python3.11 --version
```

If `python3.11` still points to the wrong version after install:
```bash
brew link python@3.11 --force
echo 'export PATH="$(brew --prefix python@3.11)/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
python3.11 --version
```

---

## 2. Required Variables & Secrets

### 2a. Local Bootstrap

Values you look up once before anything else runs.

| Variable | Where used | What to put |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | All Snowflake connections | Account identifier, e.g. `xy12345.eu-west-1.aws` (Settings → Account in the UI) |
| `SNOWFLAKE_ORG` | Snowflake org name | Organisation identifier shown alongside the account in the UI |
| RSA key pairs | `snowflake/setup/` SQL files | See [section 3a](#3a-generate-rsa-key-pairs-local-one-time) |

---

### 2b. Terraform Variables

Stored in `terraform/environments/<env>.tfvars` (one file per environment).

| Variable | Required | Default | Description |
|---|---|---|---|
| `snowflake_account` | Yes | — | Account identifier (same as above) |
| `snowflake_user` | Yes | `TERRAFORM_SVC` | Service account created in step 3b |
| `snowflake_role` | Yes | `SYSADMIN` | Role used by Terraform |
| `env` | Yes | — | `dev` \| `uat` \| `prod` |
| `dbt_wh_size` | Yes | `XSMALL` | `XSMALL` \| `SMALL` \| `MEDIUM` |
| `notification_email` | Yes | — | Recipient for `SYSTEM$SEND_EMAIL` pipeline alerts |
| `monthly_credit_budget` | No | `500` | Credit cap; triggers an email alert when exceeded |

**Terraform S3 backend** — supply via `-backend-config` flags or environment variables:

| Variable | Description |
|---|---|
| `TF_STATE_BUCKET` | S3 bucket name, e.g. `csta-terraform-state-<aws-account-id>` |
| `AWS_REGION` | AWS region for the bucket, e.g. `eu-west-1` |
| `AWS_ACCESS_KEY_ID` | AWS credential (IAM user or role) |
| `AWS_SECRET_ACCESS_KEY` | AWS credential |

---

### 2c. GitHub Actions Secrets

Set in your repository at **Settings → Secrets and variables → Actions**.

| Secret name | Used in | Description |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | `ci_dev` / `ci_uat` / `ci_prod` | Account identifier |
| `SNOWFLAKE_PRIVATE_KEY_CI_DEV` | `ci_dev.yml` | PEM contents of `svc_ci_dev.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_UAT` | `ci_uat.yml` | PEM contents of `svc_ci_uat.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_PROD` | `ci_prod.yml` | PEM contents of `svc_ci_prod.p8` |
| `AWS_ACCESS_KEY_ID` | `ci_*.yml` | For Terraform S3 backend |
| `AWS_SECRET_ACCESS_KEY` | `ci_*.yml` | For Terraform S3 backend |
| `TF_STATE_BUCKET` | `ci_*.yml` | S3 bucket name (no `s3://` prefix) |
| `NOTIFICATION_EMAIL` | Snowflake tasks | Recipient for pipeline alert emails |

---

### 2d. Snowflake Secrets

Created by Terraform (`terraform/modules/stages/main.tf`) and injected into the dbt Python stored procedure at runtime. You supply the raw `profiles.yml` content as a Terraform variable before the first pipeline run.

| Secret name (in Snowflake) | Content |
|---|---|
| `profiles_yml_dev` | Full `profiles.yml` for the dev target; references `SVC_CSTA_DBT_DEV` key-pair auth |
| `profiles_yml_uat` | Full `profiles.yml` for the uat target |
| `profiles_yml_prod` | Full `profiles.yml` for the prod target |

---

### 2e. dbt Profiles

Key fields per target. See `dbt_project/profiles.yml.example` for the full YAML template.

| Field | dev | uat | prod |
|---|---|---|---|
| `account` | `<SNOWFLAKE_ACCOUNT>` | `<SNOWFLAKE_ACCOUNT>` | `<SNOWFLAKE_ACCOUNT>` |
| `user` | `SVC_CSTA_DBT_DEV` | `SVC_CSTA_DBT_UAT` | `SVC_CSTA_DBT_PROD` |
| `private_key_path` | `/run/secrets/dev.p8` | `/run/secrets/uat.p8` | `/run/secrets/prod.p8` |
| `role` | `CSTA_DBT_DEV_ROLE` | `CSTA_DBT_UAT_ROLE` | `CSTA_DBT_PROD_ROLE` |
| `warehouse` | `CSTA_DBT_DEV_WH` | `CSTA_DBT_UAT_WH` | `CSTA_DBT_PROD_WH` |
| `database` | `CSTA_MARKETING_DEV` | `CSTA_MARKETING_UAT` | `CSTA_MARKETING_PROD` |
| `schema` | `BRONZE` | `BRONZE` | `BRONZE` |

---

### 2f. SQL Bootstrap Placeholders

The setup SQL scripts contain placeholders for RSA public keys. Replace each one before running the corresponding script.

| Placeholder | Script | Service account |
|---|---|---|
| `<RSA_PUBLIC_KEY>` | `01_databases.sql` | `TERRAFORM_SVC` |
| `<RSA_PUBLIC_KEY_SVC_DBT_DEV>` | `03_roles.sql` | `SVC_CSTA_DBT_DEV` |
| `<RSA_PUBLIC_KEY_SVC_DBT_UAT>` | `03_roles.sql` | `SVC_CSTA_DBT_UAT` |
| `<RSA_PUBLIC_KEY_SVC_DBT_PROD>` | `03_roles.sql` | `SVC_CSTA_DBT_PROD` |
| `<RSA_PUBLIC_KEY_SVC_CI_DEV>` | `03_roles.sql` | `SVC_GITHUB_CI_DEV` |
| `<RSA_PUBLIC_KEY_SVC_CI_UAT>` | `03_roles.sql` | `SVC_GITHUB_CI_UAT` |
| `<RSA_PUBLIC_KEY_SVC_CI_PROD>` | `03_roles.sql` | `SVC_GITHUB_CI_PROD` |
| `<RSA_PUBLIC_KEY_SVC_STREAMLIT>` | `03_roles.sql` | `SVC_STREAMLIT` |
| `<NOTIFICATION_EMAIL>` | `03_roles.sql` | `SYSTEM$SEND_EMAIL` recipient |

---

## 3. Deployment

### 3a. Generate RSA Key Pairs (local, one-time)

Generate one key pair per service account **before** running any SQL bootstrap script. No Snowflake access is needed at this step — it is purely local.

Replace `<name>` with each of: `terraform_svc`, `svc_dbt_dev`, `svc_dbt_uat`, `svc_dbt_prod`, `svc_ci_dev`, `svc_ci_uat`, `svc_ci_prod`, `svc_streamlit`.

```bash
openssl genrsa -out <name>.p8 2048
openssl rsa -in <name>.p8 -pubout -out <name>.pub
```

Extract the base64 body to paste into the SQL placeholders (strips PEM header/footer):

```bash
grep -v "BEGIN\|END\|^$" <name>.pub | tr -d '\n'
```

> **Important:** add `*.p8` to `.gitignore` immediately. Private keys must never be committed.

---

### 3b. Bootstrap Snowflake

Run each script once, in order, using Snowflake's SQL worksheet or SnowSQL. The required role is noted at the top of each file.

| Step | Script | Role | Objects created |
|---|---|---|---|
| 1 | [snowflake/setup/01_databases.sql](snowflake/setup/01_databases.sql) | `ACCOUNTADMIN` → `SYSADMIN` | `TERRAFORM_SVC` user · 4 databases · all schemas |
| 2 | [snowflake/setup/02_warehouses.sql](snowflake/setup/02_warehouses.sql) | `SYSADMIN` | 3 warehouses (`DEV_WH` / `UAT_WH` / `PROD_WH`) |
| 3 | [snowflake/setup/03_roles.sql](snowflake/setup/03_roles.sql) | `SECURITYADMIN` → `SYSADMIN` → `ACCOUNTADMIN` | 50 access roles · functional roles · 7 service account users |
| 4 | [snowflake/setup/04_stages.sql](snowflake/setup/04_stages.sql) | `SYSADMIN` | dbt artefact internal stage |

**Before running `01_databases.sql`:**
- Complete step 3a for `terraform_svc`
- Replace `<RSA_PUBLIC_KEY>` with:
  ```bash
  grep -v "BEGIN\|END\|^$" terraform_svc.pub | tr -d '\n'
  ```

**Before running `03_roles.sql`:**
- Complete step 3a for all remaining service accounts
- Replace all `<RSA_PUBLIC_KEY_*>` placeholders
- Replace `<NOTIFICATION_EMAIL>` with your alert recipient address

**After running `01_databases.sql`**, import the databases into Terraform state so Terraform does not try to recreate them:

```bash
terraform import snowflake_database.dev    CSTA_MARKETING_DEV
terraform import snowflake_database.uat    CSTA_MARKETING_UAT
terraform import snowflake_database.prod   CSTA_MARKETING_PROD
terraform import snowflake_database.shared CSTA_MARKETING_SHARED
```

---

### 3c. Configure Terraform

**1. Create the S3 backend bucket** (once, via AWS CLI):

```bash
aws s3api create-bucket \
  --bucket csta-terraform-state-<aws-account-id> \
  --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1

aws s3api put-bucket-versioning \
  --bucket csta-terraform-state-<aws-account-id> \
  --versioning-configuration Status=Enabled
```

**2. Fill in `terraform/environments/dev.tfvars`** (repeat for `uat.tfvars` and `prod.tfvars`):

```hcl
snowflake_account     = "xy12345.eu-west-1.aws"
snowflake_user        = "TERRAFORM_SVC"
snowflake_role        = "SYSADMIN"
env                   = "dev"
dbt_wh_size           = "XSMALL"
notification_email    = "you@example.com"
monthly_credit_budget = 500
```

**3. Export the Terraform private key** so the Snowflake provider can authenticate as `TERRAFORM_SVC`:

```bash
export SNOWFLAKE_PRIVATE_KEY_PATH=./terraform_svc.p8
```

---

### 3d. First Terraform Apply

Run from `terraform/` for each environment:

```bash
terraform init \
  -backend-config="bucket=csta-terraform-state-<aws-account-id>" \
  -backend-config="key=csta/<env>/terraform.tfstate" \
  -backend-config="region=eu-west-1"

terraform plan -var-file=environments/<env>.tfvars -out=tfplan
terraform apply tfplan
```

Terraform manages: warehouses, schemas, RBAC, the dbt artefact stage, and the Snowflake Secrets (`profiles_yml_*`) read by the dbt stored procedure. The databases were already imported in step 3b.

> After the first successful apply, Terraform is the sole source of truth for all Snowflake infrastructure. Do not re-run the setup SQL scripts.

---

### 3e. Configure GitHub Actions Secrets

In your repository at **Settings → Secrets and variables → Actions**, create:

| Secret | Value |
|---|---|
| `SNOWFLAKE_ACCOUNT` | Account identifier |
| `SNOWFLAKE_PRIVATE_KEY_CI_DEV` | Full contents of `svc_ci_dev.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_UAT` | Full contents of `svc_ci_uat.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_PROD` | Full contents of `svc_ci_prod.p8` |
| `AWS_ACCESS_KEY_ID` | AWS key (for Terraform S3 backend) |
| `AWS_SECRET_ACCESS_KEY` | AWS secret |
| `TF_STATE_BUCKET` | S3 bucket name (no `s3://` prefix) |
| `NOTIFICATION_EMAIL` | Alert recipient email address |

---

### 3f. Validate CI/CD

1. **Push a feature branch** → triggers `ci_dev.yml`
   - dbt compile (local syntax check) + SQLFluff lint on changed models
   - Snowflake CLI triggers the dev Task DAG
   - Polls `PIPELINE_RUN_LOG`; posts test tier summary as a PR comment

2. **Merge to `uat` branch** → triggers `ci_uat.yml`
   - Full pipeline run on `CSTA_MARKETING_UAT`
   - Fails the workflow on any Tier 1 or Tier 2 test failure

3. **Merge to `main`** → triggers `ci_prod.yml`
   - Full pipeline run on `CSTA_MARKETING_PROD`
   - Tier 1–4 tests enabled (including drift / statistical checks)
   - Slack alert on any critical failure

---

## 4. Running dbt Locally

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install dbt-snowflake

# Copy and fill in the profiles template
cp dbt_project/profiles.yml.example ~/.dbt/profiles.yml

cd dbt_project
dbt deps
dbt seed --target dev
dbt run  --target dev
dbt test --target dev
```

**Slim CI run** (mirrors what the dev Task DAG does):

```bash
dbt test --select state:modified+ \
         --defer \
         --state @CSTA_DBT_ARTIFACTS/uat/latest/ \
         --target dev
```

---

## 5. Cost Notes

Warehouses are created `INITIALLY_SUSPENDED` with `AUTO_SUSPEND = 60` seconds to minimise idle spend.

Credit costs are driven by:
- **Cortex AI functions** — `TRANSLATE`, `AI_SENTIMENT`, `COMPLETE` called on review text in the silver layer
- **Nightly Task DAGs** — prod at 04:00 UTC, uat at 02:00 UTC
- **`TASK_COST_REPORT`** — runs independently at 06:00 UTC, outside the pipeline DAG

Monitor spend via the Observability Streamlit app (Page 6 — Cost & Credits) or query `CSTA_MARKETING_SHARED.OBSERVABILITY.COST_DAILY` directly.

Set `monthly_credit_budget` in `tfvars` to receive an email alert before you exceed your intended spend.
