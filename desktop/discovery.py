#!/usr/bin/env python3
"""Network discovery for Watchdog devices - optimized for speed."""

import socket
import subprocess
import sys
from typing import Optional


def verify_connection(ip: str, port: int = 80, timeout: float = 0.5) -> bool:
    """
    Check if Watchdog is reachable at the given IP.
    
    Args:
        ip: IP address to check
        port: Port to connect to (default 80)
        timeout: Connection timeout in seconds (default 0.5 for LAN)
    
    Returns:
        True if connection succeeds, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except (socket.error, OSError):
        return False


def discover_watchdog_fast() -> Optional[str]:
    """
    Fast mDNS discovery with short timeouts.
    
    Returns:
        IP address if found, None otherwise
    """
    # Try direct socket resolution first (fastest)
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
        # Windows usually works with socket.gethostbyname above
    except Exception:
        pass
    
    return None


def _discover_macos_fast() -> Optional[str]:
    """Fast macOS mDNS discovery."""
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


if __name__ == "__main__":
    import time
    
    print("Testing fast discovery...")
    
    start = time.time()
    ip = discover_watchdog_fast()
    elapsed = time.time() - start
    
    if ip:
        print(f"Found: {ip} ({elapsed:.2f}s)")
    else:
        print(f"Not found ({elapsed:.2f}s)")
    
    print("\nTesting direct connection...")
    test_ip = "192.168.0.21"
    start = time.time()
    result = verify_connection(test_ip, timeout=0.5)
    elapsed = time.time() - start
    print(f"{test_ip}: {'reachable' if result else 'not reachable'} ({elapsed:.2f}s)")
