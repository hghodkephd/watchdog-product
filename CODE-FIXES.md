# Watchdog Code Fixes

**Goal:** Apply specific code fixes to improve production readiness.  
**Prerequisite:** Complete repository merge first (see MERGE-TASK-LIST.md)  
**Time Estimate:** 3-4 hours total

---

## Priority Levels

- **P0 (Ship-Blocker):** Must fix before shipping
- **P1 (High):** Fix before marketing push
- **P2 (Medium):** Fix within first month
- **P3 (Low):** Nice to have

---

## FIX-01: UTF-8 Encoding in BLE Scanner

**Priority:** P0 (Ship-Blocker)  
**File:** `monitor/ble_scanner.py`  
**Issue:** Mojibake (double-encoded UTF-8) in degree symbols

### Current Code (around line 285)

```python
_log_ble.info(
    "%s | Temp: %5.1fÃ‚Â°C (%5.1fÃ‚Â°F) | Humidity: %4.1f%% | Battery: %3d%% | RSSI: %4d dBm",
    f"{reading.sensor_id:15}",
    reading.temp_c,
    temp_f,
    reading.humidity,
    reading.battery,
    reading.rssi,
)
```

### Fixed Code

```python
_log_ble.info(
    "%s | Temp: %5.1f°C (%5.1f°F) | Humidity: %4.1f%% | Battery: %3d%% | RSSI: %4d dBm",
    f"{reading.sensor_id:15}",
    reading.temp_c,
    temp_f,
    reading.humidity,
    reading.battery,
    reading.rssi,
)
```

### What Changed

Replace `Ã‚Â°` with `°` (proper degree symbol) in two places.

### Validation

```bash
cd monitor
python -c "
from ble_scanner import main
print('Import OK - check the source file visually')
"
# Visually confirm the degree symbols look correct in the file
grep -n "°C" ble_scanner.py  # Should show clean degree symbols
```

---

## FIX-02: Remove Broken Service File Reference

**Priority:** P0 (Ship-Blocker)  
**File:** Root-level service files (if not already deleted in merge)  
**Issue:** `watchdog-monitor.service` at root references non-existent module `ble_core`

### Current Code (root watchdog-monitor.service, line ~15)

```ini
ExecStart=/home/pi/watchdog-env/bin/python3 -m ble_core
```

### Resolution

This file should have been deleted in MERGE-TASK-LIST.md Task 4.2. If it still exists:

```bash
cd monitor
rm -f watchdog-monitor.service
rm -f watchdog-dashboard.service
```

The correct service files are in `monitor/deploy/` and use:
```ini
ExecStart=/home/pi/watchdog/venv/bin/python ble_watchdog.py
```

### Validation

```bash
# Verify only deploy/ versions exist
ls monitor/*.service 2>/dev/null && echo "ERROR: Root service files still exist" || echo "OK: No root service files"
ls monitor/deploy/*.service  # Should show 2 files

# Verify correct ExecStart
grep "ExecStart" monitor/deploy/watchdog-monitor.service
# Should contain: ble_watchdog.py (NOT ble_core)
```

---

## FIX-03: Add Sensor Config Validation

**Priority:** P1 (High)  
**File:** `monitor/config.py`  
**Issue:** User can set min_temp > max_temp without error

### Current Code (validate_config function, around line 180)

The function validates alerts but not sensor temperature ranges.

### Add This Code

Inside `validate_config()`, after the alerts validation block, add:

```python
    # Validate sensor configs
    if cfg.sensors:
        for sensor_id, sensor_cfg in cfg.sensors.items():
            min_t = getattr(sensor_cfg, "min_temp_c", None)
            max_t = getattr(sensor_cfg, "max_temp_c", None)
            if min_t is not None and max_t is not None:
                if min_t > max_t:
                    errors.append(
                        f"Sensor '{sensor_id}': min_temp_c ({min_t}) cannot exceed max_temp_c ({max_t})"
                    )
```

### Validation

