#!/usr/bin/env python3
"""
Watchdog Desktop App
Cross-platform launcher for Watchdog Environmental Monitor

Optimized for instant launch with background discovery and health monitoring.
"""

import sys
import concurrent.futures
from typing import Optional

import webview

from app_config import Config
from discovery import (
    verify_connection, 
    discover_watchdog_fast,
    discover_watchdog_with_status,
    discover_and_check_health,
    check_watchdog_health,
    format_health_for_display,
    get_discovery_help,
    WatchdogHealth,
)

__version__ = "1.0.0"
APP_NAME = "Watchdog"


class WatchdogApp:
    """Main application controller."""
    
    def __init__(self):
        self.config = Config()
        self.window: Optional[webview.Window] = None
        self._current_ip: Optional[str] = None
        self._health_check_interval = 30  # seconds
        
    def get_saved_ip(self) -> Optional[str]:
        """Quick check for saved IP only (no network calls)."""
        return self.config.get("watchdog_ip")
    
    def get_setup_html(self) -> str:
        """Return HTML for the setup dialog."""
        
        # Pre-fill with saved IP if available
        saved_ip = self.get_saved_ip() or ""
        
        # Get platform-specific help text
        help_text = get_discovery_help().replace('\n', '\\n').replace("'", "\\'")
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Watchdog Setup</title>
            <style>
                * {{ box-sizing: border-box; margin: 0; padding: 0; }}
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: linear-gradient(135deg, #1e3a5f 0%, #0d1b2a 100%);
                    color: #e0e0e0;
                    height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .container {{
                    background: rgba(255,255,255,0.05);
                    backdrop-filter: blur(10px);
                    border-radius: 16px;
                    padding: 40px;
                    width: 460px;
                    text-align: center;
                    border: 1px solid rgba(255,255,255,0.1);
                }}
                .logo {{ font-size: 48px; margin-bottom: 16px; }}
                h1 {{ font-size: 24px; margin-bottom: 8px; font-weight: 600; }}
                .subtitle {{ color: #888; margin-bottom: 32px; }}
                .status {{ 
                    background: rgba(255,200,0,0.1); 
                    border: 1px solid rgba(255,200,0,0.3);
                    border-radius: 8px; 
                    padding: 16px; 
                    margin-bottom: 24px;
                    font-size: 14px;
                    min-height: 80px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    flex-direction: column;
                }}
                .status.error {{
                    background: rgba(255,100,100,0.1);
                    border-color: rgba(255,100,100,0.3);
                }}
                .status.success {{
                    background: rgba(100,255,100,0.1);
                    border-color: rgba(100,255,100,0.3);
                }}
                .status.warning {{
                    background: rgba(255,200,0,0.1);
                    border-color: rgba(255,200,0,0.3);
                }}
                .status-detail {{
                    font-size: 12px;
                    color: #888;
                    margin-top: 8px;
                }}
                .health-indicator {{
                    font-size: 12px;
                    margin-top: 8px;
                    padding: 4px 8px;
                    border-radius: 4px;
                    background: rgba(0,0,0,0.2);
                }}
                input {{
                    width: 100%;
                    padding: 14px 16px;
                    font-size: 16px;
                    border: 1px solid rgba(255,255,255,0.2);
                    border-radius: 8px;
                    background: rgba(0,0,0,0.3);
                    color: white;
                    margin-bottom: 16px;
                    text-align: center;
                }}
                input:focus {{
                    outline: none;
                    border-color: #4a9eff;
                }}
                input::placeholder {{ color: #666; }}
                button {{
                    width: 100%;
                    padding: 14px;
                    font-size: 16px;
                    font-weight: 600;
                    border: none;
                    border-radius: 8px;
                    cursor: pointer;
                    transition: all 0.2s;
                }}
                .primary {{
                    background: #4a9eff;
                    color: white;
                }}
                .primary:hover {{ background: #3a8eef; }}
                .primary:disabled {{ 
                    background: #666; 
                    cursor: not-allowed; 
                }}
                .secondary {{
                    background: transparent;
                    color: #888;
                    margin-top: 12px;
                }}
                .secondary:hover {{ color: #aaa; }}
                .help-link {{
                    background: transparent;
                    color: #4a9eff;
                    margin-top: 8px;
                    font-size: 14px;
                    text-decoration: underline;
                    cursor: pointer;
                }}
                .help-link:hover {{ color: #6ab4ff; }}
                .spinner {{
                    width: 24px;
                    height: 24px;
                    border: 3px solid transparent;
                    border-top-color: #4a9eff;
                    border-radius: 50%;
                    animation: spin 1s linear infinite;
                    margin-top: 12px;
                }}
                @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
                .help-panel {{
                    display: none;
                    background: rgba(0,0,0,0.3);
                    border-radius: 8px;
                    padding: 16px;
                    margin-top: 16px;
                    text-align: left;
                    font-size: 13px;
                    white-space: pre-line;
                    color: #aaa;
                }}
                .help-panel.visible {{
                    display: block;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">🐕</div>
                <h1>Watchdog</h1>
                <p class="subtitle">Environmental Monitor</p>
                
                <div id="status" class="status">
                    <span id="status-text">Searching for your Watchdog...</span>
                    <span id="status-detail" class="status-detail"></span>
                    <span id="health-indicator" class="health-indicator" style="display: none;"></span>
                    <div class="spinner" id="spinner"></div>
                </div>
                
                <input 
                    type="text" 
                    id="ip" 
                    placeholder="Enter IP address (e.g., 192.168.0.21)"
                    value="{saved_ip}"
                >
                
                <button class="primary" id="connect-btn" onclick="connect()">Connect</button>
                <button class="secondary" onclick="quit()">Cancel</button>
                <button class="help-link" onclick="toggleHelp()">Need help finding your Watchdog?</button>
                
                <div id="help-panel" class="help-panel">{help_text}</div>
            </div>
            
            <script>
                let discoveryComplete = false;
                let healthCheckInterval = null;
                
                // Start discovery immediately when page loads
                document.addEventListener('DOMContentLoaded', function() {{
                    setTimeout(startDiscovery, 100);
                }});
                
                function toggleHelp() {{
                    const panel = document.getElementById('help-panel');
                    panel.classList.toggle('visible');
                }}
                
                async function startDiscovery() {{
                    const existingIp = document.getElementById('ip').value.trim();
                    
                    // If we have a saved IP, try it first with health check
                    if (existingIp) {{
                        updateStatus('Checking saved address...', 'pending', 'Verifying ' + existingIp);
                        const result = await tryApi('check_health', existingIp);
                        
                        if (result && result.reachable) {{
                            showHealthStatus(result, existingIp);
                            
                            if (result.dashboard_up) {{
                                setTimeout(connect, 1000);
                                return;
                            }} else {{
                                updateStatus('Dashboard not responding', 'warning', 
                                    result.daemon_running ? 'Monitoring is running but dashboard is down' : 'Monitoring service stopped');
                            }}
                        }} else {{
                            updateStatus('Saved address not responding', 'pending', 'Searching network...');
                        }}
                    }}
                    
                    // Background discovery with health check
                    updateStatus('Searching for your Watchdog...', 'pending', '');
                    const result = await tryApi('discover_with_health');
                    discoveryComplete = true;
                    
                    document.getElementById('spinner').style.display = 'none';
                    
                    if (result && result.found) {{
                        document.getElementById('ip').value = result.ip;
                        showHealthStatus(result.health, result.ip);
                        
                        if (result.health.dashboard_up) {{
                            setTimeout(connect, 1000);
                        }}
                    }} else {{
                        updateStatus('Could not find Watchdog automatically', 'error', 
                            result ? result.discovery_message : 'Enter IP address manually');
                        document.getElementById('spinner').style.display = 'none';
                    }}
                }}
                
                function showHealthStatus(health, ip) {{
                    const healthEl = document.getElementById('health-indicator');
                    
                    if (health.status === 'healthy') {{
                        updateStatus('✓ Found Watchdog at ' + ip, 'success', 
                            health.sensors_active + ' sensors active');
                        healthEl.innerHTML = '✅ System healthy';
                        healthEl.style.background = 'rgba(100,255,100,0.2)';
                    }} else if (health.status === 'degraded') {{
                        updateStatus('⚠ Found Watchdog at ' + ip, 'warning', health.status_message);
                        healthEl.innerHTML = '⚠️ ' + health.status_message;
                        healthEl.style.background = 'rgba(255,200,0,0.2)';
                    }} else if (health.daemon_running) {{
                        updateStatus('Found Watchdog at ' + ip, 'success', 'Monitoring active');
                        healthEl.innerHTML = '🟢 Monitoring running';
                        healthEl.style.background = 'rgba(100,255,100,0.2)';
                    }} else {{
                        updateStatus('Found Watchdog at ' + ip, 'warning', 'Monitoring stopped');
                        healthEl.innerHTML = '🔴 Monitoring stopped';
                        healthEl.style.background = 'rgba(255,100,100,0.2)';
                    }}
                    
                    // Show alarm count if any
                    if (health.critical_alarms > 0) {{
                        healthEl.innerHTML += ' | 🔴 ' + health.critical_alarms + ' critical';
                    }} else if (health.active_alarms > 0) {{
                        healthEl.innerHTML += ' | 🟡 ' + health.active_alarms + ' alerts';
                    }}
                    
                    healthEl.style.display = 'block';
                }}
                
                async function tryApi(method, ...args) {{
                    try {{
                        if (window.pywebview && window.pywebview.api) {{
                            return await window.pywebview.api[method](...args);
                        }}
                    }} catch (e) {{
                        console.error('API error:', e);
                    }}
                    return null;
                }}
                
                function updateStatus(text, type, detail) {{
                    const statusEl = document.getElementById('status');
                    const statusText = document.getElementById('status-text');
                    const statusDetail = document.getElementById('status-detail');
                    const spinner = document.getElementById('spinner');
                    
                    statusText.innerHTML = text;
                    statusDetail.innerHTML = detail || '';
                    
                    if (type === 'success') {{
                        statusEl.className = 'status success';
                        spinner.style.display = 'none';
                    }} else if (type === 'error') {{
                        statusEl.className = 'status error';
                        spinner.style.display = 'none';
                    }} else if (type === 'warning') {{
                        statusEl.className = 'status warning';
                        spinner.style.display = 'none';
                    }} else {{
                        statusEl.className = 'status';
                    }}
                }}
                
                async function connect() {{
                    const ip = document.getElementById('ip').value.trim();
                    const btn = document.getElementById('connect-btn');
                    
                    if (!ip) {{
                        updateStatus('Please enter an IP address', 'error', '');
                        return;
                    }}
                    
                    if (!ip.match(/^\\d{{1,3}}\\.\\d{{1,3}}\\.\\d{{1,3}}\\.\\d{{1,3}}$/)) {{
                        updateStatus('Invalid IP address format', 'error', 'Example: 192.168.0.21');
                        return;
                    }}
                    
                    btn.disabled = true;
                    btn.textContent = 'Connecting...';
                    updateStatus('Connecting to ' + ip + '...', 'pending', '');
                    document.getElementById('spinner').style.display = 'block';
                    
                    const result = await tryApi('connect', ip);
                    
                    if (result && result.success) {{
                        updateStatus('✓ Connected! Opening dashboard...', 'success', '');
                    }} else {{
                        updateStatus('Could not connect to ' + ip, 'error', result ? result.error : 'Check IP and try again');
                        btn.disabled = false;
                        btn.textContent = 'Connect';
                    }}
                }}
                
                function quit() {{
                    tryApi('quit');
                }}
                
                document.getElementById('ip').addEventListener('keypress', function(e) {{
                    if (e.key === 'Enter') connect();
                }});
            </script>
        </body>
        </html>
        """
    
    def run(self):
        """Main entry point - shows UI immediately, discovery in background."""
        api = SetupAPI(self)
        self.window = webview.create_window(
            APP_NAME,
            html=self.get_setup_html(),
            width=500,
            height=620,
            resizable=False,
            js_api=api,
        )
        webview.start()


class SetupAPI:
    """JavaScript API exposed to setup dialog."""
    
    def __init__(self, app: WatchdogApp):
        self.app = app
    
    def quick_verify(self, ip: str) -> dict:
        """Quick verification of a known IP (500ms timeout)."""
        if verify_connection(ip, port=8501, timeout=0.5):
            return {"success": True}
        return {"success": False}
    
    def check_health(self, ip: str) -> dict:
        """Check health of a specific IP."""
        health = check_watchdog_health(ip, timeout=2.0)
        return {
            "reachable": health.reachable,
            "dashboard_up": health.dashboard_up,
            "daemon_running": health.daemon_running,
            "data_flowing": health.data_flowing,
            "sensors_active": health.sensors_active,
            "active_alarms": health.active_alarms,
            "critical_alarms": health.critical_alarms,
            "status": health.status,
            "status_message": health.status_message,
        }
    
    def discover(self) -> dict:
        """Fast parallel discovery (backward compatible)."""
        ip = discover_watchdog_fast()
        if ip:
            return {"found": True, "ip": ip}
        return {"found": False, "ip": None}
    
    def discover_with_status(self) -> dict:
        """Discovery with detailed status for better UI feedback."""
        ip, status = discover_watchdog_with_status()
        if ip:
            return {"found": True, "ip": ip, "status": status}
        return {"found": False, "ip": None, "status": status}
    
    def discover_with_health(self) -> dict:
        """Discovery with health check - the full experience."""
        # First try fast mDNS
        ip, health, discovery_msg = discover_and_check_health()
        
        if ip:
            return {
                "found": True,
                "ip": ip,
                "discovery_message": discovery_msg,
                "health": {
                    "reachable": health.reachable,
                    "dashboard_up": health.dashboard_up,
                    "daemon_running": health.daemon_running,
                    "data_flowing": health.data_flowing,
                    "sensors_active": health.sensors_active,
                    "active_alarms": health.active_alarms,
                    "critical_alarms": health.critical_alarms,
                    "status": health.status,
                    "status_message": health.status_message,
                }
            }
        
        # Fall back to parallel scan of common IPs
        common_ips = [
            "192.168.0.21", "192.168.1.21",
            "192.168.0.100", "192.168.1.100",
            "10.0.0.21", "10.0.0.100",
        ]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(check_watchdog_health, ip, 1.0): ip 
                for ip in common_ips
            }
            try:
                for future in concurrent.futures.as_completed(futures, timeout=3):
                    ip = futures[future]
                    try:
                        health = future.result()
                        if health.reachable:
                            return {
                                "found": True,
                                "ip": ip,
                                "discovery_message": "Found via network scan",
                                "health": {
                                    "reachable": health.reachable,
                                    "dashboard_up": health.dashboard_up,
                                    "daemon_running": health.daemon_running,
                                    "data_flowing": health.data_flowing,
                                    "sensors_active": health.sensors_active,
                                    "active_alarms": health.active_alarms,
                                    "critical_alarms": health.critical_alarms,
                                    "status": health.status,
                                    "status_message": health.status_message,
                                }
                            }
                    except Exception:
                        pass
            except concurrent.futures.TimeoutError:
                pass
        
        return {"found": False, "ip": None, "discovery_message": discovery_msg, "health": None}
    
    def connect(self, ip: str) -> dict:
        """Connect and launch dashboard."""
        health = check_watchdog_health(ip, timeout=2.0)
        
        if health.dashboard_up:
            self.app.config.set("watchdog_ip", ip)
            self.app._current_ip = ip
            self.app.window.load_url(f"http://{ip}:8501")
            self.app.window.resize(1200, 800)
            self.app.window.set_title(APP_NAME)
            return {"success": True}
        elif health.reachable:
            return {"success": False, "error": "Pi reachable but dashboard not running. Check the monitoring service."}
        else:
            return {"success": False, "error": "Dashboard not responding"}
    
    def quit(self):
        """Exit application."""
        if self.app.window:
            self.app.window.destroy()


def main():
    app = WatchdogApp()
    app.run()


if __name__ == "__main__":
    main()
