# Every secret-bearing value this stack's containers need. Secret
# *containers* and their *versions* are both managed here; the values
# themselves come from `sensitive` Terraform variables, never
# hardcoded. Marking a variable sensitive stops Terraform printing it in
# plan/apply output, but does NOT encrypt it out of the state file —
# see infra/README.md's runbook for why terraform.tfstate must be
# treated as a secret artifact regardless (never committed, ideally a
# remote encrypted backend once a real project exists).
locals {
  secrets = {
    neon_owner_dsn    = var.neon_owner_dsn
    neon_app_dsn      = var.neon_app_dsn
    anthropic_api_key = var.anthropic_api_key
    adzuna_app_id     = var.adzuna_app_id
    adzuna_app_key    = var.adzuna_app_key
    reed_api_key      = var.reed_api_key
    jooble_key        = var.jooble_key
  }
}

resource "google_secret_manager_secret" "this" {
  for_each = local.secrets

  project   = var.project_id
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "this" {
  for_each = local.secrets

  secret      = google_secret_manager_secret.this[each.key].id
  secret_data = each.value
}
