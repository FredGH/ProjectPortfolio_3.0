"""
Bronze Layer Loader Module

This module loads data from parquet files in the data_zone folder to the bronze layer
in DuckDB using incremental loads with composite keys for deduplication.

The incremental load strategy:
1. Read parquet files from data_zone
2. Use composite keys to identify unique records
3. Merge (upsert) data into bronze layer tables

Composite Keys for Each Data Source:
- current_weather: (lat, lon, _fetched_at) - unique per location and fetch time
- weather_forecast: (lat, lon, dt, _fetched_at) - unique per location, forecast time, and fetch time
- geocoding: (lat, lon, name, country, _fetched_at) - unique per location and name
- reverse_geocoding: (lat, lon, name, country, _fetched_at) - unique per location and name
"""
import json
import os
import pandas as pd
import pyarrow.parquet as pq
import dlt
from datetime import datetime, UTC
from typing import Dict, Any, List, Optional
from pathlib import Path
import duckdb


# Base paths - relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
DATA_ZONE_PATH = PROJECT_ROOT / "data" / "data_zone"
BRONZE_PATH = PROJECT_ROOT / "data" / "etl"


# Composite keys for each data source
COMPOSITE_KEYS = {
    "current_weather": ["lat", "lon", "_fetched_at"],
    "weather_forecast": ["lat", "lon", "dt", "_fetched_at"],
    "geocoding": ["lat", "lon", "name", "country", "_fetched_at"],
    "reverse_geocoding": ["lat", "lon", "name", "country", "_fetched_at"],
}


# Load modes
class LoadMode:
    FULL_RELOAD = "full_reload"  # Truncate tables, reload all folders
    INCREMENTAL = "incremental"  # Load latest folder only, no truncation


def list_load_folders() -> List[Path]:
    """
    List all load folders in data_zone (sorted by name, oldest first).
    
    Returns:
        List of folder paths
    """
    if not DATA_ZONE_PATH.exists():
        return []
    
    folders = sorted([d for d in DATA_ZONE_PATH.iterdir() if d.is_dir()])
    return folders


def get_latest_load_folder() -> Optional[Path]:
    """
    Get the most recent load folder.
    
    Returns:
        Path to latest folder, or None if no folders exist
    """
    folders = list_load_folders()
    return folders[-1] if folders else None


def get_all_parquet_files_in_folder(folder: Path) -> List[Path]:
    """
    Get all parquet files in a folder.
    
    Args:
        folder: Path to load folder
    
    Returns:
        List of parquet file paths
    """
    return sorted(folder.glob("*.parquet"))


