# Data Engineering Pipeline - OpenWeather ETL Release Notes

## Version 0.2.0 (March 2026)

### Overview
This release adds timestamped data zone folders and load modes for the OpenWeather ETL pipeline.

---

## New Features

### 1. Timestamped Data Zone Folders
Each extraction run now creates a timestamped folder in `data_zone/`:
```
data_zone/
└── 20260326_082633/           # Timestamp folder (YYYYMMDD_HHMMSS)
    ├── current_weather.parquet
    ├── weather_forecast.parquet
    ├── geocoding.parquet
    └── reverse_geocoding.parquet
```

### 2. Load Modes
The bronze loader now supports two load modes:

- **INCREMENTAL (Default)**: Loads only the most recent data zone folder - ideal for regular updates
- **FULL_RELOAD**: Truncates all tables and reloads data from ALL historical folders - useful for backfills

### 3. Load Metadata Tracking
The `_load_metadata` table now tracks each loaded file using a composite key `(folder_name, filename)`:

```sql
CREATE TABLE _load_metadata (
    folder_name VARCHAR NOT NULL,  -- e.g., '20260326_083459'
    filename VARCHAR NOT NULL,     -- e.g., 'current_weather'
    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (folder_name, filename)
);
```

This ensures:
- Incremental mode won't reload files already loaded from the same folder
- Each extraction run is tracked separately
- Idempotent pipeline runs

### 4. Command Line Interface
```bash
# Default: Incremental mode
python open_weather_sources/pipeline_runner.py

# Full reload mode
python open_weather_sources/pipeline_runner.py full
```

---

## Version 0.1.0 (March 2026)

### Overview
This prototype implements the Extraction and Storage layers of the Modern Real-Time & Batch Data Engineering Pipeline as described in the Technical Proposal.

---

## Implemented Functionalities

### 1. Data Extraction (dlt Sources)
Custom data extraction sources built with dlt (data load tool):

- **CSV Source** (`prototype_dlt/sources/csv_source.py`)
  - Extract data from CSV files
  - Configurable delimiter, quoting, and header options
  - Support for incremental loading

- **Parquet Source** (`prototype_dlt/sources/parquet_source.py`)
  - Extract data from Parquet files
  - Schema inference from Parquet metadata

- **REST API Source** (`prototype_dlt/sources/rest_api.py`)
  - Extract data from REST APIs
  - Support for pagination
  - Configurable endpoints and authentication

- **Google Sheets Source** (`prototype_dlt/sources/google_sheets.py`)
  - Extract data from Google Sheets
  - Support for service account authentication

- **Text Source** (`prototype_dlt/sources/text_source.py`)
  - Extract data from text files
  - Line-by-line processing

### 2. Data Storage
- **DuckDB** - Embedded OLAP database for local development
- **MinIO** - S3-compatible object storage (via Docker)
- **PostgreSQL** - For Dagster metadata storage (via Docker)

### 3. Medallion Architecture Structure
Directory structure prepared for:
- `dbt/bronze/` - Raw data layer
- `dbt/silver/` - Cleaned/validated data layer
- `dbt/gold/` - Business-ready aggregations

### 4. Orchestration
- **Dagster** - Asset-based orchestration (structure in place)
- Directory: `dagster/`

### 5. Docker Infrastructure
- `docker-compose.yml` with:
  - MinIO (ports 9000, 9001)
  - PostgreSQL (port 5433)

### 6. Testing
- pytest-based test suite in `tests/`
- Test coverage for:
  - CSV extraction
  - Parquet extraction
  - Text extraction (limited)
  - dlt pipeline creation

---

## Technical Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Data Loading | dlt 1.23.0 |
| Database | DuckDB |
| Storage | MinIO (S3-compatible) |
| Orchestration | Dagster |
| Transformation | dbt |
| Testing | pytest |

---

## Getting Started

### Prerequisites
- Python 3.11+
- Docker Desktop (Apple Silicon or Intel)
- Homebrew (recommended for macOS)

### Installation
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Start Docker services
docker compose up -d
```

### Running Tests
```bash
pytest -v
```

---

## Known Limitations

1. **dbt Models**: The bronze/silver/gold directories are placeholders - no SQL models implemented yet
2. **Dagster**: Directory structure exists but no actual orchestration code
3. **Text Source**: Limited support in dlt 1.x filesystem
4. **Integration Tests**: Some tests require additional configuration (Google Sheets credentials, REST API endpoints)

---

## Future Enhancements

1. Implement dbt transformation models for Silver/Gold layers
2. Add Great Expectations data quality checks
3. Set up CI/CD pipeline with GitHub Actions
4. Implement Slack/Jira integration for data observability
5. Add more comprehensive integration tests

---

## References

- Technical Proposal: `../implementation_templates/de_technical_proposal.html`
- README: `README.md`
