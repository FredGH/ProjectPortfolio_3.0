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
2. [Deployment](#2-deployment)
   - [2a. Generate RSA key pairs](#2a-generate-rsa-key-pairs-local-one-time)
   - [2b. Bootstrap Snowflake](#2b-bootstrap-snowflake)
   - [2c. Configure Terraform](#2c-configure-terraform)
   - [2d. First terraform apply](#2d-first-terraform-apply)
   - [2e. Configure GitHub Actions secrets](#2e-configure-github-actions-secrets)
   - [2f. Validate CI/CD](#2f-validate-cicd)
3. [Configuration System](#3-configuration-system)
   - [3a. Config files per system](#3a-config-files-per-system)
   - [3b. What to edit for a new deployment](#3b-what-to-edit-for-a-new-deployment)
4. [Required Variables & Secrets](#4-required-variables--secrets)
   - [4a. Local bootstrap values](#4a-local-bootstrap)
   - [4b. Terraform variables](#4b-terraform-variables)
   - [4c. GitHub Actions secrets](#4c-github-actions-secrets)
   - [4d. Snowflake Secrets](#4d-snowflake-secrets)
   - [4e. dbt profiles](#4e-dbt-profiles)
5. [Running dbt Locally](#5-running-dbt-locally)
6. [Cost Notes](#6-cost-notes)

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

### 1.3 `openssl` on PATH

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

### 1.4 Snowflake CLI (`snow`) >= 3.x

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

**Private key path** — a personal key pair you generate now, separate from the service account keys created in step 2a. The service account keys (`terraform_svc`, `svc_dbt_*`, etc.) are for pipeline service accounts that don't exist in Snowflake yet — they cannot be used here.

Generate your personal key pair:

```bash
cd ~/.ssh
openssl genrsa -out <your-snowflake-username>.p8 2048
openssl rsa -in <your-snowflake-username>.p8 -pubout -out <your-snowflake-username>.pub
```

Register the public key against your Snowflake user. Run this in your terminal to get the key content:

```bash
grep -v "BEGIN\|END\|^$" ~/.ssh/<your-snowflake-username>.pub | tr -d '\n'
```

Then paste the output into a Snowflake worksheet (logged in via browser) and run:

```sql
ALTER USER <your-snowflake-username> SET RSA_PUBLIC_KEY='<output>';
```

Now register the connection:

```bash
snow connection add \
  --connection-name csta-dev \
  --account <account-locator>.<region> \
  --user <your-snowflake-username> \
  --authenticator SNOWFLAKE_JWT \
  --private-key ~/.ssh/<your-snowflake-username>.p8 \
  --role ACCOUNTADMIN \
  --default

snow connection test --connection csta-dev
# Expected: Connection csta-dev is valid
```

The CLI will prompt for several additional fields after the command above. Here is what to enter for each:

| Prompt | What to enter | Why |
|---|---|---|
| Warehouse | Any existing warehouse, e.g. `COMPUTE_WH` | Project warehouses don't exist yet — created later by Terraform. Run `SHOW WAREHOUSES;` in a Snowflake worksheet to see what's available. This is just a fallback default. |
| Database | Leave blank or any existing database, e.g. `SNOWFLAKE` | Project databases don't exist yet — created in step 2b. Run `SHOW DATABASES;` to see what's available. Pipeline commands always specify the database explicitly. |
| Schema | Leave blank or `LOCAL` | `LOCAL` exists in every Snowflake database. The pipeline always specifies the schema explicitly. |
| Host | `<account-locator>.<region>.snowflakecomputing.com` | Derived from your account identifier, e.g. `jg77429.europe-west2.gcp.snowflakecomputing.com`. |
| Port | Leave blank | Default 443 (HTTPS) is used automatically. |
| Protocol | Leave blank | `https` is the default and always correct for Snowflake. |
| Region | Leave blank | Already encoded in the account identifier — specifying it separately would be redundant. |
| Workload identity provider | Leave blank | Only for federated identity (AWS IAM, GCP Workload Identity). Not applicable with JWT key-pair auth. |
| Token file path | Leave blank | Only for external token / workload identity auth. Not applicable with JWT key-pair auth. |
| Secondary roles | Leave blank | `ACCOUNTADMIN` already has full privileges — no secondary roles needed. |

---

### 1.5 Python 3.11

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

## 2. Deployment

### 2a. Generate RSA Key Pairs (local, one-time)

Generate one key pair per service account **before** running any SQL bootstrap script. No Snowflake access is needed at this step — it is purely local.

For each service account, replace `<name>` with each of: `terraform_svc`, `svc_dbt_dev`, `svc_dbt_uat`, `svc_dbt_prod`, `svc_ci_dev`, `svc_ci_uat`, `svc_ci_prod`, `svc_streamlit`.

```bash
cd ~/.ssh/

# Generate unencrypted PKCS#8 private key (required by Snowflake JWT auth)
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out <name>.p8

# Extract the public key
openssl rsa -in <name>.p8 -pubout -out <name>.pub
```

> **Key format matters:** Snowflake JWT auth requires **PKCS#8** format (`-----BEGIN PRIVATE KEY-----`). The older `openssl genrsa` command produces PKCS#1 (`-----BEGIN RSA PRIVATE KEY-----`) which will cause a `JWT token is invalid` error. Use `openssl genpkey` as shown above.

Extract the base64 body to paste into the SQL placeholders (strips PEM header/footer):

```bash
grep -v "BEGIN\|END\|^$" <name>.pub | tr -d '\n'
```

> **Important:** `*.p8` is already in `.gitignore`. Private keys must never be committed.

---

### 2b. Bootstrap Snowflake

SQL bootstrap scripts live as Jinja2 templates (`snowflake/setup/*.sql.j2`). Render them against your config YAML before running — this substitutes all database names, role names, service account names, and RSA public keys from a single source of truth.

**Step 1 — fill in RSA public keys in `config/dev.yaml`:**

For each service account key generated in step 2a, paste the base64 body into `config/dev.yaml → rsa_public_keys.*`:
```bash
grep -v "BEGIN\|END\|^$" terraform_svc.pub | tr -d '\n'  # → paste into rsa_public_keys.terraform_svc
grep -v "BEGIN\|END\|^$" svc_dbt_dev.pub   | tr -d '\n'  # → paste into rsa_public_keys.svc_dbt_dev
# ... repeat for all eight service accounts
```

**Step 2 — render the templates:**
```bash
pip install -r scripts/requirements.txt   # one-time
python scripts/render.py dev              # writes to snowflake/setup/rendered/
```

**Step 3 — run each rendered script in order** in a Snowflake worksheet or SnowSQL:

| Step | Rendered script | Role | Objects created |
|---|---|---|---|
| 1 | `snowflake/setup/rendered/01_databases.sql` | `ACCOUNTADMIN` → `SYSADMIN` | `TERRAFORM_SVC` user · 4 databases · all schemas |
| 2 | `snowflake/setup/rendered/02_warehouses.sql` | `SYSADMIN` | 3 warehouses (`DEV_WH` / `UAT_WH` / `PROD_WH`) |
| 3 | `snowflake/setup/rendered/03_roles.sql` | `SECURITYADMIN` → `SYSADMIN` → `ACCOUNTADMIN` | 59 access roles · functional roles · 7 service account users |
| 4 | `snowflake/setup/rendered/04_stages.sql` | `SYSADMIN` | dbt artefact internal stage |

After all four scripts have run, the four databases and their schemas exist in Snowflake. They need to be imported into Terraform state in step 2d (after `terraform init`) so Terraform does not try to recreate them.

---

### 2c. Configure Terraform

**Look up your Snowflake organisation and account names** — run this in a Snowflake worksheet before filling in the tfvars:

```sql
SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME();
```

These are not the same as the legacy account locator (e.g. `jg77429.europe-west2.gcp`). The new `snowflakedb/snowflake` provider requires the org name and account name separately.

**Fill in `terraform/environments/dev.tfvars`** (repeat for `uat.tfvars` and `prod.tfvars`):

```hcl
snowflake_organization_name = "<your-org-name>"    # SELECT CURRENT_ORGANIZATION_NAME()
snowflake_account_name      = "<your-account-name>" # SELECT CURRENT_ACCOUNT_NAME()
snowflake_user              = "TERRAFORM_SVC"
snowflake_private_key_path  = "~/.ssh/terraform_svc.p8"
environment                 = "dev"
```

The private key is read from disk at plan time by `file()` in `versions.tf` — no environment variable export is needed.

---

### 2d. First Terraform Apply

Run from the `terraform/` directory for each environment:

```bash
cd terraform/

terraform init -backend-config=environments/dev.backend
```

> **Re-run `terraform init` whenever you:** change the provider source or version in `versions.tf`, add or remove a module, or change the backend configuration. Running `plan` or `import` without a current init will fail with "Backend initialization required".

**Import all bootstrapped objects** into Terraform state. Three scripts handle the 3 warehouses, 4 databases + 14 schemas, and 59 RBAC roles created by the setup SQL scripts:

```bash
bash scripts/import_warehouses.sh   # 3 warehouses
bash scripts/import_databases.sh    # 4 databases + 14 schemas
bash scripts/import_roles.sh        # 59 RBAC roles (8 DB-level, 42 schema-level, 9 functional)
```

> **Note — opportunity to simplify:** The current approach bootstraps objects via SQL scripts and then imports them into Terraform state. This is intentional for the initial setup (SQL scripts provide a clear, self-contained record of what Snowflake resources exist), but it introduces redundancy: every object is defined twice — once in the SQL script and once in the Terraform HCL. A future improvement would be to remove the SQL bootstrap scripts entirely and let Terraform create all objects from scratch on a clean account. That would eliminate the import step and make the SQL scripts the single source of truth only during initial onboarding.

**Apply** to provision all remaining objects (warehouses, RBAC, stage, secrets):

```bash
terraform plan  -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars
```

Terraform manages: warehouses, schemas, RBAC, the dbt artefact stage, and the Snowflake Secrets (`profiles_yml_*`) read by the dbt stored procedure.

**Confirm zero drift after apply:**
```bash
terraform plan -var-file=environments/dev.tfvars
# Expected: No changes. Your infrastructure matches the configuration.
```

> After the first successful apply, Terraform is the sole source of truth for all Snowflake infrastructure. Do not re-run the setup SQL scripts.

**Post-apply — populate the profiles secrets** with the content of your `profiles.yml`:
```sql
USE ROLE SYSADMIN;
ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_DEV
  SET SECRET_STRING = '<paste dev profiles.yml content>';
ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_UAT
  SET SECRET_STRING = '<paste uat profiles.yml content>';
ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_PROD
  SET SECRET_STRING = '<paste prod profiles.yml content>';
```

---

### 2e. Configure GitHub Actions Secrets

In your repository at **Settings → Secrets and variables → Actions**, create:

| Secret | Value |
|---|---|
| `SNOWFLAKE_ACCOUNT` | Account identifier |
| `SNOWFLAKE_PRIVATE_KEY_CI_DEV` | Full contents of `svc_ci_dev.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_UAT` | Full contents of `svc_ci_uat.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_PROD` | Full contents of `svc_ci_prod.p8` |
| `NOTIFICATION_EMAIL` | Alert recipient email address |

---

### 2f. Validate CI/CD

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

## 3. Configuration System

All project values (database names, role names, service account names, RSA public keys) are centralised in one YAML file per environment. **Never hardcode these values** in SQL scripts or Terraform modules — always reference the config file.

### 3a. Config files per system

| System | Config location | How values get there |
|---|---|---|
| Snowflake SQL | `config/<env>.yaml` | Jinja2 templates rendered by `scripts/render.py` |
| Terraform | `terraform/environments/<env>.tfvars` | Edit directly (auth/connection values only) |
| dbt | `~/.dbt/profiles.yml` (local) · Snowflake Secret (CI) | See [section 4e](#4e-dbt-profiles) |
| GitHub Actions | Repository secrets (Settings → Secrets) | Key contents only |

### 3b. What to edit for a new deployment

1. **`config/dev.yaml`** — the primary config file. Contains every object name and RSA public key used across all SQL templates. Edit once; all four SQL scripts are rendered from it.

   Key sections to fill in:
   - `snowflake.organization` + `snowflake.account` — from `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`
   - `rsa_public_keys.*` — base64 body of each `.pub` file (8 values, see step 2a)

2. **`terraform/environments/dev.tfvars`** — Terraform connection values only:
   - `snowflake_organization_name`, `snowflake_account_name`, `snowflake_user`, `snowflake_private_key_path`

3. **`config/uat.yaml`** and **`config/prod.yaml`** — identical structure to `dev.yaml`; only `project.environment` differs. Update if environment-specific values change in future phases.

> **Adding values for a new phase:** Add keys to `config/dev.yaml` (and uat/prod), reference them in your `.sql.j2` template as `{{ config.<section>.<key> }}`, then re-render. See `CLAUDE.md` for the full convention.

---

## 4. Required Variables & Secrets

### 4a. Local Bootstrap

Values you look up once before anything else runs.

| Variable | Where used | What to put |
|---|---|---|
| `snowflake.organization` | `config/<env>.yaml` | From `SELECT CURRENT_ORGANIZATION_NAME()` |
| `snowflake.account` | `config/<env>.yaml` | From `SELECT CURRENT_ACCOUNT_NAME()` |
| RSA key pairs | `config/<env>.yaml → rsa_public_keys.*` | See [section 2a](#2a-generate-rsa-key-pairs-local-one-time) |

---

### 4b. Terraform Variables

Stored in `terraform/environments/<env>.tfvars` (one file per environment). These are the only values Terraform needs — all structural names are defined in the Terraform modules themselves (matching `config/<env>.yaml`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `snowflake_organization_name` | Yes | — | Snowflake org name — `SELECT CURRENT_ORGANIZATION_NAME()` |
| `snowflake_account_name` | Yes | — | Snowflake account name — `SELECT CURRENT_ACCOUNT_NAME()` |
| `snowflake_user` | Yes | `TERRAFORM_SVC` | Service account created in step 2b |
| `snowflake_private_key_path` | Yes | `~/.ssh/terraform_svc.p8` | Path to TERRAFORM_SVC RSA private key — read at plan time via `file()` |
| `environment` | Yes | — | `dev` \| `uat` \| `prod` |

Terraform state is stored **locally** in `terraform/environments/<env>.tfstate` — no AWS account is required. To migrate to remote state later, swap `backend "local"` for `backend "s3"` in `terraform/versions.tf` and run `terraform init -migrate-state`.

---

### 4c. GitHub Actions Secrets

Set in your repository at **Settings → Secrets and variables → Actions**.

| Secret name | Used in | Description |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | `ci_dev` / `ci_uat` / `ci_prod` | Account identifier |
| `SNOWFLAKE_PRIVATE_KEY_CI_DEV` | `ci_dev.yml` | PEM contents of `svc_ci_dev.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_UAT` | `ci_uat.yml` | PEM contents of `svc_ci_uat.p8` |
| `SNOWFLAKE_PRIVATE_KEY_CI_PROD` | `ci_prod.yml` | PEM contents of `svc_ci_prod.p8` |

---

### 4d. Snowflake Secrets

Created by Terraform (`terraform/modules/stages/main.tf`) and injected into the dbt Python stored procedure at runtime. You supply the raw `profiles.yml` content as a Terraform variable before the first pipeline run.

| Secret name (in Snowflake) | Content |
|---|---|
| `profiles_yml_dev` | Full `profiles.yml` for the dev target; references `SVC_CSTA_DBT_DEV` key-pair auth |
| `profiles_yml_uat` | Full `profiles.yml` for the uat target |
| `profiles_yml_prod` | Full `profiles.yml` for the prod target |

---

### 4e. dbt Profiles

Key fields per target. See `profiles.yml.example` for the full YAML template. All values below are derived from `config/<env>.yaml`.

| Field | dev | uat | prod |
|---|---|---|---|
| `account` | `UFNDSPC-GJ37236` | same | same |
| `user` | `SVC_CSTA_DBT_DEV` | `SVC_CSTA_DBT_UAT` | `SVC_CSTA_DBT_PROD` |
| `private_key_path` | `~/.ssh/svc_dbt_dev.p8` | `~/.ssh/svc_dbt_uat.p8` | `~/.ssh/svc_dbt_prod.p8` |
| `role` | `CSTA_DBT_DEV_ROLE` | `CSTA_DBT_UAT_ROLE` | `CSTA_DBT_PROD_ROLE` |
| `warehouse` | `CSTA_DBT_DEV_WH` | `CSTA_DBT_UAT_WH` | `CSTA_DBT_PROD_WH` |
| `database` | `CSTA_MARKETING_DEV` | `CSTA_MARKETING_UAT` | `CSTA_MARKETING_PROD` |
| `schema` | `BRONZE` | `BRONZE` | `BRONZE` |

---

## 5. Running dbt Locally

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install dbt-snowflake

# Copy and fill in the profiles template
cp profiles.yml.example ~/.dbt/profiles.yml
export SNOWFLAKE_ACCOUNT=<account-identifier>
export SNOWFLAKE_PRIVATE_KEY_PATH=~/.ssh/svc_dbt_dev.p8
export DBT_DEVELOPER=$(whoami)

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

## 6. Cost Notes

Warehouses are created `INITIALLY_SUSPENDED` with `AUTO_SUSPEND = 60` seconds to minimise idle spend.

Credit costs are driven by:
- **Cortex AI functions** — `TRANSLATE`, `AI_SENTIMENT`, `COMPLETE` called on review text in the silver layer
- **Nightly Task DAGs** — prod at 04:00 UTC, uat at 02:00 UTC
- **`TASK_COST_REPORT`** — runs independently at 06:00 UTC, outside the pipeline DAG

Monitor spend via the Observability Streamlit app (Page 6 — Cost & Credits) or query `CSTA_MARKETING_SHARED.OBSERVABILITY.COST_DAILY` directly.
