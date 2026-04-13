"""
Historical weather extraction using the Open-Meteo Archive API.

Open-Meteo is free for non-commercial use, requires no API key, and provides
daily weather data back to 1940 (ERA5 reanalysis from Copernicus/ECMWF).

Architecture:
    Open-Meteo Archive API → monthly aggregates (Python) → DuckDB bronze table

Usage:
    from weather_forecaster_sources.historical_extraction import (
        fetch_monthly_history,
        fetch_all_capitals_history,
    )
"""
import time
from datetime import date, timedelta
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Daily variables we request — aggregated to monthly averages in Python.
DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "relative_humidity_2m_mean",
    "wind_speed_10m_mean",
    "cloud_cover_mean",
    "precipitation_sum",
]


class _RateLimitError(Exception):
    """Raised on HTTP 429 — should not be retried."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException,)),
    reraise=True,
)
def _fetch_daily(
    lat: float,
    lon: float,
    start: date,
    end: date,
    timeout: int = 60,
) -> dict[str, Any]:
    """Fetch daily weather data from Open-Meteo archive API for a date range."""
    params = {
        "latitude":  lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "daily":      ",".join(DAILY_VARS),
        "timezone":   "UTC",
    }
    resp = requests.get(ARCHIVE_URL, params=params, timeout=timeout)
    if resp.status_code == 429:
        raise _RateLimitError(resp.text)
    resp.raise_for_status()
    return resp.json()


def _aggregate_to_monthly(
    daily: dict[str, Any],
    city: str,
    country: str,
    country_code: str,
) -> list[dict[str, Any]]:
    """
    Aggregate daily Open-Meteo response to one row per (city, year, month).

    Returns a list of dicts — one per calendar month present in the response.
    """
    times = daily.get("daily", {}).get("time", [])
    if not times:
        return []

    # Pull all daily series
    t_max  = daily["daily"].get("temperature_2m_max",       [None] * len(times))
    t_min  = daily["daily"].get("temperature_2m_min",       [None] * len(times))
    t_mean = daily["daily"].get("temperature_2m_mean",      [None] * len(times))
    rh     = daily["daily"].get("relative_humidity_2m_mean",[None] * len(times))
    wind   = daily["daily"].get("wind_speed_10m_mean",      [None] * len(times))
    cloud  = daily["daily"].get("cloud_cover_mean",         [None] * len(times))
    precip = daily["daily"].get("precipitation_sum",        [None] * len(times))

    # Bucket by (year, month)
    buckets: dict[tuple[int, int], dict[str, list]] = {}
    for i, t_str in enumerate(times):
        d = date.fromisoformat(t_str)
        key = (d.year, d.month)
        if key not in buckets:
            buckets[key] = {k: [] for k in
                            ["t_max", "t_min", "t_mean", "rh", "wind", "cloud", "precip"]}
        b = buckets[key]
        if t_max[i]  is not None: b["t_max"].append(t_max[i])
        if t_min[i]  is not None: b["t_min"].append(t_min[i])
        if t_mean[i] is not None: b["t_mean"].append(t_mean[i])
        if rh[i]     is not None: b["rh"].append(rh[i])
        if wind[i]   is not None: b["wind"].append(wind[i])
        if cloud[i]  is not None: b["cloud"].append(cloud[i])
        if precip[i] is not None: b["precip"].append(precip[i])

    def _avg(lst: list) -> float | None:
        return round(sum(lst) / len(lst), 2) if lst else None

    rows = []
    for (year, month), b in sorted(buckets.items()):
        rows.append({
            "city":               city,
            "country":            country,
            "country_code":       country_code,
            "lat":                daily.get("latitude"),
            "lon":                daily.get("longitude"),
            "year":               year,
            "month":              month,
            "avg_temp_c":         _avg(b["t_mean"]),
            "min_temp_c":         _avg(b["t_min"]),
            "max_temp_c":         _avg(b["t_max"]),
            "avg_humidity_pct":   _avg(b["rh"]),
            "avg_wind_speed_ms":  _avg(b["wind"]),
            "avg_cloud_cover_pct":_avg(b["cloud"]),
            "total_precip_mm":    round(sum(b["precip"]), 1) if b["precip"] else None,
            "observation_count":  len(b["t_mean"]),
            "source":             "open-meteo-archive",
        })
    return rows


def fetch_monthly_history(
    lat: float,
    lon: float,
    city: str,
    country: str,
    country_code: str,
    start_year: int = 2020,
    end_date: date | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch and aggregate monthly historical weather for one capital.

    Uses a single API request covering the full date range — one request per
    city keeps total API usage well within Open-Meteo's free-tier limits.

    Args:
        lat / lon:          Coordinates.
        city / country / country_code: Metadata attached to each row.
        start_year:         First year to fetch (default: 2020).
        end_date:           Last date to fetch (default: yesterday).
    """
    if end_date is None:
        end_date = date.today() - timedelta(days=1)

    start = date(start_year, 1, 1)
    if start > end_date:
        return []

    daily = _fetch_daily(lat, lon, start, end_date)
    return _aggregate_to_monthly(daily, city, country, country_code)


def fetch_all_capitals_history(
    capitals: list[dict],
    start_year: int = 2020,
    inter_city_delay_s: float = 1.0,
    progress_cb=None,
    rate_limit_wait_s: float = 60.0,
    rate_limit_max_retries: int = 3,
) -> list[dict[str, Any]]:
    """
    Fetch monthly historical weather for a list of capitals.

    One API request per city (full date range). On HTTP 429, waits
    ``rate_limit_wait_s`` seconds and retries up to ``rate_limit_max_retries``
    times before skipping the city and continuing.

    Args:
        capitals:               List of dicts with keys city, country, country_code, lat, lon.
        start_year:             First year to backfill.
        inter_city_delay_s:     Pause between cities (default 1 s — polite to free API).
        progress_cb:            Optional callable(i, total, city, row_count) for logging.
        rate_limit_wait_s:      Seconds to wait after a 429 before retrying (default 60).
        rate_limit_max_retries: Max retries per city on 429 before skipping (default 3).

    Returns:
        Flat list of monthly-aggregate dicts for all capitals.
    """
    all_rows: list[dict[str, Any]] = []

    for i, cap in enumerate(capitals):
        city         = cap["city"]
        country      = cap["country"]
        country_code = cap["country_code"]
        lat          = cap["lat"]
        lon          = cap["lon"]

        rows: list[dict[str, Any]] = []
        for attempt in range(rate_limit_max_retries + 1):
            try:
                rows = fetch_monthly_history(
                    lat=lat, lon=lon,
                    city=city, country=country, country_code=country_code,
                    start_year=start_year,
                )
                break  # success
            except _RateLimitError:
                if attempt < rate_limit_max_retries:
                    wait = rate_limit_wait_s * (attempt + 1)
                    print(
                        f"    Rate limit at {city} ({i+1}/{len(capitals)}), "
                        f"attempt {attempt+1}/{rate_limit_max_retries} — "
                        f"waiting {wait:.0f}s before retry.",
                        flush=True,
                    )
                    time.sleep(wait)
                else:
                    print(
                        f"    Rate limit at {city} ({i+1}/{len(capitals)}) — "
                        f"skipping after {rate_limit_max_retries} retries.",
                        flush=True,
                    )
            except Exception as exc:
                print(f"    Warning: {city}: {exc}", flush=True)
                break

        all_rows.extend(rows)

        if progress_cb:
            progress_cb(i, len(capitals), city, len(rows))

        if i < len(capitals) - 1:
            time.sleep(inter_city_delay_s)

    return all_rows
