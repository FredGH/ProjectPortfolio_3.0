variable "snowflake_organization_name" {
  description = "Snowflake organisation name — from SELECT CURRENT_ORGANIZATION_NAME()"
  type        = string
}

variable "snowflake_account_name" {
  description = "Snowflake account name — from SELECT CURRENT_ACCOUNT_NAME()"
  type        = string
}

variable "snowflake_user" {
  description = "Snowflake user for Terraform (TERRAFORM_SVC)"
  type        = string
  default     = "TERRAFORM_SVC"
}

variable "snowflake_private_key_path" {
  description = "Filesystem path to the PEM-encoded RSA private key for TERRAFORM_SVC. Read at plan time via file(). Must be unencrypted or decrypted before use."
  type        = string
  default     = "~/.ssh/terraform_svc.p8"
  sensitive   = true
}

variable "environment" {
  description = "Target environment (dev | uat | prod)"
  type        = string
  validation {
    condition     = contains(["dev", "uat", "prod"], var.environment)
    error_message = "environment must be one of: dev, uat, prod"
  }
}

