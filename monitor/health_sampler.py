#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
System Health Sampler for Watchdog Environmental Monitor.

Background daemon that collects system metrics at fixed intervals and stores
them in SQLite for the dashboard to display. Runs independently of the
Streamlit dashboard to ensure continuous data collection.

DESIGN NOTES (Pi Zero 2 W optimized):
- Samples every 5 seconds (configurable)
- Bounded storage: max 17,280 rows (24 hours of 5s samples)
- Uses /proc/meminfo and /proc/loadavg (no psutil dependency)
- Fallback to psutil if available for richer metrics
- Minimal memory footprint (~1MB RSS)
- Thread-safe, graceful shutdown
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from logging_config import get_logger

_log = get_logger("watchdog.health")

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Sample interval in seconds
SAMPLE_INTERVAL_S = 5

# Maximum rows to keep (24h at 5s intervals = 17,280 rows)
# This bounds storage to ~2MB for the health table
MAX_ROWS = 17_280

# Prune old data every N samples (every 5 minutes = 60 samples)
PRUNE_INTERVAL_SAMPLES = 60

# Maximum age of samples to keep (24 hours)
MAX_AGE_SECONDS = 24 * 3600

# -----------------------------------------------------------------------------
# Data Model
# -----------------------------------------------------------------------------

@dataclass
class HealthSample:
    """Single system health sample."""
    ts: float                    # Unix timestamp
    mem_total_mb: int            # Total system RAM
    mem_avail_mb: int            # Available RAM (usable without swapping)
    mem_used_mb: int             # Used RAM (total - available)
    swap_total_mb: int           # Total swap space
    swap_used_mb: int            # Used swap
    load_1m: float               # 1-minute load average
    load_5m: float               # 5-minute load average
    load_15m: float              # 15-minute load average
    # Optional: process-specific RSS (for watchdog processes)
    monitor_rss_mb: Optional[int] = None
    dashboard_rss_mb: Optional[int] = None


# -----------------------------------------------------------------------------
# System Stats Collection (stdlib-only, Pi optimized)
# -----------------------------------------------------------------------------

def _parse_meminfo() -> dict:
    """
    Parse /proc/meminfo to get memory statistics.
    Works on any Linux system without external dependencies.
    
    Returns dict with keys: MemTotal, MemAvailable, MemFree, Buffers, Cached,
                            SwapTotal, SwapFree (all in kB)
    """
    result = {}
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if ':' in line:
                    key, value = line.split(':', 1)
                    # Remove 'kB' suffix and convert to int
                    value = value.strip().split()[0]
                    result[key] = int(value)
    except (OSError, ValueError) as e:
        _log.warning("Failed to parse /proc/meminfo: %s", e)
    return result


def _parse_loadavg() -> tuple[float, float, float]:
    """
    Parse /proc/loadavg to get load averages.
    
    Returns (load_1m, load_5m, load_15m)
    """
    try:
        with open('/proc/loadavg', 'r') as f:
            parts = f.read().strip().split()
            return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError) as e:
        _log.warning("Failed to parse /proc/loadavg: %s", e)
        return 0.0, 0.0, 0.0


def _get_process_rss_mb(name_pattern: str) -> Optional[int]:
    """
    Find RSS (in MB) for a process matching name_pattern.
    
    Uses /proc/[pid]/stat and /proc/[pid]/cmdline.
    Returns None if not found or on error.
    """
    try:
        for pid_dir in Path('/proc').iterdir():
            if not pid_dir.name.isdigit():
                continue
            
            try:
                cmdline_path = pid_dir / 'cmdline'
                if cmdline_path.exists():
                    cmdline = cmdline_path.read_text()
                    if name_pattern in cmdline:
                        # Read RSS from /proc/[pid]/statm (second field, in pages)
                        statm_path = pid_dir / 'statm'
                        if statm_path.exists():
                            parts = statm_path.read_text().split()
                            rss_pages = int(parts[1])
                            # Page size is typically 4KB
                            page_size = os.sysconf('SC_PAGE_SIZE')
                            return (rss_pages * page_size) // (1024 * 1024)
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return None


