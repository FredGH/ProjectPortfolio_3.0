"""
Pipeline Runner Module

This module provides a unified pipeline that:
1. Extracts data from OpenWeather API to parquet files in data_zone
2. Loads data from data_zone to bronze layer incrementally

Usage:
    from open_weather_sources.pipeline_runner import run_pipeline
    
    # Run full pipeline
    results = run_pipeline(
        api_key="your_api_key",
        lat=51.5074,
        lon=-0.1278,
        city_name="London"
    )
"""
from typing import Dict, Any, Optional
from pathlib import Path

from open_weather_sources.extraction import (
    extract_all_sources,
    list_data_zone_files,
    get_data_zone_path,
)
from open_weather_sources.bronze_loader import (
    load_all_to_bronze,
    get_bronze_table_stats,
    get_duckdb_path,
)
from open_weather_sources.config import get_api_key, validate_config


def run_pipeline(
    api_key: str,
    lat: float,
    lon: float,
    city_name: str = None,
    units: str = "metric",
    lang: str = "en",
    skip_extraction: bool = False,
    skip_bronze_load: bool = False,
) -> Dict[str, Any]:
    """
    Run the complete OpenWeather ETL pipeline.
    
    Steps:
    1. Extract data from OpenWeather API to parquet files (data_zone)
    2. Load parquet files to bronze layer (DuckDB) incrementally
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        city_name: City name for geocoding
        units: Units of measurement
        lang: Language code
        skip_extraction: Skip the extraction step
        skip_bronze_load: Skip the bronze layer load step
    
    Returns:
        Dictionary with pipeline results
    """
    results = {
        "extraction": None,
        "bronze_load": None,
        "bronze_stats": None,
    }
    
    # Step 1: Extraction
    if not skip_extraction:
        print("\n" + "="*50)
        print("STEP 1: Extraction (API → Parquet in data_zone)")
        print("="*50)
        
        extracted_files = extract_all_sources(
            api_key=api_key,
            lat=lat,
            lon=lon,
            city_name=city_name,
            units=units,
            lang=lang,
        )
        
        results["extraction"] = {
            "status": "success",
            "files_created": len(extracted_files),
            "files": [str(f) for f in extracted_files.values()],
        }
    
    # Step 2: Bronze Layer Load
    if not skip_bronze_load:
        print("\n" + "="*50)
        print("STEP 2: Bronze Load (Parquet → DuckDB)")
        print("="*50)
        
        load_results = load_all_to_bronze()
        
        # Get bronze statistics
        bronze_stats = get_bronze_table_stats()
        
        results["bronze_load"] = load_results
        results["bronze_stats"] = bronze_stats
    
    return results


def run_extraction_only(
    api_key: str,
    lat: float,
    lon: float,
    city_name: str = None,
    units: str = "metric",
    lang: str = "en",
) -> Dict[str, Any]:
    """
    Run only the extraction step (API → Parquet in data_zone).
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        city_name: City name for geocoding
        units: Units of measurement
        lang: Language code
    
    Returns:
        Dictionary with extraction results
    """
    return run_pipeline(
        api_key=api_key,
        lat=lat,
        lon=lon,
        city_name=city_name,
        units=units,
        lang=lang,
        skip_bronze_load=True,
    )


def run_bronze_load_only() -> Dict[str, Any]:
    """
    Run only the bronze layer load step (Parquet → DuckDB).
    
    Returns:
        Dictionary with load results
    """
    return run_pipeline(
        api_key="",  # Not needed for bronze load
        lat=0,
        lon=0,
        skip_extraction=True,
    )


def print_pipeline_summary(results: Dict[str, Any]) -> None:
    """Print a summary of the pipeline results."""
    print("\n" + "="*50)
    print("PIPELINE SUMMARY")
    print("="*50)
    
    if results.get("extraction"):
        ext = results["extraction"]
        print(f"\nExtraction:")
        print(f"  Status: {ext['status']}")
        print(f"  Files created: {ext['files_created']}")
    
    if results.get("bronze_stats"):
        print(f"\nBronze Layer:")
        for table, stats in results["bronze_stats"].items():
            print(f"  {table}: {stats['row_count']} rows")


if __name__ == "__main__":
    # Validate configuration
    missing = validate_config()
    if missing:
        print(f"Error: Missing required configuration: {missing}")
        print("Copy .env.example to .env and add your API keys")
        exit(1)
    
    # Get API key
    API_KEY = get_api_key("OPENWEATHER_API_KEY", required=True)
    LAT = 51.5074  # London
    LON = -0.1278  # London
    CITY_NAME = "London"
    
    print("="*60)
    print("OpenWeather ETL Pipeline")
    print("="*60)
    print(f"Coordinates: {LAT}, {LON}")
    print(f"City: {CITY_NAME}")
    print(f"Data Zone: {get_data_zone_path()}")
    print(f"Bronze DB: {get_duckdb_path()}")
    
    # Run pipeline
    results = run_pipeline(
        api_key=API_KEY,
        lat=LAT,
        lon=LON,
        city_name=CITY_NAME,
        units="metric",
    )
    
    # Print summary
    print_pipeline_summary(results)
