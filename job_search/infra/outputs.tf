output "api_url" {
  description = "The deployed API service's Cloud Run URL."
  value       = google_cloud_run_v2_service.api.uri
}

output "ui_url" {
  description = "The deployed UI service's Cloud Run URL. Invocation is IAM-restricted — see authorized_user_emails."
  value       = google_cloud_run_v2_service.ui.uri
}

output "artifact_registry_repository" {
  description = "Full path to push images to, e.g. for cloudbuild.yaml or manual `docker push`."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "landing_bucket" {
  description = "GCS bucket name backing LANDING_URI in GCP."
  value       = google_storage_bucket.landing.name
}

output "pipeline_service_account_email" {
  value = google_service_account.pipeline.email
}

output "api_service_account_email" {
  value = google_service_account.api.email
}

output "ui_service_account_email" {
  value = google_service_account.ui.email
}
