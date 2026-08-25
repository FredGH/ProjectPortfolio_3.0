# Modular Data Engineering Project Template — Architecture

## Overview

This document describes the architecture for a **composable project template generator** (Approach 2: Composable Module Registry). The system allows users to select a set of components and receive a clean, self-consistent repository containing only those components — with no references, stubs, or placeholders for anything not selected.

---

## Design Principles

- **No leakage**: the output repo must not reference unselected components anywhere (no commented-out blocks, no `# TODO: add if using Airflow`)
- **Explicit contracts**: each component declares what it owns, what it needs, and what it provides
- **Fail fast**: incompatible combinations are rejected before any files are generated
- **Merge over copy**: shared files (e.g. `dbt_project.yml`, GitHub Actions YAML) are assembled by merging contributions from each selected component

---

## Repository Structure

```
template-engine/
├── components/                   # one directory per component
│   ├── core/                     # always included, base scaffolding
│   ├── cloud/
│   │   ├── aws/
│   │   ├── gcp/
│   │   └── azure/
│   ├── warehouse/
│   │   ├── snowflake/            # SaaS — no cloud component required
│   │   └── bigquery/             # requires cloud: gcp
│   ├── access_control/
│   │   ├── rbac_snowflake/       # requires warehouse: snowflake
│   │   └── abac_snowflake/       # requires warehouse: snowflake
│   ├── transformation/
│   │   └── dbt/
│   ├── orchestration/
│   │   ├── airflow_self_hosted/
│   │   ├── airflow_managed/      # MWAA (AWS) or Cloud Composer (GCP)
│   │   ├── snowflake_tasks/      # requires warehouse: snowflake
│   │   └── no_orchestrator/      # cron / scripts fallback (non-Snowflake only)
│   ├── cicd/
│   │   └── github_actions/
│   └── observability/
│       ├── datadog/
│       ├── cloudwatch/           # AWS only
│       └── gcp_monitoring/       # GCP only
├── assembler/                    # the CLI tool
│   ├── resolver.py               # dependency + conflict resolution
│   ├── merger.py                 # merges shared config files
│   ├── validator.py              # validates the component selection
│   └── generator.py             # writes the output repo
├── schemas/
│   └── component.schema.json     # JSON Schema for component manifests
└── cli.py                        # entry point
```

---

## Component Structure

Every component lives in its own directory and contains:

```
components/dbt/
├── component.yaml        # manifest (see below)
├── files/                # files owned exclusively by this component
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── packages.yml
│   ├── .sqlfluff
│   └── models/
│       ├── bronze/                   # raw ingestion layer
│       │   └── sources.yml           # {{ source() }} declarations only
│       ├── silver/                   # cleansed & typed layer
│       │   ├── schema.yml
│       │   └── stg_example__entity.sql
│       ├── gold/                     # business-ready consumption layer
│       │   ├── schema.yml
│       │   ├── dimensions/
│       │   │   └── dim_example.sql
│       │   └── facts/
│       │       └── fct_example.sql
│       └── intermediate/             # optional: cross-layer joins / unpivots
│           └── int_example.sql
└── fragments/            # partial contributions to shared files
    ├── github_actions.yml.fragment   # contributed to CI/CD workflow
    └── makefile.fragment             # contributed to root Makefile
```

### Component Manifest (`component.yaml`)

```yaml
name: dbt
group: transformation
type: optional               # optional | required | pick_one

requires:
  - warehouse                # needs a target warehouse; cloud is NOT implied
                             # snowflake works without cloud; bigquery→gcp is
                             # enforced by the warehouse component's own manifest

conflicts: []

provides:
  - sql_transformation       # abstract capability token

enhances:
  - cicd/github_actions      # injects dbt run steps into CI pipeline
  - observability            # exposes dbt test results to monitoring layer

owned_files:
  - dbt_project.yml
  - profiles.yml
  - packages.yml
  - .sqlfluff
  - models/bronze/
  - models/silver/
  - models/intermediate/
  - models/gold/

shared_contributions:
  - target: .github/workflows/main.yml
    fragment: fragments/github_actions.yml.fragment
  - target: Makefile
    fragment: fragments/makefile.fragment
```

---

## dbt Medallion Architecture

The `dbt` component structures all models in three layers following the **Medallion (Bronze → Silver → Gold)** pattern. This is a fixed convention — it is not configurable per project.

