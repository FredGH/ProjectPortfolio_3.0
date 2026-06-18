# ── Internal stage for dbt artefacts ──
# Mirrors: CREATE STAGE CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS
#
# Stage path convention:
#   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/<env>/latest/  — current run
#   @CSTA_MARKETING_SHARED.ARTIFACTS.CSTA_DBT_ARTIFACTS/<env>/<run_id>/ — historical
#
# Slim CI: dbt test --select state:modified+ --defer --state @CSTA_DBT_ARTIFACTS/uat/latest/

resource "snowflake_stage" "dbt_artifacts" {
  name        = "CSTA_DBT_ARTIFACTS"
  database    = "CSTA_MARKETING_SHARED"
  schema      = "ARTIFACTS"
  encryption  = "TYPE = 'SNOWFLAKE_SSE'"
  directory   = "ENABLE = TRUE"
  comment     = "Internal stage for dbt artefacts shared across dev/uat/prod environments"
}

# Stage access grants — READ for all dbt and observer roles, WRITE for dbt roles.
# Mirrors: GRANT READ / WRITE ON STAGE ... TO ROLE ... (in 04_stages.sql)

locals {
  stage_read_roles = toset([
    "CSTA_DBT_DEV_ROLE",
    "CSTA_DBT_UAT_ROLE",
    "CSTA_DBT_PROD_ROLE",
    "CSTA_OBSERVER_ROLE",
  ])

  stage_write_roles = toset([
    "CSTA_DBT_DEV_ROLE",
    "CSTA_DBT_UAT_ROLE",
    "CSTA_DBT_PROD_ROLE",
  ])
}

resource "snowflake_grant_privileges_to_account_role" "stage_read" {
  for_each          = local.stage_read_roles
  account_role_name = each.value
  privileges        = ["READ"]

  on_account_object {
    object_type = "STAGE"
    object_name = "${snowflake_stage.dbt_artifacts.database}.${snowflake_stage.dbt_artifacts.schema}.${snowflake_stage.dbt_artifacts.name}"
  }
}

resource "snowflake_grant_privileges_to_account_role" "stage_write" {
  for_each          = local.stage_write_roles
  account_role_name = each.value
  privileges        = ["WRITE"]

  on_account_object {
    object_type = "STAGE"
    object_name = "${snowflake_stage.dbt_artifacts.database}.${snowflake_stage.dbt_artifacts.schema}.${snowflake_stage.dbt_artifacts.name}"
  }
}

# ── Snowflake Secrets for dbt profiles.yml (one per environment) ──
# Injected into the dbt stored procedure (Phase 9) via SYSTEM$GET_SECRET.
# Secret content must be populated manually after `terraform apply`:
#   ALTER SECRET CSTA_MARKETING_SHARED.ARTIFACTS.PROFILES_YML_DEV SET SECRET_STRING = '...';
#
# Reference: https://docs.snowflake.com/en/sql-reference/sql/create-secret

resource "snowflake_secret_with_generic_string" "profiles_yml" {
  for_each = {
    dev  = { name = "PROFILES_YML_DEV",  comment = "dbt profiles.yml for dev environment" }
    uat  = { name = "PROFILES_YML_UAT",  comment = "dbt profiles.yml for uat environment" }
    prod = { name = "PROFILES_YML_PROD", comment = "dbt profiles.yml for prod environment" }
  }

  name          = each.value.name
  database      = "CSTA_MARKETING_SHARED"
  schema        = "ARTIFACTS"
  secret_string = ""
  comment       = "${each.value.comment}; populate via ALTER SECRET after apply"
}
