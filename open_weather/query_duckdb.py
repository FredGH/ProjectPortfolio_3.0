"""
Query DuckDB Bronze Layer Database

This script queries the bronze layer DuckDB database to view loaded data and metadata.

Usage:
    python query_duckdb.py
"""
import duckdb
from pathlib import Path

# Database path
DB_PATH = Path(__file__).parent / "data" / "bronze" / "bronze.duckdb"


def query_database():
    """Query the bronze layer database and display results."""
    if not DB_PATH.exists():
        print(f"Database not found at: {DB_PATH}")
        return
    
    conn = duckdb.connect(str(DB_PATH))
    
    try:
        # List all tables
        tables = conn.execute("SHOW TABLES").fetchall()
        print("=" * 60)
        print("Tables in bronze database:")
        print("=" * 60)
        for table in tables:
            print(f"  {table[0]}")
        
        # Query _load_metadata
        print("\n" + "=" * 60)
        print("Load Metadata:")
        print("=" * 60)
        try:
            result = conn.execute('SELECT * FROM _load_metadata ORDER BY loaded_at DESC').fetchall()
            print(f"Total rows: {len(result)}")
            print("\nRecent loads:")
            for row in result[:10]:  # Show last 10
                print(f"  {row[0]} | {row[1]} | {row[2]}")
        except Exception as e:
            print(f"Error querying _load_metadata: {e}")
        
        # Query current_weather
        print("\n" + "=" * 60)
        print("Current Weather Data:")
        print("=" * 60)
        try:
            result = conn.execute('SELECT COUNT(*) FROM current_weather').fetchone()
            print(f"Total rows: {result[0]}")
            
            # Show sample data
            result = conn.execute('SELECT * FROM current_weather LIMIT 3').fetchall()
            if result:
                print("\nSample data:")
                for row in result:
                    print(f"  {row}")
        except Exception as e:
            print(f"Error querying current_weather: {e}")
        
        # Query weather_forecast
        print("\n" + "=" * 60)
        print("Weather Forecast Data:")
        print("=" * 60)
        try:
            result = conn.execute('SELECT COUNT(*) FROM weather_forecast').fetchone()
            print(f"Total rows: {result[0]}")
        except Exception as e:
            print(f"Error querying weather_forecast: {e}")
        
        # Query geocoding
        print("\n" + "=" * 60)
        print("Geocoding Data:")
        print("=" * 60)
        try:
            result = conn.execute('SELECT COUNT(*) FROM geocoding').fetchone()
            print(f"Total rows: {result[0]}")
        except Exception as e:
            print(f"Error querying geocoding: {e}")
        
        # Query reverse_geocoding
        print("\n" + "=" * 60)
        print("Reverse Geocoding Data:")
        print("=" * 60)
        try:
            result = conn.execute('SELECT COUNT(*) FROM reverse_geocoding').fetchone()
            print(f"Total rows: {result[0]}")
        except Exception as e:
            print(f"Error querying reverse_geocoding: {e}")
    
    finally:
        conn.close()


if __name__ == "__main__":
    query_database()
