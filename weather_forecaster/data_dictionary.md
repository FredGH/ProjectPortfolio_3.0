# Data Dictionary

This document describes the schema definitions for all ETL sources in the data engineering pipeline.

## Table of Contents
- [Common Fields](#common-fields)
- [CSV Source Schemas](#csv-source-schemas)
- [Parquet Source Schemas](#parquet-source-schemas)
- [Text Source Schemas](#text-source-schemas)
- [REST API Source Schemas](#rest-api-source-schemas)
- [Google Sheets Source Schemas](#google-sheets-source-schemas)
- [OpenWeather Source Schemas](#openweather-source-schemas)

---

## Common Fields

All sources may include these common metadata fields:

| Field Name | Type | Description |
|------------|------|-------------|
| `_dlt_load_id` | `string` | Unique load identifier |
| `_dlt_pipeline_id` | `string` | Pipeline run identifier |
| `_fetched_at` | `timestamp` | Time when data was fetched (added by sources) |

---

## OpenWeather Source Schemas

### Current Weather Response

API Response:
```json
{
    "lat": 51.5074,
    "lon": -0.1278,
    "timezone": "Europe/London",
    "current": {
        "dt": 1704067200,
        "temp": 5.2,
        "feels_like": 3.1,
        "humidity": 82,
        "wind_speed": 3.6,
        "weather": [
            {"id": 801, "main": "Clouds", "description": "few clouds"}
        ]
    }
}
```

Output Schema:
```python
{
    "columns": [
        {"name": "lat", "dtype": "float64"},
        {"name": "lon", "dtype": "float64"},
        {"name": "timezone", "dtype": "string"},
        {"name": "current", "dtype": "json"},
        {"name": "_fetched_at", "dtype": "timestamp"}
    ]
}
```

### Weather Forecast Response

API Response includes:
- `hourly`: 48 hours of hourly forecasts
- `daily`: 7 days of daily forecasts

Output Schema:
```python
{
    "columns": [
        {"name": "lat", "dtype": "float64"},
        {"name": "lon", "dtype": "float64"},
        {"name": "timezone", "dtype": "string"},
        {"name": "hourly", "dtype": "json"},
        {"name": "daily", "dtype": "json"},
        {"name": "_fetched_at", "dtype": "timestamp"}
    ]
}
```

### Weather Alerts Response

Output Schema:
```python
{
    "columns": [
        {"name": "lat", "dtype": "float64"},
        {"name": "lon", "dtype": "float64"},
        {"name": "timezone", "dtype": "string"},
        {"name": "alerts", "dtype": "json"},
        {"name": "_fetched_at", "dtype": "timestamp"}
    ]
}
```

---

## CSV Source Schemas

### Basic CSV

Input:
```csv
id,name,email,created_at
1,john.doe@example.com,2024-01-15
```

Output Schema:
```python
{
    "columns": [
        {"name": "id", "dtype": "int64"},
        {"name": "name", "dtype": "string"},
        {"name": "email", "dtype": "string"},
        {"name": "created_at", "dtype": "date"}
    ]
}
```

### CSV with Custom Delimiter (TSV)

Input:
```tsv
id	name	email	created_at
1	john.doe@example.com	2024-01-15
```

### CSV without Header

When `header=False`:
```csv
1,john,john@example.com,2024-01-15
2,jane,jane@example.com,2024-01-16
```

Output Schema:
```python
{
    "columns": [
        {"name": "column_0", "dtype": "string"},
        {"name": "column_1", "dtype": "string"},
        {"name": "column_2", "dtype": "string"},
        {"name": "column_3", "dtype": "string"}
    ]
}
```

---

## Parquet Source Schemas

### Basic Parquet

Parquet files contain embedded schema information. The system automatically detects:

| Parquet Type | Python Type |
|--------------|-------------|
| `INT64` | `int64` |
| `INT32` | `int32` |
| `FLOAT` | `float32` |
| `DOUBLE` | `float64` |
| `STRING` | `string` |
| `BINARY` | `bytes` |
| `BOOL` | `bool` |
| `TIMESTAMP` | `timestamp` |
| `DATE` | `date` |

### Nested Structures

Parquet supports complex types:

```python
{
    "columns": [
        {"name": "id", "dtype": "int64"},
        {"name": "address", "dtype": "struct", "fields": [
            {"name": "street", "dtype": "string"},
            {"name": "city", "dtype": "string"},
            {"name": "zip", "dtype": "string"}
        ]},
        {"name": "tags", "dtype": "list", "element_type": "string"}
    ]
}
```

---

## Text Source Schemas

### Basic Text File

```text
Hello World
This is line 2
```

Output Schema:
```python
{
    "columns": [
        {"name": "file_path", "dtype": "string"},
        {"name": "content", "dtype": "string"}
    ]
}
```

### Log File Format

Standard log format:
```
2024-01-15 10:30:00 [INFO] Application started
2024-01-15 10:30:01 [DEBUG] Loading configuration
2024-01-15 10:30:02 [ERROR] Connection failed
```

Parsed Output:
```python
{
    "columns": [
        {"name": "file_path", "dtype": "string"},
        {"name": "timestamp", "dtype": "datetime"},
        {"name": "level", "dtype": "string"},
        {"name": "message", "dtype": "string"}
    ]
}
```

---

## REST API Source Schemas

### JSON Response

API Response:
```json
{
    "users": [
        {
            "id": 1,
            "name": "John Doe",
            "email": "john@example.com",
            "created_at": "2024-01-15T10:30:00Z"
        }
    ]
}
```

Output Schema:
```python
{
    "columns": [
        {"name": "id", "dtype": "int64"},
        {"name": "name", "dtype": "string"},
        {"name": "email", "dtype": "string"},
        {"name": "created_at", "dtype": "timestamp"}
    ]
}
```

### Nested JSON

```json
{
    "data": {
        "items": [
            {"id": 1, "details": {"name": "Item 1"}}
        ]
    }
}
```

### Paginated Response

```json
{
    "data": [...],
    "pagination": {
        "page": 1,
        "total_pages": 10,
        "total_count": 100
    }
}
```

---

## Google Sheets Source Schemas

### Basic Sheet

| A | B | C |
|---|---|---|
| id | name | email |
| 1 | John | john@example.com |
| 2 | Jane | jane@example.com |

Output Schema:
```python
{
    "columns": [
        {"name": "id", "dtype": "int64"},
        {"name": "name", "dtype": "string"},
        {"name": "email", "dtype": "string"}
    ]
}
```

### Mixed Types

Google Sheets may contain mixed types. dlt will infer the most common type:

| A |
|---|
| 1 |
| text |
| 3.14 |

Output: Column `A` will be `string` type

### Date Handling

Dates in Google Sheets:
- ISO format: `2024-01-15` → `date`
- US format: `01/15/2024` → `date` (if consistent)
- Mixed formats → `string`

---

## Type Mapping Reference

### dlt to DuckDB

| dlt Type | DuckDB Type |
|----------|--------------|
| `int64` | `BIGINT` |
| `int32` | `INTEGER` |
| `float64` | `DOUBLE` |
| `float32` | `FLOAT` |
| `string` | `VARCHAR` |
| `bool` | `BOOLEAN` |
| `date` | `DATE` |
| `timestamp` | `TIMESTAMP` |
| `time` | `TIME` |
| `binary` | `BLOB` |
| `json` | `JSON` |

### dlt to PostgreSQL

| dlt Type | PostgreSQL Type |
|----------|-----------------|
| `int64` | `BIGINT` |
| `int32` | `INTEGER` |
| `float64` | `DOUBLE PRECISION` |
| `float32` | `REAL` |
| `string` | `TEXT` |
| `bool` | `BOOLEAN` |
| `date` | `DATE` |
| `timestamp` | `TIMESTAMP` |
| `time` | `TIME` |
| `binary` | `BYTEA` |
| `json` | `JSONB` |

---

## Schema Evolution

### Adding Columns

When new columns appear in source data:

```python
# v1: CSV has columns [id, name]
# v2: CSV has columns [id, name, email]

# dlt automatically adds the new column
# Existing rows get NULL for new column
```

### Column Type Changes

| Old Type | New Type | Behavior |
|----------|----------|----------|
| `int64` | `int32` | Coerce to smaller type |
| `string` | `int64` | Convert if possible |
| `int64` | `string` | Always OK |
| `date` | `timestamp` | Coerce |

---

## Primary Keys

### Declaring Primary Keys

```python
source = csv_source("data.csv", primary_key="id")
```

### Composite Keys

```python
# Not directly supported, use string concatenation
source = csv_source("data.csv", primary_key="order_id_product_id")
```

### Effects

| Operation | Effect |
|-----------|--------|
| `write_disposition="replace"` | Truncates table, reloads data |
| `write_disposition="append"` | Inserts all rows (duplicates allowed) |
| `write_disposition="merge"` | Updates matching keys, inserts new |

## Bronze Layer Composite Keys

The bronze loader uses composite keys to enable incremental loads with deduplication:

| Data Source | Composite Key Columns |
|-------------|----------------------|
| `current_weather` | lat, lon, _fetched_at |
| `weather_forecast` | lat, lon, dt, _fetched_at |
| `geocoding` | lat, lon, name, country, _fetched_at |
| `reverse_geocoding` | lat, lon, name, country, _fetched_at |

### How Composite Keys Work

1. When loading from data_zone parquet files to bronze:
   - A `_composite_key` column is created by concatenating the key column values
   - The loader checks existing composite keys in the bronze table
   - Only new records (with unique composite keys) are inserted
   - Duplicate records are skipped

2. This approach ensures:
   - No duplicate data in bronze layer
   - Idempotent pipeline runs
   - Ability to replay/reprocess from data_zone parquet files

## Load Modes

The bronze layer supports two load modes controlled by the `LoadMode` enum:

### LoadMode.INCREMENTAL (Default)

This mode loads only the most recent data zone folder (the latest timestamp directory). It's designed for regular incremental updates where you only need to process new data since the last run.

**Behavior:**
- Finds the latest timestamp folder in `data_zone/`
- Checks `_load_metadata` table to skip files already loaded from this specific folder
- Loads only new/changed records using composite keys

**Tracking:** The `_load_metadata` table tracks each loaded file with a composite key of `(folder_name, filename)`, so the same file from the same folder won't be loaded twice.

**Use cases:**
- Regular scheduled runs (hourly/daily updates)
- Typical production workloads

### LoadMode.FULL_RELOAD

This mode truncates all tables and reloads data from ALL historical folders in data_zone. Useful for backfills, recovery scenarios, or when you need to rebuild the bronze layer from scratch.

**Behavior:**
- Truncates all existing tables in the bronze layer
- Loads parquet files from ALL timestamp folders in `data_zone/`
- Ignores `_load_metadata` - loads everything fresh

**Use cases:**
- Initial data load or backfills
- Data recovery or schema changes
- Testing/debugging with fresh state

### How the Load Tracking Works

The `_load_metadata` table uses a composite key `(folder_name, filename)`:

```sql
CREATE TABLE _load_metadata (
    folder_name VARCHAR NOT NULL,  -- e.g., '20260326_083459'
    filename VARCHAR NOT NULL,     -- e.g., 'current_weather'
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (folder_name, filename)
);
```

**Incremental Mode Flow:**
1. Get list of folders → selects latest folder only
2. Get list of parquet files in that folder
3. Check `_load_metadata` for `folder_name/filename` combination
4. Skip files already loaded from this specific folder
5. Load new files, mark as loaded

**Full Reload Mode Flow:**
1. Truncate all data tables (but keep `_load_metadata`)
2. Get list of ALL folders
3. Load ALL parquet files from ALL folders
4. No check against `_load_metadata` - loads everything

### How to Use

#### Command Line
```bash
# Default: incremental mode
python pipeline_runner.py

# Full reload mode
python pipeline_runner.py full
```

#### Python API
```python
from weather_forecaster_sources.pipeline_runner import run_pipeline, LoadMode

# Incremental (default)
results = run_pipeline(
    api_key="your_key",
    lat=51.5,
    lon=-0.12,
    load_mode=LoadMode.INCREMENTAL
)

# Full reload
results = run_pipeline(
    api_key="your_key",
    lat=51.5,
    lon=-0.12,
    load_mode=LoadMode.FULL_RELOAD
)
```
