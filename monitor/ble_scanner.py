"""
Govee Monitor - Simple BLE Scanner
Real-time monitoring only, no data persistence.

Fixed: Thread-safety, device detection robustness, clean shutdown
"""
import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Dict, Callable, Optional

from bleak import BleakScanner
from bleak.exc import BleakError
from logging_config import get_logger


_log_ble = get_logger("watchdog.ble")

# Memory management: prevent unbounded growth
MAX_CACHED_SENSORS = 100  # Max unique sensors to track in memory

# Module-level storage protected by lock (thread-safe)
_READINGS_LOCK = threading.Lock()
_READINGS: Dict[str, 'SensorReading'] = {}
_STOP_EVENT = threading.Event()


@dataclass
class SensorReading:
    """Single sensor reading."""
    sensor_id: str
    temp_c: float
    humidity: float
    battery: int
    rssi: int
    timestamp: float


def decode_govee(advertisement_data) -> Optional[tuple]:
    """
    Decode temperature, humidity, battery from Govee H5075 manufacturer data.
    
    Returns:
        (temp_c, humidity, battery) or None if not valid
    """
    md = advertisement_data.manufacturer_data
    
    # Govee uses manufacturer ID 0xEC88 (60552 decimal)
    payload = md.get(0xEC88) or md.get(60552)
    if not payload or len(payload) < 5:
        return None
    
    data = payload[1:5]
    temphum_raw = int.from_bytes(data[0:3], "big")
    
    # Check for negative temperature
    is_negative = (temphum_raw & 0x800000) != 0
    temphum_raw &= ~0x800000
    
    # Extract humidity (last 3 digits / 10)
    hum10 = temphum_raw % 1000
    humidity = hum10 / 10.0
    
    # Extract temperature
    temp_c = (temphum_raw - hum10) / 10000.0
    if is_negative:
        temp_c *= -1
    
    battery = int(data[3])
    
    return temp_c, humidity, battery


def prune_stale_readings(max_age_seconds: int = 3600) -> int:
    """
    Remove readings older than max_age_seconds.
    
    Thread-safe: Uses _READINGS_LOCK.
    
    Note: For long-running applications, periodically call this function
    to remove old entries and free memory. The cache automatically limits to
    MAX_CACHED_SENSORS entries, but stale data can accumulate if sensors
    go offline.
    
    Args:
        max_age_seconds: Maximum age of readings to keep (default 1 hour)
    
    Returns:
        Number of readings removed
    """
    now = time.time()
    to_remove = []
    
    with _READINGS_LOCK:
        for sensor_id, reading in _READINGS.items():
            age = now - reading.timestamp
            if age > max_age_seconds:
                to_remove.append(sensor_id)
        
        for sensor_id in to_remove:
            del _READINGS[sensor_id]
    
    return len(to_remove)


def get_cache_stats() -> dict:
    """
    Get statistics about the readings cache.
    
    Returns:
        dict with:
            - count: number of cached readings
            - max_size: maximum allowed
            - oldest_seconds: age of oldest reading
            - newest_seconds: age of newest reading
    """
    now = time.time()
    
    with _READINGS_LOCK:
        if not _READINGS:
            return {
                'count': 0,
                'max_size': MAX_CACHED_SENSORS,
                'oldest_seconds': None,
                'newest_seconds': None,
            }
        
        timestamps = [r.timestamp for r in _READINGS.values()]
        oldest = min(timestamps)
        newest = max(timestamps)
        
        return {
            'count': len(_READINGS),
            'max_size': MAX_CACHED_SENSORS,
            'oldest_seconds': now - oldest,
            'newest_seconds': now - newest,
        }


def get_readings() -> Dict[str, SensorReading]:
    """
    Thread-safe getter for current readings.
    Returns a copy to avoid race conditions.
    """
    with _READINGS_LOCK:
        return _READINGS.copy()


def clear_readings():
    """Thread-safe clear of all readings."""
    with _READINGS_LOCK:
        _READINGS.clear()


