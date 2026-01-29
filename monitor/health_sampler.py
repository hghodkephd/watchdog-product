#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog System Health Sampler
Background daemon that collects system metrics for the System Health dashboard tab.

Design principles:
- Minimal memory footprint (~1-2MB RSS)
- Uses /proc filesystem directly (no psutil dependency)
- Thread-safe singleton pattern
- Automatic data retention (24h rolling window)
- SQL-level downsampling for chart queries
"""

import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("watchdog.health")

# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

SAMPLE_INTERVAL_SECONDS = 5      # How often to collect metrics
RETENTION_HOURS = 24             # How long to keep data
MAX_ROWS = 17280                 # 24h * 60min * 60sec / 5sec = 17,280 samples

# ---------------------------------------------------------------------
# /proc parsers (no external dependencies)
# ---------------------------------------------------------------------

def parse_meminfo() -> Dict[str, int]:
    """
    Parse /proc/meminfo and return values in KB.
    Returns dict with keys: MemTotal, MemAvailable, MemFree, SwapTotal, SwapFree, etc.
    """
    result = {}
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(':')
                    value = int(parts[1])  # Value in KB
                    result[key] = value
    except (IOError, ValueError) as e:
        logger.warning("Failed to parse /proc/meminfo: %s", e)
    return result


def parse_loadavg() -> tuple[float, float, float]:
    """
    Parse /proc/loadavg and return (1min, 5min, 15min) load averages.
    """
    try:
        with open('/proc/loadavg', 'r') as f:
            parts = f.read().split()
            return float(parts[0]), float(parts[1]), float(parts[2])
    except (IOError, ValueError, IndexError) as e:
        logger.warning("Failed to parse /proc/loadavg: %s", e)
        return 0.0, 0.0, 0.0


def get_process_rss_mb(pid: int) -> float:
    """
    Get RSS (Resident Set Size) for a specific PID in MB.
    Returns 0.0 if process not found or error.
    """
    try:
        with open(f'/proc/{pid}/statm', 'r') as f:
            parts = f.read().split()
            # statm: size resident shared text lib data dt (all in pages)
            # resident is the RSS in pages
            rss_pages = int(parts[1])
            page_size = os.sysconf('SC_PAGE_SIZE')  # Usually 4096
            return (rss_pages * page_size) / (1024 * 1024)
    except (IOError, ValueError, IndexError):
        return 0.0


def find_process_by_name(name_pattern: str) -> Optional[int]:
    """
    Find a process PID by command line pattern.
    Returns first matching PID or None.
    """
    try:
        for entry in os.listdir('/proc'):
            if not entry.isdigit():
                continue
            try:
                with open(f'/proc/{entry}/cmdline', 'r') as f:
                    cmdline = f.read()
                    if name_pattern in cmdline:
                        return int(entry)
            except (IOError, PermissionError):
                continue
    except Exception as e:
        logger.debug("Error scanning processes: %s", e)
    return None


# ---------------------------------------------------------------------
# Health Sample Data Structure
# ---------------------------------------------------------------------

@dataclass
class HealthSample:
    """Single point-in-time system health measurement."""
    ts: float                    # Unix timestamp
    mem_total_mb: float
    mem_avail_mb: float
    mem_used_mb: float
    swap_total_mb: float
    swap_used_mb: float
    load_1m: float
    load_5m: float
    load_15m: float
    monitor_rss_mb: float        # watchdog-monitor process RSS
    dashboard_rss_mb: float      # watchdog-dashboard process RSS

    def to_tuple(self) -> tuple:
        """Convert to tuple for SQL INSERT."""
        return (
            self.ts,
            self.mem_total_mb,
            self.mem_avail_mb,
            self.mem_used_mb,
            self.swap_total_mb,
            self.swap_used_mb,
            self.load_1m,
            self.load_5m,
            self.load_15m,
            self.monitor_rss_mb,
            self.dashboard_rss_mb,
        )


# ---------------------------------------------------------------------
# Health Sampler Daemon
# ---------------------------------------------------------------------

class HealthSampler:
    """
    Background thread that samples system health metrics.
    
    Thread-safe singleton - only one instance runs per process.
    Writes directly to SQLite with bounded retention.
    """
    
    _instance: Optional['HealthSampler'] = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self, db_path: Path):
        if self._initialized:
            return
        
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Stats
        self.samples_collected = 0
        self.samples_pruned = 0
        self.last_sample_ts: Optional[float] = None
        self.errors = 0
        
        # Process PID cache (refreshed periodically)
        self._monitor_pid: Optional[int] = None
        self._dashboard_pid: Optional[int] = None
        self._last_pid_refresh = 0.0
        self._pid_refresh_interval = 60.0  # Refresh PIDs every 60s
        
        self._initialized = True
        logger.info("HealthSampler initialized (db=%s)", db_path)
    
    def start(self) -> None:
        """Start the background sampling thread."""
        if self._thread and self._thread.is_alive():
            logger.debug("HealthSampler already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="HealthSampler",
            daemon=True,
        )
        self._thread.start()
        logger.info("HealthSampler started (interval=%ds, retention=%dh)",
                    SAMPLE_INTERVAL_SECONDS, RETENTION_HOURS)
    
    def stop(self) -> None:
        """Stop the background sampling thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        
        logger.info("HealthSampler stopped (collected=%d, pruned=%d, errors=%d)",
                    self.samples_collected, self.samples_pruned, self.errors)
    
    def is_running(self) -> bool:
        """Check if sampler is actively running."""
        return self._thread is not None and self._thread.is_alive()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL;")
        return self._conn
    
    def _run(self) -> None:
        """Main sampling loop."""
        logger.info("HealthSampler thread starting")
        
        while not self._stop_event.is_set():
            try:
                sample = self._collect_sample()
                self._write_sample(sample)
                self._maybe_prune()
            except Exception as e:
                self.errors += 1
                logger.exception("HealthSampler error: %s", e)
            
            # Wait for next interval (interruptible)
            self._stop_event.wait(timeout=SAMPLE_INTERVAL_SECONDS)
        
        logger.info("HealthSampler thread exiting")
    
    def _refresh_process_pids(self) -> None:
        """Refresh cached PIDs for monitor and dashboard processes."""
        now = time.time()
        if (now - self._last_pid_refresh) < self._pid_refresh_interval:
            return
        
        self._monitor_pid = find_process_by_name('ble_watchdog.py')
        self._dashboard_pid = find_process_by_name('streamlit')
        self._last_pid_refresh = now
        
        logger.debug("PIDs refreshed: monitor=%s, dashboard=%s",
                     self._monitor_pid, self._dashboard_pid)
    
    def _collect_sample(self) -> HealthSample:
        """Collect a single health sample from the system."""
        now = time.time()
        
        # Memory from /proc/meminfo
        meminfo = parse_meminfo()
        mem_total_kb = meminfo.get('MemTotal', 0)
        mem_avail_kb = meminfo.get('MemAvailable', meminfo.get('MemFree', 0))
        swap_total_kb = meminfo.get('SwapTotal', 0)
        swap_free_kb = meminfo.get('SwapFree', 0)
        
        mem_total_mb = mem_total_kb / 1024
        mem_avail_mb = mem_avail_kb / 1024
        mem_used_mb = mem_total_mb - mem_avail_mb
        swap_total_mb = swap_total_kb / 1024
        swap_used_mb = (swap_total_kb - swap_free_kb) / 1024
        
        # Load averages
        load_1m, load_5m, load_15m = parse_loadavg()
        
        # Process RSS (refresh PIDs periodically)
        self._refresh_process_pids()
        monitor_rss = get_process_rss_mb(self._monitor_pid) if self._monitor_pid else 0.0
        dashboard_rss = get_process_rss_mb(self._dashboard_pid) if self._dashboard_pid else 0.0
        
        return HealthSample(
            ts=now,
            mem_total_mb=round(mem_total_mb, 1),
            mem_avail_mb=round(mem_avail_mb, 1),
            mem_used_mb=round(mem_used_mb, 1),
            swap_total_mb=round(swap_total_mb, 1),
            swap_used_mb=round(swap_used_mb, 1),
            load_1m=round(load_1m, 2),
            load_5m=round(load_5m, 2),
            load_15m=round(load_15m, 2),
            monitor_rss_mb=round(monitor_rss, 1),
            dashboard_rss_mb=round(dashboard_rss, 1),
        )
    
    def _write_sample(self, sample: HealthSample) -> None:
        """Write sample to database."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO system_health (
                ts, mem_total_mb, mem_avail_mb, mem_used_mb,
                swap_total_mb, swap_used_mb,
                load_1m, load_5m, load_15m,
                monitor_rss_mb, dashboard_rss_mb
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            sample.to_tuple()
        )
        conn.commit()
        
        self.samples_collected += 1
        self.last_sample_ts = sample.ts
    
    def _maybe_prune(self) -> None:
        """Prune old samples beyond retention window."""
        # Only prune every 100 samples to reduce overhead
        if self.samples_collected % 100 != 0:
            return
        
        cutoff_ts = time.time() - (RETENTION_HOURS * 3600)
        conn = self._get_conn()
        
        cursor = conn.execute(
            "DELETE FROM system_health WHERE ts < ?",
            (cutoff_ts,)
        )
        pruned = cursor.rowcount
        conn.commit()
        
        if pruned > 0:
            self.samples_pruned += pruned
            logger.debug("Pruned %d old health samples", pruned)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get sampler statistics."""
        return {
            "is_running": self.is_running(),
            "samples_collected": self.samples_collected,
            "samples_pruned": self.samples_pruned,
            "last_sample_ts": self.last_sample_ts,
            "errors": self.errors,
            "monitor_pid": self._monitor_pid,
            "dashboard_pid": self._dashboard_pid,
        }


