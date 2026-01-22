#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog Environmental Monitor - BLE Core with Self-Healing
PROPRIETARY - Commercial Product

This module wraps the validated OSS BLE scanner and adds production features:
- Self-healing watchdog thread
- Automatic recovery from Bluetooth failures
- Data persistence to SQLite
"""

import threading
import time
import signal
import sys

from typing import Optional
from logging_config import get_logger

# Import the validated OSS scanner components
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))


def _handle_sigterm(signum, frame):
    _log_ble.warning("Received SIGTERM from systemd, exiting immediately")
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

from ble_scanner import (
    decode_govee,
    SensorReading,
    get_readings,
    clear_readings,
    start_scanner_thread,
    stop_scanner
)

from config import AppConfig, load_config
from storage import Reading, DatabaseWriter, get_db_path

_log_ble = get_logger("watchdog.ble")
_log_db = get_logger("watchdog.db")
_log_core = get_logger("watchdog.core")


MAX_RESTARTS = 10
BACKOFF_BASE_SEC = 2.0           # seconds
BACKOFF_MAX_SEC = 120.0          # cap backoff (2 min)
HEALTHY_RESET_SEC = 120.0        # reset restart counter after 2 min healthy

_restart_attempts = 0
_last_healthy_ts = None

class WatchdogMonitor:
    """
    Production BLE monitor with self-healing capabilities.
    
    Wraps the validated OSS scanner and adds:
    - Database persistence
    - Watchdog monitoring thread
    - Automatic failure recovery
    """
    
    def __init__(self):
        self.cfg = load_config()
        self.db_writer = None  # initialized in start()
        self.scanner_thread = None
        self.watchdog_thread = None
        self.running = False
        self.restart_requested = False
        self.last_data_time = time.time()
        
    def _database_callback(self, reading: SensorReading):
        """
        Callback function that persists readings to database.
        Called by OSS scanner for each new reading.
        """
        try:
            # Update last data time for watchdog
            self.last_data_time = time.time()
            
            # Get friendly name from config if available
            friendly_name = reading.sensor_id
            if reading.sensor_id in self.cfg.sensors:
                sensor_cfg = self.cfg.sensors[reading.sensor_id]
                if sensor_cfg.name:
                    friendly_name = sensor_cfg.name
            
            # Create database reading
            db_reading = Reading(
                sensor_id=reading.sensor_id,
                name=friendly_name,
                timestamp=reading.timestamp,
                temp_c=reading.temp_c,
                humidity=reading.humidity,
                battery=reading.battery,
                rssi=reading.rssi
            )
            
            # Persist to database (thread-safe)
            if self.db_writer:
                self.db_writer.submit(db_reading)
           
                
        except Exception as e:
            _log_db.exception("Database callback error")

    def _run_archive(self, trigger: str = "manual") -> None:
        """Run the archiving process.
        
        Args:
            trigger: What triggered this archive ("startup", "periodic", "manual")
        """
        try:
            from storage import archive_old_data
            
            _log_core.info("Running archive (%s)...", trigger)
            result = archive_old_data(self.cfg.archive.retention_days)
            
            if result['success']:
                if result['rows_archived'] > 0:
                    _log_core.info("Archive complete: %s", result['message'])
                else:
                    _log_core.info("Archive: No old data to archive")
            else:
                _log_core.warning("Archive failed: %s", result.get('message', 'Unknown error'))
                
        except Exception as e:
            # Archive failure should NOT prevent monitoring from starting
            _log_core.error("Archive error (non-fatal): %s", e)
    
    def _watchdog_thread_func(self):
        """
        Watchdog monitoring thread - KEY DIFFERENTIATOR from OSS.
        
        Monitors data flow and triggers scanner restart if stalled.
        Also handles periodic archiving.
        """
        _log_core.info("Starting watchdog monitoring thread")
        
        # Track time for periodic tasks
        last_archive_time = time.time()
        ARCHIVE_INTERVAL = 86400  # 24 hours in seconds
        
        while self.running:
            time.sleep(10)  # Check every 10 seconds
            
            # === Existing watchdog checks ===
            if self.cfg.monitoring.watchdog_enabled:
                now = time.time()
                time_since_last_data = now - self.last_data_time
                
                # Calculate threshold: expected_interval * multiplier
                threshold = (
                    self.cfg.monitoring.expected_interval_seconds * 
                    self.cfg.monitoring.watchdog_threshold_multiplier
                )
                
                # If no data received beyond threshold, request restart
                if time_since_last_data > threshold:
                    _log_core.warning(
                        "No data for %.1fs (threshold: %.1fs). Requesting scanner restart...",
                        time_since_last_data,
                        threshold,
                    )
                    self.restart_requested = True
                else:
                    # Healthy - data is flowing
                    current_readings = get_readings()
                    num_sensors = len(current_readings)
                    if num_sensors > 0:
                        _log_core.info(
                            "Healthy - %d sensor(s), last data %.1fs ago",
                            num_sensors,
                            time_since_last_data,
                        )
            
            # === NEW: Periodic archiving ===
            if self.cfg.archive.enabled and self.cfg.archive.auto_archive:
                now = time.time()
                if (now - last_archive_time) > ARCHIVE_INTERVAL:
                    self._run_archive("periodic")
                    last_archive_time = now
    
    def start(self):
        """Start the monitoring system with watchdog."""
        _log_core.info("Starting Watchdog Environmental Monitor")
        
        # Start thread-safe DB writer (owns the SQLite connection)
        self.db_writer = DatabaseWriter(get_db_path())
        self.db_writer.start()
        _log_core.info("Database writer started")

        # Register signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # === Auto-archive on startup ===
        if self.cfg.archive.enabled and self.cfg.archive.auto_archive:
            self._run_archive("startup")

        self.running = True
        
        # Start watchdog thread
        if self.cfg.monitoring.watchdog_enabled:
            self.watchdog_thread = threading.Thread(
                target=self._watchdog_thread_func,
                daemon=True
            )
            self.watchdog_thread.start()
            _log_core.info("Watchdog thread started")
        
        restart_count = 0
        backoff_sec = BACKOFF_BASE_SEC
        last_restart_ts = 0.0  # when we last performed a restart/backoff
        try:
            while self.running:
                # Start OSS scanner with database callback
                _log_ble.info("Starting BLE scanner (restart #%d)...", restart_count)
                
                # Use validated OSS scanner with our callback
                self.scanner_thread = start_scanner_thread(
                    callback=self._database_callback
                )
                
                self.restart_requested = False
                threshold = self.cfg.monitoring.stall_threshold_sec
                # Monitor for restart requests
                                # Monitor for restart requests
                while self.running and not self.restart_requested:
                    # If we've been healthy for long enough after previous restarts,
                    # reset counters so we don't "brick" the monitor after transient issues.
                    if restart_count > 0:
                        time_since_last_data = time.time() - self.last_data_time
                        if time_since_last_data <= threshold:
                            if last_restart_ts > 0 and (time.time() - last_restart_ts) >= HEALTHY_RESET_SEC:
                                _log_ble.info(
                                    "BLE healthy for >=%ss; resetting restart counters/backoff (restart_count=%d -> 0)",
                                    HEALTHY_RESET_SEC,
                                    restart_count,
                                )
                                restart_count = 0
                                backoff_sec = BACKOFF_BASE_SEC
                                last_restart_ts = 0.0

                    time.sleep(1)
                
                if self.restart_requested and self.running:
                    restart_count += 1
                    if restart_count > MAX_RESTARTS:
                        _log_ble.error(
                            "Max restarts exceeded (%d). Disabling monitor to avoid a CPU spin. "
                            "Check Bluetooth health (rfkill / adapter).",
                            MAX_RESTARTS,
                        )
                        self.running = False
                        break
    
                    _log_ble.warning(
                        "Restarting scanner due to watchdog trigger (attempt %d/%d). Backoff=%ss",
                        restart_count,
                        MAX_RESTARTS,
                        backoff_sec,
                    )
    
                    try:
                        stop_scanner()
                    except Exception:
                        _log_ble.exception("Error stopping scanner during restart")
                    last_restart_ts = time.time()
                    for _ in range(int(backoff_sec)):
                        time.sleep(1)
                    backoff_sec = min(backoff_sec * 2, BACKOFF_MAX_SEC)
                    
        except KeyboardInterrupt:
            _log_core.info("Keyboard interrupt received")
        except Exception:
            _log_core.exception("FATAL ERROR")
        finally:
            self.stop()
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully (do not do heavy work here)."""
        try:
            signal_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            signal_name = str(signum)
    
        _log_core.warning("Received %s, initiating graceful shutdown...", signal_name)
        self.running = False
        
    def stop(self):
        """Stop monitoring and cleanup (idempotent; safe for SIGTERM/SIGINT)."""
        _log_core.info("Stopping Watchdog Monitor")
        
        self.running = False
        
        # Stop OSS scanner (thread-safe stop event)
        try:
            stop_scanner()
            _log_core.info("Scanner stop requested")
        except Exception:
            _log_core.exception("Error requesting scanner stop")
        
        # Stop DB writer (drains queue, closes connection)
        # Safe no-op for current builds that write directly.
        try:
            dbw = getattr(self, "db_writer", None)
            if dbw:
                _log_core.info("Flushing database writer queue...")
                dbw.stop()
                _log_core.info("Database writer stopped")
        except Exception:
            _log_core.exception("Error stopping database writer")
    
        # Close DB connection (TASK-03 code may keep a dedicated writer connection instead)
        try:
            if getattr(self, "conn", None):
                self.conn.close()
                _log_core.info("Database connection closed")
        except Exception:
            _log_core.exception("Error closing database connection")
        
        _log_core.info("Shutdown complete")


def main():
    """Entry point for running as standalone service."""
    monitor = WatchdogMonitor()
    try:
        monitor.start()
    except KeyboardInterrupt:
        _log_core.info("Stopped by user")
    except Exception:
        _log_core.exception("Fatal error")


if __name__ == "__main__":
    main()
