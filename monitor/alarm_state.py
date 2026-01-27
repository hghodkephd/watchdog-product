#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alarm state management for Watchdog.

Tracks per-sensor alarm state: active/cleared, severity, silencing.
Minimal addition - works alongside existing alerts.py check_alerts().
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from alert_engine import Severity  # canonical definition
from logging_config import get_logger

_log = get_logger("watchdog.alarms")


@dataclass
class AlarmState:
    """State for a single alarm (one per sensor+alert_type)."""
    sensor_id: str
    alert_type: str                          # "temp_high", "temp_low", "low_battery", "offline", "low_disk"
    sensor_name: str = ""
    severity: Severity = Severity.NONE
    message: str = ""
    
    # Timestamps
    first_seen_ts: Optional[float] = None
    last_updated_ts: Optional[float] = None
    last_notified_ts: Optional[float] = None
    cleared_ts: Optional[float] = None
    
    # Silencing
    silenced_until: Optional[float] = None
    silenced_severity: Optional[Severity] = None
    
    @property
    def key(self) -> str:
        return f"{self.sensor_id}:{self.alert_type}"
    
    @property
    def is_active(self) -> bool:
        return self.severity != Severity.NONE
    
    @property
    def is_silenced(self) -> bool:
        if self.silenced_until is None:
            return False
        if self.silenced_until == float('inf'):
            return True  # Until resolved
        return time.time() < self.silenced_until
    
    @property
    def should_show(self) -> bool:
        """Should display if active and (not silenced OR escalated past silence)."""
        if not self.is_active:
            return False
        if not self.is_silenced:
            return True
        # Show if escalated beyond silenced severity
        if self.silenced_severity and self.severity.value > self.silenced_severity.value:
            return True
        return False
    
    @property
    def duration_seconds(self) -> float:
        if not self.first_seen_ts:
            return 0.0
        return time.time() - self.first_seen_ts
    
    def silence(self, duration_seconds: float) -> None:
        """Silence for duration. Use float('inf') for until-resolved."""
        self.silenced_until = time.time() + duration_seconds if duration_seconds != float('inf') else float('inf')
        self.silenced_severity = self.severity
        _log.info("Silenced: %s for %s", self.key, "until resolved" if duration_seconds == float('inf') else f"{duration_seconds}s")
    
    def unsilence(self) -> None:
        self.silenced_until = None
        self.silenced_severity = None


# Silence presets
SILENCE_OPTIONS = {
    "15min": (15 * 60, "15 minutes"),
    "1hour": (60 * 60, "1 hour"),
    "until_resolved": (float('inf'), "Until resolved"),
}


