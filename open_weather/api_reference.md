# API Reference

This document describes the ETL source APIs for the data engineering pipeline.

## Table of Contents
- [CSV Source](#csv-source)
- [Parquet Source](#parquet-source)
- [Text Source](#text-source)
- [REST API Source](#rest-api-source)
- [Google Sheets Source](#google-sheets-source)
- [OpenWeather Source](#openweather-source)

---

## OpenWeather Source

Extract weather data from OpenWeatherMap's One Call API 3.0.

### Function Signature

```python
from open_weather import current_weather, weather_forecast, weather_alerts, openweather_source

@dlt.source(name="openweather_current")
def current_weather(
    api_key: str,
    lat: float,
    lon: float,
    units: str = "metric",
    lang: str = "en",
) -> dlt.source
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | Required | OpenWeather API key |
| `lat` | `float` | Required | Latitude |
| `lon` | `float` | Required | Longitude |
| `units` | `str` | `"metric"` | Units (standard, metric, imperial) |
| `lang` | `str` | `"en"` | Language code for descriptions |

### Example Usage

```python
import dlt

# Current weather only
source = current_weather(
    api_key="YOUR_API_KEY",
    lat=51.5074,
    lon=-0.1278,
    units="metric"
)

# Combined source
source = openweather_source(
    api_key="YOUR_API_KEY",
    lat=51.5074,
    lon=-0.1278,
    include_current=True,
    include_forecast=True,
    include_alerts=True
)

# Run pipeline
pipeline = dlt.pipeline(
    pipeline_name="weather_pipeline",
    destination="duckdb",
    dataset_name="bronze"
)
load_info = pipeline.run(source)
```

---

## CSV Source

Extract data from CSV files with support for glob patterns, custom delimiters, and encoding.

### Function Signature

```python
@dlt.source(name="csv_source")
def csv_source(
    file_path: str,
    chunksize: Optional[int] = None,
    delimiter: str = ",",
    quotechar: str = '"',
    encoding: str = "utf-8",
    header: bool = True,
    primary_key: Optional[str] = None,
) -> dlt.source
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` | Required | Path to CSV file or glob pattern (e.g., `data/*.csv`) |
| `chunksize` | `Optional[int]` | `None` | Number of rows to read at a time |
| `delimiter` | `str` | `","` | CSV column delimiter |
| `quotechar` | `str` | `'"'` | Quote character |
| `encoding` | `str` | `"utf-8"` | File encoding |
| `header` | `bool` | `True` | Whether CSV has header row |
| `primary_key` | `Optional[str]` | `None` | Primary key column for deduplication |

### Example Usage

```python
import dlt

# Single file
source = csv_source("data/users.csv", primary_key="user_id")

# Glob pattern
source = csv_source("data/*.csv")

# Custom delimiter
source = csv_source("data/tabs.txt", delimiter="\t")
```

### Folder Source

```python
def csv_folder_source(
    folder_path: str,
    file_pattern: str = "*.csv",
    primary_key: Optional[str] = None,
) -> dlt.source
```

---

## Parquet Source

Extract data from Apache Parquet files with column selection and filtering.

### Function Signature

```python
@dlt.source(name="parquet_source")
def parquet_source(
    file_path: str,
    columns: Optional[List[str]] = None,
    filters: Optional[List[List[Any]]] = None,
    primary_key: Optional[str] = None,
) -> dlt.source
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` | Required | Path to Parquet file or glob pattern |
| `columns` | `Optional[List[str]]` | `None` | Columns to extract (deprecated in dlt 1.x) |
| `filters` | `Optional[List[List[Any]]]` | `None` | Row filters (deprecated in dlt 1.x) |
| `primary_key` | `Optional[str]` | `None` | Primary key column |

### Example Usage

```python
import dlt

# Single file
source = parquet_source("data/users.parquet", primary_key="user_id")

# Glob pattern
source = parquet_source("data/*.parquet")
```

### Schema Detection

```python
def parquet_schema(file_path: str) -> Dict[str, Any]:
    """Get schema information from Parquet file."""
```

Returns:
```python
{
    "num_rows": 1000,
    "num_columns": 5,
    "num_row_groups": 1,
    "columns": [
        {"name": "id", "type": "int64", "nullable": False},
        {"name": "name", "type": "string", "nullable": True}
    ]
}
```

---

## Text Source

Extract data from text and log files.

### Function Signature

```python
@dlt.source(name="text_source")
def text_source(
    file_path: str,
    encoding: str = "utf-8",
    lines: bool = True,
    include_metadata: bool = True,
    primary_key: Optional[str] = None,
) -> dlt.source
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `str` | Required | Path to text file or glob pattern |
| `encoding` | `str` | `"utf-8"` | File encoding |
| `lines` | `bool` | `True` | Read line by line vs. whole file |
| `include_metadata` | `bool` | `True` | Include file metadata |
| `primary_key` | `Optional[str]` | `None` | Primary key column |

### Example Usage

```python
# Single file
source = text_source("data/logs/app.log")

# Glob pattern for log files
source = text_source("logs/*.log")
```

### Log File Helper

```python
def log_file_source(
    folder_path: str,
    file_pattern: str = "*.log",
    encoding: str = "utf-8",
    primary_key: Optional[str] = None,
) -> dlt.source
```

---

## REST API Source

Extract data from RESTful APIs with authentication, pagination, and incremental loading.

### Function Signature

```python
def rest_api_source(
    name: str,
    base_url: str,
    endpoints: List[str],
    auth: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None,
    pagination: Optional[Dict[str, Any]] = None,
    primary_key: Optional[str] = None,
    incremental_column: Optional[str] = None,
) -> dlt.source
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | Required | Source name |
| `base_url` | `str` | Required | Base URL for API |
| `endpoints` | `List[str]` | Required | API endpoints to call |
| `auth` | `Optional[Dict]` | `None` | Auth config `{"type": "bearer", "token": "..."}` |
| `headers` | `Optional[Dict]` | `None` | Additional HTTP headers |
| `pagination` | `Optional[Dict]` | `None` | Pagination config |
| `primary_key` | `Optional[str]` | `None` | Primary key column |
| `incremental_column` | `Optional[str]` | `None` | Column for incremental loads |

### Example Usage

```python
# Basic API
source = rest_api_source(
    name="github_api",
    base_url="https://api.github.com",
    endpoints=["users", "repos"]
)

# With authentication
source = rest_api_source(
    name="private_api",
    base_url="https://api.example.com",
    endpoints=["data"],
    auth={"type": "bearer", "token": "YOUR_TOKEN"}
)

# With incremental loading
source = rest_api_source(
    name="time_series_api",
    base_url="https://api.example.com",
    endpoints=["measurements"],
    incremental_column="timestamp"
)
```

### Authentication Types

```python
# Bearer Token
auth = {"type": "bearer", "token": "YOUR_TOKEN"}

# Basic Auth
auth = {"type": "basic", "username": "user", "password": "pass"}

# API Key
auth = {"type": "api_key", "key": "YOUR_KEY", "location": "header"}
```

### Pagination Config

```python
pagination = {
    "strategy": "offset",      # offset, cursor, page
    "limit": 100,
    "offset_param": "page",
    "max_offset": 1000
}
```

---

## Google Sheets Source

Extract data from Google Sheets.

### Function Signature

```python
@dlt.resource(name="google_sheets")
def google_sheets_source(
    spreadsheet_id: str,
    sheet_names: Optional[List[str]] = None,
    range: Optional[str] = None,
    credentials_path: str = CREDENTIALS_PATH,
)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `spreadsheet_id` | `str` | Required | Google Sheets ID |
| `sheet_names` | `Optional[List[str]]` | `None` | Specific sheets to extract |
| `range` | `Optional[str]` | `None` | A1 notation range |
| `credentials_path` | `str` | See below | Service account JSON |

### Default Credentials Path
```
credentials/google_service_account.json
```

### Example Usage

```python
# Using spreadsheet ID
source = google_sheets_source(
    spreadsheet_id="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
)

# Using URL
source = google_sheets_ro_source(
    spreadsheet_url="https://docs.google.com/spreadsheets/d/YOUR_ID/edit"
)
```

### Setup Instructions

1. Create a Google Cloud Project
2. Enable Google Sheets API
3. Create a Service Account with "Viewer" role
4. Download JSON key file
5. Share your spreadsheet with the service account email
6. Place credentials in `credentials/google_service_account.json`

---

## Running Pipelines

### Basic Pipeline

```python
import dlt

pipeline = dlt.pipeline(
    pipeline_name="my_pipeline",
    destination="duckdb",
    dataset_name="bronze"
)

# Run source
load_info = pipeline.run(source)
print(load_info)
```

### With Schema

```python
pipeline.run(
    csv_source("data.csv"),
    write_disposition="replace",  # replace, append, merge
    table_name="my_table"
)
```
