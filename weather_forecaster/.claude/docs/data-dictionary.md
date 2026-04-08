# Data Dictionary

This document describes the schema definitions for the weather_forecaster pipeline.

## Table of Contents
- [Common Fields](#common-fields)
- [OpenWeather Schemas](#openweather-schemas)
- [Bronze Layer Composite Keys](#bronze-layer-composite-keys)
- [Load Modes](#load-modes)
- [Type Mapping Reference](#type-mapping-reference)
- [Schema Evolution](#schema-evolution)

---

## Common Fields

All sources include these metadata fields:

| Field | Type | Description |
|---|---|---|
| `_dlt_load_id` | `string` | Unique load identifier (added by dlt) |
| `_dlt_pipeline_id` | `string` | Pipeline run identifier (added by dlt) |
| `_fetched_at` | `timestamp` | Time when data was fetched from the API |

---

## OpenWeather Schemas

### Current Weather (`current_weather` table)

API Response (Free API 2.5 `/data/2.5/weather`):
```json
{
    "coord": {"lon": -0.1278, "lat": 51.5074},
    "weather": [{"id": 801, "main": "Clouds", "description": "few clouds", "icon": "02d"}],
    "main": {"temp": 12.3, "feels_like": 10.1, "temp_min": 11.0, "temp_max": 13.5, "pressure": 1015, "humidity": 72},
    "visibility": 10000,
    "wind": {"speed": 3.6, "deg": 220},
    "clouds": {"all": 20},
    "dt": 1704067200,
    "sys": {"country": "GB", "sunrise": 1704006000, "sunset": 1704038400},
    "timezone": 0,
    "name": "London"
}
```

Flattened output schema (parquet / bronze):
```python
{
    "lat": float64,
    "lon": float64,
    "main_temp": float64,
    "main_feels_like": float64,
    "main_temp_min": float64,
    "main_temp_max": float64,
    "main_pressure": int64,
    "main_humidity": int64,
    "wind_speed": float64,
    "wind_deg": int64,
    "clouds_all": int64,
    "visibility": int64,
    "dt": int64,
    "name": string,
    "sys_country": string,
    "sys_sunrise": int64,
    "sys_sunset": int64,
    "timezone": int64,
    "_fetched_at": timestamp,
}
```

### Weather Forecast (`weather_forecast` table)

API Response (Free API 2.5 `/data/2.5/forecast`) returns a list of 3-hour interval records:
```json
{
    "list": [
        {
            "dt": 1704067200,
            "main": {"temp": 12.3, "feels_like": 10.1, "humidity": 72, "pressure": 1015},
            "weather": [{"id": 801, "main": "Clouds", "description": "few clouds"}],
            "wind": {"speed": 3.6, "deg": 220},
            "clouds": {"all": 20},
            "dt_txt": "2024-01-01 12:00:00"
        }
    ]
}
```

Flattened output schema (per forecast interval):
```python
{
    "lat": float64,
    "lon": float64,
    "dt": int64,
    "dt_txt": string,
    "main_temp": float64,
    "main_feels_like": float64,
    "main_humidity": int64,
    "main_pressure": int64,
    "wind_speed": float64,
    "wind_deg": int64,
    "clouds_all": int64,
    "_fetched_at": timestamp,
}
```

### Geocoding (`geocoding` table)

```python
{
    "lat": float64,
    "lon": float64,
    "name": string,
    "country": string,
    "state": string,
    "_fetched_at": timestamp,
}
```

### Reverse Geocoding (`reverse_geocoding` table)

```python
{
    "lat": float64,
    "lon": float64,
    "name": string,
    "country": string,
    "state": string,
    "_fetched_at": timestamp,
}
```

---

## Bronze Layer Composite Keys

Each bronze table uses a composite key for deduplication. The `_composite_key` column is created by concatenating the key column values and used to prevent duplicates on incremental load:

| Table | Composite Key Columns | Meaning |
|---|---|---|
| `current_weather` | `lat, lon, _fetched_at` | One record per location per fetch |
| `weather_forecast` | `lat, lon, dt, _fetched_at` | One forecast interval per location per fetch |
| `geocoding` | `lat, lon, name, country, _fetched_at` | One place per location per fetch |
| `reverse_geocoding` | `lat, lon, name, country, _fetched_at` | One place per location per fetch |

### How composite keys work

1. A `_composite_key` column is created by concatenating the defined key columns
2. The loader checks existing keys in the bronze table
3. Only records with a new composite key are inserted — duplicates are skipped
4. This makes pipeline runs idempotent

---

## Load Modes

### `LoadMode.INCREMENTAL` (default)

Loads only the most recent `data_zone` timestamp folder. Designed for regular scheduled runs.

**Flow:**
1. Find latest folder in `data_zone/`
2. Check `_load_metadata` for `(folder_name, filename)` — skip already-loaded files
3. Load new records using composite key deduplication

**Use when:** Regular hourly/daily updates.

### `LoadMode.FULL_RELOAD`

Truncates all tables and reloads from every historical `data_zone` folder.

**Flow:**
1. Truncate all bronze data tables
2. Load parquet files from ALL timestamp folders
3. Ignore `_load_metadata` — load everything fresh

**Use when:** Initial load, backfills, schema changes, or debugging.

### `_load_metadata` tracking table

```sql
CREATE TABLE _load_metadata (
    folder_name VARCHAR NOT NULL,  -- e.g., '20260326_083459'
    filename    VARCHAR NOT NULL,  -- e.g., 'current_weather'
    loaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (folder_name, filename)
);
```

---

## Type Mapping Reference

### dlt → DuckDB

| dlt Type | DuckDB Type |
|---|---|
| `int64` | `BIGINT` |
| `int32` | `INTEGER` |
| `float64` | `DOUBLE` |
| `float32` | `FLOAT` |
| `string` | `VARCHAR` |
| `bool` | `BOOLEAN` |
| `date` | `DATE` |
| `timestamp` | `TIMESTAMP` |
| `json` | `JSON` |

---

## Schema Evolution

### Adding columns

When new fields appear in the API response, dlt automatically adds the column. Existing rows receive `NULL` for the new column.

### Column type widening

| Old Type | New Type | Behaviour |
|---|---|---|
| `int32` | `int64` | Widened automatically |
| `int64` | `string` | Always safe |
| `string` | `int64` | Converted if all values parse cleanly |
| `date` | `timestamp` | Coerced |
