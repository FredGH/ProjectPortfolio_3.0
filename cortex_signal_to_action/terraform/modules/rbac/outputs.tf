output "functional_role_names" {
  description = "Names of all functional roles provisioned"
  value = {
    for k, v in snowflake_account_role.functional_roles : k => v.name
  }
}

output "db_access_role_names" {
  description = "Names of all DB-level access roles"
  value = {
    for k, v in snowflake_account_role.db_access_roles : k => v.name
  }
}

output "schema_access_role_names" {
  description = "Names of all schema-level access roles"
  value = {
    for k, v in snowflake_account_role.schema_access_roles : k => v.name
  }
}
