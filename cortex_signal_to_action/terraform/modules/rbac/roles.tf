# ── Layer 1a: DB-level access roles (READ + MODIFY × 4 databases = 8 roles) ──

resource "snowflake_account_role" "db_access_roles" {
  for_each = local.db_access_role_keys

  name = each.key
  comment = each.value.tier == "DB_READ" ? (
    "USAGE on ${each.value.db} database"
    ) : (
    "USAGE + CREATE SCHEMA on ${each.value.db} — enables dev schema namespacing"
  )
}

# ── Layer 1b: Schema-level access roles (READ + READ_WRITE + READ_WRITE_CREATE × 14 schemas = 42 roles) ──

resource "snowflake_account_role" "schema_access_roles" {
  for_each = local.all_schema_role_keys

  name = each.key
  comment = (
    each.value.tier == "READ" ? "SELECT on ${each.value.db}.${each.value.schema}" :
    each.value.tier == "READ_WRITE" ? "SELECT + DML on ${each.value.db}.${each.value.schema}" :
    "SELECT + DML + DDL on ${each.value.db}.${each.value.schema}"
  )
}

# ── Layer 2: Functional roles (assembled from access roles) ──

resource "snowflake_account_role" "functional_roles" {
  for_each = {
    CSTA_DBT_DEV_ROLE   = "Full access to CSTA_MARKETING_DEV + shared observability/artefacts; used by SVC_CSTA_DBT_DEV and SVC_GITHUB_CI_DEV"
    CSTA_DBT_UAT_ROLE   = "Full access to CSTA_MARKETING_UAT + shared observability/artefacts; used by SVC_CSTA_DBT_UAT and SVC_GITHUB_CI_UAT"
    CSTA_DBT_PROD_ROLE  = "Full access to CSTA_MARKETING_PROD + shared observability/artefacts; used by SVC_CSTA_DBT_PROD and SVC_GITHUB_CI_PROD"
    CSTA_CORTEX_ROLE    = "Wraps SNOWFLAKE.CORTEX_USER database role; granted to all CSTA_DBT_*_ROLEs"
    CSTA_OBSERVER_ROLE  = "Read-only access to CSTA_MARKETING_SHARED observability + CSTA_MARKETING_PROD gold; for analysts"
    CSTA_STREAMLIT_ROLE = "Service identity for the Observability Streamlit app; inherits CSTA_OBSERVER_ROLE"
    CSTA_ANALYST_ROLE   = "Read-only access to UAT and PROD GOLD schemas only; for business analysts querying mart tables"
    CSTA_DEV_ROLE       = "Full read/write/create on all DEV database objects; assigned to named human developers"
    CSTA_UAT_DEV_ROLE   = "Full read/write/create on all UAT database objects; assigned to named human developers and QA engineers"
  }

  name    = each.key
  comment = each.value
}
