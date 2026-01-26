#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog Environmental Monitor - BLE Core with Self-Healing
PROPRIETARY - Commercial Product

This module wraps the validated OSS BLE scanner and adds production features:
- Self-healing watchdog thread
- Automatic recovery from Bluetooth failures
- Data persistence to SQLite
- BACKGROUND ALERT ENGINE (runs 24/7, independent of dashboard)
"""

import sys
import threading
import time
import signal
import os

from typing import Optional
from logging_config import get_logger

# Import the validated OSS scanner components
sys.path.insert(0, os.path.dirname(__file__))

from ble_scanner import (
    decode_govee,
    SensorReading,
    get_readings,
    clear_readings,
    start_scanner_thread,
    stop_scanner,
    check_ble_adapter_sync,
)

from config import AppConfig, load_config
from storage import Reading, DatabaseWriter, get_db_path

# Import the background alert engine
from alert_engine import start_alert_engine, stop_alert_engine, get_alert_engine

_log_ble = get_logger("watchdog.ble")
_log_db = get_logger("watchdog.db")
_log_core = get_logger("watchdog.core")


# BLE adapter initialization - fail fast at startup, recover in main loop
BLE_ADAPTER_STARTUP_RETRIES = 3      # Quick initial check (fail fast)
BLE_ADAPTER_STARTUP_DELAY = 5        # Seconds between startup retries
BLE_ADAPTER_RECOVERY_DELAY = 30      # Seconds between recovery attempts in main loop

class WatchdogMonitor:
    """
    Production BLE monitor with self-healing capabilities.
    
    Wraps the validated OSS scanner and adds:
    - Database persistence
    - Watchdog monitoring thread
    - Automatic failure recovery
    - BACKGROUND ALERT ENGINE (24/7 notifications)
    """
    
    def __init__(self):
        self.cfg = load_config()
        self.db_writer = None  # initialized in start()
        self.alert_engine = None  # initialized in start()
        self.scanner_thread = None
        self.watchdog_thread = None
        self.running = False
        self.restart_requested = False
        self.last_data_time = time.time()
        self._shutdown_reason = None  # Track why we're shutting down
        
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

    def _wait_for_ble_adapter(self) -> bool:
        """
        Quick check for BLE adapter availability at startup.
        
        DESIGN: Fail fast at startup (3 attempts, 15s max), then let main loop
        handle ongoing recovery. This prevents 7+ minute startup delays.
        
        Returns:
            True if adapter is available, False if not ready yet
        """
        for attempt in range(BLE_ADAPTER_STARTUP_RETRIES):
            _log_ble.info(
                "Checking BLE adapter (attempt %d/%d)...", 
                attempt + 1, BLE_ADAPTER_STARTUP_RETRIES
            )
            
            status = check_ble_adapter_sync()
            
            if status.get('available', False):
                _log_ble.info("✓ BLE adapter ready")
                return True
            
            error_msg = status.get('error', 'Unknown error')
            error_type = status.get('error_type', 'unknown')
            
            _log_ble.warning(
                "BLE adapter not ready: %s (type: %s)", 
                error_msg, error_type
            )
            
            # Log helpful troubleshooting for common issues
            if error_type == 'no_adapter':
                _log_ble.warning(
                    "→ No Bluetooth adapter found. For Pi Zero 2 W, ensure USB BT adapter is connected."
                )
            elif error_type == 'dbus_error':
                _log_ble.warning(
                    "→ D-Bus error. Try: sudo systemctl restart bluetooth"
                )
            elif error_type == 'permission_error':
                _log_ble.warning(
                    "→ Permission denied. Ensure user is in 'bluetooth' group."
                )
            
            if attempt < BLE_ADAPTER_STARTUP_RETRIES - 1:
                _log_ble.info("Retrying in %ds...", BLE_ADAPTER_STARTUP_DELAY)
                
                # Check for shutdown during wait
                for _ in range(BLE_ADAPTER_STARTUP_DELAY):
                    if not self.running:
                        _log_ble.info("Shutdown requested during BLE adapter wait")
                        return False
                    time.sleep(1)
        
        # Failed all startup attempts - but don't give up!
        # Log prominent message and let main loop continue trying
        _log_ble.warning(
            "=" * 60 + "\n"
            "BLE adapter not ready after %d attempts.\n"
            "Will continue trying in background.\n"
            "Troubleshooting:\n"
            "  1. Check adapter: lsusb | grep -i bluetooth\n"
            "  2. Check service: systemctl status bluetooth\n"
            "  3. Unblock: sudo rfkill unblock bluetooth\n"
            "  4. Restart: sudo systemctl restart bluetooth\n"
            "=" * 60,
            BLE_ADAPTER_STARTUP_RETRIES
        )
        
        # Create a system alarm so users see this in the dashboard
        self._create_ble_alarm("BLE adapter not ready - monitoring delayed. Check Bluetooth on your Pi.")
        
        return False
    
    
    def _create_ble_alarm(self, message: str):
        """Create a system alarm for BLE issues visible in dashboard."""
        try:
            from alert_engine import PersistentAlarmStore, Severity
            from storage import get_db_path
            
            store = PersistentAlarmStore(get_db_path())
            store.upsert_alarm(
                sensor_id="SYSTEM",
                alert_type="ble_error",
                sensor_name="Bluetooth",
                severity=Severity.CRITICAL,
                message=message,
            )
            _log_ble.info("Created BLE system alarm")
        except Exception as e:
            _log_ble.warning("Could not create BLE alarm: %s", e)
    
    def _clear_ble_alarm(self):
        """Clear BLE system alarm when adapter becomes available."""
        try:
            from alert_engine import PersistentAlarmStore, Severity
            from storage import get_db_path
            
            store = PersistentAlarmStore(get_db_path())
            store.upsert_alarm(
                sensor_id="SYSTEM",
                alert_type="ble_error",
                sensor_name="Bluetooth",
                severity=Severity.NONE,
                message="",
            )
        except Exception:
            pass  # Best effort
            
    def _watchdog_thread_func(self):
        """
        Watchdog monitoring thread.
        """
        _log_core.info("Watchdog thread started")
        
        last_archive_time = time.time()
        last_prune_time = time.time()  # ADD THIS
        ARCHIVE_INTERVAL = 86400  # 24 hours
        PRUNE_INTERVAL = 3600  # 1 hour  # ADD THIS
        
        while self.running:
            time.sleep(10)
            
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
                        _log_core.debug(
                            "Healthy - %d sensor(s), last data %.1fs ago",
                            num_sensors,
                            time_since_last_data,
                        )
 
            # === Periodic stale cache pruning === ADD THIS BLOCK
            now = time.time()
            if (now - last_prune_time) > PRUNE_INTERVAL:
                try:
                    from ble_scanner import prune_stale_readings, get_cache_stats
                    pruned = prune_stale_readings(max_age_seconds=3600)
                    if pruned > 0:
                        _log_core.info("Pruned %d stale sensor cache entries", pruned)
                    stats = get_cache_stats()
                    _log_core.debug("Sensor cache: %d entries", stats.get('count', 0))
                except Exception as e:
                    _log_core.warning("Cache prune failed: %s", e)
                last_prune_time = now
                
            # === Periodic archiving ===
            if self.cfg.archive.enabled and self.cfg.archive.auto_archive:
                now = time.time()
                if (now - last_archive_time) > ARCHIVE_INTERVAL:
                    self._run_archive("periodic")
                    last_archive_time = now
        
        _log_core.info("Watchdog thread exiting (self.running=%s)", self.running)
    
    def start(self):
        """Start the monitoring system with watchdog and alert engine."""
        _log_core.info("=" * 60)
        _log_core.info("Starting Watchdog Environmental Monitor")
        _log_core.info("PID: %d", os.getpid())
        _log_core.info("=" * 60)
        
        # Also print to stderr for immediate visibility in journalctl
        print(f"[watchdog] Starting - PID {os.getpid()}", file=sys.stderr, flush=True)
        
        # Start thread-safe DB writer (owns the SQLite connection)
        self.db_writer = DatabaseWriter(get_db_path())
        self.db_writer.start()
        _log_core.info("Database writer started")

        # Register signal handlers BEFORE setting self.running = True
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        _log_core.info("Signal handlers registered")

        # === Auto-archive on startup ===
        if self.cfg.archive.enabled and self.cfg.archive.auto_archive:
            self._run_archive("startup")

        self.running = True
        self._shutdown_reason = None
        
        # === START BACKGROUND ALERT ENGINE ===
        # This is the key fix: alerts run 24/7 in the daemon, not in the dashboard
        _log_core.info("Starting background alert engine...")
        self.alert_engine = start_alert_engine(
            db_path=get_db_path(),
            config_loader=load_config,  # Reload config each check for live updates
            check_interval_seconds=60,  # Check alerts every 60 seconds
            heartbeat_interval_seconds=300,  # Heartbeat every 5 minutes
        )
        _log_core.info("Alert engine started (checking every 60s)")
        print("[watchdog] Alert engine started - notifications will work 24/7", file=sys.stderr, flush=True)
        
        # Quick check for BLE adapter (fail fast, recover in loop)
        ble_ready = self._wait_for_ble_adapter()
        
        if not self.running:
            _log_core.info("Shutdown requested during startup")
            self._shutdown_reason = "shutdown_during_startup"
            self.stop()
            return
        
        if not ble_ready:
            _log_core.warning("BLE adapter not ready at startup, will keep trying in main loop")
        
        # Start watchdog thread
        if self.cfg.monitoring.watchdog_enabled:
            self.watchdog_thread = threading.Thread(
                target=self._watchdog_thread_func,
                daemon=True,
                name="WatchdogThread"
            )
            self.watchdog_thread.start()
            _log_core.info("Watchdog thread started")
        
        restart_count = 0
        
        try:
            _log_core.info("Entering main monitoring loop")
            print("[watchdog] Entering main loop", file=sys.stderr, flush=True)
            
            while self.running:
                # Start OSS scanner with database callback
                _log_ble.info("Starting BLE scanner (attempt #%d)...", restart_count)
                
                # Re-check BLE adapter before each attempt
                if not check_ble_adapter_sync().get('available', False):
                    _log_ble.warning("BLE adapter still not available, waiting %ds...", BLE_ADAPTER_RECOVERY_DELAY)
                    self._create_ble_alarm("Bluetooth adapter not available - waiting for hardware")
                    
                    for _ in range(BLE_ADAPTER_RECOVERY_DELAY):
                        if not self.running:
                            break
                        time.sleep(1)
                    restart_count += 1
                    continue
                
                # Clear any previous BLE alarm since adapter is now available
                self._clear_ble_alarm()
                
                try:
                    # Use validated OSS scanner with our callback
                    self.scanner_thread = start_scanner_thread(
                        callback=self._database_callback
                    )
                except Exception as e:
                    _log_ble.error("Failed to start scanner: %s", e)
                    # Wait before retry
                    for _ in range(10):
                        if not self.running:
                            break
                        time.sleep(1)
                    restart_count += 1
                    continue
                
                self.restart_requested = False
                
                # Monitor for restart requests - THIS IS THE MAIN BLOCKING LOOP
                _log_core.debug("Scanner started, entering monitoring loop")
                while self.running and not self.restart_requested:
                    time.sleep(1)
                
                # Log why we exited the inner loop
                _log_core.info(
                    "Inner loop exited: running=%s, restart_requested=%s",
                    self.running, self.restart_requested
                )
                
                if self.restart_requested and self.running:
                    _log_ble.warning("Restarting scanner due to watchdog trigger")
                    stop_scanner()
                    time.sleep(2)  # Brief pause before restart
                    restart_count += 1
            
            # Log why we exited the outer loop
            _log_core.info("Main loop exited: running=%s", self.running)
            self._shutdown_reason = self._shutdown_reason or "running_set_false"
                    
        except KeyboardInterrupt:
            _log_core.info("Keyboard interrupt received")
            self._shutdown_reason = "keyboard_interrupt"
        except Exception as e:
            _log_core.exception("FATAL ERROR in main loop")
            self._shutdown_reason = f"exception: {e}"
        finally:
            _log_core.info(
                "start() completing - reason: %s", 
                self._shutdown_reason or "unknown"
            )
            print(
                f"[watchdog] start() exiting - reason: {self._shutdown_reason}", 
                file=sys.stderr, 
                flush=True
            )
            self.stop()
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully (do not do heavy work here)."""
        try:
            signal_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            signal_name = str(signum)
        
        # CRITICAL: Print immediately to stderr so we see this in journalctl
        # even if the process exits before log buffers flush
        msg = f"[watchdog] Received {signal_name} (signum={signum}), initiating shutdown..."
        print(msg, file=sys.stderr, flush=True)
        
        _log_core.warning("Received %s, initiating graceful shutdown...", signal_name)
        
        self._shutdown_reason = f"signal_{signal_name}"
        self.running = False
        
    def stop(self):
        """Stop monitoring and cleanup (idempotent; safe for SIGTERM/SIGINT)."""
        _log_core.info("Stopping Watchdog Monitor")
        print("[watchdog] stop() called", file=sys.stderr, flush=True)
        
        self.running = False
        
        # Stop alert engine first (cleanly finish any pending notifications)
        try:
            if self.alert_engine is not None:
                _log_core.info("Stopping alert engine...")
                stop_alert_engine()
                self.alert_engine = None
                _log_core.info("Alert engine stopped")
        except Exception:
            _log_core.exception("Error stopping alert engine")
        
        # Stop OSS scanner (thread-safe stop event)
        try:
            stop_scanner()
            _log_core.info("Scanner stop requested")
        except Exception:
            _log_core.exception("Error requesting scanner stop")
        
        # Stop DB writer (drains queue, closes connection)
        try:
            dbw = getattr(self, "db_writer", None)
            if dbw:
                _log_core.info("Flushing database writer queue...")
                dbw.stop()
                _log_core.info("Database writer stopped")
        except Exception:
            _log_core.exception("Error stopping database writer")
    
        # Close DB connection (legacy path)
        try:
            if getattr(self, "conn", None):
                self.conn.close()
                _log_core.info("Database connection closed")
        except Exception:
            _log_core.exception("Error closing database connection")
        
        _log_core.info("Shutdown complete")
        print("[watchdog] Shutdown complete", file=sys.stderr, flush=True)


def main():
    """Entry point for running as standalone service."""
    # Ensure unbuffered output for systemd journal
    print(f"[watchdog] main() starting - PID {os.getpid()}", file=sys.stderr, flush=True)
    
    monitor = WatchdogMonitor()
    try:
        monitor.start()
    except KeyboardInterrupt:
        _log_core.info("Stopped by user")
    except Exception:
        _log_core.exception("Fatal error in main()")
        print("[watchdog] Fatal error in main()", file=sys.stderr, flush=True)
        raise
    finally:
        print("[watchdog] main() exiting", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
    _log_core.error("MAIN FUNCTION EXITED — THIS SHOULD NEVER HAPPEN")
