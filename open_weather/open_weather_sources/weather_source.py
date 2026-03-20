i"""
DLT Source: OpenWeather Free API (2.5)
Supports extraction from OpenWeatherMap's free API.

API Documentation: https://openweathermap.org/api

Free Endpoints:
    - Current weather: /data/2.5/weather
    - 5-day forecast: /data/2.5/forecast

Note: The paid One Call API 3.0 (/data/3.0/onecall) requires a subscription.

Configuration:
    Set OPENWEATHER_API_KEY environment variable or create a .env file.
    See .env.example for required variables.
"""
import dlt
from datetime import datetime, UTC
from typing import Optional, Iterator, Dict, Any


# Default base URL for OpenWeather Free API 2.5
BASE_URL = "https://api.openweathermap.org/data/2.5"


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


def openweather_source(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
    include_current: bool = True,
    include_forecast: bool = True,
    include_alerts: bool = True,
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
    
    Returns:
        Combined dlt source with selected weather data
    """
    import requests
    
    # Create resources dynamically using closures - this works because
    # the decorated functions are defined inside this function
    resources = []
    
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
    
    # Configure the pipeline
    pipeline = dlt.pipeline(
        pipeline_name="openweather_ingestion",
        destination="duckdb",
        dataset_name="bronze",
    )
    
    # Run the source - current weather only
    source = current_weather(
        api_key=API_KEY,
        lat=LAT,
        lon=LON,
        units="metric"
    )
    
    load_info = pipeline.run(source)
    print(f"Load info: {load_info}")