def collect_sample() -> HealthSample:
    """
    Collect a single system health sample.
    
    Uses /proc filesystem (no external dependencies).
    Falls back to psutil if available for more accurate metrics.
    """
    ts = time.time()
    
    # Try psutil first (more accurate, handles edge cases)
    try:
        import psutil
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        load = os.getloadavg()
        
        sample = HealthSample(
            ts=ts,
            mem_total_mb=vm.total // (1024 * 1024),
            mem_avail_mb=vm.available // (1024 * 1024),
            mem_used_mb=vm.used // (1024 * 1024),
            swap_total_mb=swap.total // (1024 * 1024),
            swap_used_mb=swap.used // (1024 * 1024),
            load_1m=load[0],
            load_5m=load[1],
            load_15m=load[2],
        )
        
        # Try to get process-specific RSS
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
            try:
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                if 'ble_watchdog' in cmdline:
                    sample.monitor_rss_mb = proc.info['memory_info'].rss // (1024 * 1024)
                elif 'streamlit' in cmdline and 'app.py' in cmdline:
                    sample.dashboard_rss_mb = proc.info['memory_info'].rss // (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        return sample
        
    except ImportError:
        pass  # Fall through to /proc parsing
    
    # Fallback: Parse /proc directly (always works on Linux)
    meminfo = _parse_meminfo()
    load = _parse_loadavg()
    
    # Calculate memory values
    mem_total_kb = meminfo.get('MemTotal', 0)
    mem_avail_kb = meminfo.get('MemAvailable', 0)
    
    # MemAvailable may not exist on older kernels; estimate from MemFree + Buffers + Cached
    if mem_avail_kb == 0:
        mem_avail_kb = (
            meminfo.get('MemFree', 0) +
            meminfo.get('Buffers', 0) +
            meminfo.get('Cached', 0)
        )
    
    swap_total_kb = meminfo.get('SwapTotal', 0)
    swap_free_kb = meminfo.get('SwapFree', 0)
    
    sample = HealthSample(
        ts=ts,
        mem_total_mb=mem_total_kb // 1024,
        mem_avail_mb=mem_avail_kb // 1024,
        mem_used_mb=(mem_total_kb - mem_avail_kb) // 1024,
        swap_total_mb=swap_total_kb // 1024,
        swap_used_mb=(swap_total_kb - swap_free_kb) // 1024,
        load_1m=load[0],
        load_5m=load[1],
        load_15m=load[2],
        monitor_rss_mb=_get_process_rss_mb('ble_watchdog'),
        dashboard_rss_mb=_get_process_rss_mb('streamlit'),
    )
    
    return sample


# -----------------------------------------------------------------------------
# Database Operations
# -----------------------------------------------------------------------------

def init_health_table(conn: sqlite3.Connection) -> None:
    """
    Create the system_health table if it doesn't exist.
    
    Called once at startup to ensure schema is ready.
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS system_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            mem_total_mb INTEGER NOT NULL,
            mem_avail_mb INTEGER NOT NULL,
            mem_used_mb INTEGER NOT NULL,
            swap_total_mb INTEGER NOT NULL,
            swap_used_mb INTEGER NOT NULL,
            load_1m REAL NOT NULL,
            load_5m REAL NOT NULL,
            load_15m REAL NOT NULL,
            monitor_rss_mb INTEGER,
            dashboard_rss_mb INTEGER
        );
        
        CREATE INDEX IF NOT EXISTS idx_system_health_ts
        ON system_health(ts);
    """)
    conn.commit()


