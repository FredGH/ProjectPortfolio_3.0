output "database_names" {
  description = "Map of environment key to Snowflake database name"
  value       = module.databases.database_names
}

output "warehouse_names" {
  description = "Map of environment key to Snowflake warehouse name"
  value       = module.warehouses.warehouse_names
}

output "artifact_stage_url" {
  description = "Fully-qualified URL of the dbt artifact stage"
  value       = module.stages.artifact_stage_url
}

output "functional_role_names" {
  description = "Names of all functional roles provisioned"
  value       = module.rbac.functional_role_names
}