```
Raw sources
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  BRONZE  (models/bronze/)                               │
│  • Declares {{ source() }} references only              │
│  • No transformations — one-to-one with raw tables      │
│  • Materialized as views                                │
│  • Owner: data ingestion layer (e.g. Airbyte, Fivetran) │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│  SILVER  (models/silver/)                               │
│  • Cleansed, typed, deduplicated, snake_cased           │
│  • Naming: stg_<source>__<entity>.sql                   │
│  • Only reads from {{ source() }} — never from gold     │
│  • Materialized as views (or incremental for large sets)│
└────────────────────────┬────────────────────────────────┘
                         │
                         │    optional
                         ├──────────────────────────────┐
                         │                              ▼
                         │          ┌──────────────────────────────────────┐
                         │          │  INTERMEDIATE  (models/intermediate/) │
                         │          │  • Cross-entity joins, unpivots,      │
                         │          │    window functions not belonging in   │
                         │          │    a single silver or gold model       │
                         │          │  • Naming: int_<entity>_<verb>.sql    │
                         │          │  • Ephemeral or view materialization  │
                         │          └──────────────────┬───────────────────┘
                         │                             │
                         ▼                             ▼
┌─────────────────────────────────────────────────────────┐
│  GOLD  (models/gold/)                                   │
│  • Business-ready, consumption-facing                   │
│  • dimensions/: dim_<entity>.sql (SCD Type 1 default)  │
│  • facts/: fct_<event>.sql (one row per grain event)   │
│  • Only reads from silver or intermediate               │
│  • Materialized as tables                               │
└─────────────────────────────────────────────────────────┘
```

### Layer Rules (enforced via dbt `project-level` path config)

| Rule | Enforcement |
|---|---|
| Bronze models only use `{{ source() }}` | `dbt_project.yml` restricts `+source-paths` |
| Silver models never reference gold | `dbt-dag-contracts` or CI `dbt ls --select` check |
| Gold models never reference bronze directly | Same CI check |
| Intermediate models are always ephemeral or view | `dbt_project.yml` materialization default per path |

### `dbt_project.yml` Layer Configuration

```yaml
models:
  project_name:
    bronze:
      +materialized: view
      +tags: ["bronze"]
    silver:
      +materialized: view
      +tags: ["silver"]
    intermediate:
      +materialized: ephemeral
      +tags: ["intermediate"]
    gold:
      +materialized: table
      +tags: ["gold"]
      dimensions:
        +materialized: table
      facts:
        +materialized: table
```

### Testing Strategy per Layer

| Layer | Minimum tests |
|---|---|
| Bronze | Source freshness (`dbt source freshness`) |
| Silver | `unique` + `not_null` on PK |
| Intermediate | `not_null` on join keys |
| Gold (dimensions) | PK tests + `relationships` on all FKs + `accepted_values` on categoricals |
| Gold (facts) | PK tests + `relationships` on all FKs + non-negative checks on measures |

---

## Component Groups and Constraints

| Group | Cardinality | Members |
|---|---|---|
| `cloud` | zero or one | `aws`, `gcp`, `azure` |
| `warehouse` | exactly one | `snowflake`, `bigquery` |
| `access_control` | one or more | `rbac_snowflake`, `abac_snowflake` |
| `transformation` | zero or one | `dbt` |
| `orchestration` | exactly one | `airflow_self_hosted`, `airflow_managed`, `snowflake_tasks`, `no_orchestrator` |
| `cicd` | zero or one | `github_actions` |
| `observability` | zero or one | `datadog`, `cloudwatch`, `gcp_monitoring` |

`cloud` is now **zero or one** — it is only required when the selected warehouse or orchestration component demands it. Snowflake, DuckDB, and Postgres users may generate a complete project with no cloud component at all.

`orchestration` is **exactly one** — every project must declare how pipelines are scheduled. `no_orchestrator` is available only for non-Snowflake warehouses (local dev scenarios). When `warehouse: snowflake` is selected without Airflow, `snowflake_tasks` is the only valid orchestration choice — Snowflake Tasks and DAGs are the native scheduling layer and leaving orchestration undefined in a Snowflake project is not permitted.

### Compatibility Matrix

