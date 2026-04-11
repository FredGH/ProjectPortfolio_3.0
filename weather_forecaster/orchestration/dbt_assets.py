"""
Dagster dbt assets for the weather forecaster transformation layer.

Models (bronze → silver → gold):
  bronze:  stg_current_weather, stg_weather_forecast, stg_geocoding
  silver:  silver_weather_observations, silver_forecast_intervals
  gold:    gold_weather_summary
"""
from pathlib import Path

from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

PROJECT_ROOT = Path(__file__).parent.parent
DBT_PROJECT_DIR = PROJECT_ROOT / "dbt"

dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, profiles_dir=DBT_PROJECT_DIR)
# Prepare the project so the manifest is always up-to-date when running in dev.
dbt_project.prepare_if_dev()


@dbt_assets(
    manifest=dbt_project.manifest_path,
    dagster_dbt_translator=None,  # use default — one Dagster asset per dbt node
)
def weather_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    """
    Run all dbt models: bronze staging views → silver views → gold tables.

    Depends on the bronze layer (DuckDB staging schema) being populated by
    the bronze_load asset before this job runs.
    """
    yield from dbt.cli(
        ["run", "--profiles-dir", str(DBT_PROJECT_DIR), "--target", "docker"],
        context=context,
    ).stream()
