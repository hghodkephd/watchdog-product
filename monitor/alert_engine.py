#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Background Alert Engine for Watchdog.

This module runs INSIDE the ble_watchdog daemon, NOT in the dashboard.
It processes alerts 24/7 regardless of whether anyone is viewing the UI.

Key responsibilities:
- Periodically check sensor readings against thresholds
- Manage persistent alarm state (SQLite-backed)
- Send email notifications on new/escalated/cleared alarms
- Send system health heartbeats
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Callable

from logging_config import get_logger

_log = get_logger("watchdog.alert_engine")


# =============================================================================
# ALARM SEVERITY
# =============================================================================

class Severity(Enum):
    """Alarm severity levels."""
    NONE = 0
    WARNING = 1
    CRITICAL = 2


# =============================================================================
# PERSISTENT ALARM STATE (SQLite-backed)
# =============================================================================

def init_alert_tables(conn: sqlite3.Connection) -> None:
    """Create tables for persistent alarm state and heartbeat."""
    conn.executescript("""
        -- Active and historical alarm state
        CREATE TABLE IF NOT EXISTS alarm_state (
            alarm_key TEXT PRIMARY KEY,      -- "sensor_id:alert_type"
            sensor_id TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            sensor_name TEXT,
            severity INTEGER NOT NULL DEFAULT 0,
            message TEXT,
            first_seen_ts REAL,
            last_updated_ts REAL,
            last_notified_ts REAL,
            cleared_ts REAL,
            silenced_until REAL,
            silenced_severity INTEGER
        );
        
        -- System heartbeat for health monitoring
        CREATE TABLE IF NOT EXISTS system_heartbeat (
            id INTEGER PRIMARY KEY CHECK (id = 1),  -- Single row
            last_heartbeat_ts REAL NOT NULL,
            last_reading_ts REAL,
            sensors_active INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running'
        );
        
        -- Initialize heartbeat row if not exists
        INSERT OR IGNORE INTO system_heartbeat (id, last_heartbeat_ts, status)
        VALUES (1, 0, 'starting');
        
        -- Alert notification log (for debugging and audit)
        CREATE TABLE IF NOT EXISTS alert_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alarm_key TEXT NOT NULL,
            notification_type TEXT NOT NULL,  -- 'new', 'escalated', 'cleared'
            sent_ts REAL NOT NULL,
            success INTEGER NOT NULL,
            error_message TEXT
        );
        
        CREATE INDEX IF NOT EXISTS idx_alarm_state_active 
        ON alarm_state(severity) WHERE severity > 0;
        
        CREATE INDEX IF NOT EXISTS idx_notifications_ts
        ON alert_notifications(sent_ts);
    """)
    conn.commit()


@dataclass
class AlarmRecord:
    """Represents a single alarm's persistent state."""
    alarm_key: str
    sensor_id: str
    alert_type: str
    sensor_name: str
    severity: Severity
    message: str
    first_seen_ts: Optional[float]
    last_updated_ts: Optional[float]
    last_notified_ts: Optional[float]
    cleared_ts: Optional[float]
    silenced_until: Optional[float]
    silenced_severity: Optional[Severity]

    @property
    def is_active(self) -> bool:
        return self.severity != Severity.NONE

    @property
    def is_silenced(self) -> bool:
        if self.silenced_until is None:
            return False
        if self.silenced_until == float('inf'):
            return True
        return time.time() < self.silenced_until

    @property
    def should_notify(self) -> bool:
        """Should we send a notification for this alarm?"""
        if not self.is_active:
            return False
        if self.is_silenced:
            # Check if escalated past silenced severity
            if self.silenced_severity and self.severity.value > self.silenced_severity.value:
                return True
            return False
        return True

    @property
    def duration_seconds(self) -> float:
        if not self.first_seen_ts:
            return 0.0
        return time.time() - self.first_seen_ts


