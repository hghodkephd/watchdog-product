#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog Environmental Monitor - Weather API
Fetches weather data from Open-Meteo (free, no API key required)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
import requests


# ---------------------------------------------------------------------
# Simple container for a geo point with timezone
# ---------------------------------------------------------------------

@dataclass
class WeatherPoint:
    latitude: float
    longitude: float
    label: str          # e.g. "Hopkinton, Massachusetts, United States"
    timezone: str       # IANA timezone, e.g. "America/New_York"


# ---------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------

def _request_with_retry(
    url: str,
    params: dict,
    max_retries: int = 3,
    base_timeout: int = 10,
) -> requests.Response:
    """
    Make HTTP GET request with exponential backoff retry.
    
    Args:
        url: Request URL
        params: Query parameters
        max_retries: Maximum number of attempts
        base_timeout: Initial timeout in seconds
    
    Returns:
        Response object
    
    Raises:
        requests.RequestException: If all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            # Increase timeout with each retry
            timeout = base_timeout + (attempt * 5)
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout as e:
            last_exception = e
            if attempt < max_retries - 1:
                # Exponential backoff: 1s, 2s, 4s
                wait_time = 2 ** attempt
                time.sleep(wait_time)
        except requests.exceptions.RequestException as e:
            last_exception = e
            break  # Don't retry on non-timeout errors
    
    raise last_exception


# ---------------------------------------------------------------------
# ZIP → lat/lon via Open-Meteo geocoding API
# ---------------------------------------------------------------------

def geocode_zip(zipcode: str, country_code: str = "US") -> WeatherPoint:
    """
    Resolve a ZIP code to lat/lon and timezone using Open-Meteo's geocoding API.

    We first try the bare ZIP (e.g. "01748"), and if that fails we try
    "01748, US". If both fail, we raise a ValueError.
    
    Returns:
        WeatherPoint with latitude, longitude, label, and timezone
    """
    z = zipcode.strip()
    if not z:
        raise ValueError("Empty ZIP code")

    base_url = "https://geocoding-api.open-meteo.com/v1/search"

    # Try a couple of query variants to be robust
    query_variants = [z, f"{z}, {country_code}"]

    last_error: Optional[str] = None

    for name_query in query_variants:
        params = {
            "name": name_query,
            "count": 1,
            "language": "en",
            "format": "json",
        }
        try:
            resp = _request_with_retry(base_url, params, max_retries=2, base_timeout=10)
            data = resp.json()
        except Exception as e:
            last_error = f"HTTP error for '{name_query}': {e}"
            continue

        results = data.get("results") or []
        if not results:
            last_error = f"No geocoding results for '{name_query}'"
            continue

        r0 = results[0]
        lat = r0["latitude"]
        lon = r0["longitude"]
        
        # Extract timezone (Open-Meteo provides IANA timezone)
        timezone = r0.get("timezone", "America/New_York")

        label_parts = [
            r0.get("name"),
            r0.get("admin1"),
            r0.get("country"),
        ]
        label = ", ".join(p for p in label_parts if p)

        return WeatherPoint(
            latitude=lat, 
            longitude=lon, 
            label=label,
            timezone=timezone
        )

    # If we got here, both attempts failed
    if last_error is None:
        last_error = "Unknown geocoding error"
    raise ValueError(f"No location found for ZIP '{zipcode}': {last_error}")


# ---------------------------------------------------------------------
# Current weather from Open-Meteo
# ---------------------------------------------------------------------

def fetch_current_weather(lat: float, lon: float) -> dict:
    """
    Fetch current temperature, humidity, and wind speed at this lat/lon.
    
    Returns:
        dict with: time, temp_c, humidity, wind_mph
        Returns None values on failure (graceful degradation)
    """
    base_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "windspeed_unit": "mph",
        "timezone": "auto",
    }

    try:
        resp = _request_with_retry(base_url, params, max_retries=3, base_timeout=10)
        data = resp.json()

        cur = data.get("current") or {}
        return {
            "time": cur.get("time"),
            "temp_c": cur.get("temperature_2m"),
            "humidity": cur.get("relative_humidity_2m"),
            "wind_mph": cur.get("wind_speed_10m"),
            "available": True,
        }
    except Exception as e:
        # Return empty data on failure - graceful degradation
        return {
            "time": None,
            "temp_c": None,
            "humidity": None,
            "wind_mph": None,
            "available": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------
# Historical-ish series (up to a few days back) from Open-Meteo
# ---------------------------------------------------------------------

def fetch_weather_series(
    lat: float,
    lon: float,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    """
    Fetch an hourly time series between start and end (inclusive) for:
    - temperature_2m (°C)
    - relative_humidity_2m (%)
    - wind_speed_10m (mph)

    NOTE: Uses the forecast endpoint with start_date/end_date ONLY.
    We deliberately DO NOT pass 'past_days' to avoid 400 errors.
    
    Returns:
        DataFrame with columns: timestamp, wx_temp_c, wx_humidity, wx_wind_mph
        Returns None on failure
    """
    base_url = "https://api.open-meteo.com/v1/forecast"

    start_date = start.date().isoformat()
    end_date = end.date().isoformat()

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "windspeed_unit": "mph",
        "timezone": "auto",
        "start_date": start_date,
        "end_date": end_date,
    }

    try:
        resp = _request_with_retry(base_url, params, max_retries=2, base_timeout=15)
        data = resp.json()

        hourly = data.get("hourly") or {}
        times = hourly.get("time")
        temps = hourly.get("temperature_2m")
        hums = hourly.get("relative_humidity_2m")
        winds = hourly.get("wind_speed_10m")

        if not times:
            return None

        df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(times),
                "wx_temp_c": temps,
                "wx_humidity": hums,
                "wx_wind_mph": winds,
            }
        )

        return df
    except Exception:
        return None


# ---------------------------------------------------------------------
# Small manual test helper (optional)
# ---------------------------------------------------------------------

if __name__ == "__main__":
    # Quick sanity check: python weather_api.py
    print("Testing geocoding...")
    wp = geocode_zip("01748")
    print(f"ZIP 01748 → {wp}")
    print(f"Timezone: {wp.timezone}")

    print("\nTesting current weather...")
    weather = fetch_current_weather(wp.latitude, wp.longitude)
    print(f"Current weather: {weather}")

    print("\nTesting weather series...")
    now = datetime.utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    df = fetch_weather_series(wp.latitude, wp.longitude, start, now)
    if df is not None:
        print(df.head())
    else:
        print("Failed to fetch series")
