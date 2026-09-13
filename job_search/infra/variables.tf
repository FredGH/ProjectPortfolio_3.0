variable "project_id" {
  type        = string
  description = "GCP project ID. No default — must be supplied once a real project exists."
}

variable "region" {
  type        = string
  description = "GCP region for all resources. Defaults to PLAN.md's own suggestion; fully overridable."
  default     = "europe-west2"
}

variable "environment" {
  type        = string
  description = "Value for the app's ENV setting in every deployed container."
  default     = "gcp"
}

variable "github_owner" {
  type        = string
  description = "GitHub org/user that owns this repo, for the Cloud Build trigger."
}

variable "github_repo" {
  type        = string
  description = "GitHub repo name, for the Cloud Build trigger."
}

variable "neon_owner_dsn" {
  type        = string
  description = "Neon Postgres DSN for the migration/owner role (bypasses RLS). Must include ?sslmode=require."
  sensitive   = true
}

variable "neon_app_dsn" {
  type        = string
  description = "Neon Postgres DSN for the job_search_app role (RLS-enforced). Must include ?sslmode=require."
  sensitive   = true
}

variable "anthropic_api_key" {
  type        = string
  description = "Anthropic API key, for tasks config/llm_tasks.yml routes to the anthropic provider."
  sensitive   = true
  default     = ""
}

variable "adzuna_app_id" {
  type        = string
  description = "Adzuna connector application ID (not a secret key, but routed through Secret Manager for uniformity)."
  default     = ""
}

variable "adzuna_app_key" {
  type        = string
  description = "Adzuna connector application key."
  sensitive   = true
  default     = ""
}

variable "reed_api_key" {
  type        = string
  description = "Reed.co.uk connector API key."
  sensitive   = true
  default     = ""
}

variable "jooble_key" {
  type        = string
  description = "Jooble connector API key."
  sensitive   = true
  default     = ""
}

variable "authorized_user_emails" {
  type        = list(string)
  description = <<-EOT
    Google account emails allowed to invoke the UI service directly
    (PLAN.md's "two trusted users" scope, DECISIONS.md §7). Kept as an
    explicit IAM allowlist rather than the public internet — a full IAP
    setup is a stronger, more sophisticated option but depends on a
    Console-configured OAuth consent screen; this list achieves the
    same "not open to the public" goal reliably from Terraform alone.
  EOT
  default     = []
}
