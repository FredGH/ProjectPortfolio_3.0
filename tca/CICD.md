# CI/CD — PrivateBank TCA Platform

GitHub Actions pipeline for automated testing, code quality, and Docker image builds.
Deployment to AWS is out of scope for the PoC; the pipeline builds and validates artefacts only.

---

## Pipeline Overview

```
Push / PR to main
       │
       ├── quality        ruff · isort · black
       ├── test-python    TimescaleDB + Redis in CI → seed → unittest
       ├── test-dbt       dbt build + dbt test
       ├── test-angular   npm ci → ng build --prod
       └── build-images   Docker build (app, mock-server, airflow, angular)
                          └── push to ECR  (main branch only)
```

All jobs run in parallel where there are no dependencies. `build-images` is gated on all test jobs passing.

---

## Workflow File Structure

```
.github/
└── workflows/
    ├── ci.yml          # quality + tests (runs on every push and PR)
    └── cd.yml          # Docker build + ECR push (runs on merge to main only)
```

---

## ci.yml — Test Pipeline

### Trigger

```yaml
on:
  push:
    branches: ["**"]
  pull_request:
    branches: [main]
```

### Job: `quality`

Runs `ruff`, `isort`, and `black` in check mode. Fails fast on any formatting or lint error.

```yaml
steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with: { python-version: "3.11" }
  - run: pip install ruff isort black
  - run: ruff check . && isort --check . && black --check .
```

### Job: `test-python`

Requires a live PostgreSQL 16 + TimescaleDB and Redis — no mocking. Uses GitHub Actions `services:` to spin up both containers before the job runs.

**Key complexity:** the test suite requires the full database bootstrap (schema creation via `init.sql`, then the dlt seed) before any test can run. The startup sequence mirrors the local Docker Compose order.

```yaml
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    env:
      POSTGRES_USER: tca_user
      POSTGRES_PASSWORD: tca_password
      POSTGRES_DB: tca_db
    options: >-
      --health-cmd "pg_isready -U tca_user -d tca_db"
      --health-interval 10s
      --health-retries 5

  redis:
    image: redis:7-alpine
    options: >-
      --health-cmd "redis-cli ping"
      --health-interval 10s
      --health-retries 3

steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with: { python-version: "3.11" }

  - name: Install dependencies
    run: pip install -r requirements.txt

  - name: Apply database schema
    run: psql $DATABASE_URL -f init.sql

  - name: Seed synthetic data
    run: python ingestion/seed.py

  - name: Run tests with coverage
    run: coverage run -m unittest discover -s tests && coverage report -m

env:
  DATABASE_URL: postgresql://tca_user:tca_password@localhost:5432/tca_db
  REDIS_URL: redis://localhost:6379/0
```

### Job: `test-dbt`

Depends on `test-python` (needs the seeded database). Runs `dbt build` (all layers: staging → raw_vault → biz_vault → marts) and `dbt source freshness`.

```yaml
needs: [test-python]

steps:
  - run: pip install dbt-postgres dbt-utils dbt-expectations
  - run: dbt deps
  - run: dbt build --target ci
  - run: dbt source freshness --target ci
```

The `ci` target in `profiles.yml` points to `localhost:5432` with the CI credentials.

### Job: `test-angular`

Runs independently of the Python jobs (no database needed).

```yaml
steps:
  - uses: actions/setup-node@v4
    with: { node-version: "20" }
  - run: cd frontend && npm ci
  - run: cd frontend && npm run build -- --configuration production
```

---

## cd.yml — Docker Build & ECR Push

### Trigger

```yaml
on:
  push:
    branches: [main]
```

Only runs after all CI jobs pass (uses `needs: [quality, test-python, test-dbt, test-angular]`).

### Images Built

| Image | Dockerfile | ECR Repository |
|---|---|---|
| `tca-api` | `Dockerfile` | `<account>.dkr.ecr.<region>.amazonaws.com/tca-api` |
| `tca-mock-server` | `Dockerfile` | `<account>.dkr.ecr.<region>.amazonaws.com/tca-mock-server` |
| `tca-airflow` | `Dockerfile.airflow` | `<account>.dkr.ecr.<region>.amazonaws.com/tca-airflow` |
| `tca-angular` | `Dockerfile.angular` | `<account>.dkr.ecr.<region>.amazonaws.com/tca-angular` |

### Steps

```yaml
steps:
  - name: Configure AWS credentials
    uses: aws-actions/configure-aws-credentials@v4
    with:
      aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
      aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
      aws-region: eu-west-1

  - name: Login to ECR
    uses: aws-actions/amazon-ecr-login@v2

  - name: Build and push all images
    run: |
      docker build -t tca-api -f Dockerfile .
      docker tag tca-api $ECR_REGISTRY/tca-api:$GITHUB_SHA
      docker push $ECR_REGISTRY/tca-api:$GITHUB_SHA
      # ... repeated for mock-server, airflow, angular
```

Images are tagged with the Git commit SHA (`$GITHUB_SHA`) so every pushed image is traceable to an exact commit.

---

## GitHub Secrets Required

| Secret | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM user with ECR push permissions |
| `AWS_SECRET_ACCESS_KEY` | Corresponding secret |
| `AWS_ACCOUNT_ID` | Used to construct the ECR registry URL |

These are only needed for the `cd.yml` workflow. The `ci.yml` test pipeline has zero AWS dependencies.

---

## Profiles — dbt CI Target

Add a `ci` target to `profiles.yml` (already present for `docker` target):

```yaml
tca:
  outputs:
    ci:
      type: postgres
      host: localhost
      port: 5432
      user: tca_user
      password: tca_password
      dbname: tca_db
      schema: stg_raw
      threads: 4
```

---

## Estimated Build Times

| Job | Estimated Duration |
|---|---|
| `quality` | ~1 min |
| `test-python` (seed + tests) | ~3–5 min |
| `test-dbt` (full dbt build) | ~3–5 min |
| `test-angular` | ~2–3 min |
| `build-images` (4 images) | ~5–8 min |
| **Total (CI only, parallel)** | **~6–8 min** |
| **Total (CI + CD)** | **~12–15 min** |

---

## What Is NOT in Scope

- `terraform apply` from CI — infrastructure changes are applied manually via `tflocal` locally or `terraform plan/apply` from a developer machine
- Automatic ECS service updates — image tags are pushed to ECR; the ECS service update step is a manual trigger for the PoC
- Slack / PagerDuty notifications
- Environment-specific deployment gates (staging → production promotion)
