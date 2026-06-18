output "artifact_stage_url" {
  description = "Fully-qualified Snowflake URL of the dbt artifact stage"
  value       = "@${snowflake_stage.dbt_artifacts.database}.${snowflake_stage.dbt_artifacts.schema}.${snowflake_stage.dbt_artifacts.name}"
}

output "profiles_secret_names" {
  description = "Fully-qualified names of the three profiles.yml secrets"
  value = {
    for k, v in snowflake_secret_with_generic_string.profiles_yml :
      k => "${v.database}.${v.schema}.${v.name}"
  }
}
