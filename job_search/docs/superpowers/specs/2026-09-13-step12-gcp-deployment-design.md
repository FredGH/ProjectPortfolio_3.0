# Step 12 — First GCP Deployment (JOB-172)

**Status:** Approved for planning
**Date:** 2026-09-13

## Context

PLAN.md's Step 12 ("Done when": the same git commit runs locally and in
GCP with only environment variables differing, and an ingestion run in
GCP lands rows in the managed database) assumes a GCP account and
billing already exist. **They don't yet** — this spec scopes Step 12 to
what's actually deliverable right now: complete, correct Infrastructure-
as-Code (Terraform) and a setup runbook, ready to `terraform apply` the
moment an account exists. Real provisioning, and therefore PLAN.md's
literal "Done when," are deferred.

Two decisions already made with the user, both departing from PLAN.md's
literal text:

- **Database: Neon, not Cloud SQL.** PLAN.md itself recommends this
  ("Start on Neon's free tier... Move to Cloud SQL only if you outgrow
  it") — this spec just makes it the actual choice from day one rather
  than a fallback. Confirmed in `packages/core/core/settings.py`:
  `db_connection_mode` already supports `"dsn"` (works for Neon exactly
  like local Postgres) and explicitly does NOT implement
  `"cloud_sql_connector"` yet ("a later addition, not built here") — so
  choosing Neon avoids building that IAM-connector path at all, not just
  defers it.
- **`embedding_provider` stays `"ollama"` in GCP**, per DECISIONS.md's
  existing "embed locally, always" rule (already encoded in
  `settings.py`'s docstring). The GCP pipeline reads precomputed vectors;
  no Ollama container is deployed to Cloud Run.

## Scope

**In scope:**
1. Terraform: Artifact Registry (one Docker repo, region-parameterized)
2. Terraform: GCS landing bucket (same path convention as local:
   `landing/source=.../dt=.../run_id=.../part-*.jsonl.gz`)
3. Terraform: Secret Manager secrets (Neon DSNs ×2 — owner/app roles —
   plus Anthropic/Adzuna/Reed/Jooble keys), values supplied as
   `sensitive` Terraform variables, never hardcoded
4. Terraform: three least-privilege service accounts (api, ui, pipeline)
   with only the IAM bindings each workload actually needs (see
   Architecture — no `cloudsql.client` anywhere, since Neon needs none)
5. Terraform: Cloud Run service for the API
6. Terraform: Cloud Run service for the UI, with an IAP-ready IAM
   binding (the OAuth consent screen itself is a manual prerequisite —
   see Runbook)
7. Terraform: Cloud Run Job for the pipeline
8. Terraform: Cloud Build trigger resource (the GitHub App connection
   itself is a manual prerequisite — see Runbook), plus a `cloudbuild.yaml`
   defining the build steps
9. `terraform.tfvars.example` — every variable, no real values
10. `infra/README.md` — the runbook: account/billing setup, Neon setup,
    one-time manual GCP Console steps, `terraform init`/`plan`/`apply`
    order, and the local-vs-GCP environment variable matrix PLAN.md's
    own subtask list asks for
11. Tests: since this is Terraform, "tests" means `terraform validate`
    and `terraform plan` succeeding against the example tfvars (with
    placeholder/fake values) — there is no unit-test framework for HCL
    in this project, and none is being introduced

**Out of scope (this pass, explicitly):**
- Running `terraform apply` against a real project (no account exists)
- Cloud Scheduler (PLAN.md's own Step 22, not Step 12)
- n8n orchestration (Step 23)
- Verifying an ingestion run actually lands rows in GCP (needs a real
  deployment — the literal PLAN.md "Done when," deferred)
- Cloud SQL / `cloud-sql-python-connector` support in `core.db.session`
  (not needed — Neon is a plain DSN)
- Any change to application code, Dockerfiles, or `docker-compose.yml`
  (the existing images already build correctly per `apps/*/Dockerfile`;
  this step deploys them, it doesn't change them)

## Architecture

### Module layout

One flat root module under `infra/` — not a reusable child-module
structure. At this project's actual scale (personal, two trusted users,
~1 Artifact Registry repo, 1 GCS bucket, 3 service accounts, 2 Cloud Run
services + 1 Job, 1 Cloud Build trigger, 7 secrets) a child-module
layer is premature abstraction, and it cuts against this project's own
stated preference for simplicity over generality at small scale
(DECISIONS.md §3's "import a function, not an architecture" reasoning
applies just as well to infrastructure code).

```
infra/
  versions.tf          # required_providers, required_version
  providers.tf          # google provider config, project/region from vars
  variables.tf          # every input variable, documented, no defaults
                          # tied to a real account
  outputs.tf             # Cloud Run URLs, Artifact Registry repo URL,
                          # service account emails
  artifact_registry.tf
  storage.tf              # GCS landing bucket
  secrets.tf                # Secret Manager secrets (metadata only —
                              # secret *values* are set via a separate,
                              # git-ignored process, see Secrets below)
  service_accounts.tf        # 3 SAs + their IAM bindings
  cloud_run_api.tf
  cloud_run_ui.tf
  cloud_run_job_pipeline.tf
  cloud_build.tf               # trigger + cloudbuild.yaml is separate,
                                 # at repo root per Cloud Build convention
  terraform.tfvars.example
  README.md
cloudbuild.yaml                 # repo root — Cloud Build's default
                                  # discovery location
```

### Variables (no real-account defaults)

```hcl
variable "project_id" {
  type = string
  # no default — must be supplied once a real project exists
}

variable "region" {
  type    = string
  default = "europe-west2" # PLAN.md's own suggestion, fully overridable
}

variable "environment" {
  type    = string
  default = "gcp"
}

variable "github_owner" {
  type = string # no default — the user's real GitHub org/username
}

variable "github_repo" {
  type = string # no default — this repo's name
}

variable "neon_owner_dsn" {
  type      = string
  sensitive = true
}

variable "neon_app_dsn" {
  type      = string
  sensitive = true
}

variable "anthropic_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "adzuna_app_id" {
  type    = string
  default = ""
}

variable "adzuna_app_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "reed_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "jooble_key" {
  type      = string
  sensitive = true
  default   = ""
}
```

7 secret-bearing variables in total (`neon_owner_dsn`, `neon_app_dsn`,
`anthropic_api_key`, `adzuna_app_id` — not sensitive, an ID rather than a
key, but still routed through Secret Manager for uniformity with PLAN.md's
"Secrets: Secret Manager, mounted as env vars" — `adzuna_app_key`,
`reed_api_key`, `jooble_key`). Neon connection strings must include
`?sslmode=require` — Neon rejects unencrypted connections — the runbook
notes this explicitly so it isn't discovered as a confusing connection
failure later.

`region` defaults to `europe-west2` (PLAN.md's own suggestion) but is
fully overridable — "generic," not hardcoded to an account, per the
user's choice.

### Secrets — values never touch Terraform state in plaintext by policy, tfstate treated as sensitive regardless

Terraform's `google_secret_manager_secret` resource creates the secret
*container*; a `google_secret_manager_secret_version` resource sets its
value from the corresponding `sensitive` variable. Marking a variable
`sensitive` stops Terraform from printing it in plan/apply output, but
**does not encrypt it out of the state file** — this is a real,
documented Terraform limitation, not solved by this step. The runbook
must say so explicitly: never commit `terraform.tfstate`, treat it as a
secret artifact, and use a remote backend with encryption-at-rest
(GCS backend with default Google-managed encryption is sufficient) once
a real project exists, rather than the local backend Terraform defaults
to.

### Service accounts — least privilege, Neon changes what "least" means

| SA | IAM bindings | Why |
|---|---|---|
| `pipeline-sa` | `roles/storage.objectAdmin` on the landing bucket only (resource-level, not project-level); `roles/secretmanager.secretAccessor` on its Neon owner DSN + source API key secrets | Writes to the landing zone, reads bronze-load credentials and source keys |
| `api-sa` | `roles/secretmanager.secretAccessor` on its Neon app DSN secret only | Serves requests against the app-role DSN; no Cloud SQL client role exists to grant because Neon needs none |
| `ui-sa` | none beyond the default Cloud Run runtime identity | Streamlit talks to the API over HTTP (`api_base_url`) — confirmed by reading `apps/ui/app/pages/*.py`, none of which import a DB session — so it touches no secret and no bucket |

This table is a deliberate, documented departure from PLAN.md's literal
text ("The pipeline job needs `storage.objectAdmin`... and
`cloudsql.client`. The UI needs `cloudsql.client` and nothing else") —
that text assumes Cloud SQL. Neon removes the `cloudsql.client` role
from every row, and the UI ends up needing nothing at all rather than
"nothing else."

### Cloud Run

- **API**: `google_cloud_run_v2_service`, `min_instances = 0`, image from
  Artifact Registry, env vars from `variables.tf` + secret references
  for the DSN, `ENV=gcp`, `DB_CONNECTION_MODE=dsn`.
- **UI**: same shape, plus a `google_cloud_run_v2_service_iam_binding`
  restricting invocation to IAP's service identity — the binding is
  real Terraform, but IAP itself requires an OAuth consent screen
  configured once via Console first (see Runbook; this is a genuine
  GCP product limitation, not something skipped for convenience).
- **Pipeline**: `google_cloud_run_v2_job`, not a service — batch
  semantics, matching PLAN.md's explicit reasoning ("right primitive for
  batch, 24h timeout, no request lifecycle to fight").

### Cloud Build

`cloudbuild.yaml` at the repo root: three build+push steps (one per
image, tagged `region-docker.pkg.dev/$PROJECT_ID/<repo>/<service>:$COMMIT_SHA`),
then a deploy step per Cloud Run service/job using `gcloud run deploy`/
`gcloud run jobs update`. The Terraform `google_cloudbuild_trigger`
resource wires this to push events on the GitHub repo — but connecting
Cloud Build to a GitHub repo at all requires the Cloud Build GitHub App
to be installed via Console first (a real one-time manual step with no
Terraform equivalent as of this writing); the runbook documents it as
Step 1, before any `terraform apply`.

## Runbook (`infra/README.md`) — table of contents

1. Create a GCP project, enable billing (with the free-tier/trial
   caveat that Cloud Run's free tier alone likely covers this project's
   volume, per PLAN.md's own cost framing)
2. Enable required APIs (Cloud Run, Artifact Registry, Secret Manager,
   Cloud Build, IAP)
3. Create a Neon account + project, enable the `pgvector` extension,
   note the owner and app-role connection strings
4. Install the Cloud Build GitHub App and connect this repo (manual,
   Console-only)
5. Configure the IAP OAuth consent screen (manual, Console-only,
   required before the UI's IAP binding can actually gate access)
6. `gcloud auth application-default login` (or a service-account key for
   CI) so Terraform can authenticate
7. Copy `terraform.tfvars.example` to `terraform.tfvars`, fill in real
   values, confirm it's git-ignored
8. `terraform init`, `terraform plan`, review, `terraform apply`
9. First manual `gcloud run jobs execute` of the pipeline job to prove
   an ingestion run lands rows (this is PLAN.md's literal "Done when" —
   explicitly the user's job, once the above exists, not this step's)

## Local vs. GCP environment variable matrix (goes in `infra/README.md`, mirrors `.env.example`)

| Variable | Local | GCP |
|---|---|---|
| `ENV` | `local` | `gcp` |
| `DATABASE_URL` / `APP_DATABASE_URL` | local Postgres DSN | Neon DSN (from Secret Manager) |
| `DB_CONNECTION_MODE` | `dsn` | `dsn` (unchanged — Neon needs no connector) |
| `LANDING_URI` | `file:///data/landing` | `gs://<bucket>/landing` |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` (unchanged — embed locally always) |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | not set — GCP never calls Ollama directly |
| `API_BASE_URL` | `http://api:8000` (docker-compose service name) | the deployed API's Cloud Run URL |

## Testing

- `terraform fmt -check` and `terraform validate` on every `.tf` file
- `terraform plan -var-file=terraform.tfvars.example` (with placeholder
  fake values for every variable, including the `sensitive` ones) must
  succeed with no errors — this is the closest thing to a "test" HCL has
  without a real project to plan against
- Manual review of `cloudbuild.yaml` against each Dockerfile's actual
  build context (no automated test possible without a real Cloud Build
  run)

## Done when

- `terraform fmt -check` and `terraform validate` pass
- `terraform plan -var-file=terraform.tfvars.example` succeeds cleanly
- Every resource in Architecture above exists in the Terraform code
- `infra/README.md` covers every runbook step above, including the two
  manual-Console prerequisites and the full env-var matrix
- `terraform.tfvars.example` is committed; `terraform.tfvars` and
  `*.tfstate*` are added to `.gitignore`
