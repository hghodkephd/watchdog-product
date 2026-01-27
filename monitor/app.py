#!/usr/bin/env python3
"""
Watchdog Environmental Monitor - Production Dashboard
Same monitoring experience as OSS, with database persistence and production features
"""
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1

import pandas as pd
import altair as alt
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import subprocess
import shutil
import math

# Watchdog imports
from config import load_config, save_config, SensorConfig, WeatherConfig, US_TIMEZONES
from storage import get_connection, init_db, get_db_stats, archive_old_data, list_archives, get_db_path, get_downsampled_timeseries
from alerts import check_alerts, format_alert_display
from weather_api import geocode_zip, fetch_current_weather, fetch_weather_series
from process_manager import start_monitoring, stop_monitoring, get_monitoring_status

# Alarm system
from alarm_state import get_alarm_manager, process_alerts, Severity, SILENCE_OPTIONS
from alarm_ui import render_alarm_banners, process_alerts_and_notify, render_email_settings
from config import EmailConfig
from notifications import send_test_email, send_alarm_email, send_cleared_email



# -----------------------------------------------------------------------------
# P0 performance: cached trends queries
# -----------------------------------------------------------------------------
@st.cache_data(ttl=5, show_spinner=False)
def _cached_trends_fast(sensor_ids_tuple, start_ts, end_ts, bucket_seconds, max_points_per_sensor, db_path_str):
    """Short TTL cache for live hour view."""
    conn = get_connection(db_path_str)
    try:
        return get_downsampled_timeseries(
            conn,
            list(sensor_ids_tuple),
            start_ts,
            end_ts,
            bucket_seconds=bucket_seconds,
            max_points_per_sensor=max_points_per_sensor,
        )
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_trends_slow(sensor_ids_tuple, start_ts, end_ts, bucket_seconds, max_points_per_sensor, db_path_str):
    """Longer TTL cache for day/week/month (and fixed windows)."""
    conn = get_connection(db_path_str)
    try:
        return get_downsampled_timeseries(
            conn,
            list(sensor_ids_tuple),
            start_ts,
            end_ts,
            bucket_seconds=bucket_seconds,
            max_points_per_sensor=max_points_per_sensor,
        )
    finally:
        conn.close()
 