```bash
cd monitor
python -c "
from config import AppConfig, SensorConfig, validate_config

# Test invalid config
cfg = AppConfig(sensors={})
cfg.sensors['test'] = SensorConfig(id='test', name='Test', min_temp_c=30.0, max_temp_c=20.0)
is_valid, errors = validate_config(cfg)
assert not is_valid, 'Should be invalid'
assert any('min_temp' in e for e in errors), 'Should mention min_temp'
print('Validation correctly catches min > max')

# Test valid config  
cfg.sensors['test'].min_temp_c = 10.0
cfg.sensors['test'].max_temp_c = 30.0
is_valid, errors = validate_config(cfg)
assert is_valid, f'Should be valid but got: {errors}'
print('Validation passes for valid config')
"
```

---

## FIX-04: Add File Handle Cleanup

**Priority:** P1 (High)  
**File:** `monitor/process_manager.py`  
**Issue:** File handles for monitor process logs can leak if dashboard restarts

### Current Code (around line 25)

```python
_PROCESS_LOG_HANDLES: dict[int, tuple[io.TextIOWrapper, io.TextIOWrapper]] = {}
```

Handles are stored but never cleaned up for dead processes.

### Add This Function (after the existing imports, before other functions)

```python
def _cleanup_orphaned_handles():
    """Clean up file handles for processes that no longer exist."""
    orphaned = []
    for pid in list(_PROCESS_LOG_HANDLES.keys()):
        if not is_process_running(pid):
            orphaned.append(pid)
    
    for pid in orphaned:
        handles = _PROCESS_LOG_HANDLES.pop(pid, None)
        if handles:
            for h in handles:
                try:
                    h.close()
                except Exception:
                    pass
    
    if orphaned:
        _log_process.info("Cleaned up %d orphaned log handles", len(orphaned))
```

### Add Cleanup Call

At the start of `start_monitoring_process()`, add:

```python
def start_monitoring_process() -> Optional[int]:
    """..."""
    # Clean up any orphaned handles from previous runs
    _cleanup_orphaned_handles()
    
    ble_script = get_ble_core_script()
    # ... rest of function
```

### Validation

```bash
cd monitor
python -c "
from process_manager import _cleanup_orphaned_handles, _PROCESS_LOG_HANDLES
# Should run without error even with empty dict
_cleanup_orphaned_handles()
print('Cleanup function works')
"
```

---

## FIX-05: Add Automatic BLE Cache Pruning

**Priority:** P1 (High)  
**File:** `monitor/ble_watchdog.py`  
**Issue:** `prune_stale_readings()` exists in ble_scanner.py but is never called

### Current Code (_watchdog_thread_func, around line 95)

The watchdog thread monitors data flow but doesn't prune stale readings.

### Add This Code

At the start of `_watchdog_thread_func()`, add tracking variable:

```python
def _watchdog_thread_func(self):
    """..."""
    _log_core.info("Starting watchdog monitoring thread")
    
    # Track time for periodic tasks
    last_archive_time = time.time()
    last_prune_time = time.time()  # ADD THIS LINE
    ARCHIVE_INTERVAL = 86400
    PRUNE_INTERVAL = 3600  # ADD THIS LINE - prune every hour
```

Inside the `while self.running:` loop, after the archive check, add:

```python
            # === NEW: Periodic cache pruning ===
            if (now - last_prune_time) > PRUNE_INTERVAL:
                try:
                    from ble_scanner import prune_stale_readings
                    removed = prune_stale_readings(max_age_seconds=3600)
                    if removed > 0:
                        _log_core.info("Pruned %d stale readings from BLE cache", removed)
                except Exception as e:
                    _log_core.warning("Cache pruning failed: %s", e)
                last_prune_time = now
```

### Validation

```bash
cd monitor
python -c "
from ble_scanner import prune_stale_readings, get_cache_stats
# Should work even with empty cache
removed = prune_stale_readings(max_age_seconds=3600)
print(f'Pruned {removed} readings')
stats = get_cache_stats()
print(f'Cache stats: {stats}')
"
```

---

## FIX-06: Improve Desktop App Device Verification

**Priority:** P1 (High)  
**File:** `desktop/discovery.py`  
**Issue:** `verify_connection()` only checks if port is open, not if it's actually Watchdog

### Current Code (around line 15)

```python
def verify_connection(ip: str, port: int = 80, timeout: float = 0.5) -> bool:
    """..."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except (socket.error, OSError):
        return False
```

### Replace With

