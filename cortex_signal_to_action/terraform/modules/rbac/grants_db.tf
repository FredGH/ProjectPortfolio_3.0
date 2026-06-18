# ── DB-level privilege grants to DB_READ roles ──
# Mirrors: GRANT USAGE ON DATABASE <db> TO ROLE <db>_DB_READ

resource "snowflake_grant_privileges_to_account_role" "db_read_usage" {
  for_each          = local.all_databases
  account_role_name = snowflake_account_role.db_access_roles["${each.key}_DB_READ"].name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = each.value.db
  }
}

# ── DB-level privilege grants to DB_MODIFY roles ──
# Mirrors: GRANT USAGE ON DATABASE <db> TO ROLE <db>_DB_MODIFY
#          GRANT CREATE SCHEMA ON DATABASE <db> TO ROLE <db>_DB_MODIFY

resource "snowflake_grant_privileges_to_account_role" "db_modify_usage" {
  for_each          = local.all_databases
  account_role_name = snowflake_account_role.db_access_roles["${each.key}_DB_MODIFY"].name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "DATABASE"
    object_name = each.value.db
  }
}

resource "snowflake_grant_privileges_to_account_role" "db_modify_create_schema" {
  for_each          = local.all_databases
  account_role_name = snowflake_account_role.db_access_roles["${each.key}_DB_MODIFY"].name
  privileges        = ["CREATE SCHEMA"]

  on_account_object {
    object_type = "DATABASE"
    object_name = each.value.db
  }
}
