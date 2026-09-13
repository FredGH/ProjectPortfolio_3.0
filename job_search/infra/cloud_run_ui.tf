# This is CV data — never open to the public internet (PLAN.md's own
# note on the Streamlit service). Invocation is restricted to the
# explicit user allowlist in var.authorized_user_emails via IAM below,
# rather than the fuller IAP product feature — see variables.tf's
# authorized_user_emails docstring for why.
resource "google_cloud_run_v2_service" "ui" {
  project  = var.project_id
  name     = "job-search-ui"
  location = var.region

  template {
    service_account = google_service_account.ui.email

    scaling {
      min_instance_count = 0
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/ui:latest"

      ports {
        container_port = 8501
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
        name  = "API_BASE_URL"
        value = google_cloud_run_v2_service.api.uri
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
    google_secret_manager_secret_iam_member.ui_secret_access,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "ui_invoker" {
  for_each = toset(var.authorized_user_emails)

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.ui.name
  role     = "roles/run.invoker"
  member   = "user:${each.value}"
}
