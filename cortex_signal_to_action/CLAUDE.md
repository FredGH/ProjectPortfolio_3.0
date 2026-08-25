# CLAUDE.md

Team instructions for this project. Committed to git and shared with all contributors.

## Project Overview

CSTA — Cortex Signal-to-Action. A Snowflake-native marketing intelligence pipeline:
sentiment analysis, theme classification, and next-best-action generation powered by
Snowflake Cortex and dbt Core. Three environments (dev / uat / prod) in one Snowflake
account, managed by Terraform + bootstrapped by SQL scripts.

## Tech Stack

- **Python 3.11** — scripts, dbt, CI
- **Snowflake** — compute + storage (Cortex, Task DAGs, internal stage)
- **dbt Core** — transformation layer (bronze → silver → gold → marts)
- **Terraform** — infrastructure as code (`snowflakedb/snowflake` provider ~>0.98)
- **GitHub Actions** — CI/CD (`ci_dev.yml`, `ci_uat.yml`, `ci_prod.yml`)

---

## Configuration System

> **Rule: one value, one place.** Every project-level value (database name, role name,
> service account name, RSA public key) lives in `config/<env>.yaml` and nowhere else.
> Never hardcode these values in SQL scripts or Terraform modules.

### Where values live

| System | Source of truth | Notes |
|---|---|---|
| Snowflake SQL scripts | `config/<env>.yaml` via `scripts/render.py` | `.sql.j2` templates → `rendered/*.sql` |
| Terraform | `terraform/environments/<env>.tfvars` | Auth/connection only; structural names come from config YAML in future |
| dbt | `profiles.yml` (local) / Snowflake Secret (CI) | Rendered from same YAML in Phase 9 |
| GitHub Actions | Repository secrets (UI) | Key contents only; names from config YAML |

### `config/<env>.yaml` structure

```
config/
  dev.yaml    # development environment values
  uat.yaml    # UAT environment values
  prod.yaml   # production environment values
```

Each file contains:
- `project.prefix` — object name prefix (`CSTA`)
- `snowflake.*` — org, account, key path
- `databases.*` — all four database names
- `schemas.*` — layer names for env and shared databases
- `warehouses.*` — warehouse names and sizes
- `stage.*` — dbt artefact stage location
- `roles.functional` — all nine functional role names
- `service_accounts.*` — all eight service account names
- `rsa_public_keys.*` — base64 public key bodies (safe to commit; private keys are not)

### SQL template convention

All Snowflake bootstrap SQL lives as **Jinja2 templates** (`.sql.j2`) in `snowflake/setup/`.
Never write plain `.sql` files with hardcoded names.

**Render before running:**
```bash
pip install -r scripts/requirements.txt    # one-time
python scripts/render.py dev               # renders all templates for dev
python scripts/render.py dev 03_roles      # renders a single template
```

Output goes to `snowflake/setup/rendered/` (gitignored). Run the rendered `.sql` file
in a Snowflake worksheet — never the `.j2` template directly.

**Template variable syntax:**
```sql
-- ✅ Correct — value comes from config/dev.yaml
CREATE DATABASE IF NOT EXISTS {{ config.databases.dev }};
GRANT ROLE CSTA_DBT_DEV_ROLE TO USER {{ config.service_accounts.dbt_dev }};

-- ❌ Wrong — hardcoded value creates a maintenance burden
CREATE DATABASE IF NOT EXISTS CSTA_MARKETING_DEV;
```

**Use Jinja2 macros** for repetitive grant blocks (see `03_roles.sql.j2` as a reference).

### Adding a new value for a future phase

1. Add the key to `config/dev.yaml` (and `uat.yaml`, `prod.yaml`)
2. Reference it in your `.sql.j2` template as `{{ config.<section>.<key> }}`
3. Run `python scripts/render.py dev` to verify the output before committing the template

---

## Development Workflow

1. **Before starting a non-trivial change**, ask once, in a single question: is this a `feat` / `fix` / `chore` / `docs` / `refactor` / `test`, or should it just be made directly without a branch? Skip asking for docs/comment/config-only edits, if already on a non-main branch, or if already answered earlier in this conversation. Branch as `<type>/<slug>` (see the `commit-push` skill).
2. Add config values to `config/<env>.yaml` if your phase introduces new objects
3. Write SQL as `.sql.j2` templates — render and test before committing
4. Implement Terraform in the relevant `terraform/modules/<module>/`
5. Run `terraform plan -var-file=environments/dev.tfvars` to verify no unintended drift
6. Run dbt tests before opening a PR
7. Use the `commit`, `commit-push`, `pr`, or `commit-push-pr` skills to commit/branch/push/open a PR; CI runs automatically via GitHub Actions

---

## Key Commands

**Render SQL templates:**
```bash
python scripts/render.py dev        # all templates
python scripts/render.py dev 03_roles  # one template
```

**Terraform:**
```bash
cd terraform/
terraform init    -backend-config=environments/dev.backend
terraform plan    -var-file=environments/dev.tfvars
terraform apply   -var-file=environments/dev.tfvars
```

**dbt:**
```bash
source venv/bin/activate
dbt deps && dbt seed --target dev
dbt run  --target dev
dbt test --target dev
```

**Code quality:**
```bash
ruff check . && isort . && black .
```

**Tests:**
```bash
coverage run -m unittest discover
coverage report -m
```

---

## Important Notes

- Follow the Python style rules in `.claude/rules/python-style.md`
- Follow the SQL style rules in `.claude/rules/sql-style.md`
- SQL tests follow `.claude/rules/sql-testing.md`
- Python tests follow `.claude/rules/python-testing.md`
- Never commit `*.p8` private keys, `profiles.yml`, or `snowflake/setup/rendered/`
- RSA **public** key bodies (base64) in `config/*.yaml` are safe to commit

## Context Management

When compacting, preserve: config keys touched (`config/<env>.yaml`), rendered template names, Terraform plan/apply output, and any pending phase/config decisions.
