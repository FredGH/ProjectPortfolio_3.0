"""
Data Extraction Module for OpenWeather Sources

This module extracts data from OpenWeather API and saves it to parquet files
in the data_zone folder (instead of directly to the bronze layer).

The extraction process:
1. Extracts data from OpenWeather API
2. Saves each data source to a separate parquet file in data_zone
3. The bronze layer loader then loads these parquet files incrementally

Architecture:
    OpenWeather API → Extraction (to data_zone/*.parquet) → Bronze Layer (incremental load)
"""
import os
import json
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime, UTC
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import time


# Base paths - relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_ZONE_PATH = PROJECT_ROOT / "data" / "data_zone"


def ensure_data_zone_exists() -> Path:
    """Ensure the data_zone directory exists."""
    DATA_ZONE_PATH.mkdir(parents=True, exist_ok=True)
    return DATA_ZONE_PATH


def get_data_zone_path() -> Path:
    """Get the data_zone path."""
    return ensure_data_zone_exists()


def get_load_folder_path(timestamp: str = None) -> Path:
    """
    Get the folder path for a specific load (date/time based).
    
    Args:
        timestamp: Optional timestamp string. If not provided, uses current time.
                  Format: YYYYMMDD_HHMMSS
    
    Returns:
        Path to the load folder (e.g., data_zone/20260326_081551/)
    """
    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    load_folder = DATA_ZONE_PATH / timestamp
    load_folder.mkdir(parents=True, exist_ok=True)
    return load_folder


def list_load_folders() -> List[Path]:
    """
    List all load folders in data_zone (sorted by name, oldest first).
    
    Returns:
        List of folder paths
    """
    if not DATA_ZONE_PATH.exists():
        return []
    
    folders = sorted([d for d in DATA_ZONE_PATH.iterdir() if d.is_dir()])
    return folders


def get_latest_load_folder() -> Optional[Path]:
    """
    Get the most recent load folder.
    
    Returns:
        Path to latest folder, or None if no folders exist
    """
    folders = list_load_folders()
    return folders[-1] if folders else None


def flatten_dict(data: Dict[str, Any], parent_key: str = '', sep: str = '_') -> Dict[str, Any]:
    """
    Flatten nested dictionaries for parquet storage.
    
    Args:
        data: Dictionary to flatten
        parent_key: Parent key prefix
        sep: Separator for nested keys
    
    Returns:
        Flattened dictionary
    """
    items = []
    for k, v in data.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            # Convert lists to JSON string for parquet compatibility
            items.append((new_key, json.dumps(v)))
        else:
            items.append((new_key, v))
    return dict(items)


def save_to_parquet(data: Dict[str, Any], source_name: str, timestamp: Optional[str] = None, load_folder: Path = None) -> Path:
    """
    Save data to a parquet file in the data_zone load folder.
    
    Args:
        data: Dictionary containing the data to save
        source_name: Name of the data source (used in filename)
        timestamp: Optional timestamp for the filename. Defaults to current time.
        load_folder: Optional path to load folder. If not provided, creates one.
    
    Returns:
        Path to the created parquet file
    """
    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    # Flatten nested dictionaries
    flat_data = flatten_dict(data)
    
    # Create DataFrame (single row)
    df = pd.DataFrame([flat_data])
    
    # Get load folder
    if load_folder is None:
        load_folder = get_load_folder_path(timestamp)
    else:
        load_folder.mkdir(parents=True, exist_ok=True)
    
    # Create filename: {source_name}.parquet
    filename = f"{source_name}.parquet"
    filepath = load_folder / filename
    
    # Save to parquet
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(filepath))
    
    return filepath


