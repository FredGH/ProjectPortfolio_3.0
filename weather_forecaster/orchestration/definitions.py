"""
Dagster Definitions — the top-level registry for the weather forecaster pipeline.

Registers:
  - Python assets: weather_extraction, bronze_load
  - dbt assets:    stg_*, silver_*, gold_*
  - Resources:     DbtCliResource
  - Schedules:     extraction_schedule (hourly :00), dbt_schedule (hourly :15)
"""
from dagster import Definitions
from dagster_dbt import DbtCliResource

from orchestration.assets import bronze_load, weather_extraction
from orchestration.dbt_assets import DBT_PROJECT_DIR, weather_dbt_assets
from orchestration.schedules import (
    dbt_schedule,
    dbt_transform_job,
    extract_and_load_job,
    extraction_schedule,
)

defs = Definitions(
    assets=[weather_extraction, bronze_load, weather_dbt_assets],
    jobs=[extract_and_load_job, dbt_transform_job],
    schedules=[extraction_schedule, dbt_schedule],
    resources={
        "dbt": DbtCliResource(project_dir=str(DBT_PROJECT_DIR)),
    },
)