async def check_ble_adapter() -> dict:
    """Check if a Bluetooth adapter is available and working.
    
    Performs a quick test scan to verify BLE functionality.
    
    Returns:
        dict with:
            - available: bool (True if BLE is working)
            - adapter_name: str or None
            - error: str or None (error message if not available)
            - error_type: str or None (categorized error type)
    """
    try:
        # Create scanner to test adapter access
        scanner = BleakScanner()
        
        # Attempt a very short scan (0.5 seconds)
        # This will fail quickly if no adapter is present
        try:
            devices = await asyncio.wait_for(
                scanner.discover(timeout=0.5),
                timeout=2.0  # Overall timeout including setup
            )
        except asyncio.TimeoutError:
            # Timeout usually means adapter exists but is slow
            # This is okay - we just wanted to test connectivity
            pass
        
        return {
            'available': True,
            'adapter_name': 'default',
            'error': None,
            'error_type': None,
        }
    
    except BleakError as e:
        error_str = str(e).lower()
        
        # Categorize common errors
        if 'no bluetooth adapters' in error_str or 'adapter' in error_str:
            error_type = 'no_adapter'
            suggestion = "No Bluetooth adapter found. On Pi Zero 2 W, you need a USB Bluetooth adapter."
        elif 'dbus' in error_str:
            error_type = 'dbus_error'
            suggestion = "D-Bus error. Try: sudo systemctl restart bluetooth"
        elif 'permission' in error_str or 'access' in error_str:
            error_type = 'permission_error'
            suggestion = "Permission denied. User may need to be in 'bluetooth' group."
        else:
            error_type = 'bleak_error'
            suggestion = f"BLE error: {e}"
        
        return {
            'available': False,
            'adapter_name': None,
            'error': suggestion,
            'error_type': error_type,
        }
    
    except Exception as e:
        return {
            'available': False,
            'adapter_name': None,
            'error': f"Unexpected error checking BLE: {e}",
            'error_type': 'unknown',
        }


def check_ble_adapter_sync() -> dict:
    """Synchronous wrapper for check_ble_adapter().
    
    Use this from non-async code (e.g., startup checks).
    
    Returns:
        Same dict as check_ble_adapter()
    """
    try:
        # Try to get existing event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't use asyncio.run() if loop is running
            # Create a new loop in a thread (fallback)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, check_ble_adapter())
                return future.result(timeout=5.0)
        else:
            return loop.run_until_complete(check_ble_adapter())
    except RuntimeError:
        # No event loop exists
        return asyncio.run(check_ble_adapter())
    except Exception as e:
        return {
            'available': False,
            'adapter_name': None,
            'error': f"Could not check adapter: {e}",
            'error_type': 'check_failed',
        }


def get_ble_troubleshooting_tips() -> list:
    """Return list of common BLE troubleshooting steps.
    
    Returns:
        List of tip strings
    """
    return [
        "1. Check if Bluetooth adapter is connected: lsusb | grep -i bluetooth",
        "2. Check if Bluetooth service is running: systemctl status bluetooth",
        "3. Restart Bluetooth service: sudo systemctl restart bluetooth",
        "4. Check adapter power: bluetoothctl power on",
        "5. Check rfkill status: rfkill list bluetooth",
        "6. Unblock if blocked: sudo rfkill unblock bluetooth",
        "7. Verify user is in bluetooth group: groups $USER",
        "8. Add user to group: sudo usermod -a -G bluetooth $USER",
    ]


