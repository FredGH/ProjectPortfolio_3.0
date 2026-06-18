terraform {
  required_version = ">= 1.7"

  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "~> 0.98"
    }
  }

  # Local backend — state stored on disk, path set at init time:
  #   terraform init -backend-config=environments/dev.backend
  backend "local" {}
}

provider "snowflake" {
  organization_name = var.snowflake_organization_name
  account_name      = var.snowflake_account_name
  user              = var.snowflake_user
  # ACCOUNTADMIN is required: SYSADMIN cannot CREATE ROLE or GRANT ROLE in Snowflake.
  # TERRAFORM_SVC is granted ACCOUNTADMIN in 01_databases.sql.
  role          = "ACCOUNTADMIN"
  authenticator = "snowflake_jwt"

  # private_key_path was removed in provider ~>0.87; use private_key with file().
  private_key = file(var.snowflake_private_key_path)
}