```python
def verify_connection(ip: str, port: int = 80, timeout: float = 0.5) -> bool:
    """
    Check if Watchdog is reachable at the given IP.
    
    Verifies both that the port is open AND that it's serving
    a Watchdog dashboard (not just any HTTP server).
    
    Args:
        ip: IP address to check
        port: Port to connect to (default 80 for Streamlit redirect, 8501 for direct)
        timeout: Connection timeout in seconds
    
    Returns:
        True if Watchdog dashboard is found, False otherwise
    """
    import urllib.request
    import urllib.error
    
    # Try both common ports
    ports_to_try = [port]
    if port == 80:
        ports_to_try.append(8501)
    elif port == 8501:
        ports_to_try.append(80)
    
    for try_port in ports_to_try:
        try:
            url = f"http://{ip}:{try_port}/"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=timeout) as response:
                # Read first 1000 bytes to check for Watchdog identifiers
                content = response.read(1000).decode('utf-8', errors='ignore')
                
                # Check for Watchdog-specific content
                if 'Watchdog' in content or 'Environmental Monitor' in content or 'streamlit' in content.lower():
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            continue
    
    return False
```

### Validation

```bash
cd desktop
python -c "
from discovery import verify_connection

# Test against a known non-Watchdog address (should return False)
result = verify_connection('8.8.8.8', timeout=1.0)
print(f'Google DNS (should be False): {result}')

# If you have a Watchdog running, test it:
# result = verify_connection('192.168.x.x', timeout=2.0)
# print(f'Watchdog (should be True): {result}')

print('Function executes without error')
"
```

---

## FIX-07: Add mDNS Hostname Configuration to Deployment

**Priority:** P1 (High)  
**File:** `monitor/deploy_watchdog.sh`  
**Issue:** Desktop app searches for `watchdog.local` but nothing configures this hostname

### Current Code

Neither `deploy_watchdog.sh` nor `deploy_pi.sh` set the hostname.

### Add This Section

In `deploy_watchdog.sh`, after the system update section, add:

```bash
# Configure hostname for mDNS discovery
echo -e "${GREEN}[2/9] Configuring hostname for network discovery...${NC}"
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" != "watchdog" ]; then
    echo "Setting hostname to 'watchdog' for mDNS discovery..."
    sudo hostnamectl set-hostname watchdog
    
    # Update /etc/hosts
    if ! grep -q "watchdog" /etc/hosts; then
        echo "127.0.1.1    watchdog" | sudo tee -a /etc/hosts
    fi
    
    echo -e "${YELLOW}Hostname changed. Reboot required for mDNS (watchdog.local) to work.${NC}"
else
    echo "Hostname already set to 'watchdog'"
fi
```

**Note:** Also update the step numbers in subsequent sections (2/8 becomes 3/9, etc.)

### Validation

After running on Pi:
```bash
hostname  # Should output: watchdog
ping watchdog.local  # Should resolve (from another device on same network)
```

---

## FIX-08: Add Security Documentation

**Priority:** P0 (Ship-Blocker) - Documentation only  
**File:** `docs/SECURITY.md` (new file)  
**Issue:** Dashboard has no authentication - users should be informed

### Create This File

Create `docs/SECURITY.md`:

```markdown
# Watchdog Security Information

## Network Access

Watchdog is designed for **trusted home networks**. The web dashboard runs on port 8501 and is accessible to any device on your local network without authentication.

### What This Means

- Anyone on your WiFi can view sensor data
- Anyone on your WiFi can change settings
- No username/password is required

### Recommendations

1. **Do not expose Watchdog to the internet**
   - Do not set up port forwarding to your Watchdog
   - Do not put Watchdog in a DMZ
   
2. **Secure your WiFi network**
   - Use WPA2 or WPA3 encryption
   - Use a strong WiFi password
   - Consider a separate IoT network if your router supports it

3. **Physical security**
   - Anyone with physical access to the Raspberry Pi can access all data
   - The SD card contains your complete sensor history

## Data Storage

All data is stored locally on the Raspberry Pi:
- Sensor readings: SQLite database in `data/data.sqlite3`
- Configuration: JSON file (location varies by OS)
- Logs: `data/logs/watchdog.log`

No data is sent to external servers. Watchdog works entirely offline after initial setup.

## Future Improvements

Authentication is on the roadmap for a future release. If this is critical for your use case, please contact support.
```

