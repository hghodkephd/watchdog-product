#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Dec  7 17:46:20 2025

@author: harshadghodke
"""
from __future__ import annotations

import gzip
import shutil
import sqlite3
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("watchdog.db")
# ---------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------

@dataclass
class Reading:
    sensor_id: str
    name: str
    timestamp: float         # unix epoch seconds
    temp_c: float
    humidity: float
    battery: int
    rssi: int


# ---------------------------------------------------------------------
# Project-local storage (replaces platformdirs)
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

ARCHIVE_DIR = DATA_DIR / "archive"
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "data.sqlite3"

# ---------------------------------------------------------------------
# WAL checkpointing (prevent -wal file from growing without bound)
# ---------------------------------------------------------------------

# How often to checkpoint even if WAL isn't huge
WAL_CHECKPOINT_INTERVAL_S = 300  # 5 minutes

# Checkpoint when WAL exceeds this size
WAL_MAX_MB = 64  # conservative; tune later if needed


def _wal_path(db_path: Path) -> Path:
    """Return the SQLite WAL file path for a DB path."""
    return Path(str(db_path) + "-wal")


def get_wal_size_mb(db_path: Path) -> float:
    """Return current WAL file size in MB (0 if missing)."""
    p = _wal_path(db_path)
    if not p.exists():
        return 0.0
    return p.stat().st_size / (1024.0 * 1024.0)


def checkpoint_wal(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Trigger a WAL checkpoint. Returns (wal_frames, checkpointed_frames).
    Uses PASSIVE so we don't block writers.
    """
    try:
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE);").fetchone()
        # row = (busy, log, checkpointed)
        if row and len(row) >= 3:
            return int(row[1]), int(row[2])
    except Exception:
        logger.exception("WAL checkpoint failed")
    return 0, 0

# ---------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------

def get_db_path() -> Path:
    return DB_PATH


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id TEXT NOT NULL,
            name TEXT,
            ts REAL NOT NULL,
            temp_c REAL NOT NULL,
            humidity REAL NOT NULL,
            battery INTEGER NOT NULL,
            rssi INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_readings_sensor_ts
        ON readings(sensor_id, ts);
        """
    )
    conn.commit()


def insert_reading(conn: sqlite3.Connection, r: Reading) -> None:
    """
    Backward-compatible direct insert.
    Prefer DatabaseWriter for thread-safe writes.
    """
    conn.execute(
        """
        INSERT INTO readings(sensor_id, name, ts, temp_c, humidity, battery, rssi)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (
            r.sensor_id,
            r.name,
            r.timestamp,
            r.temp_c,
            r.humidity,
            r.battery,
            r.rssi,
        ),
    )
    conn.commit()

 
# ---------------------------------------------------------------------
# Thread-safe writer (single connection owned by dedicated thread)
# ---------------------------------------------------------------------

