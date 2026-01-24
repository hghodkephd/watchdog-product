#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
System Health Check for Watchdog.

Provides health status that can be queried by:
- Desktop app (to show "Pi offline" warning)
- Dashboard (for system health panel)
- External monitoring tools

The daemon writes heartbeat to SQLite; this module reads it.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from storage import get_db_path

try:
    from logging_config import get_logger
    _log = get_logger("watchdog.health")
except ImportError:
    import logging
    _log = logging.getLogger("watchdog.health")


class HealthStatus(Enum):
    """Overall system health status."""
    HEALTHY = "healthy"           # Everything working
    DEGRADED = "degraded"         # Some issues but functional
    UNHEALTHY = "unhealthy"       # Major problems
    OFFLINE = "offline"           # System not responding
    UNKNOWN = "unknown"           # Can't determine status


@dataclass
class SystemHealth:
    """Complete system health snapshot."""
    status: HealthStatus
    status_message: str
    
    # Heartbeat info (from daemon)
    last_heartbeat_ts: Optional[float]
    heartbeat_age_seconds: Optional[float]
    daemon_running: bool
    
    # Data flow info
    last_reading_ts: Optional[float]
    reading_age_seconds: Optional[float]
    sensors_active: int
    data_flowing: bool
    
    # Alert info
    active_alarms: int
    critical_alarms: int
    
    # Database info
    db_accessible: bool
    db_size_mb: float
    
    # Timestamps
    checked_at: float
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "status_message": self.status_message,
            "last_heartbeat_ts": self.last_heartbeat_ts,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "daemon_running": self.daemon_running,
            "last_reading_ts": self.last_reading_ts,
            "reading_age_seconds": self.reading_age_seconds,
            "sensors_active": self.sensors_active,
            "data_flowing": self.data_flowing,
            "active_alarms": self.active_alarms,
            "critical_alarms": self.critical_alarms,
            "db_accessible": self.db_accessible,
            "db_size_mb": self.db_size_mb,
            "checked_at": self.checked_at,
        }


# Thresholds for health determination
HEARTBEAT_HEALTHY_MAX_AGE = 600       # 10 minutes
HEARTBEAT_DEGRADED_MAX_AGE = 1800     # 30 minutes
READING_HEALTHY_MAX_AGE = 300         # 5 minutes
READING_DEGRADED_MAX_AGE = 900        # 15 minutes


def check_system_health(db_path: Optional[Path] = None) -> SystemHealth:
    """
    Check overall system health.
    
    This is the main entry point for health checks.
    
    Args:
        db_path: Path to SQLite database (uses default if not specified)
    
    Returns:
        SystemHealth with complete status information
    """
    now = time.time()
    
    if db_path is None:
        db_path = get_db_path()
    
    # Initialize with defaults
    health = SystemHealth(
        status=HealthStatus.UNKNOWN,
        status_message="Checking...",
        last_heartbeat_ts=None,
        heartbeat_age_seconds=None,
        daemon_running=False,
        last_reading_ts=None,
        reading_age_seconds=None,
        sensors_active=0,
        data_flowing=False,
        active_alarms=0,
        critical_alarms=0,
        db_accessible=False,
        db_size_mb=0.0,
        checked_at=now,
    )
    
    # Check database accessibility
    try:
        if not db_path.exists():
            health.status = HealthStatus.OFFLINE
            health.status_message = "Database not found"
            return health
        
        health.db_size_mb = db_path.stat().st_size / (1024 * 1024)
        
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        health.db_accessible = True
    except Exception as e:
        health.status = HealthStatus.OFFLINE
        health.status_message = f"Database error: {e}"
        return health
    
    try:
        # Check heartbeat (from daemon's alert engine)
        try:
            row = conn.execute(
                "SELECT * FROM system_heartbeat WHERE id = 1"
            ).fetchone()
            
            if row:
                health.last_heartbeat_ts = row['last_heartbeat_ts']
                health.sensors_active = row['sensors_active'] or 0
                
                if health.last_heartbeat_ts:
                    health.heartbeat_age_seconds = now - health.last_heartbeat_ts
                    health.daemon_running = health.heartbeat_age_seconds < HEARTBEAT_HEALTHY_MAX_AGE
                    
                health.last_reading_ts = row['last_reading_ts']
                if health.last_reading_ts:
                    health.reading_age_seconds = now - health.last_reading_ts
                    health.data_flowing = health.reading_age_seconds < READING_HEALTHY_MAX_AGE
        except sqlite3.OperationalError:
            # Table doesn't exist yet (daemon hasn't started)
            pass
        
        # Check for latest reading directly (fallback if no heartbeat)
        if health.last_reading_ts is None:
            row = conn.execute("SELECT MAX(ts) FROM readings").fetchone()
            if row and row[0]:
                health.last_reading_ts = row[0]
                health.reading_age_seconds = now - health.last_reading_ts
                health.data_flowing = health.reading_age_seconds < READING_HEALTHY_MAX_AGE
        
        # Count active sensors in last 5 minutes
        if health.sensors_active == 0:
            cutoff = now - 300
            row = conn.execute(
                "SELECT COUNT(DISTINCT sensor_id) FROM readings WHERE ts >= ?",
                (cutoff,)
            ).fetchone()
            health.sensors_active = row[0] if row else 0
        
        # Check alarm state
        try:
            row = conn.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN severity = 2 THEN 1 ELSE 0 END) as critical "
                "FROM alarm_state WHERE severity > 0"
            ).fetchone()
            if row:
                health.active_alarms = row['total'] or 0
                health.critical_alarms = row['critical'] or 0
        except sqlite3.OperationalError:
            # alarm_state table doesn't exist yet
            pass
        
        # Determine overall status
        health.status, health.status_message = _determine_status(health)
        
    finally:
        conn.close()
    
    return health


