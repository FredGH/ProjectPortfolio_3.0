"""
Dagster schedules for the weather forecaster pipeline.

Two schedules:
  1. extraction_schedule  — hourly at :00, runs extraction + bronze load
  2. dbt_schedule         — hourly at :15, runs dbt models after bronze is ready
"""
from dagster import AssetSelection, ScheduleDefinition, define_asset_job


# ── Job 1: Extract from API and load into bronze ────────────────────────────
extract_and_load_job = define_asset_job(
    name="extract_and_load_job",
    selection=AssetSelection.groups("extraction", "bronze"),
    description="Extract weather data from OpenWeather API and load into DuckDB bronze layer.",
)

# Every hour at minute 0
extraction_schedule = ScheduleDefinition(
    job=extract_and_load_job,
    cron_schedule="0 * * * *",
    name="extraction_schedule",
    description="Run extraction + bronze load every hour at :00.",
)


# ── Job 2: dbt transformations (bronze → silver → gold) ─────────────────────
# dagster-dbt registers dbt model assets under the "default" group by default.
dbt_transform_job = define_asset_job(
    name="dbt_transform_job",
    selection=AssetSelection.groups("default"),
    description="Run all dbt models to transform bronze → silver → gold.",
)

# Every hour at minute 15 — gives extraction 15 min to complete
dbt_schedule = ScheduleDefinition(
    job=dbt_transform_job,
    cron_schedule="15 * * * *",
    name="dbt_schedule",
    description="Run dbt transformations every hour at :15 (after extraction).",
)