class GoveeScanner:
    """
    Simple BLE scanner for Govee H5075 sensors.
    
    Thread-safe real-time scanning with clean shutdown support.
    """
    
    def __init__(self, callback: Optional[Callable[[SensorReading], None]] = None):
        """
        Initialize scanner.
        
        Args:
            callback: Optional function called when new reading arrives
        """
        self.callback = callback
        self._scanner = None
        self._error_count = 0
    
    def _detection_callback(self, device, advertisement_data):
        """
        Internal callback when BLE advertisement is detected.
        Thread-safe: writes to module-level storage with lock.
        """
        try:
            # Robust device name detection (fallback chain)
            # Some BLE stacks don't populate local_name reliably
            name = (
                advertisement_data.local_name or 
                device.name or 
                device.address
            )
            
            # Only process Govee H5075 sensors
            if not name or "GVH5075" not in name:
                return
            
            decoded = decode_govee(advertisement_data)
            if not decoded:
                return
            
            temp_c, humidity, battery = decoded
            
            reading = SensorReading(
                sensor_id=name,
                temp_c=temp_c,
                humidity=humidity,
                battery=battery,
                rssi=advertisement_data.rssi,
                timestamp=time.time()
            )
            
            # Thread-safe update to module-level storage
            with _READINGS_LOCK:
                _READINGS[name] = reading
                
                # Enforce maximum cache size
                if len(_READINGS) > MAX_CACHED_SENSORS:
                    # Find and remove the oldest reading
                    oldest_id = None
                    oldest_time = float('inf')
                    
                    for sid, r in _READINGS.items():
                        if r.timestamp < oldest_time:
                            oldest_time = r.timestamp
                            oldest_id = sid
                    
                    if oldest_id and oldest_id != name:  # Don't remove what we just added
                        del _READINGS[oldest_id]
                        _log_ble.warning(
                            "Cache limit exceeded (%d sensors), removed oldest: %s",
                            MAX_CACHED_SENSORS,
                            oldest_id
                        )
            
            # Notify callback (optional)
            if self.callback:
                self.callback(reading)
        
        except Exception:
            _log_ble.exception("Error processing advertisement from %s", 
                              getattr(device, 'address', 'unknown'))
            # Track error frequency for alerting
            self._error_count = getattr(self, '_error_count', 0) + 1
            if self._error_count % 100 == 0:
                _log_ble.error("BLE callback has failed %d times", self._error_count)
    
    async def start(self):
        """
        Start scanning for Govee sensors.
        
        Runs until stop event is set or cancelled.
        """
        # Check adapter availability first
        adapter_status = await check_ble_adapter()
        
        if not adapter_status['available']:
            error_msg = adapter_status.get('error', 'Unknown BLE error')
            _log_ble.error("BLE adapter check failed: %s", error_msg)
            raise RuntimeError(f"Bluetooth not available: {error_msg}")
        
        _log_ble.info("BLE adapter check passed")
        _log_ble.info("Starting BLE scan...")
        
        self._scanner = BleakScanner(self._detection_callback)
        await self._scanner.start()
        
        try:
            # Keep scanning until stop event
            while not _STOP_EVENT.is_set():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            _log_ble.info("Scan cancelled")
        finally:
            _log_ble.info("Stopping scanner...")
            if self._scanner:
                await self._scanner.stop()
    
    @staticmethod
    def request_stop():
        """Request scanner to stop (thread-safe)."""
        _STOP_EVENT.set()
    
    @staticmethod
    def reset_stop():
        """Reset stop flag for restarting scanner."""
        _STOP_EVENT.clear()


def start_scanner_thread(callback: Optional[Callable] = None):
    """
    Start BLE scanner in background thread.
    Returns the thread object.
    """
    GoveeScanner.reset_stop()
    
    def run_scanner():
        try:
            scanner = GoveeScanner(callback=callback)
            asyncio.run(scanner.start())
        except Exception:
            _log_ble.exception("Scanner thread error")
    
    thread = threading.Thread(target=run_scanner, daemon=True)
    thread.start()
    return thread


def stop_scanner():
    """Stop the BLE scanner thread (thread-safe)."""
    GoveeScanner.request_stop()


async def main():
    """Test the scanner from command line."""
    
    def print_reading(reading: SensorReading):
        temp_f = reading.temp_c * 9/5 + 32
        _log_ble.info(
            "%s | Temp: %5.1f°C (%5.1f°F) | Humidity: %4.1f%% | Battery: %3d%% | RSSI: %4d dBm",
            f"{reading.sensor_id:15}",
            reading.temp_c,
            temp_f,
            reading.humidity,
            reading.battery,
            reading.rssi,
        )
    
    scanner = GoveeScanner(callback=print_reading)
    
    try:
        await scanner.start()
    except KeyboardInterrupt:
        _log_ble.info("Stopped by user")


if __name__ == "__main__":
    _log_ble.info("Govee Monitor - BLE Scanner Test")
    _log_ble.info("Press Ctrl+C to stop")
    asyncio.run(main())
