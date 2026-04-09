# Data Engineering Pipeline Architecture

## Overview

This project implements a modern data engineering pipeline using [dlt](https://dlthub.com/) (data load tool) for extracting, loading, and transforming data from various sources to a destination.

## Pipeline Architecture

The pipeline follows a **two-stage architecture**:

1. **Extraction Stage**: Extracts data from sources (APIs, files) and saves to parquet files in `data_zone`
2. **Bronze Load Stage**: Loads parquet files from `data_zone` to the bronze layer (DuckDB) incrementally using composite keys

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Data Sources                                          │
├─────────────────┬─────────────────┬─────────────────┬────────────────────────┤
│   OpenWeather   │   CSV Files     │   Parquet       │   REST APIs            │
│   API           │                 │   Files         │                        │
└────────┬────────┴────────┬────────┴────────┬────────┴────────┬─────────────────┘
         │                │                │                │
         ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    EXTRACTION LAYER                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  extraction.py                                                       │   │
│  │  - Fetches data from APIs                                            │   │
│  │  - Saves to parquet files in data_zone/                              │   │
│  │  - One parquet file per data source                                  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼ (Parquet files in data_zone/)
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DATA ZONE LAYER                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  data_zone/                                                          │   │
│  │  ├── current_weather_20240315_120000.parquet                        │   │
│  │  ├── weather_forecast_20240315_120000.parquet                       │   │
│  │  ├── geocoding_20240315_120000.parquet                             │   │
│  │  └── reverse_geocoding_20240315_120000.parquet                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────────────┘
                                 │
                                 ▼ (Incremental load with composite keys)
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BRONZE LAYER                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  bronze_loader.py                                                    │   │
│  │  - Reads parquet files from data_zone/                              │   │
│  │  - Uses composite keys for deduplication                           │   │
│  │  - Merges new records incrementally                                 │   │
│  │  - Stores in weather_forecaster.duckdb                                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Data Sources                                     │
├─────────────────┬─────────────────┬─────────────────┬──────────────────┤
│   CSV Files     │   Parquet       │   REST APIs     │  Google Sheets   │
│   Text/Logs     │   Files         │                 │                  │
│                 │                 │   OpenWeather   │                  │
└────────┬────────┴────────┬────────┴────────┬────────┴────────┬─────────┘
         │                │                │                │
         ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ETL Sources (dlt 1.x)                               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│  │ csv_source   │ │parquet_source│ │rest_api_source│ │google_sheets │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘  │
│  ┌──────────────┐                                                     │
│  │open_weather  │                                                     │
│  └──────────────┘                                                     │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              dlt Pipeline (Extract → Load → Transform)                 │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Destinations                                    │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                   │
│  │   DuckDB    │ │  PostgreSQL  │ │    BigQuery  │  ...             │
│  └──────────────┘ └──────────────┘ └──────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Component Descriptions

### ETL Sources

| Source | Description | Use Case |
|--------|-------------|----------|
| `csv_source` | Extract from CSV files with glob patterns | Batch data ingestion |
| `parquet_source` | Extract from Parquet columnar files | Analytics datasets |
| `text_source` | Extract from text/log files | Log aggregation |
| `rest_api_source` | Extract from REST APIs with pagination | External APIs |
| `google_sheets_source` | Extract from Google Sheets | Spreadsheet data |
| `open_weather` | Extract from OpenWeather Free API 2.5 | Weather data |

### Pipeline Features

- **Incremental Loading**: Uses composite keys for deduplication
- **Schema Evolution**: Automatic schema detection and evolution
- **Data Validation**: Pydantic models for type validation
- **Error Handling**: Retry logic with exponential backoff and timeout support
- **Data Zone Layer**: Intermediate storage of raw data in parquet format
- **Two-Stage Pipeline**: Separation of extraction and loading concerns
- **API Resilience**: Automatic retry with exponential backoff (3 attempts, 2-10s wait) and configurable timeout (default 30s)

## Data Flow

### 1. Extraction Layer (data_zone)
```
OpenWeather API → extraction.py → Parquet files in data_zone/{timestamp}/

Output structure:
  data_zone/
  └── 20260326_082633/           # Timestamp folder created per extraction
      ├── current_weather.parquet
      ├── weather_forecast.parquet
      ├── geocoding.parquet
      └── reverse_geocoding.parquet
```

### 2. Bronze Layer Loading

The bronze layer supports two load modes:

#### Incremental Mode (Default)
Loads only the most recent data zone folder - ideal for regular updates:
```bash
python pipeline_runner.py  # Default: incremental mode
```

#### Full Reload Mode
Truncates all tables and reloads ALL historical folders - useful for backfills or recovery:
```bash
python pipeline_runner.py full  # Full reload mode
```

```python
from weather_forecaster_sources.pipeline_runner import run_pipeline, LoadMode

# Incremental load (default) - loads latest folder only
results = run_pipeline(api_key=..., lat=..., lon=..., load_mode=LoadMode.INCREMENTAL)

# Full reload - truncates tables and loads all folders
results = run_pipeline(api_key=..., lat=..., lon=..., load_mode=LoadMode.FULL_RELOAD)
```

### 3. Composite Keys for Incremental Loading

Each data source uses composite keys to ensure uniqueness and enable incremental loads:

| Data Source | Composite Key Columns | Description |
|-------------|---------------------|-------------|
| `current_weather` | `lat, lon, _fetched_at` | Unique per location and fetch time |
| `weather_forecast` | `lat, lon, dt, _fetched_at` | Unique per location, forecast time, and fetch time |
| `geocoding` | `lat, lon, name, country, _fetched_at` | Unique per location and place name |
| `reverse_geocoding` | `lat, lon, name, country, _fetched_at` | Unique per location and place name |

The bronze loader:
1. Reads parquet files from `data_zone/`
2. Creates a composite key from the defined columns
3. Checks existing records in bronze layer
4. Inserts only new records (skips duplicates)

### 4. Running the Pipeline
```python
from weather_forecaster_sources.pipeline_runner import run_pipeline

results = run_pipeline(
    api_key="your_api_key",
    lat=51.5074,
    lon=-0.1278,
    city_name="London"
)
```

Or run with full reload mode:
```python
from weather_forecaster_sources.pipeline_runner import run_pipeline, LoadMode

results = run_pipeline(
    api_key="your_api_key",
    lat=51.5074,
    lon=-0.1278,
    city_name="London",
    load_mode=LoadMode.FULL_RELOAD
)
```

### 5. Load Metadata Tracking

The bronze layer uses a `_load_metadata` table to track which files have been loaded:

```sql
CREATE TABLE _load_metadata (
    folder_name VARCHAR NOT NULL,  -- e.g., '20260326_083459'
    filename VARCHAR NOT NULL,     -- e.g., 'current_weather'
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (folder_name, filename)
);
```

This ensures:
- **Incremental mode**: Won't reload files already loaded from the same folder
- **Idempotency**: Running the pipeline multiple times produces the same result
- **Debugging**: Can query `_load_metadata` to see what was loaded and when

Or run steps separately:
```python
# Step 1: Extract to parquet
from weather_forecaster_sources.extraction import extract_all_sources
extract_all_sources(api_key=..., lat=..., lon=...)

# Step 2: Load to bronze
from weather_forecaster_sources.bronze_loader import load_all_to_bronze
load_all_to_bronze()
```

## Technology Stack

| Component | Technology | Version |
|-----------|------------|---------|
| ETL Framework | dlt | 1.x |
| Python | 3.11+ | - |
| Destination | DuckDB | Latest |
| Testing | pytest | 9.x |
| Container | Docker | Latest |

## Project Structure

```
weather_forecaster/
├── weather_forecaster_sources/  # ETL source modules
│   ├── __init__.py
│   ├── config.py          # Configuration and API key management
│   ├── weather_source.py  # Original dlt source definitions
│   ├── extraction.py      # Extraction to parquet in data_zone
│   ├── bronze_loader.py   # Incremental/full-reload to bronze layer
│   └── pipeline_runner.py # Unified pipeline orchestration
├── tests/
│   ├── test_extraction.py    # Tests for extraction module
│   ├── test_bronze_loader.py  # Tests for bronze loader
│   ├── test_weather_api.py    # Integration tests for API
│   └── test_weather_mock.py  # Mocked tests
├── data/
│   ├── data_zone/         # Intermediate parquet files (timestamped folders)
│   │   └── YYYYMMDD_HHMMSS/   # One folder per extraction run
│   │       ├── *.parquet
│   ├── bronze/            # Bronze layer DuckDB database
│   │   └── weather_forecaster.duckdb
│   ├── gold/              # Gold layer (aggregations)
│   └── duckdb/            # DuckDB files
├── schema/                # Schema definitions
├── .env.example
├── pyproject.toml
└── .claude/docs/architecture.md (this file)
```

## New Pipeline Components

### extraction.py
- Extracts data from OpenWeather API
- Saves to parquet files in `data_zone/{timestamp}/` folder
- One parquet file per data source
- Creates timestamped folder for each extraction run
- **API Resilience**: Implements retry logic with exponential backoff (3 attempts, 2-10s wait) and configurable timeout (default 30s)
- **Error Handling**: Catches and logs API errors, continues with other data sources if one fails

### bronze_loader.py
- Loads parquet files from `data_zone/` to bronze
- Supports two load modes:
  - **INCREMENTAL** (default): Loads only the latest folder, tracks loaded files with `(folder_name, filename)` composite key in `_load_metadata`
  - **FULL_RELOAD**: Truncates all tables and reloads ALL folders
- Uses composite keys for deduplication within tables
- Stores in DuckDB (`weather_forecaster.duckdb`)
- `_load_metadata` table tracks each loaded file using composite key `(folder_name, filename)` to prevent reloading the same file from the same folder

### pipeline_runner.py
- Orchestrates the full pipeline
- Can run extraction and load separately
- Provides summary statistics
- Supports command line arguments:
  - `python pipeline_runner.py` - Incremental mode (default)
  - `python pipeline_runner.py full` - Full reload mode

## Best Practices

1. **Use Type Hints**: All source functions include type hints
2. **Handle Secrets**: Use environment variables or .env files
3. **Test Extensively**: Unit tests for all source functions
4. **Version Control**: Track schema changes in code
5. **Monitor**: Add logging and alerting for pipeline failures
