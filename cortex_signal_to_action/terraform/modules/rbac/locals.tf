locals {
  # All four Snowflake databases
  all_databases = {
    "CSTA_MARKETING_DEV"    = { db = "CSTA_MARKETING_DEV" }
    "CSTA_MARKETING_UAT"    = { db = "CSTA_MARKETING_UAT" }
    "CSTA_MARKETING_PROD"   = { db = "CSTA_MARKETING_PROD" }
    "CSTA_MARKETING_SHARED" = { db = "CSTA_MARKETING_SHARED" }
  }

  # All 14 schemas across all databases — key matches access role name prefix
  all_schemas = {
    "CSTA_MARKETING_DEV_BRONZE"           = { db = "CSTA_MARKETING_DEV",    schema = "BRONZE" }
    "CSTA_MARKETING_DEV_SILVER"           = { db = "CSTA_MARKETING_DEV",    schema = "SILVER" }
    "CSTA_MARKETING_DEV_GOLD"             = { db = "CSTA_MARKETING_DEV",    schema = "GOLD" }
    "CSTA_MARKETING_DEV_ORCHESTRATION"    = { db = "CSTA_MARKETING_DEV",    schema = "ORCHESTRATION" }
    "CSTA_MARKETING_UAT_BRONZE"           = { db = "CSTA_MARKETING_UAT",    schema = "BRONZE" }
    "CSTA_MARKETING_UAT_SILVER"           = { db = "CSTA_MARKETING_UAT",    schema = "SILVER" }
    "CSTA_MARKETING_UAT_GOLD"             = { db = "CSTA_MARKETING_UAT",    schema = "GOLD" }
    "CSTA_MARKETING_UAT_ORCHESTRATION"    = { db = "CSTA_MARKETING_UAT",    schema = "ORCHESTRATION" }
    "CSTA_MARKETING_PROD_BRONZE"          = { db = "CSTA_MARKETING_PROD",   schema = "BRONZE" }
    "CSTA_MARKETING_PROD_SILVER"          = { db = "CSTA_MARKETING_PROD",   schema = "SILVER" }
    "CSTA_MARKETING_PROD_GOLD"            = { db = "CSTA_MARKETING_PROD",   schema = "GOLD" }
    "CSTA_MARKETING_PROD_ORCHESTRATION"   = { db = "CSTA_MARKETING_PROD",   schema = "ORCHESTRATION" }
    "CSTA_MARKETING_SHARED_OBSERVABILITY" = { db = "CSTA_MARKETING_SHARED", schema = "OBSERVABILITY" }
    "CSTA_MARKETING_SHARED_ARTIFACTS"     = { db = "CSTA_MARKETING_SHARED", schema = "ARTIFACTS" }
  }

  # 8 DB-level access roles (READ + MODIFY per database)
  db_access_role_keys = merge([
    for db_key, db in local.all_databases : {
      for tier in ["DB_READ", "DB_MODIFY"] :
        "${db_key}_${tier}" => { db = db.db, tier = tier }
    }
  ]...)

  # 42 schema-level access role entries (READ + READ_WRITE + READ_WRITE_CREATE × 14 schemas)
  all_schema_role_keys = merge([
    for schema_key, schema in local.all_schemas : {
      for tier in ["READ", "READ_WRITE", "READ_WRITE_CREATE"] :
        "${schema_key}_${tier}" => {
          db     = schema.db
          schema = schema.schema
          tier   = tier
        }
    }
  ]...)

  # 42 entries — all three tiers; used for USAGE + SELECT grants
  schema_all_tiers = local.all_schema_role_keys

  # 28 entries — READ_WRITE and READ_WRITE_CREATE only; used for DML grants
  schema_rw_tiers = {
    for k, v in local.all_schema_role_keys : k => v
    if v.tier != "READ"
  }

  # 14 entries — READ_WRITE_CREATE only; used for DDL grants
  schema_rwc_tiers = {
    for k, v in local.all_schema_role_keys : k => v
    if v.tier == "READ_WRITE_CREATE"
  }

  # Flat list of [functional_role, access_role] pairs for all role-to-role grants.
  # Each entry: { key, parent_role (receives the grant), child_role (the role being granted) }
  functional_role_access_grants_list = flatten([
    for parent_role, child_roles in {

      CSTA_DBT_DEV_ROLE = [
        "CSTA_MARKETING_DEV_DB_MODIFY",
        "CSTA_MARKETING_DEV_BRONZE_READ_WRITE_CREATE",
        "CSTA_MARKETING_DEV_SILVER_READ_WRITE_CREATE",
        "CSTA_MARKETING_DEV_GOLD_READ_WRITE_CREATE",
        "CSTA_MARKETING_DEV_ORCHESTRATION_READ_WRITE_CREATE",
        "CSTA_MARKETING_SHARED_DB_READ",
        "CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE",
        "CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE",
      ]

      CSTA_DBT_UAT_ROLE = [
        "CSTA_MARKETING_UAT_DB_READ",
        "CSTA_MARKETING_UAT_BRONZE_READ_WRITE_CREATE",
        "CSTA_MARKETING_UAT_SILVER_READ_WRITE_CREATE",
        "CSTA_MARKETING_UAT_GOLD_READ_WRITE_CREATE",
        "CSTA_MARKETING_UAT_ORCHESTRATION_READ_WRITE_CREATE",
        "CSTA_MARKETING_SHARED_DB_READ",
        "CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE",
        "CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE",
      ]

      CSTA_DBT_PROD_ROLE = [
        "CSTA_MARKETING_PROD_DB_READ",
        "CSTA_MARKETING_PROD_BRONZE_READ_WRITE_CREATE",
        "CSTA_MARKETING_PROD_SILVER_READ_WRITE_CREATE",
        "CSTA_MARKETING_PROD_GOLD_READ_WRITE_CREATE",
        "CSTA_MARKETING_PROD_ORCHESTRATION_READ_WRITE_CREATE",
        "CSTA_MARKETING_SHARED_DB_READ",
        "CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE",
        "CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE",
      ]

      CSTA_OBSERVER_ROLE = [
        "CSTA_MARKETING_SHARED_DB_READ",
        "CSTA_MARKETING_SHARED_OBSERVABILITY_READ",
        "CSTA_MARKETING_PROD_DB_READ",
        "CSTA_MARKETING_PROD_GOLD_READ",
      ]

      CSTA_ANALYST_ROLE = [
        "CSTA_MARKETING_UAT_DB_READ",
        "CSTA_MARKETING_UAT_GOLD_READ",
        "CSTA_MARKETING_PROD_DB_READ",
        "CSTA_MARKETING_PROD_GOLD_READ",
      ]

      CSTA_DEV_ROLE = [
        "CSTA_MARKETING_DEV_DB_MODIFY",
        "CSTA_MARKETING_DEV_BRONZE_READ_WRITE_CREATE",
        "CSTA_MARKETING_DEV_SILVER_READ_WRITE_CREATE",
        "CSTA_MARKETING_DEV_GOLD_READ_WRITE_CREATE",
        "CSTA_MARKETING_DEV_ORCHESTRATION_READ_WRITE_CREATE",
        "CSTA_MARKETING_SHARED_DB_READ",
        "CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE",
        "CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE",
      ]

      CSTA_UAT_DEV_ROLE = [
        "CSTA_MARKETING_UAT_DB_READ",
        "CSTA_MARKETING_UAT_BRONZE_READ_WRITE_CREATE",
        "CSTA_MARKETING_UAT_SILVER_READ_WRITE_CREATE",
        "CSTA_MARKETING_UAT_GOLD_READ_WRITE_CREATE",
        "CSTA_MARKETING_UAT_ORCHESTRATION_READ_WRITE_CREATE",
        "CSTA_MARKETING_SHARED_DB_READ",
        "CSTA_MARKETING_SHARED_OBSERVABILITY_READ_WRITE",
        "CSTA_MARKETING_SHARED_ARTIFACTS_READ_WRITE",
      ]

    } : [
      for child_role in child_roles : {
        key         = "${parent_role}__${child_role}"
        parent_role = parent_role
        child_role  = child_role
      }
    ]
  ])

  # Map form for for_each usage
  functional_role_access_grants = {
    for entry in local.functional_role_access_grants_list : entry.key => entry
  }

  # Warehouse usage grants: { key => { role, warehouse } }
  warehouse_usage_grants = {
    dev_role_dev_wh      = { role = "CSTA_DBT_DEV_ROLE",  warehouse = "CSTA_DBT_DEV_WH" }
    uat_role_uat_wh      = { role = "CSTA_DBT_UAT_ROLE",  warehouse = "CSTA_DBT_UAT_WH" }
    prod_role_prod_wh    = { role = "CSTA_DBT_PROD_ROLE", warehouse = "CSTA_DBT_PROD_WH" }
    observer_role_dev_wh = { role = "CSTA_OBSERVER_ROLE", warehouse = "CSTA_DBT_DEV_WH" }
    analyst_role_dev_wh  = { role = "CSTA_ANALYST_ROLE",  warehouse = "CSTA_DBT_DEV_WH" }
    dev_role_dev_wh2     = { role = "CSTA_DEV_ROLE",      warehouse = "CSTA_DBT_DEV_WH" }
    uat_dev_role_uat_wh  = { role = "CSTA_UAT_DEV_ROLE",  warehouse = "CSTA_DBT_UAT_WH" }
  }

  # Roles that receive CSTA_CORTEX_ROLE
  cortex_role_recipients = toset([
    "CSTA_DBT_DEV_ROLE",
    "CSTA_DBT_UAT_ROLE",
    "CSTA_DBT_PROD_ROLE",
    "CSTA_DEV_ROLE",
    "CSTA_UAT_DEV_ROLE",
  ])

  # Roles anchored under SYSADMIN in the hierarchy
  sysadmin_role_grants = toset([
    "CSTA_DBT_DEV_ROLE",
    "CSTA_DBT_UAT_ROLE",
    "CSTA_DBT_PROD_ROLE",
    "CSTA_CORTEX_ROLE",
    "CSTA_OBSERVER_ROLE",
    "CSTA_STREAMLIT_ROLE",
    "CSTA_ANALYST_ROLE",
    "CSTA_DEV_ROLE",
    "CSTA_UAT_DEV_ROLE",
  ])

  # Roles that receive EXECUTE TASK ON ACCOUNT
  execute_task_roles = toset([
    "CSTA_DBT_DEV_ROLE",
    "CSTA_DEV_ROLE",
  ])
}
