#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Memory instrumentation for Watchdog dashboard.

DISABLED by default. Enable via environment variable:
    export WATCHDOG_MEM_PROBES=1

When enabled, logs RSS memory and optional tracemalloc snapshots
with "[MEM]" prefix, visible via journalctl.

Usage in app.py:
    from mem_probes import log_mem, start_trace, stop_trace_and_diff, cleanup_render

Instrumentation points (from audit):
    - Script start (after imports)
    - Before/after DataFrame creation
    - Before/after chart data fetch (Hour→Day transition)
    - Before/after chart creation
    - Script end / gc.collect()
"""

from __future__ import annotations

import gc
import os
import sys
import tracemalloc
from typing import Optional

# --------------------------------------------------------------------------
# Configuration: Enable via WATCHDOG_MEM_PROBES=1
# --------------------------------------------------------------------------

_MEM_PROBES_ENABLED = os.environ.get("WATCHDOG_MEM_PROBES", "0") == "1"
_TRACEMALLOC_ENABLED = os.environ.get("WATCHDOG_TRACEMALLOC", "0") == "1"

# Track baseline RSS for delta reporting
_baseline_rss_mb: Optional[float] = None
_tracemalloc_snap: Optional[tracemalloc.Snapshot] = None


def is_enabled() -> bool:
    """Check if memory probes are enabled."""
    return _MEM_PROBES_ENABLED


def _get_rss_mb() -> float:
    """Get current process RSS in MB using psutil."""
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0
    except Exception:
        return 0.0


def _get_system_mem_percent() -> float:
    """Get system memory usage percentage."""
    try:
        import psutil
        return psutil.virtual_memory().percent
    except ImportError:
        return 0.0
    except Exception:
        return 0.0


def log_mem(label: str) -> None:
    """
    Log current RSS memory with a label.
    
    Output format (visible in journalctl):
        [MEM] label: RSS=123.4MB (Δ+5.2MB) System=67%
    
    Args:
        label: Descriptive label for this measurement point
    """
    if not _MEM_PROBES_ENABLED:
        return
    
    global _baseline_rss_mb
    
    rss_mb = _get_rss_mb()
    sys_percent = _get_system_mem_percent()
    
    if _baseline_rss_mb is None:
        _baseline_rss_mb = rss_mb
        delta_str = "(baseline)"
    else:
        delta = rss_mb - _baseline_rss_mb
        delta_str = f"(Δ{delta:+.1f}MB)"
    
    # Print to stderr for systemd journal capture
    print(f"[MEM] {label}: RSS={rss_mb:.1f}MB {delta_str} System={sys_percent:.0f}%", 
          file=sys.stderr, flush=True)


def reset_baseline() -> None:
    """Reset the baseline RSS for delta calculations."""
    global _baseline_rss_mb
    _baseline_rss_mb = None


def start_trace(label: str = "trace_start") -> None:
    """
    Start tracemalloc snapshot for memory attribution.
    
    Only active if WATCHDOG_TRACEMALLOC=1 is set (more expensive than RSS logging).
    """
    global _tracemalloc_snap
    
    if not (_MEM_PROBES_ENABLED and _TRACEMALLOC_ENABLED):
        return
    
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    
    _tracemalloc_snap = tracemalloc.take_snapshot()
    print(f"[MEM] {label}: tracemalloc snapshot captured", file=sys.stderr, flush=True)


def stop_trace_and_diff(label: str = "trace_end", top_n: int = 5) -> None:
    """
    Take a new snapshot and diff against the previous one.
    
    Prints top N memory allocations by file:line.
    """
    global _tracemalloc_snap
    
    if not (_MEM_PROBES_ENABLED and _TRACEMALLOC_ENABLED):
        return
    
    if _tracemalloc_snap is None:
        print(f"[MEM] {label}: no previous snapshot to diff", file=sys.stderr, flush=True)
        return
    
    try:
        snap_after = tracemalloc.take_snapshot()
        top_stats = snap_after.compare_to(_tracemalloc_snap, 'lineno')
        
        print(f"[MEM] {label}: Top {top_n} memory changes:", file=sys.stderr, flush=True)
        for stat in top_stats[:top_n]:
            print(f"[MEM]   {stat}", file=sys.stderr, flush=True)
        
        _tracemalloc_snap = snap_after  # Update for next diff
        
    except Exception as e:
        print(f"[MEM] {label}: tracemalloc diff failed: {e}", file=sys.stderr, flush=True)


def cleanup_render() -> None:
    """
    Call at end of Streamlit render cycle.
    
    - Runs gc.collect() to free unreferenced objects
    - Logs memory after cleanup
    - Warns if system memory is critically low
    """
    # Always run gc.collect() for stability (even if probes disabled)
    collected = gc.collect()
    
    if not _MEM_PROBES_ENABLED:
        return
    
    rss_mb = _get_rss_mb()
    sys_percent = _get_system_mem_percent()
    
    print(f"[MEM] cleanup_render: gc.collect() freed {collected} objects, "
          f"RSS={rss_mb:.1f}MB, System={sys_percent:.0f}%", 
          file=sys.stderr, flush=True)
    
    # Warn if system memory is critically low (>85%)
    if sys_percent > 85:
        print(f"[MEM] WARNING: System memory pressure high ({sys_percent:.0f}%)", 
              file=sys.stderr, flush=True)


def check_memory_pressure() -> bool:
    """
    Check if system is under memory pressure.
    
    Returns True if memory usage > 80%, indicating caution.
    Can be used to skip expensive operations or show warnings.
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.percent > 80
    except ImportError:
        return False
    except Exception:
        return False