class PersistentAlarmStore:
    """
    SQLite-backed alarm state storage.
    
    Thread-safe. Used by both the daemon (writes) and dashboard (reads).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._local = threading.local()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=10.0
            )
            self._local.conn.row_factory = sqlite3.Row
            init_alert_tables(self._local.conn)
        return self._local.conn

    def get_alarm(self, sensor_id: str, alert_type: str) -> Optional[AlarmRecord]:
        """Get a specific alarm by sensor and type."""
        conn = self._get_conn()
        key = f"{sensor_id}:{alert_type}"
        row = conn.execute(
            "SELECT * FROM alarm_state WHERE alarm_key = ?",
            (key,)
        ).fetchone()
        
        if row is None:
            return None
        return self._row_to_record(row)

    def get_all_active(self) -> List[AlarmRecord]:
        """Get all active (non-cleared) alarms."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM alarm_state WHERE severity > 0 ORDER BY severity DESC, first_seen_ts ASC"
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_all_visible(self) -> List[AlarmRecord]:
        """Get active alarms that should be displayed (not silenced, or escalated past silence)."""
        active = self.get_all_active()
        return [a for a in active if a.should_notify or not a.is_silenced]

    def upsert_alarm(
        self,
        sensor_id: str,
        alert_type: str,
        sensor_name: str,
        severity: Severity,
        message: str,
    ) -> tuple[AlarmRecord, bool, bool]:
        """
        Update or insert alarm state.
        
        Returns: (alarm_record, is_new, is_escalated)
        """
        conn = self._get_conn()
        key = f"{sensor_id}:{alert_type}"
        now = time.time()
        
        existing = self.get_alarm(sensor_id, alert_type)
        is_new = False
        is_escalated = False
        
        if existing is None:
            # New alarm
            if severity != Severity.NONE:
                is_new = True
                conn.execute("""
                    INSERT INTO alarm_state 
                    (alarm_key, sensor_id, alert_type, sensor_name, severity, message,
                     first_seen_ts, last_updated_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (key, sensor_id, alert_type, sensor_name, severity.value, message, now, now))
                conn.commit()
                _log.warning("New alarm: %s severity=%s msg=%s", key, severity.name, message)
        else:
            old_severity = existing.severity
            
            if severity == Severity.NONE:
                # Alarm cleared
                if existing.is_active:
                    conn.execute("""
                        UPDATE alarm_state 
                        SET severity = 0, cleared_ts = ?, last_updated_ts = ?,
                            silenced_until = CASE WHEN silenced_until = ? THEN NULL ELSE silenced_until END,
                            silenced_severity = CASE WHEN silenced_until = ? THEN NULL ELSE silenced_severity END
                        WHERE alarm_key = ?
                    """, (now, now, float('inf'), float('inf'), key))
                    conn.commit()
                    _log.info("Alarm cleared: %s (was %s)", key, old_severity.name)
            else:
                # Alarm active - check for escalation
                if severity.value > old_severity.value:
                    is_escalated = True
                    _log.warning("Alarm escalated: %s %s→%s", key, old_severity.name, severity.name)
                
                if not existing.is_active:
                    # Was cleared, now active again
                    is_new = True
                    conn.execute("""
                        UPDATE alarm_state 
                        SET severity = ?, sensor_name = ?, message = ?,
                            first_seen_ts = ?, last_updated_ts = ?, cleared_ts = NULL
                        WHERE alarm_key = ?
                    """, (severity.value, sensor_name, message, now, now, key))
                else:
                    # Update existing active alarm
                    conn.execute("""
                        UPDATE alarm_state 
                        SET severity = ?, sensor_name = ?, message = ?, last_updated_ts = ?
                        WHERE alarm_key = ?
                    """, (severity.value, sensor_name, message, now, key))
                conn.commit()
        
        # Return current state
        alarm = self.get_alarm(sensor_id, alert_type)
        if alarm is None:
            # Create a minimal record for cleared alarm that was never stored
            alarm = AlarmRecord(
                alarm_key=key, sensor_id=sensor_id, alert_type=alert_type,
                sensor_name=sensor_name, severity=Severity.NONE, message="",
                first_seen_ts=None, last_updated_ts=now, last_notified_ts=None,
                cleared_ts=now, silenced_until=None, silenced_severity=None
            )
        return alarm, is_new, is_escalated

    def mark_notified(self, sensor_id: str, alert_type: str) -> None:
        """Mark that we sent a notification for this alarm."""
        conn = self._get_conn()
        key = f"{sensor_id}:{alert_type}"
        conn.execute(
            "UPDATE alarm_state SET last_notified_ts = ? WHERE alarm_key = ?",
            (time.time(), key)
        )
        conn.commit()

    def silence_alarm(self, sensor_id: str, alert_type: str, duration_seconds: float) -> bool:
        """Silence an alarm for a duration. Use float('inf') for until-resolved."""
        conn = self._get_conn()
        key = f"{sensor_id}:{alert_type}"
        
        alarm = self.get_alarm(sensor_id, alert_type)
        if not alarm or not alarm.is_active:
            return False
        
        if duration_seconds == float('inf'):
            silenced_until = float('inf')
        else:
            silenced_until = time.time() + duration_seconds
        
        conn.execute("""
            UPDATE alarm_state 
            SET silenced_until = ?, silenced_severity = ?
            WHERE alarm_key = ?
        """, (silenced_until, alarm.severity.value, key))
        conn.commit()
        _log.info("Silenced alarm: %s for %s", key, 
                  "until resolved" if duration_seconds == float('inf') else f"{duration_seconds}s")
        return True

    def unsilence_alarm(self, sensor_id: str, alert_type: str) -> bool:
        """Remove silence from an alarm."""
        conn = self._get_conn()
        key = f"{sensor_id}:{alert_type}"
        conn.execute(
            "UPDATE alarm_state SET silenced_until = NULL, silenced_severity = NULL WHERE alarm_key = ?",
            (key,)
        )
        conn.commit()
        return True

    def should_notify(self, sensor_id: str, alert_type: str, rate_limit_sec: float = 1800) -> bool:
        """Check if we should send a notification (respects rate limit)."""
        alarm = self.get_alarm(sensor_id, alert_type)
        if not alarm or not alarm.is_active:
            return False
        if alarm.is_silenced and not (alarm.silenced_severity and 
                                       alarm.severity.value > alarm.silenced_severity.value):
            return False
        if alarm.last_notified_ts is None:
            return True
        return (time.time() - alarm.last_notified_ts) >= rate_limit_sec

    def log_notification(
        self, 
        sensor_id: str, 
        alert_type: str, 
        notification_type: str,
        success: bool,
        error_message: Optional[str] = None
    ) -> None:
        """Log a notification attempt for audit purposes."""
        conn = self._get_conn()
        key = f"{sensor_id}:{alert_type}"
        conn.execute("""
            INSERT INTO alert_notifications (alarm_key, notification_type, sent_ts, success, error_message)
            VALUES (?, ?, ?, ?, ?)
        """, (key, notification_type, time.time(), 1 if success else 0, error_message))
        conn.commit()

    def update_heartbeat(self, last_reading_ts: Optional[float], sensors_active: int) -> None:
        """Update system heartbeat."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE system_heartbeat 
            SET last_heartbeat_ts = ?, last_reading_ts = ?, sensors_active = ?, status = 'running'
            WHERE id = 1
        """, (time.time(), last_reading_ts, sensors_active))
        conn.commit()

    def get_heartbeat(self) -> dict:
        """Get current heartbeat status."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM system_heartbeat WHERE id = 1").fetchone()
        if row is None:
            return {"status": "unknown", "last_heartbeat_ts": None}
        return dict(row)

    def cleanup_old_cleared(self, max_age_hours: float = 24) -> int:
        """Remove old cleared alarms."""
        conn = self._get_conn()
        cutoff = time.time() - (max_age_hours * 3600)
        cursor = conn.execute(
            "DELETE FROM alarm_state WHERE severity = 0 AND cleared_ts < ?",
            (cutoff,)
        )
        conn.commit()
        return cursor.rowcount

    def _row_to_record(self, row: sqlite3.Row) -> AlarmRecord:
        """Convert a database row to an AlarmRecord."""
        return AlarmRecord(
            alarm_key=row['alarm_key'],
            sensor_id=row['sensor_id'],
            alert_type=row['alert_type'],
            sensor_name=row['sensor_name'] or "",
            severity=Severity(row['severity']),
            message=row['message'] or "",
            first_seen_ts=row['first_seen_ts'],
            last_updated_ts=row['last_updated_ts'],
            last_notified_ts=row['last_notified_ts'],
            cleared_ts=row['cleared_ts'],
            silenced_until=row['silenced_until'],
            silenced_severity=Severity(row['silenced_severity']) if row['silenced_severity'] else None,
        )