def insert_sample(conn: sqlite3.Connection, sample: HealthSample) -> None:
    """Insert a single health sample into the database."""
    conn.execute("""
        INSERT INTO system_health (
            ts, mem_total_mb, mem_avail_mb, mem_used_mb,
            swap_total_mb, swap_used_mb, load_1m, load_5m, load_15m,
            monitor_rss_mb, dashboard_rss_mb
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sample.ts,
        sample.mem_total_mb,
        sample.mem_avail_mb,
        sample.mem_used_mb,
        sample.swap_total_mb,
        sample.swap_used_mb,
        sample.load_1m,
        sample.load_5m,
        sample.load_15m,
        sample.monitor_rss_mb,
        sample.dashboard_rss_mb,
    ))
    conn.commit()


def prune_old_samples(conn: sqlite3.Connection) -> int:
    """
    Remove samples older than MAX_AGE_SECONDS.
    
    Returns number of rows deleted.
    """
    cutoff_ts = time.time() - MAX_AGE_SECONDS
    cursor = conn.execute(
        "DELETE FROM system_health WHERE ts < ?",
        (cutoff_ts,)
    )
    deleted = cursor.rowcount
    conn.commit()
    
    if deleted > 0:
        _log.debug("Pruned %d old health samples", deleted)
    
    return deleted


def enforce_row_limit(conn: sqlite3.Connection) -> int:
    """
    Ensure we don't exceed MAX_ROWS.
    
    Deletes oldest rows if over limit.
    Returns number of rows deleted.
    """
    cursor = conn.execute("SELECT COUNT(*) FROM system_health")
    count = cursor.fetchone()[0]
    
    if count <= MAX_ROWS:
        return 0
    
    to_delete = count - MAX_ROWS
    
    # Delete oldest rows
    conn.execute("""
        DELETE FROM system_health
        WHERE id IN (
            SELECT id FROM system_health
            ORDER BY ts ASC
            LIMIT ?
        )
    """, (to_delete,))
    conn.commit()
    
    _log.debug("Enforced row limit: deleted %d excess rows", to_delete)
    return to_delete


def get_recent_samples(
    conn: sqlite3.Connection,
    seconds: int,
    max_points: int = 300
) -> list[dict]:
    """
    Get recent health samples for charting.
    
    Args:
        conn: Database connection
        seconds: How many seconds of history to fetch
        max_points: Maximum number of points to return (downsamples if needed)
    
    Returns:
        List of dicts with sample data, sorted by timestamp ascending
    """
    start_ts = time.time() - seconds
    
    # Calculate bucket size for downsampling
    if seconds <= 300:  # 5 minutes
        bucket_seconds = 5  # Full resolution
    elif seconds <= 3600:  # 1 hour
        bucket_seconds = 15  # ~240 points
    elif seconds <= 86400:  # 24 hours
        bucket_seconds = 60  # ~1440 points, but limited by max_points
    else:
        bucket_seconds = 300  # 5-minute buckets for longer ranges
    
    # Downsample via SQL GROUP BY
    query = """
        SELECT
            (CAST((ts - ?) / ? AS INTEGER) * ? + ?) AS ts_bucket,
            AVG(mem_total_mb) AS mem_total_mb,
            AVG(mem_avail_mb) AS mem_avail_mb,
            AVG(mem_used_mb) AS mem_used_mb,
            AVG(swap_used_mb) AS swap_used_mb,
            AVG(load_1m) AS load_1m,
            AVG(monitor_rss_mb) AS monitor_rss_mb,
            AVG(dashboard_rss_mb) AS dashboard_rss_mb
        FROM system_health
        WHERE ts >= ?
        GROUP BY ts_bucket
        ORDER BY ts_bucket ASC
        LIMIT ?
    """
    
    cursor = conn.execute(query, (
        start_ts, bucket_seconds, bucket_seconds, start_ts,
        start_ts,
        max_points
    ))
    
    columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    return rows


# -----------------------------------------------------------------------------
# Health Sampler Thread
# -----------------------------------------------------------------------------

class HealthSampler:
    """
    Background daemon that samples system health at fixed intervals.
    
    Runs in its own thread, independent of the dashboard.
    Thread-safe, supports graceful shutdown.
    """
    
    def __init__(self, db_path: Path, interval_s: int = SAMPLE_INTERVAL_S):
        self.db_path = db_path
        self.interval_s = interval_s
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._sample_count = 0
    
    def start(self) -> None:
        """Start the sampler thread."""
        if self._thread is not None and self._thread.is_alive():
            _log.warning("Health sampler already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="HealthSampler",
            daemon=True
        )
        self._thread.start()
        _log.info("Health sampler started (interval=%ds)", self.interval_s)
    
    def stop(self, timeout: float = 10.0) -> None:
        """
        Stop the sampler thread gracefully.
        
        Args:
            timeout: Maximum seconds to wait for thread to finish
        """
        self._stop_event.set()
        
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                _log.warning("Health sampler did not stop within timeout")
        
        self._thread = None
        _log.info("Health sampler stopped (collected %d samples)", self._sample_count)
    
    def is_running(self) -> bool:
        """Check if sampler thread is alive."""
        return self._thread is not None and self._thread.is_alive()
    
    def _run(self) -> None:
        """Main sampling loop."""
        # Initialize database connection (owned by this thread only)
        try:
            self._conn = sqlite3.connect(self.db_path, timeout=10.0)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            init_health_table(self._conn)
        except Exception as e:
            _log.error("Health sampler failed to connect to DB: %s", e)
            return
        
        try:
            while not self._stop_event.is_set():
                try:
                    # Collect and store sample
                    sample = collect_sample()
                    insert_sample(self._conn, sample)
                    self._sample_count += 1
                    
                    # Periodic maintenance
                    if self._sample_count % PRUNE_INTERVAL_SAMPLES == 0:
                        prune_old_samples(self._conn)
                        enforce_row_limit(self._conn)
                    
                except Exception as e:
                    _log.exception("Health sampler error: %s", e)
                
                # Sleep with interruptible wait
                self._stop_event.wait(timeout=self.interval_s)
        
        finally:
            # Clean up connection
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None


# -----------------------------------------------------------------------------
# Module-level singleton for easy access
# -----------------------------------------------------------------------------

_sampler_instance: Optional[HealthSampler] = None
_sampler_lock = threading.Lock()


def get_health_sampler() -> Optional[HealthSampler]:
    """Get the singleton health sampler instance."""
    return _sampler_instance


def start_health_sampler(db_path: Path) -> HealthSampler:
    """
    Start the global health sampler singleton.
    
    Safe to call multiple times - will return existing instance if running.
    """
    global _sampler_instance
    
    with _sampler_lock:
        if _sampler_instance is not None and _sampler_instance.is_running():
            return _sampler_instance
        
        _sampler_instance = HealthSampler(db_path)
        _sampler_instance.start()
        return _sampler_instance


def stop_health_sampler() -> None:
    """Stop the global health sampler singleton."""
    global _sampler_instance
    
    with _sampler_lock:
        if _sampler_instance is not None:
            _sampler_instance.stop()
            _sampler_instance = None


# -----------------------------------------------------------------------------
# CLI for testing
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from storage import get_db_path
    
    # Configure logging for CLI
    import logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
    )
    
    print("Testing health sampler...")
    print(f"Database: {get_db_path()}")
    
    # Collect a single sample
    sample = collect_sample()
    print(f"\nSample collected:")
    print(f"  Memory: {sample.mem_used_mb}/{sample.mem_total_mb} MB used")
    print(f"  Swap: {sample.swap_used_mb}/{sample.swap_total_mb} MB used")
    print(f"  Load: {sample.load_1m:.2f} / {sample.load_5m:.2f} / {sample.load_15m:.2f}")
    if sample.monitor_rss_mb:
        print(f"  Monitor RSS: {sample.monitor_rss_mb} MB")
    if sample.dashboard_rss_mb:
        print(f"  Dashboard RSS: {sample.dashboard_rss_mb} MB")
    
    # Test sampler for 15 seconds
    print("\nStarting sampler for 15 seconds...")
    sampler = start_health_sampler(get_db_path())
    time.sleep(15)
    stop_health_sampler()
    
    # Show collected data
    conn = sqlite3.connect(get_db_path())
    init_health_table(conn)
    samples = get_recent_samples(conn, 60)
    conn.close()
    
    print(f"\nCollected {len(samples)} samples in last 60 seconds")
    if samples:
        latest = samples[-1]
        print(f"Latest: mem_used={latest['mem_used_mb']}MB, load={latest['load_1m']:.2f}")