# Page config
st.set_page_config(
    page_title="Watchdog Environmental Monitor",
    page_icon="🐕",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load config
cfg = load_config()


def is_first_run(cfg) -> bool:
    """
    Detect if this is effectively a first run.
    
    Returns True if:
    - No sensors are configured AND
    - No data in the database (or very little)
    """
    # Check if any sensors are configured
    if cfg.sensors and len(cfg.sensors) > 0:
        return False
    
    # Check if database has significant data
    try:
        conn = get_connection()
        result = conn.execute("SELECT COUNT(*) FROM readings").fetchone()
        conn.close()
        row_count = result[0] if result else 0
        
        # If less than 100 readings, treat as first run
        return row_count < 100
    except Exception:
        return True  # Assume first run if we can't check


def render_onboarding(cfg, status):
    """
    Render a guided onboarding flow for first-time users.
    
    Returns True if onboarding was rendered (caller should skip normal content).
    """
    st.markdown("""
    ## 👋 Welcome to Watchdog!
    
    Let's get your environmental monitoring set up. This will take about 2 minutes.
    """)
    
    # Step tracking
    monitoring_running = status.get('is_running', False)
    
    # Check for detected sensors
    detected_count = 0
    try:
        conn = get_connection()
        cutoff = time.time() - 300  # Last 5 minutes
        result = conn.execute(
            "SELECT COUNT(DISTINCT sensor_id) FROM readings WHERE ts >= ?",
            (cutoff,)
        ).fetchone()
        conn.close()
        detected_count = result[0] if result else 0
    except Exception:
        pass
    
    sensors_detected = detected_count > 0
    sensors_configured = bool(cfg.sensors and len(cfg.sensors) > 0)
    
    # Calculate current step
    if not monitoring_running:
        current_step = 1
    elif not sensors_detected:
        current_step = 2
    elif not sensors_configured:
        current_step = 3
    else:
        current_step = 4  # Complete
    
    # Progress indicator
    st.progress(current_step / 4, text=f"Step {current_step} of 4")
    
    st.divider()
    
    # Step 1: Start Monitoring
    col1, col2 = st.columns([1, 20])
    with col1:
        if monitoring_running:
            st.markdown("### ✅")
        else:
            st.markdown("### 1️⃣")
    with col2:
        st.markdown("### Start Monitoring")
        if not monitoring_running:
            st.markdown("Click the button below to power on the Bluetooth scanner.")
            if st.button("▶️ Start Monitoring", type="primary", key="onboard_start"):
                success, msg, pid = start_monitoring()
                if success:
                    st.success(msg)
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(msg)
        else:
            st.markdown("✓ Monitoring is running")
    
    st.divider()
    
    # Step 2: Detect Sensors
    col1, col2 = st.columns([1, 20])
    with col1:
        if sensors_detected:
            st.markdown("### ✅")
        elif monitoring_running:
            st.markdown("### 2️⃣")
        else:
            st.markdown("### ⬜")
    with col2:
        st.markdown("### Detect Sensors")
        if not monitoring_running:
            st.caption("Start monitoring first")
        elif not sensors_detected:
            st.markdown("Scanning for Govee sensors... This usually takes 10-30 seconds.")
            st.markdown("Make sure your sensors are powered on and within range (~30 feet).")
            
            # Auto-refresh while scanning
            with st.spinner("Scanning for sensors..."):
                st.caption("This page will refresh automatically.")
                time.sleep(10)
                st.rerun()
        else:
            st.markdown(f"✓ Found **{detected_count}** sensor(s)")
    
    st.divider()
    
    # Step 3: Configure Sensors
    col1, col2 = st.columns([1, 20])
    with col1:
        if sensors_configured:
            st.markdown("### ✅")
        elif sensors_detected:
            st.markdown("### 3️⃣")
        else:
            st.markdown("### ⬜")
    with col2:
        st.markdown("### Configure Sensors")
        if not sensors_detected:
            st.caption("Waiting for sensor detection")
        elif not sensors_configured:
            st.markdown("Great! Now let's configure your sensors.")
            st.markdown("Click the **Setup** tab above to name your sensors and set temperature thresholds.")
            
            # Show detected sensors preview
            try:
                conn = get_connection()
                cutoff = time.time() - 300
                rows = conn.execute("""
                    SELECT DISTINCT sensor_id FROM readings WHERE ts >= ?
                """, (cutoff,)).fetchall()
                conn.close()
                
                sensor_ids = [row[0] for row in rows]
                st.caption(f"Detected: {', '.join(sensor_ids)}")
            except Exception:
                pass
            
            st.info("👆 Click the **⚙️ Setup** tab to continue")
        else:
            st.markdown(f"✓ Configured **{len(cfg.sensors)}** sensor(s)")
    
    st.divider()
    
    # Step 4: Complete
    col1, col2 = st.columns([1, 20])
    with col1:
        if sensors_configured:
            st.markdown("### 🎉")
        else:
            st.markdown("### ⬜")
    with col2:
        st.markdown("### All Set!")
        if sensors_configured:
            st.markdown("Your Watchdog is ready to monitor!")
            st.markdown("You'll now see real-time data on this tab.")
            
            # Clear first-run state
            st.balloons()
            time.sleep(2)
            st.rerun()
        else:
            st.caption("Complete the steps above")
    
    # Help section
    st.divider()
    with st.expander("🆘 Troubleshooting"):
        st.markdown("""
        **Sensors not detected?**
        - Make sure sensors are powered on (batteries inserted)
        - Bring sensors within 30 feet of the Raspberry Pi
        - Wait up to 60 seconds for detection
        - Try restarting the sensors (remove and reinsert batteries)
        
        **Bluetooth issues?**
        - SSH to your Pi and run: `sudo bash ~/Watchdog/monitor/deploy/watchdog-bt-unblock.sh`
        - Then restart monitoring
        
        **Need help?**
        - Check the README in your Watchdog installation
        - Visit our support documentation
        """)
    
    return True  # Onboarding was rendered


# ---------------------------------------------------------------------
# Session guards (prevents "autostart" feel when a monitor is already running)
# ---------------------------------------------------------------------
if "monitor_owned_by_ui" not in st.session_state:
    st.session_state.monitor_owned_by_ui = False

if "monitor_acknowledged" not in st.session_state:
    st.session_state.monitor_acknowledged = False
    

# ---------------------------------------------------------------------
# Timezone helper
# ---------------------------------------------------------------------

def get_user_timezone() -> ZoneInfo:
    """Get user's configured timezone, with fallback to Eastern."""
    if cfg.weather and cfg.weather.timezone:
        try:
            return ZoneInfo(cfg.weather.timezone)
        except Exception:
            pass
    return ZoneInfo("America/New_York")


def convert_to_local(utc_timestamp: float) -> datetime:
    """Convert Unix timestamp to user's local timezone."""
    tz = get_user_timezone()
    utc_dt = datetime.fromtimestamp(utc_timestamp, tz=ZoneInfo("UTC"))
    return utc_dt.astimezone(tz)


# ---------------------------------------------------------------------
# Weather caching (prevents API timeouts on refresh)
# ---------------------------------------------------------------------

@st.cache_data(ttl=300)  # Cache for 5 minutes
def cached_current_weather(lat: float, lon: float) -> dict:
    """Fetch current weather with caching to prevent API overload."""
    return fetch_current_weather(lat, lon)


@st.cache_data(ttl=600)  # Cache for 10 minutes
def cached_weather_series(
    lat: float,
    lon: float,
    start_iso: str,
    end_iso: str,
) -> pd.DataFrame:
    """Fetch weather time series with caching."""
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    result = fetch_weather_series(lat, lon, start, end)
    return result if result is not None else pd.DataFrame()

# ---------------------------------------------------------------------
# System health (reassurance panel)
# ---------------------------------------------------------------------

@st.cache_data(ttl=10)
def get_system_health(window_s: int = 300) -> dict:
    """
    Lightweight health signals for user reassurance.
    Authoritative signal is DB freshness (not BLE internals).
    Cached to avoid expensive calls on every Streamlit rerun.
    """
    health = {
        "db_ok": False,
        "db_path": None,
        "last_reading_ts": None,
        "last_reading_age_s": None,
        "distinct_sensors_window": 0,
        "disk_free_gb": None,
        "bluetooth_powered": None,   # True/False/None
        "bluetooth_error": None,
        "now": time.time(),
    }

    # DB checks (authoritative)
    try:
        db_path = get_db_path()
        health["db_path"] = str(db_path)

        conn = get_connection()
        try:
            row = conn.execute("SELECT MAX(ts) FROM readings;").fetchone()
            last_ts = float(row[0]) if row and row[0] is not None else None
            health["last_reading_ts"] = last_ts
            if last_ts is not None:
                health["last_reading_age_s"] = max(0.0, health["now"] - last_ts)

            cutoff = health["now"] - float(window_s)
            row2 = conn.execute(
                "SELECT COUNT(DISTINCT sensor_id) FROM readings WHERE ts > ?;",
                (cutoff,),
            ).fetchone()
            health["distinct_sensors_window"] = int(row2[0]) if row2 and row2[0] is not None else 0

            health["db_ok"] = True
        finally:
            conn.close()
    except Exception:
        # Keep health["db_ok"] False; UI will show red.
        pass

    # Disk free (nice reassurance)
    try:
        if health["db_path"]:
            usage = shutil.disk_usage(str(get_db_path().parent))
            health["disk_free_gb"] = round(usage.free / (1024**3), 1)
    except Exception:
        pass

    # Bluetooth powered state (best-effort reassurance)
    try:
        p = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if p.returncode == 0:
            powered = None
            for line in p.stdout.splitlines():
                if "Powered:" in line:
                    powered = line.split("Powered:", 1)[1].strip().lower() == "yes"
                    break
            health["bluetooth_powered"] = powered
        else:
            health["bluetooth_error"] = (p.stderr or "").strip()[:200] or "bluetoothctl failed"
    except Exception as e:
        health["bluetooth_error"] = str(e)[:200]

    return health

# ---------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------

st.title("🐕 Watchdog Environmental Monitor")
st.caption("Production Environmental Monitoring with Self-Healing")

# Top-level tabs
tab1, tab2, tab3 = st.tabs(["📊 Monitoring", "⚙️ Setup", "🔧 Settings"])

# ====================
# TAB 1: MONITORING (OSS-STYLE)
# ====================
with tab1:
    # === CRITICAL: Email system warning (must be first!) ===
    from alarm_ui import render_email_system_warning
    render_email_system_warning()
    
    # Check monitoring status
    status = get_monitoring_status()
    
    # === First-run onboarding ===
    is_onboarding = is_first_run(cfg)
    
    if is_onboarding:
        render_onboarding(cfg, status)
        # Don't render rest of Monitoring tab during onboarding
        # But DO NOT use st.stop() - that blocks tab2 and tab3!
    
    else:
        # ===========================================
        # NORMAL MONITORING UI (only when not onboarding)
        # ===========================================
        
        # --------------------
        # System Health panel
        # --------------------
        health = get_system_health(window_s=300)

        # Compute simple status lights
        is_running = bool(status.get("is_running", False))
        age = health.get("last_reading_age_s", None)
        sensors_5m = int(health.get("distinct_sensors_window", 0) or 0)

        # Freshness thresholds (tune later)
        if (age is None) or (not health.get("db_ok", False)):
            freshness_light = "🔴"
            freshness_text = "No readings yet"
        elif age <= 120:
            freshness_light = "🟢"
            freshness_text = f"{int(age)}s ago"
        elif age <= 600:
            freshness_light = "🟡"
            freshness_text = f"{int(age)}s ago"
        else:
            freshness_light = "🔴"
            freshness_text = f"{int(age)}s ago"

        run_light = "🟢" if is_running else "🔴"
        bt = health.get("bluetooth_powered", None)
        bt_light = "🟢" if bt is True else ("🔴" if bt is False else "🟡")
        bt_text = "Powered" if bt is True else ("Off" if bt is False else "Unknown")

        st.markdown("### 🩺 System Health")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Monitor service", f"{run_light} {'Running' if is_running else 'Stopped'}")
        with c2:
            st.metric("Bluetooth", f"{bt_light} {bt_text}")
        with c3:
            st.metric("Sensors (last 5m)", f"{'🟢' if sensors_5m > 0 else '🔴'} {sensors_5m}")
        with c4:
            db_ok = bool(health.get("db_ok", False))
            st.metric("Database", f"{'🟢' if db_ok else '🔴'} {'OK' if db_ok else 'Error'}")

        # Check for dropped readings
        try:
            from storage import get_dropped_readings_count, get_connection
            conn = get_connection()
            dropped_count = get_dropped_readings_count(conn)
            conn.close()
            if dropped_count > 0:
                st.warning(
                    f"⚠️ **{dropped_count} readings have been dropped** due to database backlog. "
                    "Consider archiving old data in Settings → Data Management."
                )
        except Exception:
            pass  # Don't let this break the dashboard

        # Optional: compact "what to do" hints only when red
        if not is_running:
            st.info("Monitor is stopped. Use **Start monitoring** below.")
        if bt is False:
            st.warning("Bluetooth is OFF. Run: `sudo bash ~/Watchdog/deploy/watchdog-bt-unblock.sh`")
        if db_ok is False:
            st.warning("Database check failed. Verify `~/Watchdog/monitor/data/data.sqlite3` exists and permissions are correct.")
        
        # --------------------
        # Start/Stop controls
        # --------------------
        col_start, col_stop, col_spacer = st.columns([1, 1, 3])
        with col_start:
            if st.button("▶️ Start", disabled=is_running, use_container_width=True):
                success, msg, pid = start_monitoring()
                if success:
                    st.success(f"Started monitoring (PID {pid})")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"Failed to start: {msg}")
        
        with col_stop:
            if st.button("⏹️ Stop", disabled=not is_running, use_container_width=True):
                success, msg = stop_monitoring()
                if success:
                    st.success("Stopped monitoring")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(f"Failed to stop: {msg}")

        st.divider()

        # --------------------
        # Current Readings
        # --------------------
        conn = None
        df_latest = pd.DataFrame()
        
        try:
            conn = get_connection()
            init_db(conn)
            
            # Get latest readings from last 5 minutes
            cutoff = time.time() - 300
            df_latest = pd.read_sql_query(
                """
                SELECT r.sensor_id, r.name, r.ts, r.temp_c, r.humidity, r.battery, r.rssi
                FROM readings r
                INNER JOIN (
                    SELECT sensor_id, MAX(ts) as max_ts
                    FROM readings
                    WHERE ts >= ?
                    GROUP BY sensor_id
                ) latest ON r.sensor_id = latest.sensor_id AND r.ts = latest.max_ts
                ORDER BY r.name
                """,
                conn,
                params=(cutoff,)
            )
        except Exception as e:
            st.error(f"Database error: {e}")

        # Get configured sensor IDs
        configured_ids = set(cfg.sensors.keys()) if cfg.sensors else set()

        # Handle case where monitoring isn't running or no data yet
        if df_latest.empty:
            if not is_running:
                st.session_state.pop("scan_autorefresh_started_at", None)
                st.info("👆 Click **Start** to begin monitoring")
            else:
                # Auto-refresh while scanning
                if "scan_autorefresh_started_at" not in st.session_state:
                    st.session_state.scan_autorefresh_started_at = time.time()
                
                elapsed = time.time() - st.session_state.scan_autorefresh_started_at
                remaining = max(0, 300 - int(elapsed))
                
                if elapsed < 300:
                    st.warning("Waiting for sensor data... This can take 10-30 seconds after starting.")
                    st.caption(f"Auto-refresh in 15s ({remaining}s until timeout)...")
                    
                    refresh_html = """
                    <script>
                        setTimeout(function() {
                            window.parent.location.reload();
                        }, 15000);
                    </script>
                    """
                    st.components.v1.html(refresh_html, height=0)
                    
                    if st.button("🔄 Refresh Now", key="manual_refresh_scanning"):
                        st.rerun()
                else:
                    st.info("Auto-refresh stopped after 5 minutes. Click below to refresh manually.")
                    if st.button("🔄 Refresh Page", key="manual_refresh_timeout"):
                        st.rerun()
        else:
            # We have data - render the main dashboard
            st.session_state.pop("scan_autorefresh_started_at", None)
            
            # Determine what to display
            detected_ids = set(df_latest["sensor_id"].unique())
            unconfigured_detected = sorted(list(detected_ids - configured_ids))

            if configured_ids:
                df_display = df_latest[df_latest["sensor_id"].isin(configured_ids)].copy()
            else:
                df_display = df_latest.copy()

            # If sensors are configured but none have reported recently
            if configured_ids and df_display.empty:
                st.warning("No configured sensors have reported in the last 5 minutes.")
                if unconfigured_detected:
                    st.info(
                        "Watchdog has detected unconfigured sensor(s): "
                        + ", ".join(unconfigured_detected)
                        + ". Go to **Setup** to add/configure them."
                    )
            else:
                # Surface newly detected sensors
                if unconfigured_detected:
                    st.info(
                        "🆕 New sensor(s) detected but not configured yet: "
                        + ", ".join(unconfigured_detected)
                        + ". Go to **Setup** to select and configure."
                    )

                # Check for alerts
                alerts = check_alerts(df_display, cfg)
                email_config = getattr(cfg, 'email', None)
                process_alerts_and_notify(alerts, cfg, email_config)

                # Render alarm banners
                render_alarm_banners(cfg)

                # Display current readings
                st.subheader("Current Readings")

                num_sensors = len(df_display)
                if num_sensors > 0:
                    cols = st.columns(min(3, num_sensors))

                    for idx, row in df_display.reset_index(drop=True).iterrows():
                        col = cols[idx % 3]
                        
                        with col:
                            # Convert to user's units
                            if cfg.units.upper() == "F":
                                temp = row['temp_c'] * 9/5 + 32
                                unit = "°F"
                            else:
                                temp = row['temp_c']
                                unit = "°C"
                            
                            st.markdown(f"**{row['name']}**")
                            st.metric("Temperature", f"{temp:.1f}{unit}")
                            st.metric("Humidity", f"{row['humidity']:.1f}%")
                
                st.divider()
                
                # Historical charts section
                if len(df_display) > 0:
                    # Time window selector
                    col_window, col_weather, col_spacer = st.columns([2, 2, 2])
                    with col_window:
                        time_window = st.selectbox(
                            "Time Range",
                            ["Hour", "Day", "Week", "Month"],
                            index=0,
                            key="time_window"
                        )
                    with col_weather:
                        show_weather = st.checkbox(
                            "Show Outdoor Temp", 
                            value=cfg.weather is not None,
                            disabled=cfg.weather is None,
                            help="Overlay outdoor temperature from weather API" if cfg.weather else "Set location in Settings to enable"
                        )
                    
                    # Calculate time range                
                    user_tz = ZoneInfo(cfg.weather.timezone if cfg.weather else "America/New_York")
                    now_dt = datetime.now(tz=user_tz)
                    
                    window_config = {
                        "Hour": (timedelta(hours=1), 10, 360),      # 1h, 10s buckets, 360 points
                        "Day": (timedelta(days=1), 300, 288),       # 24h, 5m buckets, 288 points
                        "Week": (timedelta(weeks=1), 1800, 336),    # 7d, 30m buckets, 336 points
                        "Month": (timedelta(days=30), 3600, 720),   # 30d, 1h buckets, 720 points
                    }
                    
                    delta, bucket_sec, max_points = window_config[time_window]
                    start_dt = now_dt - delta
                    start_ts = start_dt.timestamp()
                    end_ts = now_dt.timestamp()
                    
                    # Get historical data
                    sensor_ids = tuple(df_display["sensor_id"].unique())
                    db_path_str = str(get_db_path())
                    
                    if time_window == "Hour":
                        df_history = _cached_trends_fast(sensor_ids, start_ts, end_ts, bucket_sec, max_points, db_path_str)
                    else:
                        df_history = _cached_trends_slow(sensor_ids, start_ts, end_ts, bucket_sec, max_points, db_path_str)
                    
                    if not df_history.empty:
                        # Convert timestamp to datetime
                        # Note: get_downsampled_timeseries returns 'timestamp' column, not 'ts'
                        ts_col = 'timestamp' if 'timestamp' in df_history.columns else 'ts'
                        df_history['datetime'] = pd.to_datetime(df_history[ts_col], unit='s', utc=True).dt.tz_convert(user_tz)
                        
                        # Convert temp to user's units
                        if cfg.units.upper() == "F":
                            df_history['temp'] = df_history['temp_c'] * 9/5 + 32
                            temp_label = "Temperature (°F)"
                        else:
                            df_history['temp'] = df_history['temp_c']
                            temp_label = "Temperature (°C)"
                        
                        # Temperature chart
                        st.subheader("Temperature History")
                        temp_chart = alt.Chart(df_history).mark_line().encode(
                            x=alt.X('datetime:T', title='Time'),
                            y=alt.Y('temp:Q', title=temp_label),
                            color=alt.Color('name:N', title='Sensor'),
                            tooltip=['name', 'datetime:T', 'temp:Q']
                        ).properties(height=300)
                        st.altair_chart(temp_chart, use_container_width=True)
                        
                        # Humidity chart
                        st.subheader("Humidity History")
                        hum_chart = alt.Chart(df_history).mark_line().encode(
                            x=alt.X('datetime:T', title='Time'),
                            y=alt.Y('humidity:Q', title='Humidity (%)'),
                            color=alt.Color('name:N', title='Sensor'),
                            tooltip=['name', 'datetime:T', 'humidity:Q']
                        ).properties(height=300)
                        st.altair_chart(hum_chart, use_container_width=True)
                    else:
                        st.info(f"No historical data available for the selected {time_window.lower()} range.")

        # Close connection if opened
        if conn is not None:
            conn.close()

