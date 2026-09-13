# Cloud Run Job, not a service — right primitive for batch (24h
# timeout, no request lifecycle to fight), per PLAN.md's own reasoning.
# Invoked manually (`gcloud run jobs execute`) until Step 22 wires
# Cloud Scheduler — deliberately out of scope here.
resource "google_cloud_run_v2_job" "pipeline" {
  project  = var.project_id
  name     = "job-search-pipeline"
  location = var.region

  template {
    template {
      service_account = google_service_account.pipeline.email

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/pipeline:latest"

        env {
          name  = "ENV"
          value = var.environment
        }
        env {
          name  = "DB_CONNECTION_MODE"
          value = "dsn"
        }
        env {
          name  = "LANDING_URI"
          value = "gs://${google_storage_bucket.landing.name}/landing"
        }
        # embedding_provider stays "ollama" even in GCP (DECISIONS.md's
        # "embed locally, always" rule) — the pipeline reads
        # precomputed vectors rather than generating them, so no
        # OLLAMA_BASE_URL is set here; there is no Ollama server to
        # reach from this job.
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
        env {
          name = "ANTHROPIC_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["anthropic_api_key"].secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "ADZUNA_APP_ID"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["adzuna_app_id"].secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "ADZUNA_APP_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["adzuna_app_key"].secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "REED_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["reed_api_key"].secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "JOOBLE_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.this["jooble_key"].secret_id
              version = "latest"
            }
          }
        }
      }

      timeout     = "86400s" # 24h ceiling PLAN.md flags as the reason Cloud Run Jobs fit this workload
      max_retries = 1
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.pipeline_secret_access,
    google_storage_bucket_iam_member.pipeline_landing_object_admin,
  ]
}
