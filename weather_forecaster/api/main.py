"""
Weather Forecaster — FastAPI data layer.

Exposes three read-only endpoints over the DuckDB database produced by the
Dagster pipeline and dbt transformation layer.

Endpoints:
    GET /api/current   — latest observation + 24 h forecast summary
                         (gold.gold_weather_summary, one row per location)
    GET /api/forecast  — 5-day/3-hour forecast intervals
                         (silver.silver_forecast_intervals)
    GET /api/history   — last 48 h of weather observations
                         (silver.silver_weather_observations)
    GET /health        — liveness probe for Docker / ECS

Environment variables:
    DUCKDB_PATH  — absolute path to weather_forecaster.duckdb
                   default: /app/data/etl/weather_forecaster.duckdb
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ── Configuration ──────────────────────────────────────────────────────────────
DUCKDB_PATH = Path(
    os.getenv("DUCKDB_PATH", "/app/data/etl/weather_forecaster.duckdb")
)


def get_conn() -> duckdb.DuckDBPyConnection:
    """Open a short-lived read-only connection to DuckDB."""
    if not DUCKDB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Database not found at {DUCKDB_PATH}. Run the pipeline first.",
        )
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def query(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """Execute a SQL query and return rows as a list of dicts."""
    conn = get_conn()
    try:
        rel = conn.execute(sql, params or [])
        columns = [desc[0] for desc in rel.description]
        rows = rel.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


# ── App ────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate DB path on startup so errors surface immediately.
    if not DUCKDB_PATH.exists():
        print(f"WARNING: DuckDB not found at {DUCKDB_PATH}. Endpoints will return 503.")
    else:
        print(f"DuckDB connected (read-only): {DUCKDB_PATH}")
    yield


app = FastAPI(
    title="Weather Forecaster API",
    description="Read-only REST API over the DuckDB weather pipeline.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to CloudFront domain in production
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok", "db": str(DUCKDB_PATH), "db_exists": DUCKDB_PATH.exists()}


@app.get("/api/current")
def current_weather() -> list[dict[str, Any]]:
    """
    Latest weather observation + 24 h forecast summary per location.
    Source: gold.gold_weather_summary (one row per lat/lon).
    """
    sql = """
        SELECT
            city_name,
            country_code,
            state,
            lat,
            lon,
            observed_at,
            current_temp_c,
            feels_like_c,
            temp_min_c,
            temp_max_c,
            humidity_pct,
            pressure_hpa,
            visibility_m,
            wind_speed_ms,
            wind_direction_deg,
            cloud_cover_pct,
            current_cloud_description,
            current_wind_description,
            sunrise_at,
            sunset_at,
            avg_temp_c_24h,
            max_temp_c_24h,
            min_temp_c_24h,
            avg_wind_speed_ms_24h,
            avg_cloud_cover_pct_24h,
            predominant_cloud_description,
            predominant_wind_description,
            forecast_window_start,
            forecast_window_end
        FROM gold.gold_weather_summary
        ORDER BY city_name
    """
    return query(sql)


@app.get("/api/forecast")
def forecast(hours: int = 120) -> list[dict[str, Any]]:
    """
    3-hour forecast intervals for the next N hours (default 120 = 5 days).
    Source: silver.silver_forecast_intervals.

    Query params:
        hours  — forecast horizon in hours (max 120)
    """
    hours = min(hours, 120)
    sql = """
        SELECT
            lat,
            lon,
            forecast_at,
            hours_from_now,
            temp_c,
            feels_like_c,
            humidity_pct,
            wind_speed_ms,
            wind_direction_deg,
            cloud_cover_pct,
            cloud_description,
            wind_description
        FROM silver.silver_forecast_intervals
        WHERE hours_from_now BETWEEN 0 AND ?
        ORDER BY lat, lon, forecast_at
    """
    return query(sql, [hours])


@app.get("/api/history")
def history(hours: int = 48) -> list[dict[str, Any]]:
    """
    Weather observations for the last N hours (default 48).
    Source: silver.silver_weather_observations.

    Query params:
        hours  — look-back window in hours (max 168 = 7 days)
    """
    hours = min(hours, 168)
    sql = """
        SELECT
            city_name,
            country_code,
            lat,
            lon,
            observed_at,
            temp_c,
            feels_like_c,
            humidity_pct,
            pressure_hpa,
            wind_speed_ms,
            wind_direction_deg,
            cloud_cover_pct,
            cloud_description,
            wind_description
        FROM silver.silver_weather_observations
        WHERE observed_at >= NOW() - INTERVAL (? || ' hours')
        ORDER BY lat, lon, observed_at DESC
    """
    return query(sql, [hours])