def get_memory_stats() -> dict:
    """
    Get current memory statistics.
    
    Returns dict with:
        - rss_mb: Process RSS in MB
        - system_percent: System memory usage %
        - system_available_mb: Available system memory in MB
        - probes_enabled: Whether probes are active
    """
    stats = {
        "rss_mb": 0.0,
        "system_percent": 0.0,
        "system_available_mb": 0.0,
        "probes_enabled": _MEM_PROBES_ENABLED,
    }
    
    try:
        import psutil
        process = psutil.Process()
        stats["rss_mb"] = process.memory_info().rss / (1024 * 1024)
        
        vm = psutil.virtual_memory()
        stats["system_percent"] = vm.percent
        stats["system_available_mb"] = vm.available / (1024 * 1024)
    except ImportError:
        pass
    except Exception:
        pass
    
    return stats


# --------------------------------------------------------------------------
# Convenience: Context manager for profiling specific blocks
# --------------------------------------------------------------------------

class MemoryProfileBlock:
    """
    Context manager for profiling a code block.
    
    Usage:
        with MemoryProfileBlock("chart_render"):
            # expensive code
            chart = alt.Chart(...)
    
    Logs memory before and after the block.
    """
    
    def __init__(self, label: str):
        self.label = label
        self.start_rss: float = 0.0
    
    def __enter__(self):
        if _MEM_PROBES_ENABLED:
            self.start_rss = _get_rss_mb()
            log_mem(f"{self.label}_start")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if _MEM_PROBES_ENABLED:
            end_rss = _get_rss_mb()
            delta = end_rss - self.start_rss
            print(f"[MEM] {self.label}_end: RSS={end_rss:.1f}MB (block Δ{delta:+.1f}MB)",
                  file=sys.stderr, flush=True)
        return False


# --------------------------------------------------------------------------
# Module initialization
# --------------------------------------------------------------------------

if _MEM_PROBES_ENABLED:
    print("[MEM] Memory probes ENABLED (WATCHDOG_MEM_PROBES=1)", file=sys.stderr, flush=True)
    if _TRACEMALLOC_ENABLED:
        print("[MEM] Tracemalloc ENABLED (WATCHDOG_TRACEMALLOC=1)", file=sys.stderr, flush=True)