class DatabaseWriter:
    """
    Thread-safe, batched SQLite writer.

    One dedicated thread owns the SQLite connection and processes writes
    submitted from other threads via a queue.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._queue: "queue.Queue[Reading]" = queue.Queue(maxsize=10000)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None

        # Stats
        self.total_writes = 0
        self.total_batches = 0
        self.errors = 0
        # WAL checkpointing state
        self._last_checkpoint_ts = 0.0
        self._checkpoint_interval_s = WAL_CHECKPOINT_INTERVAL_S
        self._wal_max_mb = WAL_MAX_MB

    def start(self) -> None:
        """
        Start the writer thread and initialize the SQLite connection.
        """
        if self._thread and self._thread.is_alive():
            return

        # Connection is created here, but only used by the writer thread.
        self._conn = get_connection(self.db_path)
        init_db(self._conn)
        # Start checkpoint timer once DB is initialized
        self._last_checkpoint_ts = time.time()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="DatabaseWriter",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """
        Signal the writer to stop, drain the queue (with timeout),
        then close the connection and join the thread.
        """
        self._stop_event.set()

        timeout_s = 10.0
        start_t = time.time()
        while (not self._queue.empty()) and ((time.time() - start_t) < timeout_s):
            time.sleep(0.05)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)

        if self._conn is not None:
            try:
                # Final checkpoint on shutdown to keep WAL bounded
                self._maybe_checkpoint(force=True)
            except Exception:
                logger.exception("DatabaseWriter: final WAL checkpoint failed")
            try:
                self._conn.close()
            except Exception:
                logger.exception("DatabaseWriter: error closing SQLite connection")
            finally:
                self._conn = None

    def submit(self, reading: Reading) -> None:
        """
        Non-blocking submit.
        If queue is full, drop oldest item, then try again.
        """
        try:
            self._queue.put_nowait(reading)
        except queue.Full:
            logger.warning("DatabaseWriter queue full; dropping oldest item")
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(reading)
            except queue.Full:
                logger.warning("DatabaseWriter queue still full; dropping new item")

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _maybe_checkpoint(self, force: bool = False) -> None:
        """
        Periodically checkpoint WAL to prevent unbounded growth.
        - force=True: checkpoint regardless of size/interval (used on shutdown)
        """
        if self._conn is None:
            return

        now = time.time()
        wal_mb = get_wal_size_mb(self.db_path)

        due_by_time = (now - self._last_checkpoint_ts) >= self._checkpoint_interval_s
        due_by_size = wal_mb >= self._wal_max_mb

        if not (force or due_by_time or due_by_size):
            return

        wal_frames, ckpt_frames = checkpoint_wal(self._conn)
        self._last_checkpoint_ts = now

        logger.info(
            "WAL checkpoint completed (wal_mb=%.1f, wal_frames=%d, checkpointed=%d, force=%s)",
            wal_mb, wal_frames, ckpt_frames, force
        )

    # -------------------------
    # Internal implementation
    # -------------------------

    def _run(self) -> None:
        if self._conn is None:
            logger.error("DatabaseWriter started without a connection")
            return

        while True:
            # stop when requested AND queue is empty
            if self._stop_event.is_set() and self._queue.empty():
                break

            batch: list[Reading] = []

            # wait up to 1s for first item
            try:
                first = self._queue.get(timeout=1.0)
                batch.append(first)
            except queue.Empty:
                # 1s elapsed, flush nothing
                continue

            # drain up to 50 total items
            while len(batch) < 50:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            # write batch
            try:
                self._write_batch(batch)
                self.total_batches += 1
                self.total_writes += len(batch)
                self._maybe_checkpoint()
            except Exception as e:
                self.errors += 1
                logger.exception("DatabaseWriter failed batch (%d items): %s", len(batch), e)

    def _write_batch(self, batch: list[Reading]) -> None:
        assert self._conn is not None

        rows = [
            (
                r.sensor_id,
                r.name,
                r.timestamp,
                r.temp_c,
                r.humidity,
                r.battery,
                r.rssi,
            )
            for r in batch
        ]

        cur = self._conn.cursor()
        try:
            cur.executemany(
                """
                INSERT INTO readings(sensor_id, name, ts, temp_c, humidity, battery, rssi)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )
            self._conn.commit()
        finally:
            try:
                cur.close()
            except Exception:
                pass

# ---------------------------------------------------------------------
# Database statistics
# ---------------------------------------------------------------------

def get_db_stats() -> dict:
    """Get database statistics including size and record counts."""
    try:
        db_path = get_db_path()
        size_mb = db_path.stat().st_size / (1024 * 1024)
        
        conn = get_connection()
        with conn:
            total_rows = conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            
            oldest = conn.execute("SELECT MIN(ts) FROM readings").fetchone()[0]
            newest = conn.execute("SELECT MAX(ts) FROM readings").fetchone()[0]
        
        oldest_dt = datetime.fromtimestamp(oldest) if oldest else None
        newest_dt = datetime.fromtimestamp(newest) if newest else None
        
        return {
            "size_mb": size_mb,
            "total_rows": total_rows,
            "oldest_reading": oldest_dt,
            "newest_reading": newest_dt,
        }
    except Exception as e:
        return {
            "size_mb": 0,
            "total_rows": 0,
            "oldest_reading": None,
            "newest_reading": None,
            "error": str(e),
        }

 
# ---------------------------------------------------------------------
# Disk space monitoring
# ---------------------------------------------------------------------

def get_disk_usage() -> dict:
    """Get disk usage for the data directory.
    
    Returns:
        dict with:
            - total_gb: float (total disk size)
            - used_gb: float (space used)
            - free_gb: float (space available)
            - percent_used: float (0-100)
            - path: str (mount point path)
    """
    try:
        # Get disk usage for the partition containing DATA_DIR
        usage = shutil.disk_usage(DATA_DIR)
        
        return {
            "total_gb": usage.total / (1024 ** 3),
            "used_gb": usage.used / (1024 ** 3),
            "free_gb": usage.free / (1024 ** 3),
            "percent_used": (usage.used / usage.total) * 100,
            "path": str(DATA_DIR),
        }
    except Exception as e:
        return {
            "total_gb": 0,
            "used_gb": 0,
            "free_gb": 0,
            "percent_used": 0,
            "path": str(DATA_DIR),
            "error": str(e),
        }


