#!/usr/bin/env python3
"""Network discovery for Watchdog devices - with health check support."""
import json
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class WatchdogHealth:
    """Health status of a discovered Watchdog device."""
    reachable: bool
    dashboard_up: bool
    daemon_running: bool
    data_flowing: bool
    sensors_active: int
    active_alarms: int
    critical_alarms: int
    status: str  # "healthy", "degraded", "unhealthy", "offline"
    status_message: str
    last_reading_age_seconds: Optional[float]
    
    @property
    def is_healthy(self) -> bool:
        return self.status == "healthy"
    
    @property
    def needs_attention(self) -> bool:
        return self.status in ("degraded", "unhealthy") or self.critical_alarms > 0


def verify_connection(ip: str, port: int = 8501, timeout: float = 1.0) -> bool:
    """
    Verify that a Watchdog dashboard is reachable.

    We prefer an HTTP check over a raw socket check to avoid false positives
    (port open but wrong service / stale endpoint).
    """
    url = f"http://{ip}:{port}/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "watchdog-desktop"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Any 2xx is fine
            if not (200 <= resp.status < 300):
                return False
            # Read a small chunk to confirm it's actually serving content
            _ = resp.read(256)
            return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return False


def check_watchdog_health(ip: str, timeout: float = 2.0) -> WatchdogHealth:
    """
    Check the health of a Watchdog device.
    
    Queries the health endpoint (port 8502) to get detailed status.
    Falls back to basic dashboard check if health endpoint unavailable.
    
    Args:
        ip: IP address of the Watchdog Pi
        timeout: Request timeout in seconds
    
    Returns:
        WatchdogHealth with detailed status
    """
    # Default unhealthy state
    health = WatchdogHealth(
        reachable=False,
        dashboard_up=False,
        daemon_running=False,
        data_flowing=False,
        sensors_active=0,
        active_alarms=0,
        critical_alarms=0,
        status="offline",
        status_message="Cannot reach device",
        last_reading_age_seconds=None,
    )
    
    # Try dedicated health endpoint first (port 8502)
    health_url = f"http://{ip}:8502/health"
    try:
        req = urllib.request.Request(health_url, headers={"User-Agent": "watchdog-desktop"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                data = json.loads(resp.read().decode())
                health.reachable = True
                health.daemon_running = data.get("daemon_running", False)
                health.data_flowing = data.get("data_flowing", False)
                health.sensors_active = data.get("sensors_active", 0)
                health.active_alarms = data.get("active_alarms", 0)
                health.critical_alarms = data.get("critical_alarms", 0)
                health.status = data.get("status", "unknown")
                health.status_message = data.get("status_message", "")
                health.last_reading_age_seconds = data.get("reading_age_seconds")
                
                # Also check dashboard
                health.dashboard_up = verify_connection(ip, 8501, timeout=1.0)
                return health
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, json.JSONDecodeError):
        pass
    
    # Fallback: just check if dashboard is up
    health.dashboard_up = verify_connection(ip, 8501, timeout=timeout)
    
    if health.dashboard_up:
        health.reachable = True
        health.status = "unknown"
        health.status_message = "Dashboard reachable, health endpoint unavailable"
    
    return health


def discover_watchdog_fast() -> Optional[str]:
    """
    Fast mDNS discovery with short timeouts.
    
    Returns:
        IP address if found, None otherwise
    """
    # Try direct socket resolution first (fastest, works on all platforms)
    try:
        ip = socket.gethostbyname("watchdog.local")
        if _is_valid_private_ip(ip):
            return ip
    except socket.gaierror:
        pass
    
    # Platform-specific mDNS with short timeout
    try:
        if sys.platform == "darwin":
            return _discover_macos_fast()
        elif sys.platform == "linux":
            return _discover_linux_fast()
        elif sys.platform == "win32":
            return _discover_windows_fast()
    except Exception:
        pass
    
    return None


def discover_watchdog_with_status() -> Tuple[Optional[str], str]:
    """
    Discovery with detailed status message for UI feedback.
    
    Returns:
        Tuple of (ip_or_none, status_message)
    """
    # Try direct socket resolution first
    try:
        ip = socket.gethostbyname("watchdog.local")
        if _is_valid_private_ip(ip):
            return ip, "Found via mDNS"
    except socket.gaierror:
        pass
    
    # Platform-specific discovery
    platform_name = {
        "darwin": "macOS",
        "linux": "Linux", 
        "win32": "Windows"
    }.get(sys.platform, sys.platform)
    
    try:
        if sys.platform == "darwin":
            ip = _discover_macos_fast()
            if ip:
                return ip, f"Found via {platform_name} mDNS"
            return None, "Not found via macOS dns-sd"
            
        elif sys.platform == "linux":
            ip = _discover_linux_fast()
            if ip:
                return ip, f"Found via {platform_name} avahi"
            return None, "Not found via Linux avahi (is avahi-daemon running?)"
            
        elif sys.platform == "win32":
            ip = _discover_windows_fast()
            if ip:
                return ip, f"Found via {platform_name} mDNS"
            return None, "Not found via Windows mDNS. Try installing Bonjour or enter IP manually."
            
        else:
            return None, f"mDNS discovery not supported on {platform_name}"
            
    except FileNotFoundError as e:
        return None, f"mDNS tool not found on {platform_name}: {e}"
    except subprocess.TimeoutExpired:
        return None, f"mDNS discovery timed out on {platform_name}"
    except Exception as e:
        return None, f"mDNS error on {platform_name}: {e}"


def discover_and_check_health() -> Tuple[Optional[str], WatchdogHealth, str]:
    """
    Discover Watchdog and check its health status.
    
    Returns:
        Tuple of (ip_or_none, health_status, discovery_message)
    """
    ip, discovery_msg = discover_watchdog_with_status()
    
    if ip is None:
        return None, WatchdogHealth(
            reachable=False, dashboard_up=False, daemon_running=False,
            data_flowing=False, sensors_active=0, active_alarms=0,
            critical_alarms=0, status="offline",
            status_message="Device not found", last_reading_age_seconds=None
        ), discovery_msg
    
    health = check_watchdog_health(ip)
    return ip, health, discovery_msg


def _discover_macos_fast() -> Optional[str]:
    """Fast macOS mDNS discovery using dns-sd."""
    try:
        # Use timeout to prevent hanging
        result = subprocess.run(
            ["dns-sd", "-G", "v4", "watchdog.local"],
            capture_output=True,
            text=True,
            timeout=2  # 2 second timeout
        )
        
        for line in result.stdout.split("\n"):
            if "watchdog.local" in line.lower():
                parts = line.split()
                for part in parts:
                    if _is_valid_private_ip(part):
                        return part
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    return None


def _discover_linux_fast() -> Optional[str]:
    """Fast Linux mDNS discovery using avahi."""
    try:
        result = subprocess.run(
            ["avahi-resolve", "-4", "-n", "watchdog.local"],
            capture_output=True,
            text=True,
            timeout=2  # 2 second timeout
        )
        
        for line in result.stdout.split("\n"):
            parts = line.split()
            if len(parts) >= 2:
                ip = parts[-1]
                if _is_valid_private_ip(ip):
                    return ip
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    
    return None


def _discover_windows_fast() -> Optional[str]:
    """
    Fast Windows mDNS discovery.
    
    Windows mDNS support varies:
    - Windows 10 1809+ has native mDNS (usually works with gethostbyname)
    - Older Windows needs Bonjour Print Services or iTunes installed
    - dns-sd.exe is available if Bonjour is installed
    
    This function tries multiple approaches.
    """
    # Approach 1: Try dns-sd.exe (available if Bonjour is installed)
    try:
        # dns-sd on Windows needs different handling than macOS
        result = subprocess.run(
            ["dns-sd", "-G", "v4", "watchdog.local"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        
        for line in result.stdout.split("\n"):
            # Look for IP addresses in output
            parts = line.split()
            for part in parts:
                if _is_valid_private_ip(part):
                    return part
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    
    # Approach 2: Try PowerShell Resolve-DnsName (Windows 10+)
    try:
        result = subprocess.run(
            ["powershell", "-Command", 
             "Resolve-DnsName -Name 'watchdog.local' -Type A -DnsOnly -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        
        for line in result.stdout.strip().split("\n"):
            ip = line.strip()
            if _is_valid_private_ip(ip):
                return ip
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    
    # Approach 3: NetBIOS name resolution (legacy fallback)
    # This won't find .local names but might work if Watchdog advertises NetBIOS
    try:
        # Try without .local suffix
        ip = socket.gethostbyname("watchdog")
        if _is_valid_private_ip(ip):
            return ip
    except socket.gaierror:
        pass
    
    return None


def _is_valid_private_ip(ip: str) -> bool:
    """Check if string is a valid private IPv4 address."""
    try:
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        
        octets = [int(part) for part in parts]
        
        for octet in octets:
            if octet < 0 or octet > 255:
                return False
        
        # Private IP ranges only
        if octets[0] == 10:
            return True
        if octets[0] == 172 and 16 <= octets[1] <= 31:
            return True
        if octets[0] == 192 and octets[1] == 168:
            return True
        
        return False
        
    except (ValueError, AttributeError):
        return False


def get_discovery_help() -> str:
    """Return platform-specific help text for discovery issues."""
    if sys.platform == "darwin":
        return """
macOS Discovery Help:
• mDNS should work automatically on macOS
• Ensure your Mac is on the same network as Watchdog
• Try: dns-sd -G v4 watchdog.local
"""
    elif sys.platform == "linux":
        return """
Linux Discovery Help:
• Install avahi: sudo apt install avahi-daemon avahi-utils
• Check avahi is running: systemctl status avahi-daemon
• Try: avahi-resolve -4 -n watchdog.local
"""
    elif sys.platform == "win32":
        return """
Windows Discovery Help:
• Windows 10 1809+ should support mDNS natively
• For older Windows, install Bonjour Print Services from Apple
• Alternatively, enter the IP address manually
• Find your Watchdog's IP in your router's admin panel
"""
    else:
        return """
Discovery Help:
• Ensure your computer is on the same network as Watchdog
• You can find the IP address in your router's admin panel
• Enter the IP address manually if auto-discovery fails
"""


def format_health_for_display(health: WatchdogHealth) -> str:
    """Format health status for UI display."""
    if not health.reachable:
        return "❌ Device not reachable"
    
    parts = []
    
    # Status icon
    if health.status == "healthy":
        parts.append("✅")
    elif health.status == "degraded":
        parts.append("⚠️")
    elif health.status == "unhealthy":
        parts.append("❌")
    else:
        parts.append("❓")
    
    # Main status
    if health.daemon_running and health.data_flowing:
        parts.append(f"Monitoring active ({health.sensors_active} sensors)")
    elif health.daemon_running:
        parts.append("Monitoring active, no recent data")
    elif health.dashboard_up:
        parts.append("Dashboard up, monitoring stopped")
    else:
        parts.append(health.status_message)
    
    # Alarms
    if health.critical_alarms > 0:
        parts.append(f"| 🔴 {health.critical_alarms} critical")
    elif health.active_alarms > 0:
        parts.append(f"| 🟡 {health.active_alarms} alerts")
    
    return " ".join(parts)


if __name__ == "__main__":
    import time
    
    print("Testing discovery with health check...")
    print(f"Platform: {sys.platform}")
    print()
    
    start = time.time()
    ip, health, status = discover_and_check_health()
    elapsed = time.time() - start
    
    if ip:
        print(f"✓ Found: {ip}")
        print(f"  Discovery: {status}")
        print(f"  Health: {format_health_for_display(health)}")
        print(f"  Details:")
        print(f"    Dashboard up: {health.dashboard_up}")
        print(f"    Daemon running: {health.daemon_running}")
        print(f"    Data flowing: {health.data_flowing}")
        print(f"    Sensors: {health.sensors_active}")
        print(f"    Alarms: {health.active_alarms} ({health.critical_alarms} critical)")
    else:
        print(f"✗ Not found")
        print(f"  Status: {status}")
    print(f"  Time: {elapsed:.2f}s")
    
    print()
    print("Help text:")
    print(get_discovery_help())
