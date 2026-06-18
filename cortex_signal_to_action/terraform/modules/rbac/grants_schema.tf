# ────────────────────────────────────────────────────────────────────────────
# Schema-level grants to access roles.
#
# Each of 14 schemas gets three access roles (READ / READ_WRITE / READ_WRITE_CREATE).
# for_each on the three local tier maps keeps this DRY:
#   schema_all_tiers  (42 entries) — all tiers receive USAGE + SELECT
#   schema_rw_tiers   (28 entries) — READ_WRITE + READ_WRITE_CREATE receive DML
#   schema_rwc_tiers  (14 entries) — READ_WRITE_CREATE receives DDL
# ────────────────────────────────────────────────────────────────────────────

# USAGE on schema — all three tiers
resource "snowflake_grant_privileges_to_account_role" "schema_usage" {
  for_each          = local.schema_all_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["USAGE"]

  on_schema {
    schema_name = "${each.value.db}.${each.value.schema}"
  }
}

# SELECT on ALL existing TABLES — all three tiers
resource "snowflake_grant_privileges_to_account_role" "schema_select_all_tables" {
  for_each          = local.schema_all_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["SELECT"]

  on_schema_object {
    all {
      object_type_plural = "TABLES"
      in_schema          = "${each.value.db}.${each.value.schema}"
    }
  }
}

# SELECT on FUTURE TABLES — all three tiers
resource "snowflake_grant_privileges_to_account_role" "schema_select_future_tables" {
  for_each          = local.schema_all_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["SELECT"]

  on_schema_object {
    future {
      object_type_plural = "TABLES"
      in_schema          = "${each.value.db}.${each.value.schema}"
    }
  }
}

# SELECT on ALL existing VIEWS — all three tiers
resource "snowflake_grant_privileges_to_account_role" "schema_select_all_views" {
  for_each          = local.schema_all_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["SELECT"]

  on_schema_object {
    all {
      object_type_plural = "VIEWS"
      in_schema          = "${each.value.db}.${each.value.schema}"
    }
  }
}

# SELECT on FUTURE VIEWS — all three tiers
resource "snowflake_grant_privileges_to_account_role" "schema_select_future_views" {
  for_each          = local.schema_all_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["SELECT"]

  on_schema_object {
    future {
      object_type_plural = "VIEWS"
      in_schema          = "${each.value.db}.${each.value.schema}"
    }
  }
}

# DML on ALL existing TABLES — READ_WRITE and READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_dml_all_tables" {
  for_each          = local.schema_rw_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["INSERT", "UPDATE", "DELETE"]

  on_schema_object {
    all {
      object_type_plural = "TABLES"
      in_schema          = "${each.value.db}.${each.value.schema}"
    }
  }
}

# DML on FUTURE TABLES — READ_WRITE and READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_dml_future_tables" {
  for_each          = local.schema_rw_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["INSERT", "UPDATE", "DELETE"]

  on_schema_object {
    future {
      object_type_plural = "TABLES"
      in_schema          = "${each.value.db}.${each.value.schema}"
    }
  }
}

# CREATE TABLE — READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_create_table" {
  for_each          = local.schema_rwc_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["CREATE TABLE"]

  on_schema {
    schema_name = "${each.value.db}.${each.value.schema}"
  }
}

# CREATE VIEW — READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_create_view" {
  for_each          = local.schema_rwc_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["CREATE VIEW"]

  on_schema {
    schema_name = "${each.value.db}.${each.value.schema}"
  }
}

# CREATE STAGE — READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_create_stage" {
  for_each          = local.schema_rwc_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["CREATE STAGE"]

  on_schema {
    schema_name = "${each.value.db}.${each.value.schema}"
  }
}

# CREATE PROCEDURE — READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_create_procedure" {
  for_each          = local.schema_rwc_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["CREATE PROCEDURE"]

  on_schema {
    schema_name = "${each.value.db}.${each.value.schema}"
  }
}

# CREATE TASK — READ_WRITE_CREATE only
resource "snowflake_grant_privileges_to_account_role" "schema_create_task" {
  for_each          = local.schema_rwc_tiers
  account_role_name = snowflake_account_role.schema_access_roles[each.key].name
  privileges        = ["CREATE TASK"]

  on_schema {
    schema_name = "${each.value.db}.${each.value.schema}"
  }
}
