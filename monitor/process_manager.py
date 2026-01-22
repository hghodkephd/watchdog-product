#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Process manager for BLE monitoring service
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime
import io
from typing import Optional
from config import AppConfig, load_config, save_config
import psutil
from logging_config import get_logger
from storage import DATA_DIR

# ---------------------------------------------------------------------
# Process output capture
# ---------------------------------------------------------------------

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Keep file handles alive while the subprocess is running.
_PROCESS_LOG_HANDLES: dict[int, tuple[io.TextIOWrapper, io.TextIOWrapper]] = {}

_log_process = get_logger("watchdog.process")

def get_ble_core_script() -> Path:
    """Get the path to ble_watchdog.py"""
    # Assume it's in the same directory as this file
    base_dir = Path(__file__).resolve().parent
    return base_dir / "ble_watchdog.py"


def is_process_running(pid: Optional[int]) -> bool:
    """Check if a process with given PID is running."""
    if pid is None:
        return False
    
    try:
        process = psutil.Process(pid)
        # Check if it's actually our ble_watchdog process
        cmdline = " ".join(process.cmdline())
        if "ble_watchdog" in cmdline:
            return process.is_running()
        return False
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False

def _cleanup_dead_log_handles() -> None:
    """
    Close and remove any cached stdout/stderr log file handles
    for monitor processes that are no longer alive.

    This prevents file handle leaks if the monitor process is killed
    externally (OOM, SIGKILL, crash) and our normal stop path isn't hit.
    """
    dead_pids: list[int] = []

    for pid, handles in list(_PROCESS_LOG_HANDLES.items()):
        try:
            if not psutil.pid_exists(pid):
                dead_pids.append(pid)
                continue

            p = psutil.Process(pid)

            # If PID got reused by some other process, don't keep handles
            cmdline = " ".join(p.cmdline())
            if "ble_watchdog" not in cmdline:
                dead_pids.append(pid)
                continue

            if not p.is_running():
                dead_pids.append(pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            dead_pids.append(pid)

    for pid in dead_pids:
        handles = _PROCESS_LOG_HANDLES.pop(pid, None)
        if not handles:
            continue
        for h in handles:
            try:
                h.close()
            except Exception:
                pass
            
def start_monitoring_process() -> Optional[int]:
    """
    Start the BLE monitoring service as a background process.
    Returns the process ID (PID) if successful, None otherwise.
    """
    ble_script = get_ble_core_script()
    _cleanup_dead_log_handles()
    
    if not ble_script.exists():
        raise FileNotFoundError(f"BLE core script not found at {ble_script}")
    
    # Get the Python interpreter being used
    python_exe = sys.executable

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    
    # Create log file paths
    stdout_log = LOG_DIR / "monitor_stdout.log"
    stderr_log = LOG_DIR / "monitor_stderr.log"
    
    # Open log files in append mode
    stdout_file = open(stdout_log, "a", encoding="utf-8")
    stderr_file = open(stderr_log, "a", encoding="utf-8")
    
    # Write startup marker
    timestamp = datetime.now().isoformat()
    for f in (stdout_file, stderr_file):
        f.write(f"\n{'='*60}\n")
        f.write(f"=== Monitor started at {timestamp} ===\n")
        f.write(f"{'='*60}\n")
        f.flush()
    
    try:
        # Start the process in the background
        if sys.platform == "win32":
            # Windows: Use CREATE_NEW_PROCESS_GROUP to detach
            process = subprocess.Popen(
                [python_exe, str(ble_script)],
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                cwd=ble_script.parent,
            )
            
        else:
            # Linux/Mac: Use nohup-style approach
            process = subprocess.Popen(
                [python_exe, str(ble_script)],
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
                cwd=ble_script.parent,
            )
        # IMPORTANT: keep the file handles alive (do NOT close them here)
        _PROCESS_LOG_HANDLES[process.pid] = (stdout_file, stderr_file)

        # Give it a moment to start
        time.sleep(2)
        
        # Verify it's running
        if is_process_running(process.pid):
            return process.pid
        else:
            # Process failed to start; close handles + forget them
            handles = _PROCESS_LOG_HANDLES.pop(process.pid, None)
            if handles:
                for h in handles:
                    try:
                        h.close()
                    except Exception:
                        pass
            return None
            
    except Exception:
        _log_process.exception("Error starting monitoring process")
        # Close opened files to avoid leaks
        try:
            stdout_file.close()
        except Exception:
            pass
        try:
            stderr_file.close()
        except Exception:
            pass
        return None


def stop_monitoring_process(pid: Optional[int]) -> bool:
    """
    Stop the BLE monitoring service.
    Returns True if successful, False otherwise.
    """
    if pid is None:
        return False
    
    try:
        process = psutil.Process(pid)
        
        # Verify it's our process
        cmdline = " ".join(process.cmdline())
        if "ble_watchdog" not in cmdline:
            return False
        
        # Try graceful termination first
        process.terminate()
        
        # Wait up to 5 seconds for it to stop
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            # Force kill if it doesn't stop gracefully
            process.kill()
            process.wait(timeout=2)
        
        # Close any log file handles we're keeping alive
        handles = _PROCESS_LOG_HANDLES.pop(pid, None)
        if handles:
            for h in handles:
                try:
                    h.close()
                except Exception:
                    pass
        return True
        
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        # Close any log file handles we're keeping alive
        handles = _PROCESS_LOG_HANDLES.pop(pid, None)
        if handles:
            for h in handles:
                try:
                    h.close()
                except Exception:
                    pass
        return False

def get_recent_logs(n_lines: int = 100) -> dict:
    """Return last n_lines from the monitor's stdout/stderr log files.

    Returns:
        dict: {'stdout': [...], 'stderr': [...]} where each value is a list of
        recent lines (strings).
    """
    result = {"stdout": [], "stderr": []}

    stdout_log = LOG_DIR / "monitor_stdout.log"
    stderr_log = LOG_DIR / "monitor_stderr.log"

    for key, path in (("stdout", stdout_log), ("stderr", stderr_log)):
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            result[key] = lines[-n_lines:] if len(lines) > n_lines else lines
        except Exception:
            # Best-effort: don't let log reading break the app
            pass

    return result

def get_monitoring_status() -> dict:
    """
    Get the current status of the monitoring service.
    Returns dict with status info.
    """
    cfg = load_config()
    _cleanup_dead_log_handles()
    
    pid = cfg.monitoring.process_pid if hasattr(cfg.monitoring, 'process_pid') else None
    is_running = is_process_running(pid)
    
    status = {
        "is_running": is_running,
        "pid": pid,
        "config_says_running": cfg.monitoring.is_running,
        "is_healthy": False,  # Will check data freshness
    }
    
    if is_running and pid:
        try:
            process = psutil.Process(pid)
            status["cpu_percent"] = process.cpu_percent(interval=0.1)
            status["memory_mb"] = process.memory_info().rss / (1024 * 1024)
            status["create_time"] = process.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    # Check if actually collecting data
    try:
        import sqlite3
        from storage import get_db_path
        
        db_path = get_db_path()
        conn = sqlite3.connect(db_path)
        with conn:
            result = conn.execute("SELECT MAX(ts) FROM readings").fetchone()
            if result and result[0]:
                from datetime import datetime
                last_data_time = datetime.fromtimestamp(result[0])
                age_minutes = (datetime.now() - last_data_time).total_seconds() / 60
                status["data_age_minutes"] = age_minutes
                status["is_healthy"] = age_minutes < 5  # Fresh if < 5 min old
        conn.close()
    except Exception:
        pass
    
    return status


def start_monitoring() -> tuple[bool, str, Optional[int]]:
    """
    Start monitoring service and update config.
    Returns (success: bool, message: str, pid: Optional[int])
    """
    cfg = load_config()
    
    # Check if already running
    if hasattr(cfg.monitoring, 'process_pid') and is_process_running(cfg.monitoring.process_pid):
        return (True, "Monitoring is already running", cfg.monitoring.process_pid)
    
    # Start the process
    pid = start_monitoring_process()
    
    if pid:
        # Update config
        cfg.monitoring.is_running = True
        cfg.monitoring.last_scan_time = time.time()
        cfg.monitoring.process_pid = pid
        save_config(cfg)
        return (True, f"Monitoring started successfully (PID: {pid})", pid)
    else:
        return (False, "Failed to start monitoring process", None)


def stop_monitoring() -> tuple[bool, str]:
    """
    Stop monitoring service and update config.
    Returns (success: bool, message: str)
    """
    cfg = load_config()
    
    pid = cfg.monitoring.process_pid if hasattr(cfg.monitoring, 'process_pid') else None
    
    if not pid:
        # Update config anyway
        cfg.monitoring.is_running = False
        save_config(cfg)
        return (True, "No monitoring process found to stop")
    
    # Stop the process
    success = stop_monitoring_process(pid)
    
    # Update config
    cfg.monitoring.is_running = False
    if hasattr(cfg.monitoring, 'process_pid'):
        cfg.monitoring.process_pid = None
    save_config(cfg)
    
    if success:
        return (True, f"Monitoring stopped successfully (PID: {pid})")
    else:
        return (False, f"Failed to stop monitoring process (PID: {pid})")