def truncate_all_tables(db_path: Path) -> None:
    """
    Truncate all data tables in the bronze layer.
    Does not truncate _load_metadata.
    
    Args:
        db_path: Path to DuckDB database
    """
    if not db_path.exists():
        return
    
    conn = duckdb.connect(str(db_path))
    try:
        # Get all tables except metadata
        tables = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'staging' AND table_name != '_load_metadata'
        """).fetchall()

        for (table_name,) in tables:
            conn.execute(f"TRUNCATE TABLE staging.{table_name}")
            print(f"  Truncated: {table_name}")
    finally:
        conn.close()


def get_bronze_path() -> Path:
    """Get the bronze layer path."""
    BRONZE_PATH.mkdir(parents=True, exist_ok=True)
    return BRONZE_PATH


def get_duckdb_path() -> Path:
    """Get the DuckDB database path."""
    return get_bronze_path() / "weather_forecaster.duckdb"


def read_parquet_file(filepath: Path) -> pd.DataFrame:
    """
    Read a parquet file and return a DataFrame.
    
    Args:
        filepath: Path to the parquet file
    
    Returns:
        DataFrame containing the parquet data
    """
    return pd.read_parquet(filepath)


def get_composite_key_columns(table_name: str) -> List[str]:
    """
    Get the composite key columns for a given table.
    
    Args:
        table_name: Name of the table
    
    Returns:
        List of column names that form the composite key
    """
    return COMPOSITE_KEYS.get(table_name, ["_fetched_at"])


def create_composite_key(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """
    Create a composite key column for deduplication.
    
    Args:
        df: DataFrame to add composite key to
        table_name: Name of the table (determines which columns to use)
    
    Returns:
        DataFrame with added _composite_key column
    """
    key_columns = get_composite_key_columns(table_name)
    
    # Filter to only existing columns
    available_columns = [col for col in key_columns if col in df.columns]
    
    if available_columns:
        # Create composite key by concatenating column values
        df = df.copy()
        df["_composite_key"] = df[available_columns].astype(str).agg("|".join, axis=1)
    else:
        # Fallback: use _fetched_at if no key columns available
        if "_fetched_at" in df.columns:
            df = df.copy()
            df["_composite_key"] = df["_fetched_at"].astype(str)
        else:
            # Use current timestamp
            df = df.copy()
            df["_composite_key"] = datetime.now(UTC).isoformat()
    
    return df


def load_parquet_to_bronze(
    parquet_filepath: Path,
    table_name: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load a parquet file to the bronze layer using incremental merge.
    
    Args:
        parquet_filepath: Path to the parquet file
        table_name: Name of the table to load into
        db_path: Optional path to DuckDB database. Defaults to weather_forecaster.duckdb
    
    Returns:
        Dictionary with load statistics
    """
    if db_path is None:
        db_path = get_duckdb_path()
    
    # Read parquet file
    df = read_parquet_file(parquet_filepath)
    
    if df.empty:
        return {"status": "skipped", "reason": "empty dataframe", "rows": 0}
    
    # Add composite key for deduplication
    df = create_composite_key(df, table_name)
    
    # Connect to DuckDB
    conn = duckdb.connect(str(db_path))
    
    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS staging")

        # Check if table exists
        table_exists = conn.execute(f"""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'staging' AND table_name = '{table_name}'
        """).fetchone()[0] > 0

        if table_exists:
            # Get existing records with their _fetched_at timestamps
            existing_records = conn.execute(f"""
                SELECT _composite_key, _fetched_at FROM staging.{table_name}
            """).fetchall()

            # Build a dict of composite_key -> most recent _fetched_at
            existing_keys: Dict[str, str] = {r[0]: r[1] for r in existing_records}

            # For each record in new_df, check if we need to update or insert
            records_to_insert = []
            records_to_update = 0

            for idx, row in df.iterrows():
                composite_key = row["_composite_key"]
                new_fetched_at = row["_fetched_at"]

                if composite_key in existing_keys:
                    # Check if new record is more recent
                    existing_fetched_at = existing_keys[composite_key]
                    if new_fetched_at > existing_fetched_at:
                        # Update existing record
                        records_to_update += 1
                        # Delete old record
                        conn.execute(f"""
                            DELETE FROM staging.{table_name} WHERE _composite_key = ?
                        """, [composite_key])
                        records_to_insert.append(row)
                else:
                    # New record
                    records_to_insert.append(row)

            if not records_to_insert and records_to_update == 0:
                conn.close()
                return {
                    "status": "skipped",
                    "reason": "no new or updated records",
                    "rows": 0,
                    "duplicates": len(df)
                }

            # Insert new/updated records
            if records_to_insert:
                insert_df = pd.DataFrame(records_to_insert)
                conn.execute(f"""
                    INSERT INTO staging.{table_name}
                    SELECT * FROM insert_df
                """)

            conn.close()
            return {
                "status": "loaded",
                "rows": len(records_to_insert),
                "updated": records_to_update,
                "duplicates": len(df) - len(records_to_insert) - records_to_update
            }
        else:
            # Create new table
            conn.execute(f"""
                CREATE TABLE staging.{table_name} AS SELECT * FROM df
            """)
            
            conn.close()
            return {
                "status": "created",
                "rows": len(df),
                "duplicates": 0
            }
    
    except Exception as e:
        conn.close()
        raise Exception(f"Error loading {table_name}: {e}")


