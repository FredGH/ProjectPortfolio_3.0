# Enables every GCP API this stack's resources need. Terraform-managed
# rather than a manual Console step, so `terraform apply` is the single
# source of truth for what's turned on.
locals {
  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudbuild.googleapis.com",
    "iap.googleapis.com",
    "storage.googleapis.com",
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false # never disable an API just because Terraform is torn down
}