### Also Add to Main README

In `monitor/README.md`, add a Security section:

```markdown
## Security

⚠️ **Network Security:** Watchdog is designed for trusted home networks. Anyone on your WiFi can access the dashboard. Do not expose to the internet.

See [docs/SECURITY.md](../docs/SECURITY.md) for details.
```

### Validation

- [ ] `docs/SECURITY.md` exists and is readable
- [ ] Main README mentions security

---

## FIX-09: Add SQLite Connection Context Manager Support

**Priority:** P2 (Medium)  
**File:** `monitor/storage.py`  
**Issue:** Connections opened without guaranteed cleanup if exceptions occur

### Current Code (get_connection function)

```python
def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn
```

### Enhanced Code

The sqlite3.Connection already supports context manager protocol, but we should document this and provide a helper:

```python
from contextlib import contextmanager

@contextmanager  
def get_db_connection(db_path: Optional[Path] = None):
    """
    Context manager for database connections.
    
    Usage:
        with get_db_connection() as conn:
            conn.execute("SELECT ...")
        # Connection automatically closed
    """
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()
```

### Update app.py Usage (Example)

Find patterns like:
```python
conn = get_connection()
# ... code ...
conn.close()
```

Replace with:
```python
with get_db_connection() as conn:
    # ... code ...
# No explicit close needed
```

### Validation

```bash
cd monitor
python -c "
from storage import get_db_connection

with get_db_connection() as conn:
    result = conn.execute('SELECT 1').fetchone()
    print(f'Query result: {result}')
print('Connection closed automatically')
"
```

---

## FIX-10: Add Weather API Rate Limiting

**Priority:** P2 (Medium)  
**File:** `monitor/weather_api.py`  
**Issue:** No rate limiting - rapid refreshes could get blocked by Open-Meteo

### Add This Code (at module level, after imports)

```python
import threading

# Simple rate limiting
_RATE_LIMIT_LOCK = threading.Lock()
_LAST_REQUEST_TIME = 0.0
_MIN_REQUEST_INTERVAL = 5.0  # seconds between requests


def _rate_limit():
    """Ensure minimum interval between API requests."""
    global _LAST_REQUEST_TIME
    
    with _RATE_LIMIT_LOCK:
        now = time.time()
        elapsed = now - _LAST_REQUEST_TIME
        
        if elapsed < _MIN_REQUEST_INTERVAL:
            sleep_time = _MIN_REQUEST_INTERVAL - elapsed
            time.sleep(sleep_time)
        
        _LAST_REQUEST_TIME = time.time()
```

### Add Call to Existing Functions

At the start of `_request_with_retry()`, add:

```python
def _request_with_retry(...):
    """..."""
    _rate_limit()  # ADD THIS LINE
    
    last_exception = None
    # ... rest of function
```

### Validation

```bash
cd monitor
python -c "
import time
from weather_api import fetch_current_weather

# Make two rapid requests - second should be delayed
start = time.time()
fetch_current_weather(42.3601, -71.0589)  # Boston
fetch_current_weather(42.3601, -71.0589)  # Boston again
elapsed = time.time() - start

print(f'Two requests took {elapsed:.1f}s')
assert elapsed >= 5.0, 'Rate limiting not working'
print('Rate limiting working correctly')
"
```

---

## Summary Checklist

### P0 - Ship-Blockers
- [ ] FIX-01: UTF-8 encoding in ble_scanner.py
- [ ] FIX-02: Remove broken service file (or verify deleted in merge)
- [ ] FIX-08: Security documentation

### P1 - High Priority
- [ ] FIX-03: Sensor config validation
- [ ] FIX-04: File handle cleanup
- [ ] FIX-05: Automatic BLE cache pruning
- [ ] FIX-06: Desktop app device verification
- [ ] FIX-07: mDNS hostname configuration

### P2 - Medium Priority
- [ ] FIX-09: SQLite connection context manager
- [ ] FIX-10: Weather API rate limiting

---

## After All Fixes

1. Run full validation checklist from merge task list
2. Test on actual Pi hardware if possible
3. Commit all changes with descriptive messages
4. Push to GitHub