class AlarmManager:
    """
    Thread-safe alarm state manager.
    
    Tracks all alarms, handles state transitions, silencing.
    """
    
    _instance: Optional['AlarmManager'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'AlarmManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self._alarms: Dict[str, AlarmState] = {}
        self._alarms_lock = threading.Lock()
        self._initialized = True
    
    def get(self, sensor_id: str, alert_type: str) -> Optional[AlarmState]:
        key = f"{sensor_id}:{alert_type}"
        with self._alarms_lock:
            return self._alarms.get(key)
    
    def get_all_active(self) -> List[AlarmState]:
        with self._alarms_lock:
            return [a for a in self._alarms.values() if a.is_active]
    
    def get_all_visible(self) -> List[AlarmState]:
        """Active alarms that should be displayed (not silenced, or escalated)."""
        with self._alarms_lock:
            return [a for a in self._alarms.values() if a.should_show]
    
    def update(
        self,
        sensor_id: str,
        alert_type: str,
        sensor_name: str,
        severity: Severity,
        message: str,
    ) -> tuple[AlarmState, bool, bool]:
        """
        Update alarm state.
        
        Returns: (alarm, is_new, escalated)
        """
        key = f"{sensor_id}:{alert_type}"
        now = time.time()
        is_new = False
        escalated = False
        
        with self._alarms_lock:
            alarm = self._alarms.get(key)
            
            if alarm is None:
                alarm = AlarmState(sensor_id=sensor_id, alert_type=alert_type)
                self._alarms[key] = alarm
            
            old_severity = alarm.severity
            
            if severity == Severity.NONE:
                # Cleared
                if alarm.is_active:
                    alarm.cleared_ts = now
                    # Clear until-resolved silence
                    if alarm.silenced_until == float('inf'):
                        alarm.silenced_until = None
                        alarm.silenced_severity = None
                    _log.info("Alarm cleared: %s", key)
                alarm.severity = Severity.NONE
                alarm.first_seen_ts = None
            else:
                # Active
                if not alarm.is_active:
                    alarm.first_seen_ts = now
                    is_new = True
                    _log.warning("New alarm: %s severity=%s", key, severity.name)
                elif severity.value > old_severity.value:
                    escalated = True
                    _log.warning("Escalated: %s %s→%s", key, old_severity.name, severity.name)
                
                alarm.severity = severity
                alarm.sensor_name = sensor_name
                alarm.message = message
                alarm.last_updated_ts = now
                alarm.cleared_ts = None
        
        return alarm, is_new, escalated
    
    def clear(self, sensor_id: str, alert_type: str) -> None:
        self.update(sensor_id, alert_type, "", Severity.NONE, "")
    
    def silence(self, sensor_id: str, alert_type: str, duration_key: str) -> bool:
        """Silence alarm. duration_key: '15min', '1hour', 'until_resolved'."""
        alarm = self.get(sensor_id, alert_type)
        if not alarm:
            return False
        duration, _ = SILENCE_OPTIONS.get(duration_key, (None, None))
        if duration is None:
            return False
        alarm.silence(duration)
        return True
    
    def unsilence(self, sensor_id: str, alert_type: str) -> bool:
        alarm = self.get(sensor_id, alert_type)
        if alarm:
            alarm.unsilence()
            return True
        return False
    
    def mark_notified(self, sensor_id: str, alert_type: str) -> None:
        alarm = self.get(sensor_id, alert_type)
        if alarm:
            alarm.last_notified_ts = time.time()
    
    def should_notify(self, sensor_id: str, alert_type: str, rate_limit_sec: float = 1800) -> bool:
        """Check if notification should be sent (respects rate limit)."""
        alarm = self.get(sensor_id, alert_type)
        if not alarm or not alarm.is_active or alarm.is_silenced:
            return False
        if alarm.last_notified_ts is None:
            return True
        return (time.time() - alarm.last_notified_ts) >= rate_limit_sec
    
    def clear_all_inactive(self, max_age_hours: float = 24) -> int:
        """Remove old cleared alarms. Returns count removed."""
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        with self._alarms_lock:
            keys_to_remove = [
                k for k, a in self._alarms.items()
                if not a.is_active and a.cleared_ts and a.cleared_ts < cutoff
            ]
            for k in keys_to_remove:
                del self._alarms[k]
                removed += 1
        return removed


# Module-level singleton
_manager: Optional[AlarmManager] = None


def get_alarm_manager() -> AlarmManager:
    global _manager
    if _manager is None:
        _manager = AlarmManager()
    return _manager


def process_alerts(alerts: list) -> tuple[List[AlarmState], List[AlarmState], List[AlarmState]]:
    """
    Process alerts from check_alerts() into alarm states.
    
    Bridges existing alerts.py with alarm state system.
    
    Returns: (all_updated, new_alarms, escalated_alarms)
    """
    manager = get_alarm_manager()
    current_keys = set()
    updated = []
    new_alarms = []
    escalated = []
    
    for alert in alerts:
        severity = Severity.CRITICAL if alert.severity == "critical" else Severity.WARNING
        
        alarm, is_new, is_escalated = manager.update(
            sensor_id=alert.sensor_id,
            alert_type=alert.alert_type,
            sensor_name=alert.sensor_name,
            severity=severity,
            message=alert.message,
        )
        
        current_keys.add(alarm.key)
        updated.append(alarm)
        if is_new:
            new_alarms.append(alarm)
        if is_escalated:
            escalated.append(alarm)
    
    # Clear alarms that are no longer in alerts
    for alarm in manager.get_all_active():
        if alarm.key not in current_keys:
            manager.clear(alarm.sensor_id, alarm.alert_type)
    
    return updated, new_alarms, escalated