def load_parquet_to_bronze_for_full_reload(
    parquet_filepath: Path,
    table_name: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load a parquet file to the bronze layer for full reload mode.
    
    This is simpler than incremental - just insert all records since
    tables are already truncated.
    
    Args:
        parquet_filepath: Path to the parquet file
        table_name: Name of the table to load into
        db_path: Optional path to DuckDB database. Defaults to weather_forecaster.duckdb
    
    Returns:
        Dictionary with load statistics
    """
    if db_path is None:
        db_path = get_duckdb_path()
    
    # Read parquet file
    df = read_parquet_file(parquet_filepath)
    
    if df.empty:
        return {"status": "skipped", "reason": "empty dataframe", "rows": 0}
    
    # Add composite key
    df = create_composite_key(df, table_name)
    
    # Connect to DuckDB
    conn = duckdb.connect(str(db_path))
    
    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS staging")

        # Check if table exists
        table_exists = conn.execute(f"""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'staging' AND table_name = '{table_name}'
        """).fetchone()[0] > 0

        if table_exists:
            # Just append all records (table was truncated)
            conn.execute(f"""
                INSERT INTO staging.{table_name}
                SELECT * FROM df
            """)
            conn.close()
            return {
                "status": "loaded",
                "rows": len(df),
                "duplicates": 0
            }
        else:
            # Create new table
            conn.execute(f"""
                CREATE TABLE staging.{table_name} AS SELECT * FROM df
            """)
            conn.close()
            return {
                "status": "created",
                "rows": len(df),
                "duplicates": 0
            }
    
    except Exception as e:
        conn.close()
        raise Exception(f"Error loading {table_name}: {e}")


def get_source_from_filename(filename: str) -> Optional[str]:
    """
    Extract source type from parquet filename.
    
    Args:
        filename: Parquet filename (without extension)
    
    Returns:
        Source type or None if unknown
    """
    if "current_weather" in filename:
        return "current_weather"
    elif "weather_forecast" in filename:
        return "weather_forecast"
    elif "geocoding" in filename and "reverse" not in filename:
        return "geocoding"
    elif "reverse_geocoding" in filename:
        return "reverse_geocoding"
    return None


def get_loaded_files(db_path: Path) -> set:
    """
    Get set of already loaded parquet filenames from metadata table.
    
    Args:
        db_path: Path to DuckDB database
    
    Returns:
        Set of loaded filename stems (format: folder/filename)
    """
    if not db_path.exists():
        return set()
    
    conn = duckdb.connect(str(db_path))
    try:
        # Check if _load_metadata table exists using DuckDB syntax
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'staging' AND table_name = '_load_metadata'"
        ).fetchall()

        if tables:
            loaded = conn.execute("SELECT folder_name, filename FROM staging._load_metadata").fetchall()
            # Return as "folder/filename" format for uniqueness
            return {f"{r[0]}/{r[1]}" for r in loaded}
    except:
        pass
    finally:
        conn.close()
    
    return set()


def mark_file_loaded(db_path: Path, folder_name: str, filename: str) -> None:
    """
    Mark a parquet file as loaded in metadata table.
    
    Args:
        db_path: Path to DuckDB database
        folder_name: Name of the timestamped folder (e.g., "20260326_082633")
        filename: Name of the parquet file (e.g., "current_weather.parquet")
    """
    conn = duckdb.connect(str(db_path))
    try:
        # Create metadata table if not exists - track both folder and filename
        conn.execute("""
            CREATE TABLE IF NOT EXISTS staging._load_metadata (
                folder_name VARCHAR NOT NULL,
                filename VARCHAR NOT NULL,
                loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (folder_name, filename)
            )
        """)

        # Insert filename with folder context
        conn.execute(
            "INSERT OR IGNORE INTO staging._load_metadata (folder_name, filename) VALUES (?, ?)",
            [folder_name, filename]
        )
    finally:
        conn.close()


def load_all_to_bronze(
    data_zone_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    load_mode: str = LoadMode.INCREMENTAL,
) -> Dict[str, Dict[str, Any]]:
    """
    Load parquet files from data_zone to bronze layer with configurable load modes.
    
    Two modes available:
    1. FULL_RELOAD: Truncate all tables, load ALL files from ALL folders
    2. INCREMENTAL: Load only the latest folder, no truncation
    
    Args:
        data_zone_path: Path to data_zone folder. Defaults to project data_zone
        db_path: Optional path to DuckDB database
        load_mode: Either LoadMode.FULL_RELOAD or LoadMode.INCREMENTAL
    
    Returns:
        Dictionary with load statistics
    """
    if data_zone_path is None:
        data_zone_path = DATA_ZONE_PATH
    
    if db_path is None:
        db_path = get_duckdb_path()
    
    # Ensure bronze path exists
    get_bronze_path()
    
    # Get folders based on mode
    if load_mode == LoadMode.FULL_RELOAD:
        folders = list_load_folders()
        print(f"FULL RELOAD MODE: Loading {len(folders)} folders")
        
        # Truncate all data tables
        print("Truncating all tables...")
        truncate_all_tables(db_path)
        
        # Clear load metadata (optional - keeps track of what's been loaded)
        # We'll track by folder instead
        
    elif load_mode == LoadMode.INCREMENTAL:
        latest_folder = get_latest_load_folder()
        if latest_folder is None:
            return {"status": "no_folders", "message": "No load folders found in data_zone"}
        
        folders = [latest_folder]
        print(f"INCREMENTAL MODE: Loading latest folder: {latest_folder.name}")
    else:
        return {"status": "error", "message": f"Unknown load mode: {load_mode}"}
    
    if not folders:
        return {"status": "no_folders", "message": "No folders to load"}
    
    # Collect all parquet files from all folders
    all_parquet_files = []
    for folder in folders:
        files = get_all_parquet_files_in_folder(folder)
        print(f"  Found {len(files)} files in {folder.name}")
        all_parquet_files.extend(files)
    
    if not all_parquet_files:
        return {"status": "no_files", "message": "No parquet files found in folders"}
    
    print(f"Total files to load: {len(all_parquet_files)}")
    
    # Get already loaded files for incremental mode to skip duplicates
    already_loaded = set()
    if load_mode == LoadMode.INCREMENTAL:
        already_loaded = get_loaded_files(db_path)
        print(f"Already loaded: {len(already_loaded)} files")
    
    results = {}
    
    for filepath in all_parquet_files:
        # Extract table name from filename (without path)
        filename = filepath.stem
        
        # Check if this specific file (folder+filename) has already been loaded
        loaded_key = f"{filepath.parent.name}/{filename}"
        if load_mode == LoadMode.INCREMENTAL and loaded_key in already_loaded:
            print(f"Skipping already loaded: {filepath.parent.name}/{filename}")
            continue
        
        # Use helper function to get source type
        table_name = get_source_from_filename(filename)
        if table_name is None:
            print(f"  Skipping unknown file: {filename}")
            continue
        
        print(f"Loading {table_name} from {filepath.parent.name}/{filepath.name}...")
        
        try:
            # For full reload, we don't track loaded files - just load everything
            if load_mode == LoadMode.FULL_RELOAD:
                result = load_parquet_to_bronze_for_full_reload(filepath, table_name, db_path)
            else:
                result = load_parquet_to_bronze(filepath, table_name, db_path)
            
            results[table_name] = result
            print(f"  Status: {result['status']}, Rows: {result.get('rows', 0)}")
            
            # Mark file as loaded for incremental mode
            if load_mode == LoadMode.INCREMENTAL:
                if result.get('rows', 0) > 0 or result['status'] in ('created', 'loaded'):
                    # Pass folder_name and filename separately
                    mark_file_loaded(db_path, filepath.parent.name, filename)
                    print(f"  Marked as loaded: {filepath.parent.name}/{filename}")
        except Exception as e:
            print(f"  Error: {e}")
            results[table_name] = {"status": "error", "error": str(e)}
    
    return results


def load_capitals_to_staging(
    json_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Load world capitals reference data from JSON into staging.world_capitals.

    Creates the table if it does not exist; replaces all rows on every call
    so the reference data stays in sync with the JSON file.

    Args:
        json_path: Path to world_capitals.json. Defaults to data/world_capitals.json.
        db_path: Path to DuckDB database. Defaults to weather_forecaster.duckdb.

    Returns:
        Dict with status and row count.
    """
    if json_path is None:
        json_path = PROJECT_ROOT / "data" / "world_capitals.json"
    if db_path is None:
        db_path = get_duckdb_path()

    if not json_path.exists():
        return {"status": "error", "error": f"JSON not found: {json_path}"}

    with open(json_path, "r", encoding="utf-8") as fh:
        capitals = json.load(fh)

    df = pd.DataFrame(capitals)
    # Ensure correct types
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)

    get_bronze_path()
    conn = duckdb.connect(str(db_path))
    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS staging")
        conn.execute("DROP TABLE IF EXISTS staging.world_capitals")
        conn.execute("""
            CREATE TABLE staging.world_capitals AS
            SELECT
                city,
                country,
                country_code,
                CAST(lat AS DOUBLE) AS lat,
                CAST(lon AS DOUBLE) AS lon
            FROM df
        """)
        row_count = conn.execute("SELECT COUNT(*) FROM staging.world_capitals").fetchone()[0]
    finally:
        conn.close()

    return {"status": "loaded", "rows": row_count}