# ---------------------------------------------------------------------
# Module-level API (matches pattern used by AlertEngine)
# ---------------------------------------------------------------------

_sampler: Optional[HealthSampler] = None


def start_health_sampler(db_path: Path) -> HealthSampler:
    """Start the global health sampler instance."""
    global _sampler
    _sampler = HealthSampler(db_path)
    _sampler.start()
    return _sampler


def stop_health_sampler() -> None:
    """Stop the global health sampler instance."""
    global _sampler
    if _sampler:
        _sampler.stop()
        _sampler = None


def get_health_sampler() -> Optional[HealthSampler]:
    """Get the global health sampler instance."""
    return _sampler


# ---------------------------------------------------------------------
# Query functions for dashboard
# ---------------------------------------------------------------------

def get_health_timeseries(
    conn: sqlite3.Connection,
    hours: float = 1.0,
    max_points: int = 300,
) -> list[dict]:
    """
    Get health timeseries data for charts.
    
    Uses SQL-level downsampling when data exceeds max_points.
    Returns list of dicts with all health metrics.
    """
    start_ts = time.time() - (hours * 3600)
    
    # Count available points
    row = conn.execute(
        "SELECT COUNT(*) FROM system_health WHERE ts >= ?",
        (start_ts,)
    ).fetchone()
    total_points = row[0] if row else 0
    
    if total_points <= max_points:
        # Return all points
        rows = conn.execute(
            """
            SELECT ts, mem_total_mb, mem_avail_mb, mem_used_mb,
                   swap_total_mb, swap_used_mb,
                   load_1m, load_5m, load_15m,
                   monitor_rss_mb, dashboard_rss_mb
            FROM system_health
            WHERE ts >= ?
            ORDER BY ts ASC
            """,
            (start_ts,)
        ).fetchall()
    else:
        # Downsample using bucket averaging
        bucket_seconds = int((hours * 3600) / max_points)
        rows = conn.execute(
            """
            SELECT 
                (? + ((ts - ?) / ?) * ?) AS ts_bin,
                AVG(mem_total_mb), AVG(mem_avail_mb), AVG(mem_used_mb),
                AVG(swap_total_mb), AVG(swap_used_mb),
                AVG(load_1m), AVG(load_5m), AVG(load_15m),
                AVG(monitor_rss_mb), AVG(dashboard_rss_mb)
            FROM system_health
            WHERE ts >= ?
            GROUP BY ts_bin
            ORDER BY ts_bin ASC
            LIMIT ?
            """,
            (start_ts, start_ts, bucket_seconds, bucket_seconds, start_ts, max_points)
        ).fetchall()
    
    return [
        {
            "ts": r[0],
            "mem_total_mb": r[1],
            "mem_avail_mb": r[2],
            "mem_used_mb": r[3],
            "swap_total_mb": r[4],
            "swap_used_mb": r[5],
            "load_1m": r[6],
            "load_5m": r[7],
            "load_15m": r[8],
            "monitor_rss_mb": r[9],
            "dashboard_rss_mb": r[10],
        }
        for r in rows
    ]


