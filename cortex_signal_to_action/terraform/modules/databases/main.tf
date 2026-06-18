locals {
  databases = {
    dev = {
      name                        = "CSTA_MARKETING_DEV"
      data_retention_time_in_days = 1
      comment                     = "Development environment — feature branches and dev branch"
      schemas = {
        BRONZE        = "Raw typed tables sourced from Olist CSVs and synthetic seed data"
        SILVER        = "Enriched and joined tables including Cortex NLP outputs"
        GOLD          = "Consumption-ready mart tables — segments, MMM attribution, NBA actions"
        ORCHESTRATION = "Snowflake Task DAG definitions and dbt stored procedure"
      }
    }
    uat = {
      name                        = "CSTA_MARKETING_UAT"
      data_retention_time_in_days = 7
      comment                     = "UAT environment — uat branch; staging for prod promotion"
      schemas = {
        BRONZE        = "Raw typed tables sourced from Olist CSVs and synthetic seed data"
        SILVER        = "Enriched and joined tables including Cortex NLP outputs"
        GOLD          = "Consumption-ready mart tables — segments, MMM attribution, NBA actions"
        ORCHESTRATION = "Snowflake Task DAG definitions and dbt stored procedure"
      }
    }
    prod = {
      name                        = "CSTA_MARKETING_PROD"
      data_retention_time_in_days = 14
      comment                     = "Production environment — main branch; source of truth"
      schemas = {
        BRONZE        = "Raw typed tables sourced from Olist CSVs and synthetic seed data"
        SILVER        = "Enriched and joined tables including Cortex NLP outputs"
        GOLD          = "Consumption-ready mart tables — segments, MMM attribution, NBA actions"
        ORCHESTRATION = "Snowflake Task DAG definitions and dbt stored procedure"
      }
    }
    shared = {
      name                        = "CSTA_MARKETING_SHARED"
      data_retention_time_in_days = 14
      comment                     = "Shared artefact stage and observability schema — accessible to all envs"
      schemas = {
        OBSERVABILITY = "Pipeline run log, model health, Cortex usage, data quality, and cost tables"
        ARTIFACTS     = "Internal stage for dbt artefacts (manifest.json, run_results.json) shared across envs"
      }
    }
  }

  # Flattened map of all db/schema pairs keyed by "<db_key>_<schema>" for for_each.
  db_schema_pairs = merge([
    for db_key, db_cfg in local.databases : {
      for schema_name, schema_comment in db_cfg.schemas :
        "${db_key}_${schema_name}" => {
          database_key   = db_key
          database_name  = db_cfg.name
          schema_name    = schema_name
          schema_comment = schema_comment
        }
    }
  ]...)
}

resource "snowflake_database" "this" {
  for_each = local.databases

  name                        = each.value.name
  data_retention_time_in_days = each.value.data_retention_time_in_days
  comment                     = each.value.comment
}

resource "snowflake_schema" "this" {
  for_each = local.db_schema_pairs

  database = snowflake_database.this[each.value.database_key].name
  name     = each.value.schema_name
  comment  = each.value.schema_comment
}
