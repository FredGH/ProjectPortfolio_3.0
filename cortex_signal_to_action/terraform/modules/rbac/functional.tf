# ── Grant access roles to functional roles ──
# Mirrors: GRANT ROLE <access_role> TO ROLE <functional_role>

resource "snowflake_grant_account_role" "functional_access_role_grants" {
  for_each = local.functional_role_access_grants

  role_name = each.value.child_role

  # child_role is an access role → look up in the combined pool
  # (db_access_roles or schema_access_roles depending on the suffix)
  parent_role_name = snowflake_account_role.functional_roles[each.value.parent_role].name

  depends_on = [
    snowflake_account_role.db_access_roles,
    snowflake_account_role.schema_access_roles,
    snowflake_account_role.functional_roles,
  ]
}

# ── CSTA_CORTEX_ROLE wraps the SNOWFLAKE.CORTEX_USER database role ──
# Mirrors: GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE CSTA_CORTEX_ROLE

resource "snowflake_grant_database_role" "cortex_user" {
  database_role_name = "SNOWFLAKE.CORTEX_USER"
  parent_role_name   = snowflake_account_role.functional_roles["CSTA_CORTEX_ROLE"].name
}

# ── Grant CSTA_CORTEX_ROLE to each dbt functional role ──
# Mirrors: GRANT ROLE CSTA_CORTEX_ROLE TO ROLE CSTA_DBT_*_ROLE

resource "snowflake_grant_account_role" "cortex_role_grants" {
  for_each = local.cortex_role_recipients

  role_name        = snowflake_account_role.functional_roles["CSTA_CORTEX_ROLE"].name
  parent_role_name = snowflake_account_role.functional_roles[each.value].name
}

# ── Grant CSTA_OBSERVER_ROLE to CSTA_STREAMLIT_ROLE ──
# Mirrors: GRANT ROLE CSTA_OBSERVER_ROLE TO ROLE CSTA_STREAMLIT_ROLE

resource "snowflake_grant_account_role" "streamlit_inherits_observer" {
  role_name        = snowflake_account_role.functional_roles["CSTA_OBSERVER_ROLE"].name
  parent_role_name = snowflake_account_role.functional_roles["CSTA_STREAMLIT_ROLE"].name
}

# ── Warehouse USAGE grants ──
# Mirrors: GRANT USAGE ON WAREHOUSE <wh> TO ROLE <role>

resource "snowflake_grant_privileges_to_account_role" "warehouse_usage" {
  for_each          = local.warehouse_usage_grants
  account_role_name = snowflake_account_role.functional_roles[each.value.role].name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = each.value.warehouse
  }
}

# ── EXECUTE TASK ON ACCOUNT ──
# Mirrors: GRANT EXECUTE TASK ON ACCOUNT TO ROLE CSTA_DBT_DEV_ROLE / CSTA_DEV_ROLE

resource "snowflake_grant_privileges_to_account_role" "execute_task" {
  for_each          = local.execute_task_roles
  account_role_name = snowflake_account_role.functional_roles[each.value].name
  privileges        = ["EXECUTE TASK"]
  on_account        = true
}

# ── Anchor all functional roles under SYSADMIN ──
# Mirrors: GRANT ROLE <functional_role> TO ROLE SYSADMIN

resource "snowflake_grant_account_role" "anchor_under_sysadmin" {
  for_each = local.sysadmin_role_grants

  role_name        = snowflake_account_role.functional_roles[each.value].name
  parent_role_name = "SYSADMIN"
}