def get_latest_health(conn: sqlite3.Connection) -> Optional[dict]:
    """Get the most recent health sample."""
    row = conn.execute(
        """
        SELECT ts, mem_total_mb, mem_avail_mb, mem_used_mb,
               swap_total_mb, swap_used_mb,
               load_1m, load_5m, load_15m,
               monitor_rss_mb, dashboard_rss_mb
        FROM system_health
        ORDER BY ts DESC
        LIMIT 1
        """
    ).fetchone()
    
    if not row:
        return None
    
    return {
        "ts": row[0],
        "mem_total_mb": row[1],
        "mem_avail_mb": row[2],
        "mem_used_mb": row[3],
        "swap_total_mb": row[4],
        "swap_used_mb": row[5],
        "load_1m": row[6],
        "load_5m": row[7],
        "load_15m": row[8],
        "monitor_rss_mb": row[9],
        "dashboard_rss_mb": row[10],
    }


# ---------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    
    logging.basicConfig(level=logging.DEBUG)
    
    # Test collection
    print("Testing health sample collection...")
    sample = HealthSample(
        ts=time.time(),
        mem_total_mb=0, mem_avail_mb=0, mem_used_mb=0,
        swap_total_mb=0, swap_used_mb=0,
        load_1m=0, load_5m=0, load_15m=0,
        monitor_rss_mb=0, dashboard_rss_mb=0,
    )
    
    # Collect real sample
    meminfo = parse_meminfo()
    load = parse_loadavg()
    
    print(f"\nMemory:")
    print(f"  Total: {meminfo.get('MemTotal', 0) / 1024:.0f} MB")
    print(f"  Available: {meminfo.get('MemAvailable', 0) / 1024:.0f} MB")
    print(f"  Used: {(meminfo.get('MemTotal', 0) - meminfo.get('MemAvailable', 0)) / 1024:.0f} MB")
    
    print(f"\nSwap:")
    print(f"  Total: {meminfo.get('SwapTotal', 0) / 1024:.0f} MB")
    print(f"  Used: {(meminfo.get('SwapTotal', 0) - meminfo.get('SwapFree', 0)) / 1024:.0f} MB")
    
    print(f"\nLoad: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}")
    
    # Test sampler with in-memory DB
    print("\n\nTesting HealthSampler with in-memory DB...")
    
    test_db = Path("/tmp/test_health.db")
    test_db.unlink(missing_ok=True)
    
    conn = sqlite3.connect(test_db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            mem_total_mb REAL,
            mem_avail_mb REAL,
            mem_used_mb REAL,
            swap_total_mb REAL,
            swap_used_mb REAL,
            load_1m REAL,
            load_5m REAL,
            load_15m REAL,
            monitor_rss_mb REAL,
            dashboard_rss_mb REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_system_health_ts ON system_health(ts)")
    conn.commit()
    conn.close()
    
    sampler = start_health_sampler(test_db)
    print(f"Sampler started: {sampler.is_running()}")
    
    # Let it collect a few samples
    time.sleep(15)
    
    stats = sampler.get_stats()
    print(f"\nSampler stats: {stats}")
    
    # Query the data
    conn = sqlite3.connect(test_db)
    latest = get_latest_health(conn)
    print(f"\nLatest sample: {latest}")
    
    timeseries = get_health_timeseries(conn, hours=0.01)  # ~36 seconds
    print(f"Timeseries points: {len(timeseries)}")
    conn.close()
    
    stop_health_sampler()
    print("\nTest complete!")