| Selection | Valid? | Reason |
|---|---|---|
| `warehouse: bigquery` + no `cloud: gcp` | No | BigQuery is GCP-native |
| `warehouse: snowflake` + no `cloud` | Yes | Snowflake is cloud-agnostic SaaS |
| `dbt` + `warehouse: snowflake` | Yes | No cloud implied |
| `dbt` + `warehouse: bigquery` | Yes | Implies `cloud: gcp` |
| `dbt` + no `warehouse` | No | dbt requires a warehouse target |
| `rbac_snowflake` + `warehouse: bigquery` | No | RBAC component is Snowflake-specific |
| `gcp` + `cloudwatch` | No | CloudWatch is AWS-only |
| `aws` + `airflow_managed` | Yes | Resolves to MWAA |
| `gcp` + `airflow_managed` | Yes | Resolves to Cloud Composer |
| `azure` + `airflow_managed` | No | Not supported (no managed Airflow on Azure in scope) |
| `warehouse: snowflake` + `orchestration: snowflake_tasks` | Yes | Native Snowflake scheduling |
| `warehouse: snowflake` + `orchestration: airflow_*` | Yes | Airflow can orchestrate dbt against Snowflake |
| `warehouse: snowflake` + `orchestration: no_orchestrator` | No | Snowflake projects must use either Airflow or Snowflake Tasks |
| `warehouse: bigquery` + `orchestration: snowflake_tasks` | No | Snowflake Tasks require a Snowflake warehouse |
| `rbac_snowflake` + `abac_snowflake` | Yes | Can coexist |

---

## Resolution and Assembly Pipeline

```
User selects components
        │
        ▼
┌─────────────────────┐
│   Validator         │  checks group cardinality, conflict rules
└────────┬────────────┘
         │ fail → clear error message, stop
         ▼
┌─────────────────────┐
│   Resolver          │  walks `requires` graph, auto-adds implied components
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Merger            │  assembles shared files from fragments in dependency order
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Generator         │  writes output repo: owned files + merged files
└────────┬────────────┘
         │
         ▼
  Clean output repo
  (no unselected references)
```

### Merger Strategy for Shared Files

Shared files like `.github/workflows/main.yml` are assembled by concatenating or deep-merging fragments in a defined order. Each fragment is a partial YAML/TOML/Makefile snippet. The merger knows the structure of each target file type:

- **GitHub Actions YAML**: merges `jobs` blocks by key
- **Makefile**: appends targets, detects duplicate target names and errors
- **`pyproject.toml` / `setup.cfg`**: merges `[tool.*]` sections by key
- **Plain text** (e.g. `.gitignore`): appends with deduplication

---

## Terraform as a Component Sub-layer

### Why Terraform, and Why Only as a Sub-layer

Terraform is used to provision all infrastructure that surrounds the data platform — cloud resources, Snowflake objects, and GitHub repository settings. It is a natural fit for three component groups:

| Component | What Terraform provisions |
|---|---|
| `cloud/aws` | VPC, IAM roles & policies, S3 buckets, MWAA environment |
| `cloud/gcp` | GCS buckets, IAM bindings, Cloud Composer environment, service accounts |
| `cloud/azure` | Resource groups, ADLS Gen2, managed identities |
| `access_control/rbac_snowflake` | Snowflake warehouses, databases, schemas, roles, grants |
| `access_control/abac_snowflake` | Snowflake row-access policies, object tags, masking policies |
| `cicd/github_actions` | Repo branch protection rules, Actions secrets, team permissions |

**dbt models are explicitly excluded** — dbt manages its own Snowflake objects and runs inside the warehouse. Terraform handles the infrastructure dbt runs *on* (the warehouse size, the role dbt assumes, the database it writes to), not the dbt project itself. The boundary is: Terraform provisions, dbt transforms.

### Why Terraform Is a Sub-layer, Not a Top-level Component

Terraform is not modelled as its own component group (e.g. `--terraform yes/no`) because making it a first-class component would require a **shared Terraform state file across all selected components**. This creates an unsolvable bootstrapping problem:

```
Problem: Terraform state must live somewhere before terraform apply runs.

Option A — Local state:  breaks in teams (no locking, no sharing)
Option B — S3/GCS backend: requires the cloud bucket to exist first,
           but that bucket is created by Terraform itself → chicken-and-egg
Option C — Terraform Cloud: adds an external dependency and account
           requirement outside the generated repo's control
```

By embedding Terraform **within each component** as a sub-layer, each component owns its own isolated `terraform/` directory with its own `backend.tf`. This means:

- The cloud component bootstraps first (its state can be local or a pre-created backend bucket)
- Downstream components (Snowflake RBAC, GitHub) reference cloud outputs via `terraform_remote_state` data sources, keeping state files decoupled
- No cross-component state entanglement — destroying one component's Terraform does not affect another's

### Component File Layout with Terraform Sub-layer

```
components/
├── cloud/aws/
│   ├── component.yaml
│   └── files/
│       └── terraform/
│           ├── main.tf           # VPC, subnets, S3, IAM
│           ├── variables.tf
│           ├── outputs.tf        # exports: bucket_name, role_arn, etc.
│           └── backend.tf        # configurable via state_backend group
│
├── access_control/rbac_snowflake/
│   ├── component.yaml
│   └── files/
│       └── terraform/
│           ├── main.tf           # Snowflake provider, roles, grants
│           ├── variables.tf      # reads cloud outputs via remote state
│           ├── outputs.tf
│           └── backend.tf
│
└── cicd/github_actions/
    ├── component.yaml
    └── files/
        └── terraform/
            ├── main.tf           # GitHub provider, branch protection, secrets
            ├── variables.tf
            └── backend.tf
```