# =============================================================================
# ALERT CHECKING LOGIC (moved from alerts.py, no pandas dependency)
# =============================================================================

@dataclass
class AlertCondition:
    """An alert condition detected by the engine."""
    sensor_id: str
    sensor_name: str
    alert_type: str
    message: str
    severity: str  # "warning" or "critical"
    current_value: Optional[str] = None
    threshold: Optional[str] = None


def check_alert_conditions(
    conn: sqlite3.Connection,
    cfg,  # AppConfig
    lookback_seconds: int = 300,
) -> List[AlertCondition]:
    """
    Check for alert conditions based on recent sensor readings.
    
    This is the core alert logic, designed to run in the background daemon.
    No Streamlit or pandas dependencies.
    
    Args:
        conn: SQLite connection
        cfg: AppConfig with alert thresholds
        lookback_seconds: How far back to look for "latest" readings
    
    Returns:
        List of AlertCondition for all detected issues
    """
    if not cfg.alerts.enabled:
        return []

    alerts: List[AlertCondition] = []
    now = time.time()
    cutoff = now - lookback_seconds

    # Get latest reading per sensor
    rows = conn.execute("""
        SELECT 
            r.sensor_id,
            r.name,
            r.ts,
            r.temp_c,
            r.humidity,
            r.battery,
            r.rssi
        FROM readings r
        INNER JOIN (
            SELECT sensor_id, MAX(ts) as max_ts
            FROM readings
            WHERE ts >= ?
            GROUP BY sensor_id
        ) latest ON r.sensor_id = latest.sensor_id AND r.ts = latest.max_ts
    """, (cutoff,)).fetchall()

    # Check disk space first (system-level alert)
    try:
        from storage import get_disk_usage
        disk_usage = get_disk_usage()
        free_mb = disk_usage.get("free_gb", 0) * 1024

        if free_mb < cfg.alerts.disk_critical_mb:
            alerts.append(AlertCondition(
                sensor_id="SYSTEM",
                sensor_name="System",
                alert_type="low_disk",
                message=f"Critical: Only {free_mb:.0f} MB disk space remaining!",
                severity="critical",
                current_value=f"{free_mb:.0f} MB",
                threshold=f"{cfg.alerts.disk_critical_mb} MB",
            ))
        elif free_mb < cfg.alerts.disk_warning_mb:
            alerts.append(AlertCondition(
                sensor_id="SYSTEM",
                sensor_name="System",
                alert_type="low_disk",
                message=f"Low disk space: {free_mb:.0f} MB remaining",
                severity="warning",
                current_value=f"{free_mb:.0f} MB",
                threshold=f"{cfg.alerts.disk_warning_mb} MB",
            ))
    except Exception as e:
        _log.warning("Could not check disk space: %s", e)

    # Check each sensor
    for row in rows:
        sensor_id = row[0]
        sensor_name = row[1] or sensor_id
        ts = row[2]
        temp_c = row[3]
        humidity = row[4]
        battery = row[5]
        rssi = row[6]

        # Get friendly name from config if available
        if sensor_id in cfg.sensors:
            sensor_name = cfg.sensors[sensor_id].name or sensor_name

        # Check low battery
        if battery is not None and battery < cfg.alerts.low_battery_threshold:
            alerts.append(AlertCondition(
                sensor_id=sensor_id,
                sensor_name=sensor_name,
                alert_type="low_battery",
                message=f"Low battery: {battery}%",
                severity="warning",
                current_value=f"{battery}%",
                threshold=f"{cfg.alerts.low_battery_threshold}%",
            ))

        # Check offline (stale data)
        reading_age_sec = now - ts if ts else float('inf')
        offline_threshold_sec = cfg.alerts.sensor_offline_minutes * 60

        if reading_age_sec > offline_threshold_sec:
            minutes_ago = int(reading_age_sec / 60)
            alerts.append(AlertCondition(
                sensor_id=sensor_id,
                sensor_name=sensor_name,
                alert_type="offline",
                message=f"No data for {minutes_ago} minutes",
                severity="critical",
                current_value=f"{minutes_ago} min",
                threshold=f"{cfg.alerts.sensor_offline_minutes} min",
            ))

        # Check temperature thresholds (if configured for this sensor)
        if cfg.alerts.temp_alerts_enabled and sensor_id in cfg.sensors:
            sensor_cfg = cfg.sensors[sensor_id]

            # Convert for display
            if cfg.units.upper() == "F":
                temp_display = temp_c * 9.0 / 5.0 + 32.0
                unit = "°F"
                min_display = sensor_cfg.min_temp_c * 9.0 / 5.0 + 32.0 if sensor_cfg.min_temp_c else None
                max_display = sensor_cfg.max_temp_c * 9.0 / 5.0 + 32.0 if sensor_cfg.max_temp_c else None
            else:
                temp_display = temp_c
                unit = "°C"
                min_display = sensor_cfg.min_temp_c
                max_display = sensor_cfg.max_temp_c

            if sensor_cfg.min_temp_c is not None and temp_c < sensor_cfg.min_temp_c:
                alerts.append(AlertCondition(
                    sensor_id=sensor_id,
                    sensor_name=sensor_name,
                    alert_type="temp_low",
                    message=f"Temperature too low: {temp_display:.1f}{unit}",
                    severity="warning",
                    current_value=f"{temp_display:.1f}{unit}",
                    threshold=f"min {min_display:.1f}{unit}",
                ))

            if sensor_cfg.max_temp_c is not None and temp_c > sensor_cfg.max_temp_c:
                alerts.append(AlertCondition(
                    sensor_id=sensor_id,
                    sensor_name=sensor_name,
                    alert_type="temp_high",
                    message=f"Temperature too high: {temp_display:.1f}{unit}",
                    severity="warning",
                    current_value=f"{temp_display:.1f}{unit}",
                    threshold=f"max {max_display:.1f}{unit}",
                ))

    return alerts


