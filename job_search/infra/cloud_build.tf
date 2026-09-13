# Wires push events on this GitHub repo's main branch to the
# cloudbuild.yaml build defined at the repo root. Connecting Cloud
# Build to a GitHub repo at all requires the Cloud Build GitHub App to
# be installed via Console first — a genuine one-time manual
# prerequisite with no Terraform equivalent as of this writing (see
# infra/README.md's runbook, Step 4). This resource will fail to apply
# until that connection exists.
resource "google_cloudbuild_trigger" "main_push" {
  project     = var.project_id
  location    = var.region
  name        = "job-search-main-push"
  description = "Build and deploy api/ui/pipeline images on every push to main"

  github {
    owner = var.github_owner
    name  = var.github_repo

    push {
      branch = "^main$"
    }
  }

  # This repo is a monorepo with several unrelated projects
  # (job_search, etl, extractor, analytics, ...) — only rebuild/redeploy
  # when something under job_search/ actually changed.
  included_files = ["job_search/**"]

  filename = "job_search/cloudbuild.yaml"

  depends_on = [google_project_service.required]
}

# Default Cloud Build identity — cloudbuild.yaml's deploy steps run
# `gcloud run deploy`/`gcloud run jobs update` as this account, so it
# needs Cloud Run admin plus permission to act as each target service's
# runtime service account (Cloud Run's own deploy-time requirement).
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_project_iam_member" "cloudbuild_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

resource "google_service_account_iam_member" "cloudbuild_act_as_api" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

resource "google_service_account_iam_member" "cloudbuild_act_as_ui" {
  service_account_id = google_service_account.ui.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}

resource "google_service_account_iam_member" "cloudbuild_act_as_pipeline" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_project.this.number}@cloudbuild.gserviceaccount.com"
}
