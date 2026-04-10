"""
Dagster assets for the weather forecaster pipeline.

Stage 1: Extract from OpenWeather API → parquet files in data_zone
Stage 2: Load parquet files → DuckDB bronze layer (staging schema)
"""
from pathlib import Path

from dagster import AssetExecutionContext, asset

from weather_forecaster_sources.bronze_loader import LoadMode, load_all_to_bronze
from weather_forecaster_sources.config import get_api_key
from weather_forecaster_sources.extraction import extract_all_sources

PROJECT_ROOT = Path(__file__).parent.parent

# Default location: London
_DEFAULT_LAT = 51.5074
_DEFAULT_LON = -0.1278
_DEFAULT_CITY = "London"


@asset(group_name="extraction", compute_kind="python")
def weather_extraction(context: AssetExecutionContext) -> dict:
    """
    Extract current weather, forecast, and geocoding data from OpenWeather API.

    Saves each source as a parquet file inside a timestamped folder under
    data/data_zone/<YYYYMMDD_HHMMSS>/.
    """
    api_key = get_api_key("OPENWEATHER_API_KEY", required=True)

    context.log.info(
        f"Extracting weather data for {_DEFAULT_CITY} "
        f"({_DEFAULT_LAT}, {_DEFAULT_LON})"
    )

    extracted_files, load_folder = extract_all_sources(
        api_key=api_key,
        lat=_DEFAULT_LAT,
        lon=_DEFAULT_LON,
        city_name=_DEFAULT_CITY,
        units="metric",
        lang="en",
    )

    context.log.info(
        f"Extraction complete: {len(extracted_files)} files in {load_folder}"
    )
    for source, path in extracted_files.items():
        context.log.info(f"  {source}: {path}")

    return {
        "load_folder": str(load_folder),
        "files_created": len(extracted_files),
        "sources": list(extracted_files.keys()),
    }


@asset(
    deps=[weather_extraction],
    group_name="bronze",
    compute_kind="duckdb",
)
def bronze_load(context: AssetExecutionContext) -> dict:
    """
    Incrementally load the latest parquet files from data_zone into the DuckDB
    bronze layer (staging schema) using composite-key deduplication.
    """
    context.log.info("Loading latest data_zone folder into DuckDB bronze layer...")

    results = load_all_to_bronze(load_mode=LoadMode.INCREMENTAL)

    for table, result in results.items():
        if isinstance(result, dict):
            context.log.info(
                f"  {table}: status={result.get('status')}, "
                f"rows={result.get('rows', 0)}"
            )

    return results
