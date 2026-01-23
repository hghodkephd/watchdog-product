#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog Environmental Monitor - Configuration
"""

from __future__ import annotations
import time
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, Optional

from platformdirs import user_config_dir

APP_NAME = "watchdog"
APP_AUTHOR = "watchdog-env-monitor"


# Bump this when config schema changes
CONFIG_VERSION = 1


def _log():
    """
    Lazy logger accessor to avoid import-time coupling.
    Uses logging_config.get_logger if available, otherwise stdlib logging.
    """
    try:
        from logging_config import get_logger
        return get_logger("watchdog.config")
    except Exception:
        import logging
        return logging.getLogger("watchdog.config")


# US Timezone options for dropdown
US_TIMEZONES = [
    ("America/New_York", "Eastern (ET)"),
    ("America/Chicago", "Central (CT)"),
    ("America/Denver", "Mountain (MT)"),
    ("America/Phoenix", "Mountain - Arizona (no DST)"),
    ("America/Los_Angeles", "Pacific (PT)"),
    ("America/Anchorage", "Alaska (AKT)"),
    ("Pacific/Honolulu", "Hawaii (HT)"),
]


# ------------------------
# Alert configuration
# ------------------------

@dataclass
class AlertConfig:
    enabled: bool = True
    low_battery_threshold: int = 20  # %
    sensor_offline_minutes: int = 15
    temp_alerts_enabled: bool = True
    disk_warning_mb: int = 500      # Warning when below 500 MB
    disk_critical_mb: int = 100     # Critical when below 100 MB


# ------------------------
# Email configuration
# ------------------------

@dataclass
class EmailConfig:
    """Email notification settings."""
    enabled: bool = False
    recipient: str = ""
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    sender_email: str = ""
    sender_password: str = ""
    rate_limit_minutes: int = 30
    notify_on_clear: bool = True

    def is_configured(self) -> bool:
        """Check if email is fully configured and ready to send."""
        return bool(
            self.enabled and
            self.recipient and
            self.sender_email and
            self.sender_password
        )


# ------------------------
# Archive configuration
# ------------------------

@dataclass
class ArchiveConfig:
    enabled: bool = True
    retention_days: int = 90  # Keep this many days in active DB
    auto_archive: bool = True  # Auto-archive on app startup


# ------------------------
# Sensor configuration
# ------------------------

@dataclass
class SensorConfig:
    id: str               # e.g. "GVH5075_1325"
    name: str             # friendly name, shown in UI
    min_temp_c: Optional[float] = None
    max_temp_c: Optional[float] = None


# ------------------------
# Weather configuration
# ------------------------

@dataclass
class WeatherConfig:
    zipcode: str
    latitude: float
    longitude: float
    label: str            # e.g. "Hopkinton, Massachusetts, United States"
    timezone: str = "America/New_York"  # IANA timezone identifier


# ------------------------
# Monitoring state
# ------------------------

@dataclass
class MonitoringState:
    is_running: bool = False
    last_scan_time: Optional[float] = None
    process_pid: Optional[int] = None
    watchdog_enabled: bool = True  # Auto-restart on failure
    watchdog_threshold_multiplier: float = 3.0
    expected_interval_seconds: float = 3.0


# ------------------------
# App configuration
# ------------------------

@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    units: str = "F"                 # "C" or "F"
    global_min_temp_c: float = -1.0  # 30°F
    stale_after_sec: int = 600

    sensors: Dict[str, SensorConfig] = None
    weather: Optional[WeatherConfig] = None
    alerts: AlertConfig = field(default_factory=AlertConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    monitoring: MonitoringState = field(default_factory=MonitoringState)

    # ------------------------
    # Serialization
    # ------------------------

    def to_json(self) -> str:
        def encode(obj):
            if isinstance(obj, (SensorConfig, WeatherConfig, AlertConfig, EmailConfig, ArchiveConfig, MonitoringState)):
                return asdict(obj)
            if isinstance(obj, AppConfig):
                data = asdict(obj)
                data["sensors"] = {
                    k: asdict(v) for k, v in (obj.sensors or {}).items()
                }
                data["weather"] = (
                    asdict(obj.weather) if obj.weather is not None else None
                )
                data["alerts"] = asdict(obj.alerts)
                data["email"] = asdict(obj.email)
                data["archive"] = asdict(obj.archive)
                data["monitoring"] = asdict(obj.monitoring)
                return data
            raise TypeError(f"Type {type(obj)} not serializable")

        return json.dumps(self, default=encode, indent=2)

    @staticmethod
    def from_json(text: str) -> "AppConfig":
        raw = json.loads(text)
        # Backward compat: configs may not have version yet
        raw.setdefault("version", 0)
        
        # sensors
        sensors_raw = raw.get("sensors") or {}
        sensors: Dict[str, SensorConfig] = {}
        for sid, sconf in sensors_raw.items():
            sconf = {**sconf, "id": sid}
            sensors[sid] = SensorConfig(**sconf)
        raw["sensors"] = sensors

        # weather (optional, backward compatible)
        weather_raw = raw.get("weather")
        if weather_raw:
            if "timezone" not in weather_raw:
                weather_raw["timezone"] = "America/New_York"
            raw["weather"] = WeatherConfig(**weather_raw)
        else:
            raw["weather"] = None

        # alerts (backward compatible)
        alerts_raw = raw.get("alerts")
        if alerts_raw:
            raw["alerts"] = AlertConfig(**alerts_raw)
        else:
            raw["alerts"] = AlertConfig()

        # email (backward compatible)
        email_raw = raw.get("email")
        if email_raw:
            raw["email"] = EmailConfig(**email_raw)
        else:
            raw["email"] = EmailConfig()

        # archive (backward compatible)
        archive_raw = raw.get("archive")
        if archive_raw:
            raw["archive"] = ArchiveConfig(**archive_raw)
        else:
            raw["archive"] = ArchiveConfig()

        # monitoring state (backward compatible)
        monitoring_raw = raw.get("monitoring")
        if monitoring_raw:
            raw["monitoring"] = MonitoringState(**monitoring_raw)
        else:
            raw["monitoring"] = MonitoringState()

        return AppConfig(**raw)


def validate_config(cfg: AppConfig) -> tuple[bool, list[str]]:
    """
    Validate critical config fields. Returns (is_valid, [errors...]).
    """
    errors: list[str] = []

    # units
    if getattr(cfg, "units", None) not in ("C", "F"):
        errors.append(f"units must be 'C' or 'F' (got {getattr(cfg, 'units', None)!r})")

    # stale_after_sec
    stale = getattr(cfg, "stale_after_sec", None)
    if not isinstance(stale, int) or stale <= 0:
        errors.append(f"stale_after_sec must be a positive int (got {stale!r})")

    # alerts
    alerts = getattr(cfg, "alerts", None)
    if alerts is not None:
        lb = getattr(alerts, "low_battery_threshold", None)
        if lb is not None and (not isinstance(lb, int) or not (0 <= lb <= 100)):
            errors.append(f"alerts.low_battery_threshold must be 0-100 (got {lb!r})")

        offline = getattr(alerts, "sensor_offline_minutes", None)
        if offline is not None and (not isinstance(offline, int) or offline < 0):
            errors.append(f"alerts.sensor_offline_minutes must be >= 0 (got {offline!r})")

        dw = getattr(alerts, "disk_warning_mb", None)
        dc = getattr(alerts, "disk_critical_mb", None)
        if dw is not None and (not isinstance(dw, int) or dw < 0):
            errors.append(f"alerts.disk_warning_mb must be >= 0 (got {dw!r})")
        if dc is not None and (not isinstance(dc, int) or dc < 0):
            errors.append(f"alerts.disk_critical_mb must be >= 0 (got {dc!r})")
        if (dw is not None) and (dc is not None) and isinstance(dw, int) and isinstance(dc, int):
            if dc > dw:
                errors.append("alerts.disk_critical_mb should be <= alerts.disk_warning_mb")

    return (len(errors) == 0), errors


def _migrate_config(cfg: AppConfig) -> AppConfig:
    """Migrate config forward when cfg.version < CONFIG_VERSION."""
    original_version = getattr(cfg, "version", 0)

    if getattr(cfg, "version", 0) < 1:
        cfg.version = 1

    if cfg.version != original_version:
        _log().info("Config migrated from v%s to v%s", original_version, cfg.version)

    return cfg


# ------------------------
# Config I/O
# ------------------------

def get_config_path() -> Path:
    cfg_dir = Path(user_config_dir(APP_NAME, APP_AUTHOR))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "config.json"


def load_config() -> AppConfig:
    cfg_path = get_config_path()

    if not cfg_path.exists():
        cfg = AppConfig(sensors={})
        save_config(cfg)
        return cfg

    try:
        text = cfg_path.read_text(encoding="utf-8")
        cfg = AppConfig.from_json(text)

        if cfg.sensors is None:
            cfg.sensors = {}

        if getattr(cfg, "version", 0) < CONFIG_VERSION:
            cfg = _migrate_config(cfg)
            save_config(cfg)

        is_valid, errors = validate_config(cfg)
        if not is_valid:
            _log().warning("Config validation warnings: %s", "; ".join(errors))

        return cfg

    except json.JSONDecodeError as e:
        backup_path = cfg_path.with_suffix(cfg_path.suffix + f".corrupted.{int(time.time())}")
        _log().error("Config JSON corrupted (%s). Backing up to %s", e, backup_path)
        try:
            cfg_path.rename(backup_path)
        except Exception:
            pass

        cfg = AppConfig(sensors={})
        save_config(cfg)
        return cfg

    except Exception as e:
        _log().error("Error loading config: %s", e)
        cfg = AppConfig(sensors={})
        return cfg


def save_config(cfg: AppConfig) -> None:
    cfg_path = get_config_path()

    is_valid, errors = validate_config(cfg)
    if not is_valid:
        raise ValueError("Invalid config: " + "; ".join(errors))

    tmp_path = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    try:
        tmp_path.write_text(cfg.to_json() + "\n", encoding="utf-8")
        tmp_path.replace(cfg_path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        raise
