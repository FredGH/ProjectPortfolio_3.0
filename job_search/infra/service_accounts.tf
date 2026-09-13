# One least-privilege service account per workload. All three read both
# Neon DSN secrets: core.settings.Settings requires database_url AND
# app_database_url with no default (settings.py), and get_settings() is
# called at import time in every app (api, ui, pipeline) — a container
# missing either variable fails Pydantic validation and never starts,
# regardless of which DSN its own code path actually queries with.
locals {
  dsn_secret_ids = ["neon_owner_dsn", "neon_app_dsn"]
}

resource "google_service_account" "pipeline" {
  project      = var.project_id
  account_id   = "job-search-pipeline"
  display_name = "job-search pipeline (Cloud Run Job)"
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "job-search-api"
  display_name = "job-search API (Cloud Run service)"
}

resource "google_service_account" "ui" {
  project      = var.project_id
  account_id   = "job-search-ui"
  display_name = "job-search UI (Cloud Run service, IAP-gated)"
}

# pipeline-sa: writes/replays the landing zone. Resource-level binding
# on the bucket itself, not a project-level role.
resource "google_storage_bucket_iam_member" "pipeline_landing_object_admin" {
  bucket = google_storage_bucket.landing.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

# pipeline-sa: both DSNs (see note above) plus every source API key it
# needs to run connectors and the LLM residual classification stage.
resource "google_secret_manager_secret_iam_member" "pipeline_secret_access" {
  for_each = toset(concat(local.dsn_secret_ids, [
    "anthropic_api_key",
    "adzuna_app_id",
    "adzuna_app_key",
    "reed_api_key",
    "jooble_key",
  ]))

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.pipeline.email}"
}

# api-sa: both DSNs (see note above) — no Cloud SQL client role exists
# to grant, because Neon needs none.
resource "google_secret_manager_secret_iam_member" "api_secret_access" {
  for_each = toset(local.dsn_secret_ids)

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# ui-sa: both DSNs (see note above), and nothing else — Streamlit talks
# to the API over HTTP (api_base_url) and never queries Postgres
# directly. No bucket access, no other secrets.
resource "google_secret_manager_secret_iam_member" "ui_secret_access" {
  for_each = toset(local.dsn_secret_ids)

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.ui.email}"
}