# ====================
# TAB 2: SETUP
# ====================
with tab2:
    st.header("Sensor Setup")

    st.info("Detect sensors first, then choose which ones to configure and monitor.")

    # --------------------
    # Detection (runtime truth, from DB)
    # --------------------
    status = get_monitoring_status()
    if not status.get("is_running", False):
        st.warning("Monitoring is not running. Click **Start** on the Monitoring tab to begin detection.")
    else:
        st.caption("Monitoring is running. Recent detections are shown below.")

    detected_df = pd.DataFrame()
    try:
        conn = get_connection()
        init_db(conn)

        # Use a wider window for setup so sensors have time to appear on first run
        cutoff = time.time() - 900  # last 15 minutes
        start_ts = cutoff
        end_ts = time.time()

        detected_query = """
        SELECT
            r.sensor_id,
            r.name,
            r.ts AS timestamp,
            r.temp_c,
            r.humidity,
            r.battery,
            r.rssi
        FROM readings r
        JOIN (
            SELECT sensor_id, MAX(ts) AS max_ts
            FROM readings
            WHERE ts BETWEEN ? AND ?
            GROUP BY sensor_id
        ) m
        ON r.sensor_id = m.sensor_id AND r.ts = m.max_ts
        ORDER BY r.name
        """
        detected_df = pd.read_sql_query(detected_query, conn, params=(start_ts, end_ts))
        conn.close()
    except Exception as e:
        st.error(f"Could not load recent detections: {e}")

    st.subheader("Detected Sensors")

    if detected_df.empty:
        if status.get("is_running", False):
            st.warning("No sensors detected yet. This can take ~10–30 seconds after starting monitoring.")

            # ---------------------------------------------------------
            # Auto-refresh while scanning
            # ---------------------------------------------------------

            if "setup_autorefresh_started_at" not in st.session_state:
                st.session_state.setup_autorefresh_started_at = time.time()
            
            elapsed = time.time() - st.session_state.setup_autorefresh_started_at
            remaining = max(0, 300 - int(elapsed))
            
            if elapsed < 300:
                st.caption(f"Auto-refresh in {15}s ({remaining}s until timeout)...")
                
                refresh_html = """
                <script>
                    setTimeout(function() {
                        window.parent.location.reload();
                    }, 15000);
                </script>
                """
                st.components.v1.html(refresh_html, height=0)
                
                if st.button("🔄 Refresh Now", key="setup_manual_refresh"):
                    st.rerun()
            else:
                st.info("Auto-refresh stopped. Click below to check for sensors.")
                if st.button("🔄 Refresh Page", key="setup_manual_timeout"):
                    st.rerun()       
        
        else:
            st.session_state.pop("setup_autorefresh_started_at", None)
            st.info("Start monitoring to detect sensors.")
    else:
        configured_ids = set(cfg.sensors.keys()) if cfg.sensors else set()
        detected_ids = detected_df["sensor_id"].tolist()

        with st.expander("📡 Detected sensor details", expanded=False):
            display_df = detected_df.copy()
            if cfg.units.upper() == "F":
                display_df["temp"] = display_df["temp_c"] * 9 / 5 + 32
            else:
                display_df["temp"] = display_df["temp_c"]

            display_df["last_seen_local"] = display_df["timestamp"].apply(
                lambda ts: convert_to_local(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
            )
            st.dataframe(
                display_df[["sensor_id", "name", "last_seen_local", "temp", "humidity", "battery", "rssi"]],
                use_container_width=True,
                hide_index=True,
            )

        st.markdown(
            "**Watchdog has detected the following sensors. Select the sensors you want to configure and monitor:**"
        )

        default_to_add = [sid for sid in detected_ids if sid not in configured_ids]

        with st.form("sensor_add_form", clear_on_submit=False):
            to_add = st.multiselect(
                "Detected sensors",
                options=detected_ids,
                default=default_to_add,
                help="Only selected sensors will be added to your configuration and used for monitoring/alerts.",
            )
            submitted = st.form_submit_button("➕ Add selected sensors to configuration")

        if submitted:
            added = 0
            cfg.sensors = cfg.sensors or {}
            for sid in to_add:
                if sid not in cfg.sensors:
                    cfg.sensors[sid] = SensorConfig(id=sid, name=sid)
                    added += 1

            if added:
                save_config(cfg)
                st.success(f"Added {added} sensor(s) to configuration.")
                time.sleep(0.5)
                st.rerun()
            else:
                st.info("No new sensors were added (they may already be configured).")

    # -------------------------------------------------------------
    # Sensor Health Trends (time series): Battery and Signal Strength
    # -------------------------------------------------------------
    st.subheader("Sensor Health Trends")
    st.caption("Battery and signal strength are shown here (Setup), not on Monitoring.")

    # Choose time window (separate from Monitoring tab selector)
    col_hw, col_sp = st.columns([2, 6])
    with col_hw:
        health_window = st.selectbox(
            "Time Range",
            ["Hour", "Day", "Week", "Month"],
            index=0,
            key="health_time_window"
        )

    now_dt = datetime.now()
    if health_window == "Hour":
        start_dt = now_dt - timedelta(hours=1)
        time_format = "%H:%M"
    elif health_window == "Day":
        start_dt = now_dt - timedelta(days=1)
        time_format = "%H:%M"
    elif health_window == "Week":
        start_dt = now_dt - timedelta(weeks=1)
        time_format = "%a %H:%M"
    else:  # Month
        start_dt = now_dt - timedelta(days=30)
        time_format = "%m/%d"

    start_ts = start_dt.timestamp()

    # Decide which sensors to plot:
    # - If config exists, plot configured sensors (preferred).
    # - Otherwise, plot any sensors detected in Setup (from detected_df).
    configured_ids = set(cfg.sensors.keys()) if cfg.sensors else set()
    detected_ids = set(detected_df["sensor_id"].tolist()) if not detected_df.empty else set()
    plot_ids = configured_ids if configured_ids else detected_ids
 
    if not plot_ids:
        st.info("No sensors available to plot yet. Start monitoring and wait for detections.")
    else:
        try:
            conn_h = get_connection()
            init_db(conn_h)

            q_health = """
            SELECT
                sensor_id,
                ts AS timestamp,
                battery,
                rssi
            FROM readings
            WHERE ts >= ?
            ORDER BY ts
            """
            df_h = pd.read_sql_query(q_health, conn_h, params=(start_ts,))
            conn_h.close()

            # Filter to selected sensors
            df_h = df_h[df_h["sensor_id"].isin(list(plot_ids))].copy()

            if df_h.empty or len(df_h) < 5:
                st.caption("Collecting sensor health data...")
            else:
                user_tz = get_user_timezone()
                df_h["time"] = pd.to_datetime(df_h["timestamp"], unit="s", utc=True).dt.tz_convert(user_tz)

                # Friendly names
                if cfg.sensors:
                    label_map = {sid: scfg.name for sid, scfg in cfg.sensors.items()}
                else:
                    # fall back to detected names (if present); otherwise sensor_id
                    tmp = detected_df.set_index("sensor_id")["name"].to_dict() if not detected_df.empty else {}
                    label_map = tmp

                df_h["sensor_label"] = df_h["sensor_id"].map(label_map).fillna(df_h["sensor_id"])

                # ---- Plot 3/4: Battery ----
                battery_chart = alt.Chart(df_h).mark_line(strokeWidth=2).encode(
                    x=alt.X(
                        "time:T",
                        title="Time",
                        axis=alt.Axis(format=time_format, labelAngle=-45)
                    ),
                    y=alt.Y(
                        "battery:Q",
                        title="Sensor Battery (%)",
                        scale=alt.Scale(domain=[0, 100])
                    ),
                    color=alt.Color(
                        "sensor_label:N",
                        title="Sensor",
                        legend=alt.Legend(orient="bottom")
                    ),
                    tooltip=[
                        alt.Tooltip("sensor_label:N", title="Sensor"),
                        alt.Tooltip("time:T", title="Time", format="%Y-%m-%d %H:%M:%S"),
                        alt.Tooltip("battery:Q", title="Battery (%)", format=".0f"),
                    ],
                ).properties(height=260).interactive()

                st.altair_chart(battery_chart, use_container_width=True)

                # ---- Plot 4/4: Signal Strength ----
                signal_chart = alt.Chart(df_h).mark_line(strokeWidth=2).encode(
                    x=alt.X(
                        "time:T",
                        title="Time",
                        axis=alt.Axis(format=time_format, labelAngle=-45)
                    ),
                    y=alt.Y(
                        "rssi:Q",
                        title="Sensor Signal Strength (dBm)",
                        scale=alt.Scale(zero=False)
                    ),
                    color=alt.Color(
                        "sensor_label:N",
                        title="Sensor",
                        legend=alt.Legend(orient="bottom")
                    ),
                    tooltip=[
                        alt.Tooltip("sensor_label:N", title="Sensor"),
                        alt.Tooltip("time:T", title="Time", format="%Y-%m-%d %H:%M:%S"),
                        alt.Tooltip("rssi:Q", title="Signal (dBm)", format=".0f"),
                    ],
                ).properties(height=260).interactive()

                st.altair_chart(signal_chart, use_container_width=True)

        except Exception as e:
            st.error(f"Could not build sensor health trends: {e}")

    st.divider()

    # --------------------
    # Configuration (policy truth, from config)
    # --------------------
    st.subheader("Configured Sensors")

    if cfg.sensors:
        for sensor_id, sensor_cfg in cfg.sensors.items():
            with st.expander(f"⚙️ {sensor_cfg.name} ({sensor_id})"):
                new_name = st.text_input(
                    "Sensor Name",
                    value=sensor_cfg.name,
                    key=f"name_{sensor_id}"
                )

                col1, col2 = st.columns(2)
                with col1:
                    if cfg.units.upper() == "F":
                        default_min = sensor_cfg.min_temp_c * 9/5 + 32 if sensor_cfg.min_temp_c is not None else 32.0
                        min_temp = st.number_input(
                            "Min Temperature (°F)",
                            value=float(default_min),
                            key=f"min_{sensor_id}_{cfg.units.upper()}"
                        )
                        sensor_cfg.min_temp_c = (min_temp - 32) * 5/9
                    else:
                        min_temp = st.number_input(
                            "Min Temperature (°C)",
                            value=float(sensor_cfg.min_temp_c) if sensor_cfg.min_temp_c is not None else 0.0,
                            key=f"min_{sensor_id}_{cfg.units.upper()}"
                        )
                        sensor_cfg.min_temp_c = min_temp

                with col2:
                    if cfg.units.upper() == "F":
                        default_max = sensor_cfg.max_temp_c * 9/5 + 32 if sensor_cfg.max_temp_c is not None else 90.0
                        max_temp = st.number_input(
                            "Max Temperature (°F)",
                            value=float(default_max),
                            key=f"max_{sensor_id}_{cfg.units.upper()}"
                        )
                        sensor_cfg.max_temp_c = (max_temp - 32) * 5/9
                    else:
                        max_temp = st.number_input(
                            "Max Temperature (°C)",
                            value=float(sensor_cfg.max_temp_c) if sensor_cfg.max_temp_c is not None else 30.0,
                            key=f"max_{sensor_id}_{cfg.units.upper()}"
                        )
                        sensor_cfg.max_temp_c = max_temp

                sensor_cfg.name = new_name
                cfg.sensors[sensor_id] = sensor_cfg

        col_save, col_remove = st.columns([1, 3])
        with col_save:
            if st.button("💾 Save Configuration", type="primary"):
                save_config(cfg)
                st.success("Configuration saved!")
                time.sleep(0.5)
                st.rerun()

        with col_remove:
            to_remove = st.multiselect(
                "Remove configured sensors",
                options=list(cfg.sensors.keys()),
                default=[],
                help="Removing a sensor stops monitoring/alerts for it. Historical data remains in the database.",
                key="remove_sensors",
            )
            if st.button("🗑️ Remove selected sensors"):
                for sid in to_remove:
                    cfg.sensors.pop(sid, None)
                save_config(cfg)
                st.success(f"Removed {len(to_remove)} sensor(s).")
                time.sleep(0.5)
                st.rerun()
    else:
        st.warning("No sensors configured yet.")
        st.caption("Start monitoring to detect sensors, then select which ones to configure above.")

# ====================
# TAB 3: SETTINGS
# ====================
with tab3:
    st.header("Settings")
    
    # Temperature units
    st.subheader("Display Preferences")
    new_units = st.radio(
        "Temperature Units",
        options=["F", "C"],
        index=0 if cfg.units.upper() == "F" else 1,
        horizontal=True
    )
    cfg.units = new_units
    
    st.divider()
    
    # Alerts
    st.subheader("Alert Configuration")
    cfg.alerts.enabled = st.checkbox("Enable Alerts", value=cfg.alerts.enabled)
    
    if cfg.alerts.enabled:
        cfg.alerts.low_battery_threshold = st.slider(
            "Low Battery Alert (%)",
            min_value=10,
            max_value=50,
            value=cfg.alerts.low_battery_threshold
        )
        
        cfg.alerts.sensor_offline_minutes = st.slider(
            "Sensor Offline Alert (minutes)",
            min_value=5,
            max_value=60,
            value=cfg.alerts.sensor_offline_minutes
        )
        
        cfg.alerts.temp_alerts_enabled = st.checkbox(
            "Enable Temperature Alerts",
            value=cfg.alerts.temp_alerts_enabled
        )
    
    # Email notifications
    new_email_config = render_email_settings(getattr(cfg, 'email', None))
    if new_email_config is not None:
        cfg.email = new_email_config
    
    st.divider()
    
    # Location & Timezone
    st.subheader("Location & Timezone")
    
    if cfg.weather:
        st.success(f"📍 {cfg.weather.label}")
        
        # Show current timezone with override option
        current_tz = cfg.weather.timezone
        
        # Find current index in US_TIMEZONES
        tz_options = [tz[0] for tz in US_TIMEZONES]
        tz_labels = [tz[1] for tz in US_TIMEZONES]
        
        try:
            current_idx = tz_options.index(current_tz)
        except ValueError:
            current_idx = 0  # Default to Eastern if not found
        
        selected_tz_label = st.selectbox(
            "Timezone",
            options=tz_labels,
            index=current_idx,
            key="timezone_select"
        )
        
        # Map label back to timezone ID
        selected_tz = tz_options[tz_labels.index(selected_tz_label)]
        
        if selected_tz != cfg.weather.timezone:
            cfg.weather.timezone = selected_tz
            st.info(f"Timezone will be updated to {selected_tz_label} when you save.")
        
        # Current weather display
        st.markdown("**Current Outdoor Conditions:**")
        weather_data = cached_current_weather(
            cfg.weather.latitude, 
            cfg.weather.longitude
        )
        
        if weather_data.get('available', False):
            col1, col2, col3 = st.columns(3)
            with col1:
                temp_c = weather_data['temp_c']
                if cfg.units.upper() == "F":
                    temp_f = temp_c * 9/5 + 32
                    st.metric("Outdoor Temperature", f"{temp_f:.1f}°F")
                else:
                    st.metric("Outdoor Temperature", f"{temp_c:.1f}°C")
            
            with col2:
                st.metric("Outdoor Humidity", f"{weather_data['humidity']:.0f}%")
            
            with col3:
                st.metric("Wind Speed", f"{weather_data['wind_mph']:.1f} mph")
        else:
            st.caption("☁️ Weather data unavailable")
        
        if st.button("Change Location"):
            cfg.weather = None
            save_config(cfg)
            st.rerun()
    
    else:
        st.info("Set your location to enable timezone detection and outdoor weather comparison.")
        
        zipcode = st.text_input("ZIP Code", placeholder="e.g., 02134")
        
        if st.button("Set Location"):
            if zipcode:
                try:
                    with st.spinner("Looking up location..."):
                        weather_point = geocode_zip(zipcode)
                    
                    cfg.weather = WeatherConfig(
                        zipcode=zipcode,
                        latitude=weather_point.latitude,
                        longitude=weather_point.longitude,
                        label=weather_point.label,
                        timezone=weather_point.timezone
                    )
                    save_config(cfg)
                    st.success(f"Location set: {weather_point.label}")
                    st.info(f"Detected timezone: {weather_point.timezone}")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to set location: {e}")
            else:
                st.warning("Please enter a ZIP code")
    
    st.divider()
    
    # Data management
    st.subheader("Data Management")
    
    db_stats = get_db_stats()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Database Size", f"{db_stats['size_mb']:.1f} MB")
        st.metric("Total Readings", f"{db_stats['total_rows']:,}")
    
    with col2:
        if db_stats['oldest_reading']:
            st.metric("Oldest Reading", 
                     db_stats['oldest_reading'].strftime("%Y-%m-%d"))
        if db_stats['newest_reading']:
            st.metric("Newest Reading", 
                     db_stats['newest_reading'].strftime("%Y-%m-%d"))
    
    # Archiving
    st.subheader("Data Archiving")
    cfg.archive.enabled = st.checkbox(
        "Enable Auto-Archiving", 
        value=cfg.archive.enabled
    )
    
    if cfg.archive.enabled:
        cfg.archive.retention_days = st.slider(
            "Keep Active Data For (days)",
            min_value=7,
            max_value=365,
            value=cfg.archive.retention_days
        )
        
        if st.button("Archive Old Data Now"):
            with st.spinner("Archiving..."):
                result = archive_old_data(cfg.archive.retention_days)
            
            if result['success']:
                st.success(result['message'])
            else:
                st.error(result['message'])
    
    # List archives
    archives = list_archives()
    if archives:
        st.subheader("Archived Data")
        for archive in archives:
            st.caption(
                f"📦 {archive['filename']} - "
                f"{archive['size_mb']:.1f} MB - "
                f"{archive['modified'].strftime('%Y-%m-%d %H:%M')}"
            )
    
    st.divider()
    
    # System info
    st.subheader("System Information")
    
    status = get_monitoring_status()
    
    # Show timezone info
    if cfg.weather:
        tz_display = cfg.weather.timezone
    else:
        tz_display = "Not configured (defaulting to Eastern)"
    
    st.json({
        "Monitoring Active": status['is_running'],
        "Process ID": status.get('pid'),
        "CPU Usage": f"{status.get('cpu_percent', 0):.1f}%",
        "Memory Usage": f"{status.get('memory_mb', 0):.1f} MB",
        "Data Age (minutes)": f"{status.get('data_age_minutes', 0):.1f}",
        "System Healthy": status.get('is_healthy', False),
        "Timezone": tz_display
    })
    
    st.divider()
    
    # Save settings
    if st.button("💾 Save All Settings", type="primary"):
        save_config(cfg)
        st.success("Settings saved!")
        time.sleep(1)
        st.rerun()

# Footer with manual refresh
st.divider()
col_footer, col_refresh = st.columns([3, 1])
with col_footer:
    st.caption("Watchdog Environmental Monitor v1.0.0 | © 2026")
with col_refresh:
    if st.button("🔄 Refresh Page", use_container_width=True, key="footer_refresh"):
        st.rerun()
