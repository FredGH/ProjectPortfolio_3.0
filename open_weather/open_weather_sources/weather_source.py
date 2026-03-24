"""
DLT Source: OpenWeather Free API (2.5)
Supports extraction from OpenWeatherMap's free API.

API Documentation: https://openweathermap.org/api

Free Endpoints:
    - Current weather: /data/2.5/weather
    - 5-day forecast: /data/2.5/forecast
    - Forward geocoding: /geo/1.0/direct
    - Reverse geocoding: /geo/1.0/reverse

Paid Endpoints (not implemented):
    - One Call API 3.0: /data/3.0/onecall
    - Air Pollution: /data/2.5/air_pollution
    - UV Index: /data/2.5/uvi

Configuration:
    Set OPENWEATHER_API_KEY environment variable or create a .env file.
    See .env.example for required variables.
"""
import dlt
from datetime import datetime, UTC
from typing import Optional, Iterator, Dict, Any


# Default base URL for OpenWeather Free API 2.5
BASE_URL = "https://api.openweathermap.org/data/2.5"
# Geocoding API base URL
GEO_URL = "https://api.openweathermap.org/geo/1.0"


def current_weather(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> dlt.source:
    """
    Extract current weather data from OpenWeather Free API.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
    
    Returns:
        A dlt source with current weather data
    """
    import requests
    
    @dlt.resource(name="current_weather")
    def _current() -> Iterator[Dict[str, Any]]:
        url = f"{BASE_URL}/weather"
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
        yield data
    
    return _current()


def weather_forecast(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> dlt.source:
    """
    Extract weather forecast from OpenWeather Free API (5-day/3-hour forecast).
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
    
    Returns:
        A dlt source with weather forecast data
    """
    import requests
    
    @dlt.resource(name="weather_forecast")
    def _forecast() -> Iterator[Dict[str, Any]]:
        url = f"{BASE_URL}/forecast"
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
        yield data
    
    return _forecast()


def weather_alerts(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> dlt.source:
    """
    Weather alerts are not available in the free API.
    This function returns an empty source with a notice.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
    
    Returns:
        A dlt source with empty data (alerts require paid API)
    """
    import requests
    
    @dlt.resource(name="weather_alerts")
    def _alerts() -> Iterator[Dict[str, Any]]:
        # Free API doesn't support weather alerts - yield empty with notice
        yield {
            "notice": "Weather alerts require One Call API 3.0 (paid subscription)",
            "_fetched_at": datetime.now(UTC).isoformat()
        }
    
    return _alerts()


def geocoding(
    api_key: str,
    city_name: str,
    limit: int = 5,
    lang: str = "en",
) -> dlt.source:
    """
    Forward geocoding - convert city name to coordinates.
    
    Args:
        api_key: OpenWeather API key
        city_name: City name (e.g., "London", "Paris, France")
        limit: Maximum number of results to return
        lang: Language code for response
    
    Returns:
        A dlt source with geocoding data
    """
    import requests
    
    @dlt.resource(name="geocoding")
    def _geocode() -> Iterator[Dict[str, Any]]:
        url = f"{GEO_URL}/direct"
        params = {
            "q": city_name,
            "appid": api_key,
            "limit": limit,
            "lang": lang,
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # Handle the response as a list
        if isinstance(data, list):
            for item in data:
                item["_fetched_at"] = datetime.now(UTC).isoformat()
                yield item
        else:
            yield {
                "error": data,
                "_fetched_at": datetime.now(UTC).isoformat()
            }
    
    return _geocode()


def reverse_geocoding(
    api_key: str,
    lat: float,
    lon: float,
    limit: int = 5,
    lang: str = "en",
) -> dlt.source:
    """
    Reverse geocoding - convert coordinates to location name.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        limit: Maximum number of results to return
        lang: Language code for response
    
    Returns:
        A dlt source with reverse geocoding data
    """
    import requests
    
    @dlt.resource(name="reverse_geocoding")
    def _reverse() -> Iterator[Dict[str, Any]]:
        url = f"{GEO_URL}/reverse"
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
        
        # Handle the response as a list
        if isinstance(data, list):
            for item in data:
                item["_fetched_at"] = datetime.now(UTC).isoformat()
                yield item
        else:
            yield {
                "error": data,
                "_fetched_at": datetime.now(UTC).isoformat()
            }
    
    return _reverse()


def openweather_source(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
    include_current: bool = True,
    include_forecast: bool = True,
    include_alerts: bool = True,
    city_name: str = None,
    include_geocoding: bool = False,
    include_reverse_geocoding: bool = False,
) -> dlt.source:
    """
    Create a comprehensive OpenWeather source using the free API.
    
    Args:
        api_key: OpenWeather API key
        lat: Latitude
        lon: Longitude
        units: Units of measurement (standard, metric, imperial)
        lang: Language code for weather descriptions
        include_current: Include current weather
        include_forecast: Include 5-day forecast
        include_alerts: Include weather alerts (not available in free API)
        city_name: City name for forward geocoding
        include_geocoding: Include forward geocoding (requires city_name)
        include_reverse_geocoding: Include reverse geocoding
    
    Returns:
        Combined dlt source with selected weather data
    """
    import requests
    
    # Create resources dynamically using closures - this works because
    # the decorated functions are defined inside this function
    resources = []
    
    if include_geocoding and city_name:
        @dlt.resource(name="geocoding")
        def _geocode():
            url = f"{GEO_URL}/direct"
            params = {
                "q": city_name,
                "appid": api_key,
                "limit": 5,
                "lang": lang,
            }
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    item["_fetched_at"] = datetime.now(UTC).isoformat()
                    yield item
            else:
                yield {"error": data, "_fetched_at": datetime.now(UTC).isoformat()}
        resources.append(_geocode)
    
    if include_reverse_geocoding:
        @dlt.resource(name="reverse_geocoding")
        def _reverse():
            url = f"{GEO_URL}/reverse"
            params = {
                "lat": lat,
                "lon": lon,
                "appid": api_key,
                "limit": 5,
                "lang": lang,
            }
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    item["_fetched_at"] = datetime.now(UTC).isoformat()
                    yield item
            else:
                yield {"error": data, "_fetched_at": datetime.now(UTC).isoformat()}
        resources.append(_reverse)
    
    if include_current:
        @dlt.resource(name="current_weather")
        def _current():
            url = f"{BASE_URL}/weather"
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
            yield data
        resources.append(_current)
    
    if include_forecast:
        @dlt.resource(name="weather_forecast")
        def _forecast():
            url = f"{BASE_URL}/forecast"
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
            yield data
        resources.append(_forecast)
    
    if include_alerts:
        @dlt.resource(name="weather_alerts")
        def _alerts():
            # Free API doesn't support weather alerts
            yield {
                "notice": "Weather alerts require One Call API 3.0 (paid)",
                "_fetched_at": datetime.now(UTC).isoformat()
            }
        resources.append(_alerts)
    
    if not resources:
        @dlt.resource(name="empty")
        def _empty():
            yield {}
        return _empty()
    
    # Use @dlt.source decorator pattern - return resources directly
    @dlt.source(name="openweather")
    def _source():
        for res in resources:
            yield res()
    
    return _source()


# Example: How to run this pipeline
#run locally:
# cd /Users/fredericmarechal/Documents/GitHub/ai-engineering-courses/ai-agentic-eng-course/projects/agents
# source .venv/bin/activate
# cd projects/ProjectPortfolio_3.0/open_weather
# PYTHONPATH=. python open_weather_sources/weather_source.py

if __name__ == "__main__":
    from open_weather_sources.config import get_api_key, validate_config
    
    # Validate configuration
    missing = validate_config()
    if missing:
        print(f"Error: Missing required configuration: {missing}")
        print("Copy .env.example to .env and add your API keys")
        exit(1)
    
    # Get API key from environment
    API_KEY = get_api_key("OPENWEATHER_API_KEY", required=True)
    LAT = 51.5074  # London latitude
    LON = -0.1278  # London longitude
    CITY_NAME = "London"
    
    # Close any existing connections and use a fresh database
    db_path = "/Users/fredericmarechal/Documents/GitHub/ai-engineering-courses/ai-agentic-eng-course/projects/agents/projects/ProjectPortfolio_3.0/open_weather/openweather_ingestion.duckdb"
    # Remove old database if exists to avoid lock issues
    import os
    if os.path.exists(db_path):
        os.remove(db_path)
    print(f"Using database: {db_path}")
    
    # Configure the pipeline with explicit database path
    pipeline = dlt.pipeline(
        pipeline_name="openweather_ingestion",
        destination=dlt.destinations.duckdb(db_path),
        dataset_name="bronze",
    )
    
    # Run all sources
    print("="*50)
    print("Running OpenWeather Sources to Bronze Layer")
    print("="*50)
    
    # 1. Current Weather
    print("\n1. Extracting current weather...")
    source = current_weather(
        api_key=API_KEY,
        lat=LAT,
        lon=LON,
        units="metric"
    )
    load_info = pipeline.run(source)
    print(f"   Current weather: {load_info}")
    
    # 2. Weather Forecast
    print("\n2. Extracting weather forecast...")
    source = weather_forecast(
        api_key=API_KEY,
        lat=LAT,
        lon=LON,
        units="metric"
    )
    load_info = pipeline.run(source)
    print(f"   Weather forecast: {load_info}")
    
    # 3. Weather Alerts (placeholder - requires paid API)
    print("\n3. Extracting weather alerts (placeholder)...")
    source = weather_alerts(
        api_key=API_KEY,
        lat=LAT,
        lon=LON
    )
    load_info = pipeline.run(source)
    print(f"   Weather alerts: {load_info}")
    
    # 4. Forward Geocoding
    print("\n4. Extracting geocoding (forward)...")
    source = geocoding(
        api_key=API_KEY,
        city_name=CITY_NAME,
        limit=5
    )
    load_info = pipeline.run(source)
    print(f"   Geocoding: {load_info}")
    
    # 5. Reverse Geocoding
    print("\n5. Extracting reverse geocoding...")
    source = reverse_geocoding(
        api_key=API_KEY,
        lat=LAT,
        lon=LON,
        limit=5
    )
    load_info = pipeline.run(source)
    print(f"   Reverse geocoding: {load_info}")
    
    print("\n" + "="*50)
    print("All sources extracted successfully!")
    print("="*50)
