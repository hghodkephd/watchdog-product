#!/usr/bin/env python3
"""Network discovery for Watchdog devices - optimized for speed with cross-platform support."""
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from typing import Optional, Tuple


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


if __name__ == "__main__":
    import time
    
    print("Testing discovery with status...")
    print(f"Platform: {sys.platform}")
    print()
    
    start = time.time()
    ip, status = discover_watchdog_with_status()
    elapsed = time.time() - start
    
    if ip:
        print(f"✓ Found: {ip}")
        print(f"  Status: {status}")
    else:
        print(f"✗ Not found")
        print(f"  Status: {status}")
    print(f"  Time: {elapsed:.2f}s")
    
    print()
    print("Testing fast discovery...")
    start = time.time()
    ip = discover_watchdog_fast()
    elapsed = time.time() - start
    
    if ip:
        print(f"✓ Found: {ip} ({elapsed:.2f}s)")
    else:
        print(f"✗ Not found ({elapsed:.2f}s)")
    
    print()
    print("Testing direct connection...")
    test_ip = "192.168.0.21"
    start = time.time()
    result = verify_connection(test_ip, timeout=0.5)
    elapsed = time.time() - start
    print(f"{test_ip}: {'reachable' if result else 'not reachable'} ({elapsed:.2f}s)")
    
    print()
    print("Help text:")
    print(get_discovery_help())
