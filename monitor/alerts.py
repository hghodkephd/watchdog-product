

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alert checking and notification system for Govee Monitor
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from storage import get_disk_usage

import pandas as pd

from config import AppConfig, AlertConfig


@dataclass
class Alert:
    sensor_id: str
    sensor_name: str
    alert_type: str  # "low_battery", "offline", "temp_high", "temp_low"
    message: str
    severity: str  # "warning", "critical"
    timestamp: float


def check_alerts(df_latest: pd.DataFrame, cfg: AppConfig) -> List[Alert]:
    """
    Check for alert conditions based on latest sensor readings.
    
    Returns list of active alerts.
    """
    if not cfg.alerts.enabled:
        return []

    alerts: List[Alert] = []
    now = time.time()

    # === NEW: Check disk space first ===
    disk_usage = get_disk_usage()
    free_mb = disk_usage["free_gb"] * 1024  # Convert to MB

    if free_mb < cfg.alerts.disk_critical_mb:
        alerts.append(Alert(
            sensor_id="SYSTEM",
            sensor_name="System",
            alert_type="low_disk",
            message=f"Critical: Only {free_mb:.0f} MB disk space remaining!",
            severity="critical",
            timestamp=now,
        ))
    elif free_mb < cfg.alerts.disk_warning_mb:
        alerts.append(Alert(
            sensor_id="SYSTEM",
            sensor_name="System",
            alert_type="low_disk",
            message=f"Low disk space: {free_mb:.0f} MB remaining",
            severity="warning",
            timestamp=now,
        ))

    # If no sensor data, return disk alerts (if any)
    if df_latest.empty:
        return alerts
    
    for _, row in df_latest.iterrows():
        sensor_id = row["sensor_id"]
        sensor_name = row["name"]
        
        # Check low battery
        if row["battery"] is not None and row["battery"] < cfg.alerts.low_battery_threshold:
            alerts.append(Alert(
                sensor_id=sensor_id,
                sensor_name=sensor_name,
                alert_type="low_battery",
                message=f"Low battery: {row['battery']}%",
                severity="warning",
                timestamp=now,
            ))
        
        # Check if sensor is offline (stale data)
        ts_val = row.get("timestamp")
        if ts_val is None or pd.isna(ts_val):
            reading_age_sec = float("inf")
        else:
            # ts_val may be:
            # - float/int epoch seconds (SQLite MAX(ts))
            # - pandas Timestamp / datetime (if converted elsewhere)
            if hasattr(ts_val, "timestamp"):
                ts_epoch = float(ts_val.timestamp())
            else:
                ts_epoch = float(ts_val)
            reading_age_sec = now - ts_epoch
        offline_threshold_sec = cfg.alerts.sensor_offline_minutes * 60
        
        if reading_age_sec > offline_threshold_sec:
            minutes_ago = int(reading_age_sec / 60)
            alerts.append(Alert(
                sensor_id=sensor_id,
                sensor_name=sensor_name,
                alert_type="offline",
                message=f"No data for {minutes_ago} minutes",
                severity="critical",
                timestamp=now,
            ))
        
        # Check temperature thresholds (if configured for this sensor)
        if cfg.alerts.temp_alerts_enabled and sensor_id in cfg.sensors:
            sensor_cfg = cfg.sensors[sensor_id]
            temp_c = row["temp_c"]
            
            if sensor_cfg.min_temp_c is not None and temp_c < sensor_cfg.min_temp_c:
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                min_f = sensor_cfg.min_temp_c * 9.0 / 5.0 + 32.0
                unit = "°F" if cfg.units.upper() == "F" else "°C"
                temp_display = temp_f if cfg.units.upper() == "F" else temp_c
                min_display = min_f if cfg.units.upper() == "F" else sensor_cfg.min_temp_c
                
                alerts.append(Alert(
                    sensor_id=sensor_id,
                    sensor_name=sensor_name,
                    alert_type="temp_low",
                    message=f"Temperature too low: {temp_display:.1f}{unit} (min: {min_display:.1f}{unit})",
                    severity="warning",
                    timestamp=now,
                ))
            
            if sensor_cfg.max_temp_c is not None and temp_c > sensor_cfg.max_temp_c:
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                max_f = sensor_cfg.max_temp_c * 9.0 / 5.0 + 32.0
                unit = "°F" if cfg.units.upper() == "F" else "°C"
                temp_display = temp_f if cfg.units.upper() == "F" else temp_c
                max_display = max_f if cfg.units.upper() == "F" else sensor_cfg.max_temp_c
                
                alerts.append(Alert(
                    sensor_id=sensor_id,
                    sensor_name=sensor_name,
                    alert_type="temp_high",
                    message=f"Temperature too high: {temp_display:.1f}{unit} (max: {max_display:.1f}{unit})",
                    severity="warning",
                    timestamp=now,
                ))
    
    return alerts


def format_alert_display(alert: Alert) -> str:
    """Format alert for display in UI."""
    icon = "⚠️" if alert.severity == "warning" else "🚨"
    return f"{icon} **{alert.sensor_name}**: {alert.message}"