def get_bronze_table_stats(db_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """
    Get statistics for all bronze layer tables.
    
    Args:
        db_path: Optional path to DuckDB database
    
    Returns:
        Dictionary with table statistics
    """
    if db_path is None:
        db_path = get_duckdb_path()
    
    if not db_path.exists():
        return {}
    
    conn = duckdb.connect(str(db_path))
    
    try:
        # Get all tables
        tables = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'staging'
        """).fetchall()
        tables = [t[0] for t in tables]

        stats = {}
        for table in tables:
            row_count = conn.execute(f"SELECT COUNT(*) FROM staging.{table}").fetchone()[0]
            columns = conn.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema = 'staging' AND table_name = '{table}'").fetchall()
            columns = [c[0] for c in columns]
            
            stats[table] = {
                "row_count": row_count,
                "columns": columns
            }
        
        conn.close()
        return stats
    
    except Exception as e:
        conn.close()
        return {}


if __name__ == "__main__":
    print("="*50)
    print("Loading Data to Bronze Layer")
    print("="*50)
    print(f"Data Zone: {DATA_ZONE_PATH}")
    print(f"Bronze Database: {get_duckdb_path()}")
    print()
    
    # Load all parquet files to bronze
    results = load_all_to_bronze()
    
    print()
    print("="*50)
    print("Bronze Layer Statistics")
    print("="*50)
    
    stats = get_bronze_table_stats()
    for table, stat in stats.items():
        print(f"\n{table}:")
        print(f"  Rows: {stat['row_count']}")
        print(f"  Columns: {stat['columns']}")
