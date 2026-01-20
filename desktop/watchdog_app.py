#!/usr/bin/env python3
"""
Watchdog Desktop App
Cross-platform launcher for Watchdog Environmental Monitor

Optimized for instant launch with background discovery.
"""

import sys
import concurrent.futures
from typing import Optional

import webview

from app_config import Config
from discovery import verify_connection, discover_watchdog_fast

__version__ = "1.0.0"
APP_NAME = "Watchdog"


class WatchdogApp:
    """Main application controller."""
    
    def __init__(self):
        self.config = Config()
        self.window: Optional[webview.Window] = None
        
    def get_saved_ip(self) -> Optional[str]:
        """Quick check for saved IP only (no network calls)."""
        return self.config.get("watchdog_ip")
    
    def get_setup_html(self) -> str:
        """Return HTML for the setup dialog."""
        
        # Pre-fill with saved IP if available
        saved_ip = self.get_saved_ip() or ""
        
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
                    width: 400px;
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
                    min-height: 60px;
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
            </style>
        </head>
        <body>
            <div class="container">
                <div class="logo">🐕</div>
                <h1>Watchdog</h1>
                <p class="subtitle">Environmental Monitor</p>
                
                <div id="status" class="status">
                    <span id="status-text">Searching for your Watchdog...</span>
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
            </div>
            
            <script>
                let discoveryComplete = false;
                
                // Start discovery immediately when page loads
                document.addEventListener('DOMContentLoaded', function() {{
                    setTimeout(startDiscovery, 100);
                }});
                
                async function startDiscovery() {{
                    const existingIp = document.getElementById('ip').value.trim();
                    
                    // If we have a saved IP, try it first (fast path)
                    if (existingIp) {{
                        updateStatus('Checking saved address...', 'pending');
                        const result = await tryApi('quick_verify', existingIp);
                        if (result && result.success) {{
                            updateStatus('✓ Found Watchdog at ' + existingIp, 'success');
                            setTimeout(connect, 500);
                            return;
                        }}
                    }}
                    
                    // Background discovery
                    updateStatus('Searching for your Watchdog...', 'pending');
                    const result = await tryApi('discover');
                    discoveryComplete = true;
                    
                    document.getElementById('spinner').style.display = 'none';
                    
                    if (result && result.found) {{
                        document.getElementById('ip').value = result.ip;
                        updateStatus('✓ Found Watchdog at ' + result.ip, 'success');
                        setTimeout(connect, 800);
                    }} else {{
                        updateStatus('Enter your Watchdog IP address:', 'pending');
                        document.getElementById('spinner').style.display = 'none';
                    }}
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
                
                function updateStatus(text, type) {{
                    const statusEl = document.getElementById('status');
                    const statusText = document.getElementById('status-text');
                    const spinner = document.getElementById('spinner');
                    
                    statusText.innerHTML = text;
                    
                    if (type === 'success') {{
                        statusEl.className = 'status success';
                        spinner.style.display = 'none';
                    }} else if (type === 'error') {{
                        statusEl.className = 'status error';
                        spinner.style.display = 'none';
                    }} else {{
                        statusEl.className = 'status';
                    }}
                }}
                
                async function connect() {{
                    const ip = document.getElementById('ip').value.trim();
                    const btn = document.getElementById('connect-btn');
                    
                    if (!ip) {{
                        updateStatus('Please enter an IP address', 'error');
                        return;
                    }}
                    
                    if (!ip.match(/^\\d{{1,3}}\\.\\d{{1,3}}\\.\\d{{1,3}}\\.\\d{{1,3}}$/)) {{
                        updateStatus('Invalid IP address format', 'error');
                        return;
                    }}
                    
                    btn.disabled = true;
                    btn.textContent = 'Connecting...';
                    updateStatus('Connecting to ' + ip + '...', 'pending');
                    document.getElementById('spinner').style.display = 'block';
                    
                    const result = await tryApi('connect', ip);
                    
                    if (result && result.success) {{
                        updateStatus('✓ Connected! Opening dashboard...', 'success');
                    }} else {{
                        updateStatus('Could not connect to ' + ip, 'error');
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
            width=450,
            height=520,
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
    
    def discover(self) -> dict:
        """Fast parallel discovery."""
        # Try mDNS first
        ip = discover_watchdog_fast()
        if ip:
            return {"found": True, "ip": ip}
        
        # Parallel scan common IPs
        common_ips = [
            "192.168.0.21", "192.168.1.21",
            "192.168.0.100", "192.168.1.100",
            "10.0.0.21", "10.0.0.100",
        ]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                
                executor.submit(verify_connection, ip, 8501, 0.8): ip 
                for ip in common_ips
            }
            try:
                for future in concurrent.futures.as_completed(futures, timeout=2):
                    ip = futures[future]
                    try:
                        if future.result():
                            return {"found": True, "ip": ip}
                    except Exception:
                        pass
            except concurrent.futures.TimeoutError:
                pass
        
        return {"found": False, "ip": None}
    
    def connect(self, ip: str) -> dict:
        """Connect and launch dashboard."""
        if verify_connection(ip, port=8501, timeout=2.0):
            self.app.config.set("watchdog_ip", ip)
            self.app.window.load_url(f"http://{ip}")
            self.app.window.resize(1200, 800)
            self.app.window.set_title(APP_NAME)
            return {"success": True}
        return {"success": False}
    
    def quit(self):
        """Exit application."""
        if self.app.window:
            self.app.window.destroy()


def main():
    app = WatchdogApp()
    app.run()


if __name__ == "__main__":
    main()
