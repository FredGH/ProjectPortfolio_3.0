resource "google_cloud_run_v2_service" "api" {
  project  = var.project_id
  name     = "job-search-api"
  location = var.region

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = 0
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/api:latest"

      ports {
        container_port = 8000
      }

      env {
        name  = "ENV"
        value = var.environment
      }
      env {
        name  = "DB_CONNECTION_MODE"
        value = "dsn"
      }
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.this["neon_owner_dsn"].secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "APP_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.this["neon_app_dsn"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.api_secret_access,
  ]
}

# Personal-use, two trusted users (PLAN.md's own scope note,
# DECISIONS.md §7) — open to authenticated invocations from the UI's
# service identity, not the public internet. Tighten to per-user IAM if
# this ever moves beyond two trusted users.
resource "google_cloud_run_v2_service_iam_member" "api_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.ui.email}"
}
