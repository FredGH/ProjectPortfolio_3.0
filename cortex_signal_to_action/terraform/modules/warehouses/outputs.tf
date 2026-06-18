output "warehouse_names" {
  description = "Map of environment key (dev/uat/prod) to Snowflake warehouse name"
  value = {
    for k, v in snowflake_warehouse.this : k => v.name
  }
}

output "warehouses" {
  description = "All snowflake_warehouse resources"
  value       = snowflake_warehouse.this
}
