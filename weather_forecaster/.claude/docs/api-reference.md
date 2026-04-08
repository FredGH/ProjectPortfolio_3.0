# API Reference

This document describes the OpenWeather source API for the weather_forecaster pipeline.

> **Note:** The free API 2.5 endpoints are used. The paid One Call API 3.0 requires a subscription and is not used here.

## Table of Contents
- [OpenWeather Endpoints](#openweather-endpoints)
- [Function Signatures](#function-signatures)
- [Example Usage](#example-usage)
- [Running Pipelines](#running-pipelines)

---

## OpenWeather Endpoints

| Endpoint | Path | Description | Tier |
|----------|------|-------------|------|
| Current Weather | `/data/2.5/weather` | Current weather for a location | Free |
| 5-Day Forecast | `/data/2.5/forecast` | 5-day / 3-hour forecast | Free |
| Geocoding | `/geo/1.0/direct` | City name → coordinates | Free |
| Reverse Geocoding | `/geo/1.0/reverse` | Coordinates → location name | Free |

### Current Weather (`/data/2.5/weather`)

**Base URL:** `https://api.openweathermap.org/data/2.5/weather`

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `lat` | float | Yes | Latitude |
| `lon` | float | Yes | Longitude |
| `appid` | string | Yes | API key |
| `units` | string | No | `standard`, `metric`, or `imperial` |
| `lang` | string | No | Language code (e.g., `en`, `fr`, `es`) |

**Example:**
```
https://api.openweathermap.org/data/2.5/weather?lat=51.5074&lon=-0.1278&appid=YOUR_API_KEY&units=metric
```

**Response fields:**
- `coord` — coordinates (lon, lat)
- `weather` — condition array (id, main, description, icon)
- `main` — temp, feels_like, temp_min, temp_max, pressure, humidity, sea_level, grnd_level
- `visibility` — visibility in metres
- `wind` — speed, deg, gust
- `clouds` — cloudiness percentage (all)
- `rain` — rain volume (1h, 3h)
- `snow` — snow volume (1h, 3h)
- `dt` — data calculation time (Unix timestamp)
- `sys` — country, sunrise, sunset
- `timezone` — UTC offset in seconds
- `name` — city name

### 5-Day Forecast (`/data/2.5/forecast`)

**Base URL:** `https://api.openweathermap.org/data/2.5/forecast`

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `lat` | float | Yes | Latitude |
| `lon` | float | Yes | Longitude |
| `appid` | string | Yes | API key |
| `units` | string | No | `standard`, `metric`, or `imperial` |
| `lang` | string | No | Language code |
| `cnt` | integer | No | Number of timestamps (max 40, default 40) |

**Example:**
```
https://api.openweathermap.org/data/2.5/forecast?lat=51.5074&lon=-0.1278&appid=YOUR_API_KEY&units=metric
```

---

## Function Signatures

```python
from weather_forecaster_sources.weather_source import (
    current_weather,
    weather_forecast,
    weather_alerts,
    openweather_source,
    geocoding,
    reverse_geocoding,
)
```

### `current_weather`

```python
@dlt.source(name="openweather_current")
def current_weather(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> dlt.source
```

### `weather_forecast`

```python
@dlt.source(name="openweather_forecast")
def weather_forecast(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> dlt.source
```

### `openweather_source` (combined)

```python
@dlt.source(name="openweather")
def openweather_source(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
    include_current: bool = True,
    include_forecast: bool = True,
    include_alerts: bool = False,
) -> dlt.source
```

### Parameters (all sources)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | Required | OpenWeather API key |
| `lat` | `float` | Required | Latitude |
| `lon` | `float` | Required | Longitude |
| `units` | `str` | `"metric"` | `standard`, `metric`, or `imperial` |
| `lang` | `str` | `"en"` | Language code for descriptions |

---

## Example Usage

```python
from weather_forecaster_sources.config import get_api_key
from weather_forecaster_sources.weather_source import current_weather, openweather_source

api_key = get_api_key("OPENWEATHER_API_KEY", required=True)

# Current weather only
source = current_weather(api_key=api_key, lat=51.5074, lon=-0.1278, units="metric")

# Combined source
source = openweather_source(
    api_key=api_key,
    lat=51.5074,
    lon=-0.1278,
    include_current=True,
    include_forecast=True,
    include_alerts=False,
)
```

---

## Running Pipelines

### Via pipeline_runner (recommended)

```python
from weather_forecaster_sources.pipeline_runner import run_pipeline, LoadMode
from weather_forecaster_sources.config import get_api_key

api_key = get_api_key("OPENWEATHER_API_KEY", required=True)

results = run_pipeline(
    api_key=api_key,
    lat=51.5074,
    lon=-0.1278,
    city_name="London",
    load_mode=LoadMode.INCREMENTAL,  # or LoadMode.FULL_RELOAD
)
```

### Step by step

```python
# Step 1: Extract to parquet
from weather_forecaster_sources.extraction import extract_all_sources
extract_all_sources(api_key=api_key, lat=51.5074, lon=-0.1278)

# Step 2: Load parquet → bronze DuckDB
from weather_forecaster_sources.bronze_loader import load_all_to_bronze
load_all_to_bronze()
```

### Via dlt directly

```python
import dlt

pipeline = dlt.pipeline(
    pipeline_name="weather_pipeline",
    destination="duckdb",
    dataset_name="bronze",
)
load_info = pipeline.run(source)
print(load_info)
```
