# Data Engineering Pipeline Architecture

## Overview

This project implements a modern data engineering pipeline using [dlt](https://dlthub.com/) (data load tool) for extracting, loading, and transforming data from various sources to a destination.

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
| `open_weather` | Extract from OpenWeather One Call API 3.0 | Weather data |

### Pipeline Features

- **Incremental Loading**: Uses `dlt.sources.incremental` for watermark-based loading
- **Schema Evolution**: Automatic schema detection and evolution
- **Data Validation**: Pydantic models for type validation
- **Error Handling**: Retry logic and dead letter queue support

## Data Flow

### 1. Extraction Layer
```
Source Files → dlt.filesystem() → read_csv/read_parquet → Yield rows
```

### 2. Transformation Layer (Optional)
```python
@dlt.transformer
def transform_rows(items):
    for item in items:
        yield transform(item)
```

### 3. Loading Layer
```python
pipeline.run(source, destination="duckdb", dataset_name="bronze")
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
prototype/
├── etl_sources/           # ETL source modules
│   └── sources/
│       ├── csv_source.py
│       ├── parquet_source.py
│       ├── text_source.py
│       ├── rest_api_source.py
│       └── google_sheets.py
├── tests/
│   ├── test_etl_sources.py
│   └── test_extraction.py
├── data/                  # Sample data files
├── pipelines/             # Pipeline definitions
├── credentials/           # API credentials
├── docker-compose.yml
├── UNIT_TESTING_GUIDE.md
└── ARCHITECTURE.md (this file)
```

## Best Practices

1. **Use Type Hints**: All source functions include type hints
2. **Handle Secrets**: Use environment variables or .env files
3. **Test Extensively**: Unit tests for all source functions
4. **Version Control**: Track schema changes in code
5. **Monitor**: Add logging and alerting for pipeline failures
