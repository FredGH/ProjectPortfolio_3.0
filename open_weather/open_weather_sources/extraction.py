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
from typing import Dict, Any, List, Optional
from pathlib import Path
import requests


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


def save_to_parquet(data: Dict[str, Any], source_name: str, timestamp: Optional[str] = None) -> Path:
    """
    Save data to a parquet file in the data_zone folder.
    
    Args:
        data: Dictionary containing the data to save
        source_name: Name of the data source (used in filename)
        timestamp: Optional timestamp for the filename. Defaults to current time.
    
    Returns:
        Path to the created parquet file
    """
    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    # Flatten nested dictionaries
    flat_data = flatten_dict(data)
    
    # Create DataFrame (single row)
    df = pd.DataFrame([flat_data])
    
    # Ensure data_zone exists
    data_zone = get_data_zone_path()
    
    # Create filename: {source_name}_{timestamp}.parquet
    filename = f"{source_name}_{timestamp}.parquet"
    filepath = data_zone / filename
    
    # Save to parquet
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(filepath))
    
    return filepath


def save_list_to_parquet(data: List[Dict[str, Any]], source_name: str, timestamp: Optional[str] = None) -> Path:
    """
    Save a list of records to a parquet file in the data_zone folder.
    
    Args:
        data: List of dictionaries to save
        source_name: Name of the data source (used in filename)
        timestamp: Optional timestamp for the filename. Defaults to current time.
    
    Returns:
        Path to the created parquet file
    """
    if timestamp is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    # Flatten nested dictionaries in each record
    flat_data = [flatten_dict(item) for item in data]
    
    # Create DataFrame
    df = pd.DataFrame(flat_data)
    
    # Ensure data_zone exists
    data_zone = get_data_zone_path()
    
    # Create filename: {source_name}_{timestamp}.parquet
    filename = f"{source_name}_{timestamp}.parquet"
    filepath = data_zone / filename
    
    # Save to parquet
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(filepath))
    
    return filepath


def extract_current_weather(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> Dict[str, Any]:
    """
    Extract current weather data from OpenWeather API.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
    
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
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    data["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data


def extract_weather_forecast(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> Dict[str, Any]:
    """
    Extract weather forecast data from OpenWeather API (5-day/3-hour forecast).
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
    
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
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    data["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data


def extract_geocoding(
    api_key: str,
    city_name: str,
    limit: int = 5,
    lang: str = "en",
) -> List[Dict[str, Any]]:
    """
    Extract geocoding data from OpenWeather API.
    
    Args:
        api_key: OpenWeather API key
        city_name: City name (e.g., "London", "Paris, France")
        limit: Maximum number of results to return
        lang: Language code for response
    
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
    
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    
    # Add fetched_at timestamp to each item
    if isinstance(data, list):
        for item in data:
            item["_fetched_at"] = datetime.now(UTC).isoformat()
    
    return data if isinstance(data, list) else []


def extract_reverse_geocoding(
    api_key: str,
    lat: float,
    lon: float,
    limit: int = 5,
    lang: str = "en",
) -> List[Dict[str, Any]]:
    """
    Extract reverse geocoding data from OpenWeather API.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        limit: Maximum number of results to return
        lang: Language code for response
    
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
    
    response = requests.get(url, params=params)
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
) -> Dict[str, Path]:
    """
    Extract all OpenWeather data sources and save to parquet files.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        city_name: City name for geocoding
        units: Units of measurement
        lang: Language code
    
    Returns:
        Dictionary mapping source names to parquet file paths
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    extracted_files = {}
    
    # 1. Current Weather
    print("Extracting current weather...")
    try:
        current_data = extract_current_weather(api_key, lat, lon, units, lang)
        filepath = save_to_parquet(current_data, "current_weather", timestamp)
        extracted_files["current_weather"] = filepath
        print(f"  Saved to: {filepath}")
    except Exception as e:
        print(f"  Error extracting current weather: {e}")
    
    # 2. Weather Forecast
    print("Extracting weather forecast...")
    try:
        forecast_data = extract_weather_forecast(api_key, lat, lon, units, lang)
        filepath = save_to_parquet(forecast_data, "weather_forecast", timestamp)
        extracted_files["weather_forecast"] = filepath
        print(f"  Saved to: {filepath}")
    except Exception as e:
        print(f"  Error extracting weather forecast: {e}")
    
    # 3. Forward Geocoding (if city_name provided)
    if city_name:
        print(f"Extracting geocoding for {city_name}...")
        try:
            geocoding_data = extract_geocoding(api_key, city_name, limit=5, lang=lang)
            if geocoding_data:
                filepath = save_list_to_parquet(geocoding_data, "geocoding", timestamp)
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
            filepath = save_list_to_parquet(reverse_data, "reverse_geocoding", timestamp)
            extracted_files["reverse_geocoding"] = filepath
            print(f"  Saved to: {filepath}")
        else:
            print("  No reverse geocoding data returned")
    except Exception as e:
        print(f"  Error extracting reverse geocoding: {e}")
    
    return extracted_files


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
    from open_weather_sources.config import get_api_key, validate_config
    
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
