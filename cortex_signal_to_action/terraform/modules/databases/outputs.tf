output "database_names" {
  description = "Map of environment key (dev/uat/prod/shared) to Snowflake database name"
  value = {
    for k, v in snowflake_database.this : k => v.name
  }
}

output "databases" {
  description = "All snowflake_database resources"
  value       = snowflake_database.this
}

output "schemas" {
  description = "All snowflake_schema resources"
  value       = snowflake_schema.this
}