def save_list_to_parquet(data: List[Dict[str, Any]], source_name: str, timestamp: Optional[str] = None, load_folder: Path = None) -> Path:
    """
    Save a list of records to a parquet file in the data_zone load folder.
    
    Args:
        data: List of dictionaries to save
        source_name: Name of the data source (used in filename)
        timestamp: Optional timestamp for the filename. Defaults to current time.
        load_folder: Optional path to load folder. If not provided, creates one.
    
    Returns:
        Path to the created parquet file
    """
    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    # Flatten nested dictionaries in each record
    flat_data = [flatten_dict(item) for item in data]
    
    # Create DataFrame
    df = pd.DataFrame(flat_data)
    
    # Get load folder
    if load_folder is None:
        load_folder = get_load_folder_path(timestamp)
    else:
        load_folder.mkdir(parents=True, exist_ok=True)
    
    # Create filename: {source_name}.parquet
    filename = f"{source_name}.parquet"
    filepath = load_folder / filename
    
    # Save to parquet
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(filepath))
    
    return filepath


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout))
)
def extract_current_weather(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Extract current weather data from OpenWeather API.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
        timeout: Request timeout in seconds (default: 30)
    
    Returns:
        Dictionary containing the API response
    """
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": units,
        "lang": lang,
    }
    
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    data["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout))
)
def extract_weather_forecast(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Extract weather forecast data from OpenWeather API (5-day/3-hour forecast).
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
        timeout: Request timeout in seconds (default: 30)
    
    Returns:
        Dictionary containing the API response
    """
    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": units,
        "lang": lang,
    }
    
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    data["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout))
)
def extract_geocoding(
    api_key: str,
    city_name: str,
    limit: int = 5,
    lang: str = "en",
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """
    Extract geocoding data from OpenWeather API.
    
    Args:
        api_key: OpenWeather API key
        city_name: City name (e.g., "London", "Paris, France")
        limit: Maximum number of results to return
        lang: Language code for response
        timeout: Request timeout in seconds (default: 30)
    
    Returns:
        List of dictionaries containing geocoding data
    """
    url = "https://api.openweathermap.org/geo/1.0/direct"
    params = {
        "q": city_name,
        "appid": api_key,
        "limit": limit,
        "lang": lang,
    }
    
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    
    # Add fetched_at timestamp to each item
    if isinstance(data, list):
        for item in data:
            item["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data if isinstance(data, list) else []


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout))
)
def extract_reverse_geocoding(
    api_key: str,
    lat: float,
    lon: float,
    limit: int = 5,
    lang: str = "en",
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """
    Extract reverse geocoding data from OpenWeather API.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        limit: Maximum number of results to return
        lang: Language code for response
        timeout: Request timeout in seconds (default: 30)
    
    Returns:
        List of dictionaries containing reverse geocoding data
    """
    url = "https://api.openweathermap.org/geo/1.0/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "limit": limit,
        "lang": lang,
    }
    
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    
    # Add fetched_at timestamp to each item
    if isinstance(data, list):
        for item in data:
            item["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data if isinstance(data, list) else []


def extract_all_sources(
    api_key: str,
    lat: float,
    lon: float,
    city_name: str = None,
    units: str = "metric",
    lang: str = "en",
    load_folder: Path = None,
    with_geocoding: bool = True,
) -> Tuple[Dict[str, Path], Path]:
    """
    Extract all OpenWeather data sources and save to parquet files.

    When load_folder is provided, files are written into that shared folder with
    lat/lon appended to the filename (e.g. current_weather_51.5074_-0.1278.parquet)
    so multiple locations can coexist in the same folder.

    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        city_name: City name for geocoding
        units: Units of measurement
        lang: Language code
        load_folder: Optional shared folder path. If None, creates a new timestamped folder.
        with_geocoding: If False, skip geocoding + reverse geocoding steps (saves API calls
                        when extracting many locations that already have reference data).

    Returns:
        Tuple of (dictionary mapping source names to parquet file paths, load_folder path)
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    # Use provided folder or create a new timestamped one
    if load_folder is None:
        load_folder = get_load_folder_path(timestamp)
    else:
        load_folder.mkdir(parents=True, exist_ok=True)

    # When sharing a folder across locations, embed lat/lon in the filename
    # so files don't overwrite each other.
    loc_suffix = f"_{lat:.4f}_{lon:.4f}" if load_folder is not None else ""

    print(f"Using load folder: {load_folder}")

    extracted_files = {}

    # 1. Current Weather
    print("Extracting current weather...")
    try:
        current_data = extract_current_weather(api_key, lat, lon, units, lang)
        # Promote nested coord.lat / coord.lon to top level so dbt sources can
        # reference plain `lat` and `lon` columns.
        current_data["lat"] = current_data.get("coord", {}).get("lat")
        current_data["lon"] = current_data.get("coord", {}).get("lon")
        source_name = f"current_weather{loc_suffix}"
        filepath = save_to_parquet(current_data, source_name, timestamp, load_folder)
        extracted_files["current_weather"] = filepath
        print(f"  Saved to: {filepath}")
    except Exception as e:
        print(f"  Error extracting current weather: {e}")

    # 2. Weather Forecast
    print("Extracting weather forecast...")
    try:
        forecast_data = extract_weather_forecast(api_key, lat, lon, units, lang)
        # Explode the `list` array into one row per 3-hour interval.
        # Attach lat/lon from city.coord and the shared _fetched_at to every row.
        city = forecast_data.get("city", {})
        coord = city.get("coord", {})
        fetched_at = forecast_data["_fetched_at"]
        intervals = []
        for item in forecast_data.get("list", []):
            row = dict(item)
            row["lat"] = coord.get("lat")
            row["lon"] = coord.get("lon")
            row["_fetched_at"] = fetched_at
            intervals.append(row)
        source_name = f"weather_forecast{loc_suffix}"
        filepath = save_list_to_parquet(intervals, source_name, timestamp, load_folder)
        extracted_files["weather_forecast"] = filepath
        print(f"  Saved to: {filepath}")
    except Exception as e:
        print(f"  Error extracting weather forecast: {e}")

    if with_geocoding:
        # 3. Forward Geocoding (if city_name provided)
        if city_name:
            print(f"Extracting geocoding for {city_name}...")
            try:
                geocoding_data = extract_geocoding(api_key, city_name, limit=5, lang=lang)
                if geocoding_data:
                    source_name = f"geocoding{loc_suffix}"
                    filepath = save_list_to_parquet(geocoding_data, source_name, timestamp, load_folder)
                    extracted_files["geocoding"] = filepath
                    print(f"  Saved to: {filepath}")
                else:
                    print("  No geocoding data returned")
            except Exception as e:
                print(f"  Error extracting geocoding: {e}")

        # 4. Reverse Geocoding
        print("Extracting reverse geocoding...")
        try:
            reverse_data = extract_reverse_geocoding(api_key, lat, lon, limit=5, lang=lang)
            if reverse_data:
                source_name = f"reverse_geocoding{loc_suffix}"
                filepath = save_list_to_parquet(reverse_data, source_name, timestamp, load_folder)
                extracted_files["reverse_geocoding"] = filepath
                print(f"  Saved to: {filepath}")
            else:
                print("  No reverse geocoding data returned")
        except Exception as e:
            print(f"  Error extracting reverse geocoding: {e}")

    return extracted_files, load_folder


def list_data_zone_files() -> List[Path]:
    """
    List all parquet files in the data_zone folder.
    
    Returns:
        List of parquet file paths
    """
    data_zone = get_data_zone_path()
    return sorted(data_zone.glob("*.parquet"))


def get_latest_files_by_source() -> Dict[str, Path]:
    """
    Get the latest parquet file for each data source.
    
    Returns:
        Dictionary mapping source names to their latest parquet file paths
    """
    files = list_data_zone_files()
    latest_files = {}
    
    for filepath in files:
        # Extract source name from filename (e.g., "current_weather_20240315_123456.parquet")
        filename = filepath.stem  # Remove extension
        parts = filename.rsplit('_', 1)  # Split from the right to separate timestamp
        if len(parts) >= 2:
            source_name = parts[0]
            # If there are multiple underscores in source name, we need to handle that
            # For now, assume source names don't have underscores
            # A better approach would be to parse more carefully
            
            # Check if this is the latest for this source
            if source_name not in latest_files or filepath.stat().st_mtime > latest_files[source_name].stat().st_mtime:
                latest_files[source_name] = filepath
    
    return latest_files


if __name__ == "__main__":
    from weather_forecaster_sources.config import get_api_key, validate_config
    
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
    
    print("="*50)
    print("Extracting OpenWeather Data to Parquet Files")
    print("="*50)
    print(f"Data Zone: {get_data_zone_path()}")
    print()
    
    # Extract all sources
    extracted = extract_all_sources(
        api_key=API_KEY,
        lat=LAT,
        lon=LON,
        city_name=CITY_NAME,
        units="metric"
    )
    
    print()
    print("="*50)
    print(f"Extraction complete! {len(extracted)} files created.")
    print("="*50)
    
    # List all files in data_zone
    print("\nAll files in data_zone:")
    for f in list_data_zone_files():
        print(f"  {f.name}")
