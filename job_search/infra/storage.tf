# Landing zone bucket. Same immutable, replayable path convention as
# local (landing/source=.../dt=.../run_id=.../part-*.jsonl.gz) — this
# bucket is just the gs:// root fsspec writes/reads through, per
# PLAN.md's "every environment difference is an env var" rule. Code
# never branches on local vs GCP here.
resource "google_storage_bucket" "landing" {
  project                     = var.project_id
  name                        = "${var.project_id}-job-search-landing"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  depends_on = [google_project_service.required]
}
