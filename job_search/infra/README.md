# GCP deployment (PLAN.md Step 12, JOB-172)

This directory holds the Terraform for the job-search platform's first
GCP deployment. It has been validated with `terraform fmt -check` and
`terraform validate` — both pass without a GCP account. It has **not**
been applied, and `terraform plan` has not been run, because neither is
possible without real GCP credentials (verified directly: the google
provider refuses to plan at all without Application Default
Credentials, even against a project that doesn't exist yet). This
runbook is what to do once you have an account.

## 1. Create a GCP project and enable billing

Console → create a new project, note its **project ID** (not its
display name — the ID is what goes in `terraform.tfvars`). Enable
billing on it. At this project's volume, Cloud Run's free tier
(2 million requests/month, generous CPU/memory-seconds) likely covers
the API and UI services entirely; the pipeline Job only runs when you
invoke it. Artifact Registry, Secret Manager, and Cloud Build also have
free-tier allowances well above what two trusted users generate.

## 2. Required APIs

Handled by Terraform itself (`infra/apis.tf`'s `google_project_service`
resources) — nothing to do here manually. Listed for reference:
Cloud Run, Artifact Registry, Secret Manager, Cloud Build, IAP,
Cloud Storage.

## 3. Create a Neon project

[neon.tech](https://neon.tech) → new project → enable the `pgvector`
extension (`CREATE EXTENSION vector;` via Neon's SQL editor, or your own
migration). Create the same two roles this project already uses locally
(`job_search_owner`, `job_search_app`) with the same privilege split —
owner bypasses RLS, app is RLS-enforced — and copy both connection
strings. **Both must include `?sslmode=require`** — Neon rejects
unencrypted connections, and a DSN missing this will fail with a
confusing connection error rather than an obvious one.

## 4. Connect Cloud Build to this GitHub repo (manual, Console-only)

Cloud Build Console → Triggers → Connect Repository → GitHub → install
the Cloud Build GitHub App on `ProjectPortfolio_3.0` (or your fork).
**There is no Terraform resource for this step** — the
`google_cloudbuild_trigger` resource in `infra/cloud_build.tf` will fail
to apply until this connection exists.

## 5. Configure the IAP OAuth consent screen — only if you later add full IAP

This Terraform deliberately does **not** use the full IAP product —
the UI service is instead restricted via a plain
`roles/run.invoker` IAM allowlist (`var.authorized_user_emails`), which
needs no OAuth consent screen at all and is fully expressible in
Terraform. Skip this step unless you specifically want to upgrade to
IAP later (stronger audit logging, a hosted sign-in page) — if so,
Console → APIs & Services → OAuth consent screen, configure it as
"Internal" (two trusted users, PLAN.md's own scope), then add the
`google_iap_web_cloud_run_service_iam_member` resource yourself; it
isn't in this configuration.

## 6. Authenticate Terraform

```bash
gcloud auth application-default login
```

(Or, for CI, a service account key — out of scope for a personal,
two-user deployment.)

## 7. Fill in tfvars

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your real `project_id`, GitHub
owner/repo, both Neon DSNs, and whichever source API keys you have.
`terraform.tfvars` is git-ignored — confirm with `git check-ignore -v
infra/terraform.tfvars` before your first `terraform apply` if you're
ever unsure.

## 8. Init, plan, apply

```bash
terraform init
terraform plan    # first real plan — needs the credentials from step 6
terraform apply
```

Review the plan output before applying, as always. `terraform apply`
creates: the Artifact Registry repo, the landing bucket, 7 Secret
Manager secrets, 3 service accounts, 2 Cloud Run services, 1 Cloud Run
Job, and the Cloud Build trigger.

**On the state file:** marking a variable `sensitive` stops Terraform
printing it in plan/apply output, but does **not** encrypt it out of
`terraform.tfstate` — this is a real, documented Terraform limitation,
not something this configuration solves. Never commit
`terraform.tfstate` (already git-ignored). The local backend Terraform
defaults to is fine to start; if you want stronger protection, move to
a GCS backend (Google-managed encryption at rest) once the project
exists — `infra/versions.tf` currently has no `backend` block, so
Terraform uses the local `terraform.tfstate` file next to the other
`.tf` files.

## 9. Build and deploy the first images

Push to `main` (or manually trigger the Cloud Build trigger in Console)
— `cloudbuild.yaml` builds all three images, pushes them, and deploys
the two Cloud Run services plus updates the Job's image. First deploy
will be slow (cold Artifact Registry, cold Cloud Run revisions); later
ones are fast.

## 10. Run the pipeline and verify rows land

This is PLAN.md's literal Step 12 "Done when" — explicitly **not**
done by this Terraform, since it needs everything above to exist first:

```bash
gcloud run jobs execute job-search-pipeline --region=<your region>
```

Then check Neon (via its SQL editor, or `psql` against the app DSN)
for rows in `bronze.raw_jobs`. Once this works, the same git commit is
proven to run locally and in GCP with only environment variables
differing.

## Local vs. GCP environment variable matrix

| Variable | Local | GCP |
|---|---|---|
| `ENV` | `local` | `gcp` |
| `DATABASE_URL` | local Postgres owner DSN | Neon owner DSN (Secret Manager) |
| `APP_DATABASE_URL` | local Postgres app DSN | Neon app DSN (Secret Manager) |
| `DB_CONNECTION_MODE` | `dsn` | `dsn` (unchanged — Neon needs no IAM connector) |
| `LANDING_URI` | `file:///data/landing` | `gs://<project-id>-job-search-landing/landing` |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` (unchanged — embed locally, always; see DECISIONS.md) |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | not set — the GCP pipeline never calls an embedding server directly, it reads precomputed vectors |
| `API_BASE_URL` | `http://api:8000` (docker-compose service name) | the deployed API service's Cloud Run URL (Terraform output `api_url`) |
| `ANTHROPIC_API_KEY` / `ADZUNA_*` / `REED_API_KEY` / `JOOBLE_KEY` | `.env` | Secret Manager, injected as env vars |

## Why every service account reads both Neon DSNs

`core.settings.Settings` requires both `database_url` and
`app_database_url` with no default — confirmed by reading
`packages/core/core/settings.py` directly. `get_settings()` runs at
import time in all three apps (api, ui, pipeline). A container missing
either variable fails Pydantic validation and never starts, regardless
of which DSN its own code actually queries with — so every service
account in `infra/service_accounts.tf` gets `secretAccessor` on both
secrets, even the UI, which never issues a Postgres query directly.

## What's deliberately out of scope here

- Cloud Scheduler (PLAN.md's Step 22)
- n8n orchestration (Step 23)
- `cloud-sql-python-connector` support in `core.db.session` — not
  needed, Neon is a plain DSN like local Postgres
- Any change to application code or the existing Dockerfiles — this
  Terraform deploys what already builds correctly locally, it doesn't
  change it
