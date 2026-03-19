#!/usr/bin/env python
"""
DuckDB Query Tool
Usage: python query_duckdb.py

This opens an interactive DuckDB shell to query your weather data.
Database: prototype/openweather_ingestion.duckdb
"""
import duckdb

def main():
    db_path = "prototype/openweather_ingestion.duckdb"
    print(f"Connecting to {db_path}...")
    print("Tables available:")
    con = duckdb.connect(db_path)
    
    tables = con.sql("SHOW TABLES FROM bronze").fetchall()
    for t in tables:
        print(f"  - bronze.{t[0]}")
    
    print("\n" + "="*50)
    print("Interactive DuckDB Shell")
    print("="*50)
    print("Example queries:")
    print("  SELECT * FROM bronze.current_weather;")
    print("  SELECT name, main__temp FROM bronze.current_weather;")
    print("  .quit to exit")
    print("="*50 + "\n")
    
    while True:
        try:
            query = input("duckdb> ")
            if query.strip() == ".quit":
                break
            if query.strip():
                result = con.sql(query)
                result.show()
        except Exception as e:
            print(f"Error: {e}")
        except KeyboardInterrupt:
            break
    
    con.close()
    print("Goodbye!")

if __name__ == "__main__":
    main()