# =============================================================================
# BACKGROUND ALERT ENGINE (runs as thread in daemon)
# =============================================================================

class AlertEngine:
    """
    Background alert processing engine.
    
    Runs as a thread in ble_watchdog.py, checking alerts and sending
    notifications 24/7, regardless of whether the dashboard is open.
    """

    def __init__(
        self,
        db_path: Path,
        config_loader: Callable,  # Function that returns current AppConfig
        check_interval_seconds: int = 60,
        heartbeat_interval_seconds: int = 300,
    ):
        self.db_path = db_path
        self.config_loader = config_loader
        self.check_interval = check_interval_seconds
        self.heartbeat_interval = heartbeat_interval_seconds
        
        self.alarm_store = PersistentAlarmStore(db_path)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        # Stats
        self.checks_performed = 0
        self.notifications_sent = 0
        self.last_check_ts: Optional[float] = None
        self.last_heartbeat_ts: Optional[float] = None

    def start(self) -> None:
        """Start the alert engine thread."""
        if self._thread and self._thread.is_alive():
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="AlertEngine",
            daemon=True,
        )
        self._thread.start()
        _log.info("Alert engine started (check every %ds, heartbeat every %ds)",
                  self.check_interval, self.heartbeat_interval)

    def stop(self) -> None:
        """Stop the alert engine thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        _log.info("Alert engine stopped")

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        """Main loop for alert processing."""
        last_heartbeat = 0
        
        while not self._stop_event.is_set():
            now = time.time()
            
            try:
                # Run alert check
                self._check_alerts()
                self.checks_performed += 1
                self.last_check_ts = now
                
                # Periodic heartbeat
                if (now - last_heartbeat) >= self.heartbeat_interval:
                    self._update_heartbeat()
                    last_heartbeat = now
                    self.last_heartbeat_ts = now
                
                # Periodic cleanup
                if self.checks_performed % 60 == 0:  # Every ~hour at 60s interval
                    cleaned = self.alarm_store.cleanup_old_cleared(max_age_hours=24)
                    if cleaned > 0:
                        _log.debug("Cleaned %d old cleared alarms", cleaned)
                
            except Exception as e:
                _log.exception("Alert engine error: %s", e)
            
            # Wait for next check
            self._stop_event.wait(timeout=self.check_interval)

    def _check_alerts(self) -> None:
        """Run one alert check cycle."""
        cfg = self.config_loader()
        if not cfg.alerts.enabled:
            return
        
        # Get fresh connection for this check
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            # Check for alert conditions
            conditions = check_alert_conditions(conn, cfg)
            
            # Track which alarms are currently active
            current_keys = set()
            
            for cond in conditions:
                severity = Severity.CRITICAL if cond.severity == "critical" else Severity.WARNING
                
                alarm, is_new, is_escalated = self.alarm_store.upsert_alarm(
                    sensor_id=cond.sensor_id,
                    alert_type=cond.alert_type,
                    sensor_name=cond.sensor_name,
                    severity=severity,
                    message=cond.message,
                )
                
                current_keys.add(alarm.alarm_key)
                
                # Send notifications
                email_config = getattr(cfg, 'email', None)
                if email_config and email_config.is_configured():
                    rate_limit_sec = email_config.rate_limit_minutes * 60
                    
                    if is_new and self.alarm_store.should_notify(cond.sensor_id, cond.alert_type, rate_limit_sec):
                        self._send_alarm_notification(email_config, alarm, cond, "new")
                    elif is_escalated and self.alarm_store.should_notify(cond.sensor_id, cond.alert_type, rate_limit_sec):
                        self._send_alarm_notification(email_config, alarm, cond, "escalated")
            
            # Clear alarms that are no longer in conditions
            for alarm in self.alarm_store.get_all_active():
                if alarm.alarm_key not in current_keys:
                    # Alarm condition cleared
                    old_alarm = alarm
                    self.alarm_store.upsert_alarm(
                        sensor_id=alarm.sensor_id,
                        alert_type=alarm.alert_type,
                        sensor_name=alarm.sensor_name,
                        severity=Severity.NONE,
                        message="",
                    )
                    
                    # Send cleared notification if configured
                    email_config = getattr(cfg, 'email', None)
                    if (email_config and email_config.is_configured() and 
                        email_config.notify_on_clear and old_alarm.duration_seconds >= 300):
                        self._send_cleared_notification(email_config, old_alarm)
        
        finally:
            conn.close()

    def _send_alarm_notification(
        self,
        email_config,
        alarm: AlarmRecord,
        condition: AlertCondition,
        notification_type: str,
    ) -> None:
        """Send an alarm notification email."""
        try:
            from notifications import send_alarm_email
            
            message = condition.message
            if notification_type == "escalated":
                message = f"ESCALATED: {message}"
            
            success, error = send_alarm_email(
                config=email_config,
                sensor_name=condition.sensor_name,
                sensor_id=condition.sensor_id,
                severity=condition.severity,
                alert_type=condition.alert_type,
                message=message,
                current_value=condition.current_value,
                threshold=condition.threshold,
            )
            
            if success:
                self.alarm_store.mark_notified(alarm.sensor_id, alarm.alert_type)
                self.notifications_sent += 1
                _log.info("Sent %s notification for %s", notification_type, alarm.alarm_key)
            else:
                _log.error("Failed to send notification for %s: %s", alarm.alarm_key, error)
            
            self.alarm_store.log_notification(
                alarm.sensor_id, alarm.alert_type, notification_type, success, error if not success else None
            )
        
        except Exception as e:
            _log.exception("Error sending alarm notification: %s", e)
            self.alarm_store.log_notification(
                alarm.sensor_id, alarm.alert_type, notification_type, False, str(e)
            )

    def _send_cleared_notification(self, email_config, alarm: AlarmRecord) -> None:
        """Send a notification that an alarm has cleared."""
        try:
            from notifications import send_cleared_email
            
            duration_minutes = int(alarm.duration_seconds / 60)
            
            success, error = send_cleared_email(
                config=email_config,
                sensor_name=alarm.sensor_name,
                sensor_id=alarm.sensor_id,
                alert_type=alarm.alert_type,
                duration_minutes=duration_minutes,
            )
            
            if success:
                _log.info("Sent cleared notification for %s", alarm.alarm_key)
            else:
                _log.error("Failed to send cleared notification for %s: %s", alarm.alarm_key, error)
            
            self.alarm_store.log_notification(
                alarm.sensor_id, alarm.alert_type, "cleared", success, error if not success else None
            )
        
        except Exception as e:
            _log.exception("Error sending cleared notification: %s", e)

    def _update_heartbeat(self) -> None:
        """Update system heartbeat."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        try:
            # Get stats about recent readings
            now = time.time()
            cutoff = now - 300  # Last 5 minutes
            
            row = conn.execute("""
                SELECT MAX(ts), COUNT(DISTINCT sensor_id)
                FROM readings
                WHERE ts >= ?
            """, (cutoff,)).fetchone()
            
            last_reading_ts = row[0] if row else None
            sensors_active = row[1] if row else 0
            
            self.alarm_store.update_heartbeat(last_reading_ts, sensors_active)
            
            _log.debug("Heartbeat updated: %d sensors active, last reading %s",
                       sensors_active,
                       f"{int(now - last_reading_ts)}s ago" if last_reading_ts else "never")
        
        finally:
            conn.close()


# =============================================================================
# MODULE-LEVEL CONVENIENCE FUNCTIONS
# =============================================================================

_engine: Optional[AlertEngine] = None


def get_alert_engine() -> Optional[AlertEngine]:
    """Get the global alert engine instance."""
    return _engine


def start_alert_engine(db_path: Path, config_loader: Callable) -> AlertEngine:
    """Start the global alert engine."""
    global _engine
    if _engine is not None:
        _engine.stop()
    
    _engine = AlertEngine(db_path, config_loader)
    _engine.start()
    return _engine


def stop_alert_engine() -> None:
    """Stop the global alert engine."""
    global _engine
    if _engine is not None:
        _engine.stop()
        _engine = None


def get_alarm_store(db_path: Path) -> PersistentAlarmStore:
    """Get an alarm store instance for the given database."""
    return PersistentAlarmStore(db_path)