### State Backend as a Selector Group

To solve the backend configuration cleanly, a `state_backend` group is added to the component selector. The assembler injects the correct `backend.tf` into every component's `terraform/` directory based on this selection:

| `state_backend` | Generated `backend.tf` |
|---|---|
| `s3` | S3 + DynamoDB locking (AWS only, validated against cloud selection) |
| `gcs` | GCS backend (GCP only) |
| `azurerm` | Azure Blob Storage backend (Azure only) |
| `terraform_cloud` | Terraform Cloud workspace (cloud-agnostic) |
| `local` | Local `.tfstate` file (single-developer use only, warned at generation) |

The validator enforces that `s3` backend is only selectable with `cloud: aws`, `gcs` with `cloud: gcp`, etc.

### Provisioning Order in the Generated Repo

The generated `Makefile` (assembled from each component's `makefile.fragment`) enforces the correct apply order:

```makefile
infra-up:
    cd terraform/cloud && terraform init && terraform apply
    cd terraform/access_control && terraform init && terraform apply
    cd terraform/cicd && terraform init && terraform apply

infra-down:
    cd terraform/cicd && terraform destroy
    cd terraform/access_control && terraform destroy
    cd terraform/cloud && terraform destroy
```

This explicit ordering avoids implicit dependency issues between state files and ensures teardown is safe.

---

## Cloud × Orchestration Interaction (Key Seam)

The `airflow_managed` component is the most complex because its deployment config differs per cloud:

```
airflow_managed/
├── component.yaml
├── files/
│   └── shared/               # DAG definitions, connection templates
├── fragments/
│   ├── aws/                  # MWAA-specific Terraform, IAM, env vars
│   └── gcp/                  # Cloud Composer-specific config
```

The resolver, after confirming `airflow_managed` + a cloud is selected, automatically activates the correct cloud-specific subfragments. From the user's perspective they only select `airflow_managed` — the cloud binding is implicit.

---

## CLI Interface (Intended UX)

```bash
# Interactive mode
template-gen init

# Non-interactive mode
template-gen init \
  --cloud gcp \
  --warehouse bigquery \
  --transformation dbt \
  --orchestration airflow_managed \
  --cicd github_actions \
  --observability gcp_monitoring \
  --output ./my-data-project

# Snowflake-only project — no cloud component needed, Tasks for orchestration
template-gen init \
  --warehouse snowflake \
  --access-control rbac_snowflake \
  --transformation dbt \
  --orchestration snowflake_tasks \
  --cicd github_actions \
  --output ./my-snowflake-project
```

Output:
```
Resolving components...
  ✓ gcp
  ✓ bigquery  (requires: cloud: gcp ✓)
  ✓ dbt  (requires: warehouse ✓)
  ✓ airflow_managed → resolved to Cloud Composer (gcp)
  ✓ github_actions
  ✓ gcp_monitoring  (requires: gcp ✓)

Validating compatibility...  ✓

Assembling repository...
  Writing owned files...       42 files
  Merging shared files...       4 files (.github/workflows/main.yml, Makefile, pyproject.toml, .gitignore)

Output written to ./my-data-project
```

---

## Complexity Estimate

| Area | Effort |
|---|---|
| Assembler core (resolver, merger, generator) | 2 days |
| Component manifest design + validation schema | 0.5 day |
| Component authoring — dbt + medallion models | 1 day |
| Component authoring — cloud Terraform (AWS/GCP/Azure) | 2–3 days |
| Component authoring — Snowflake RBAC/ABAC Terraform | 1 day |
| Component authoring — GitHub Actions + CI/CD fragments | 1 day |
| Component authoring — orchestration (Airflow variants) | 1–2 days |
| Cloud × orchestrator seam (MWAA vs Cloud Composer) | 1 day |
| State backend selector + backend.tf injection | 0.5 day |
| CLI UX + error messages | 0.5 day |
| Testing (snapshot tests on output repos) | 1 day |

The majority of time is spent **authoring Terraform modules and component fragments**, not building the assembler. The assembler itself is relatively straightforward once the manifest schema and state backend strategy are locked.

---

## What Remains Out of Scope Here

- A web UI for component selection (the CLI is the interface)
- GitHub template repo auto-provisioning via the GitHub API
- Versioning of individual components independently
- An open plugin ecosystem (components are curated, not third-party)