def check_disk_space(min_free_mb: int = 100) -> tuple:
    """Check if there's enough free disk space.
    
    Args:
        min_free_mb: Minimum required free space in megabytes
    
    Returns:
        tuple of (is_ok: bool, message: str)
        - is_ok is True if free space >= min_free_mb
        - message describes the current state
    """
    try:
        usage = shutil.disk_usage(DATA_DIR)
        free_mb = usage.free / (1024 ** 2)
        
        if free_mb >= min_free_mb:
            return (True, f"OK: {free_mb:.0f} MB free")
        else:
            return (False, f"Low disk space: {free_mb:.0f} MB free (minimum: {min_free_mb} MB)")
    except Exception as e:
        return (False, f"Could not check disk space: {e}")

 

# ---------------------------------------------------------------------
# Data archiving
# ---------------------------------------------------------------------

def archive_old_data(retention_days: int = 90) -> dict:
    """
    Archive readings older than retention_days to compressed CSV.
    
    Returns dict with statistics about the archiving operation.
    """
    cutoff_date = datetime.now() - timedelta(days=retention_days)
    cutoff_ts = cutoff_date.timestamp()
    
    conn = get_connection()
    
    try:
        # Count how many rows will be archived
        with conn:
            count_result = conn.execute(
                "SELECT COUNT(*) FROM readings WHERE ts < ?", (cutoff_ts,)
            ).fetchone()
            rows_to_archive = count_result[0]
        
        if rows_to_archive == 0:
            return {
                "success": True,
                "rows_archived": 0,
                "archive_file": None,
                "message": "No old data to archive",
            }
        
        # Fetch old data
        with conn:
            rows = conn.execute(
                """
                SELECT sensor_id, name, ts, temp_c, humidity, battery, rssi
                FROM readings
                WHERE ts < ?
                ORDER BY ts
                """,
                (cutoff_ts,)
            ).fetchall()
        
        # Convert to DataFrame
        df = pd.DataFrame(
            rows,
            columns=["sensor_id", "name", "ts", "temp_c", "humidity", "battery", "rssi"]
        )
        df["timestamp"] = pd.to_datetime(df["ts"], unit="s")
        
        # Create archive filename
        archive_filename = f"archive_{cutoff_date.strftime('%Y%m%d')}.csv.gz"
        archive_path = ARCHIVE_DIR / archive_filename
        
        # Save to compressed CSV
        df.to_csv(archive_path, index=False, compression="gzip")
        
        # Delete old data from database
        with conn:
            conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff_ts,))
        conn.execute("VACUUM")  # Reclaim space
        
        return {
            "success": True,
            "rows_archived": rows_to_archive,
            "archive_file": str(archive_path),
            "cutoff_date": cutoff_date.isoformat(),
            "message": f"Archived {rows_to_archive} rows to {archive_filename}",
        }
    
    except Exception as e:
        return {
            "success": False,
            "rows_archived": 0,
            "archive_file": None,
            "error": str(e),
            "message": f"Archive failed: {e}",
        }
    finally:
        conn.close()


def list_archives() -> list:
    """List all available archive files."""
    archives = []
    for archive_file in sorted(ARCHIVE_DIR.glob("archive_*.csv.gz")):
        size_mb = archive_file.stat().st_size / (1024 * 1024)
        archives.append({
            "filename": archive_file.name,
            "path": str(archive_file),
            "size_mb": size_mb,
            "modified": datetime.fromtimestamp(archive_file.stat().st_mtime),
        })
    return archives


def restore_archive(archive_filename: str) -> dict:
    """
    Restore data from an archive file back into the active database.
    
    Returns dict with statistics about the restore operation.
    """
    archive_path = ARCHIVE_DIR / archive_filename
    
    if not archive_path.exists():
        return {
            "success": False,
            "rows_restored": 0,
            "message": f"Archive file not found: {archive_filename}",
        }
    
    try:
        # Read archive
        df = pd.read_csv(archive_path, compression="gzip")
        rows_to_restore = len(df)
        
        # Insert into database
        conn = get_connection()
        with conn:
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT INTO readings(sensor_id, name, ts, temp_c, humidity, battery, rssi)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["sensor_id"],
                        row["name"],
                        row["ts"],
                        row["temp_c"],
                        row["humidity"],
                        row["battery"],
                        row["rssi"],
                    ),
                )
            conn.commit()
        
        conn.close()
        
        return {
            "success": True,
            "rows_restored": rows_to_restore,
            "message": f"Restored {rows_to_restore} rows from {archive_filename}",
        }
    
    except Exception as e:
        return {
            "success": False,
            "rows_restored": 0,
            "message": f"Restore failed: {e}",
        }