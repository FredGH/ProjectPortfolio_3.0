# One Docker repo for all three images (api, ui, pipeline) — matches
# this project's "one image per workload" convention from the local
# docker-compose setup, just hosted remotely.
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = "job-search"
  format        = "DOCKER"
  description   = "Container images for the job-search platform (api, ui, pipeline)"

  depends_on = [google_project_service.required]
}
