"""
Dagster assets for the weather forecaster pipeline.

Stage 0: Load world capitals reference data → staging.world_capitals
Stage 1: Extract from OpenWeather API for all world capitals → parquet files in data_zone
Stage 2: Load parquet files → DuckDB bronze layer (staging schema)
"""
import json
import time
from pathlib import Path
from datetime import datetime, UTC

from dagster import AssetExecutionContext, asset

from weather_forecaster_sources.bronze_loader import (
    LoadMode,
    load_all_to_bronze,
    load_capitals_to_staging,
    get_duckdb_path,
)
from weather_forecaster_sources.config import get_api_key
from weather_forecaster_sources.extraction import extract_all_sources, get_load_folder_path

PROJECT_ROOT = Path(__file__).parent.parent
CAPITALS_JSON = PROJECT_ROOT / "data" / "world_capitals.json"

# Delay between locations to respect the OpenWeather free tier (60 req/min).
# Each location makes 2 API calls (current + forecast), so 0.5s/location keeps
# us well under the rate limit.
_INTER_LOCATION_DELAY_S = 0.5


def _load_capitals() -> list[dict]:
    with open(CAPITALS_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


@asset(group_name="bronze", compute_kind="duckdb")
def capitals_load(context: AssetExecutionContext) -> dict:
    """
    Load world capitals reference data from data/world_capitals.json into
    staging.world_capitals. Re-runs replace all rows so the table stays in
    sync with the JSON file. Run this asset once on first deploy, or whenever
    the capitals list changes.
    """
    context.log.info(f"Loading capitals from {CAPITALS_JSON}")
    result = load_capitals_to_staging(json_path=CAPITALS_JSON)
    context.log.info(f"Capitals loaded: {result}")
    return result


@asset(group_name="extraction", compute_kind="python")
def weather_extraction(context: AssetExecutionContext) -> dict:
    """
    Extract current weather + 5-day forecast for every world capital.

    All locations share a single timestamped load folder. Filenames are unique
    per location (lat/lon suffix), so the bronze loader can distinguish them.
    Geocoding is skipped — the capitals reference table already provides names.

    Rate limiting: {_INTER_LOCATION_DELAY_S}s pause between locations to stay
    within the OpenWeather free tier (60 req/min, 2 calls/location).
    """
    api_key = get_api_key("OPENWEATHER_API_KEY", required=True)
    capitals = _load_capitals()

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    shared_folder = get_load_folder_path(timestamp)
    shared_folder.mkdir(parents=True, exist_ok=True)

    context.log.info(
        f"Extracting weather for {len(capitals)} capitals → {shared_folder}"
    )

    total_files = 0
    errors: list[str] = []

    for i, capital in enumerate(capitals):
        city = capital["city"]
        lat = capital["lat"]
        lon = capital["lon"]

        try:
            extracted, _ = extract_all_sources(
                api_key=api_key,
                lat=lat,
                lon=lon,
                city_name=city,
                units="metric",
                lang="en",
                load_folder=shared_folder,
                with_geocoding=False,
            )
            total_files += len(extracted)
            context.log.info(
                f"[{i+1}/{len(capitals)}] {city}: {len(extracted)} files"
            )
        except Exception as exc:
            errors.append(f"{city}: {exc}")
            context.log.warning(f"[{i+1}/{len(capitals)}] {city} failed: {exc}")

        if i < len(capitals) - 1:
            time.sleep(_INTER_LOCATION_DELAY_S)

    context.log.info(
        f"Extraction complete: {total_files} files, {len(errors)} errors"
    )
    if errors:
        for err in errors:
            context.log.warning(f"  {err}")

    return {
        "load_folder": str(shared_folder),
        "capitals_processed": len(capitals),
        "files_created": total_files,
        "errors": len(errors),
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