def _determine_status(health: SystemHealth) -> tuple[HealthStatus, str]:
    """Determine overall health status from individual metrics."""
    issues = []
    
    # Critical issues
    if not health.db_accessible:
        return HealthStatus.OFFLINE, "Database not accessible"
    
    if health.heartbeat_age_seconds is not None:
        if health.heartbeat_age_seconds > HEARTBEAT_DEGRADED_MAX_AGE:
            return HealthStatus.OFFLINE, f"Daemon not responding (last heartbeat {int(health.heartbeat_age_seconds)}s ago)"
        elif health.heartbeat_age_seconds > HEARTBEAT_HEALTHY_MAX_AGE:
            issues.append(f"Daemon heartbeat stale ({int(health.heartbeat_age_seconds)}s)")
    elif health.last_heartbeat_ts is None:
        # No heartbeat ever recorded
        if health.last_reading_ts is None:
            return HealthStatus.OFFLINE, "Monitoring service not started"
        else:
            issues.append("Heartbeat system not initialized")
    
    # Data flow issues
    if health.reading_age_seconds is not None:
        if health.reading_age_seconds > READING_DEGRADED_MAX_AGE:
            issues.append(f"No sensor data for {int(health.reading_age_seconds)}s")
        elif health.reading_age_seconds > READING_HEALTHY_MAX_AGE:
            issues.append(f"Sensor data stale ({int(health.reading_age_seconds)}s)")
    elif health.last_reading_ts is None:
        issues.append("No sensor data received yet")
    
    # Sensor count
    if health.sensors_active == 0 and health.daemon_running:
        issues.append("No sensors detected")
    
    # Alarm status
    if health.critical_alarms > 0:
        issues.append(f"{health.critical_alarms} critical alarm(s)")
    
    # Determine final status
    if not issues:
        return HealthStatus.HEALTHY, f"System healthy ({health.sensors_active} sensors active)"
    elif len(issues) == 1 and health.active_alarms > 0 and health.daemon_running:
        # Only issue is alarms - system is working, just has alerts
        return HealthStatus.HEALTHY, issues[0]
    elif health.daemon_running:
        return HealthStatus.DEGRADED, "; ".join(issues)
    else:
        return HealthStatus.UNHEALTHY, "; ".join(issues)


def is_daemon_running(db_path: Optional[Path] = None) -> bool:
    """Quick check if daemon is running (based on recent heartbeat)."""
    health = check_system_health(db_path)
    return health.daemon_running


def get_health_summary(db_path: Optional[Path] = None) -> dict:
    """Get a simple health summary dict (for JSON API)."""
    health = check_system_health(db_path)
    return health.to_dict()


# =============================================================================
# HTTP Health Endpoint (optional, for external monitoring)
# =============================================================================

def create_health_server(host: str = "0.0.0.0", port: int = 8502):
    """
    Create a simple HTTP server for health checks.
    
    This is optional - you can run this as a separate process if you want
    external monitoring tools to check Watchdog health.
    
    Usage:
        python health_check.py --serve
    
    Then curl http://pi-ip:8502/health
    """
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json
    
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health" or self.path == "/":
                health = check_system_health()
                
                # HTTP status based on health
                if health.status == HealthStatus.HEALTHY:
                    status_code = 200
                elif health.status == HealthStatus.DEGRADED:
                    status_code = 200  # Still OK, just degraded
                else:
                    status_code = 503  # Service unavailable
                
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(json.dumps(health.to_dict(), indent=2).encode())
            else:
                self.send_response(404)
                self.end_headers()
        
        def log_message(self, format, *args):
            # Suppress default logging
            pass
    
    server = HTTPServer((host, port), HealthHandler)
    _log.info("Health check server starting on %s:%d", host, port)
    return server


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    import json
    
    parser = argparse.ArgumentParser(description="Watchdog System Health Check")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--serve", action="store_true", help="Run HTTP health server")
    parser.add_argument("--port", type=int, default=8502, help="HTTP server port")
    args = parser.parse_args()
    
    if args.serve:
        server = create_health_server(port=args.port)
        print(f"Health server running on http://0.0.0.0:{args.port}/health")
        print("Press Ctrl+C to stop")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped")
    else:
        health = check_system_health()
        
        if args.json:
            print(json.dumps(health.to_dict(), indent=2))
        else:
            # Human-readable output
            status_icons = {
                HealthStatus.HEALTHY: "✅",
                HealthStatus.DEGRADED: "⚠️",
                HealthStatus.UNHEALTHY: "❌",
                HealthStatus.OFFLINE: "💀",
                HealthStatus.UNKNOWN: "❓",
            }
            
            print(f"\n{status_icons[health.status]} System Status: {health.status.value.upper()}")
            print(f"   {health.status_message}")
            print()
            print(f"Daemon Running:  {'Yes' if health.daemon_running else 'No'}")
            if health.heartbeat_age_seconds is not None:
                print(f"Last Heartbeat:  {int(health.heartbeat_age_seconds)}s ago")
            print(f"Sensors Active:  {health.sensors_active}")
            print(f"Data Flowing:    {'Yes' if health.data_flowing else 'No'}")
            if health.reading_age_seconds is not None:
                print(f"Last Reading:    {int(health.reading_age_seconds)}s ago")
            print(f"Active Alarms:   {health.active_alarms} ({health.critical_alarms} critical)")
            print(f"Database Size:   {health.db_size_mb:.1f} MB")
            print()
