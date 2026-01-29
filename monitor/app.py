#!/usr/bin/env python3
"""
Watchdog Environmental Monitor - Production Dashboard
Same monitoring experience as OSS, with database persistence and production features

MODIFIED (Audit Sections A/B):
- Added memory instrumentation (enable via WATCHDOG_MEM_PROBES=1)
- Added gc.collect() at end of render cycle
- Increased auto-refresh interval from 15s to 30s for stability
- Added memory pressure warning in System Health panel
- Reduced chart point limits for Pi Zero 2 W compatibility
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
import gc  # Section B: explicit garbage collection

# Memory instrumentation (Section A) - OFF by default
from mem_probes import log_mem, cleanup_render, check_memory_pressure, get_memory_stats, MemoryProfileBlock

# Log memory at script start (Section A instrumentation point 1)
log_mem("script_start_after_imports")

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

SETUP_DETECTION_WINDOW_SEC = 900

# Section B: Increased auto-refresh interval from 15000ms to 30000ms for stability on Pi Zero 2 W
DEFAULT_AUTOREFRESH_MS = 30_000  # Was 15_000

# Section B: Reduced chart point limits for memory stability
# These are more conservative limits suitable for Pi Zero 2 W (512MB RAM)
CHART_MAX_POINTS_HOUR = 120    # Was 360 (1h at 30s buckets)
CHART_MAX_POINTS_DAY = 144     # Was 288 (24h at 10m buckets)
CHART_MAX_POINTS_WEEK = 168    # Same (7d at 1h buckets)
CHART_MAX_POINTS_MONTH = 360   # Same (30d at 2h buckets)

# -----------------------------------------------------------------------------
# P0 performance: cached trends queries
# -----------------------------------------------------------------------------
@st.cache_data(ttl=5, show_spinner=False)
def _get_health_data(seconds: int, max_points: int, db_path_str: str):
    """
    Returns (latest_dict_or_None, timeseries_list_of_dicts)
    - latest: newest row in system_health
    - timeseries: downsampled buckets from health_sampler.get_recent_samples
    """
    conn = get_connection(db_path_str)
    try:
        row = conn.execute("""
            SELECT ts, mem_total_mb, mem_avail_mb, mem_used_mb,
                   swap_total_mb, swap_used_mb,
                   load_1m, load_5m, load_15m,
                   monitor_rss_mb, dashboard_rss_mb
            FROM system_health
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()

        latest = None
        if row:
            cols = [
                "ts","mem_total_mb","mem_avail_mb","mem_used_mb",
                "swap_total_mb","swap_used_mb",
                "load_1m","load_5m","load_15m",
                "monitor_rss_mb","dashboard_rss_mb"
            ]
            latest = dict(zip(cols, row))
            # Normalize None -> 0 for arithmetic
            latest["monitor_rss_mb"] = latest["monitor_rss_mb"] or 0
            latest["dashboard_rss_mb"] = latest["dashboard_rss_mb"] or 0

        timeseries = get_recent_samples(conn, seconds=seconds, max_points=max_points)
        return latest, timeseries
    except Exception:
        return None, []
    finally:
        conn.close()
        
@st.cache_data(ttl=5, show_spinner=False)
def _cached_trends_fast(sensor_ids_tuple, start_ts, end_ts, bucket_seconds, max_points_per_sensor, db_path_str):
    """Short TTL cache for live hour view."""
    log_mem("cached_trends_fast_start")  # Section A instrumentation
    conn = get_connection(db_path_str)
    try:
        result = get_downsampled_timeseries(
            conn,
            list(sensor_ids_tuple),
            start_ts,
            end_ts,
            bucket_seconds=bucket_seconds,
            max_points_per_sensor=max_points_per_sensor,
        )
        log_mem("cached_trends_fast_end")  # Section A instrumentation
        return result
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def _cached_trends_slow(sensor_ids_tuple, start_ts, end_ts, bucket_seconds, max_points_per_sensor, db_path_str):
    """Longer TTL cache for day/week/month (and fixed windows)."""
    log_mem("cached_trends_slow_start")  # Section A instrumentation
    conn = get_connection(db_path_str)
    try:
        result = get_downsampled_timeseries(
            conn,
            list(sensor_ids_tuple),
            start_ts,
            end_ts,
            bucket_seconds=bucket_seconds,
            max_points_per_sensor=max_points_per_sensor,
        )
        log_mem("cached_trends_slow_end")  # Section A instrumentation
        return result
    finally:
        conn.close()
 
# Page config
st.set_page_config(
    page_title="Watchdog Environmental Monitor",
    page_icon="🐕",
    layout="wide",
    initial_sidebar_state="expanded"
)

@st.cache_resource
def get_app_config():
    """Load and cache dashboard configuration."""
    return load_config()

def invalidate_app_config_cache() -> None:
    """Force the next rerun to reload configuration from disk."""
    try:
        get_app_config.clear()
    except Exception:
        pass

# Load config (cached)
cfg = get_app_config()

# ---------------------------------------------------------------------
# Cached monitoring status (prevents repeated psutil/DB work on rerun)
# ---------------------------------------------------------------------
@st.cache_data(ttl=5)
def get_cached_monitoring_status():
    return get_monitoring_status()

def invalidate_monitoring_status_cache() -> None:
    try:
        get_cached_monitoring_status.clear()
    except Exception:
        pass

# ---------------------------------------------------------------------
# Global autorefresh scheduler (ensures only ONE st_autorefresh exists)
# ---------------------------------------------------------------------
# Reset desired refresh each rerun
st.session_state["_autorefresh_interval_ms"] = None

def schedule_autorefresh(interval_ms: int) -> None:
    """Request a single global refresh; the smallest interval wins."""
    cur = st.session_state.get("_autorefresh_interval_ms")
    if cur is None or interval_ms < cur:
        st.session_state["_autorefresh_interval_ms"] = interval_ms

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
            (cutoff,),
        ).fetchone()
        detected_count = result[0] if result else 0
        conn.close()
    except Exception:
        pass
    
    # Progress indicator
    steps_done = 0
    if monitoring_running:
        steps_done = 1
    if detected_count > 0:
        steps_done = 2
    if cfg.sensors and len(cfg.sensors) > 0:
        steps_done = 3
    
    st.progress(steps_done / 3, text=f"Step {steps_done + 1} of 3")
    
    # Step 1: Start monitoring
    st.markdown("### Step 1: Start Monitoring")
    if not monitoring_running:
        st.info("Click the button below to start scanning for sensors.")
        if st.button("▶️ Start Monitoring", type="primary", key="onboard_start"):
            with st.spinner("Starting monitoring service..."):
                success, message, pid = start_monitoring()
            if success:
                st.success("Monitoring started!")
                invalidate_monitoring_status_cache()
                time.sleep(1)
                st.rerun()
            else:
                st.error(f"Failed to start: {message}")
    else:
        st.success("✅ Monitoring is running")
    
    # Step 2: Detect sensors
    st.markdown("### Step 2: Detect Sensors")
    if not monitoring_running:
        st.caption("Start monitoring first to detect sensors.")
    elif detected_count == 0:
        st.warning("Scanning for sensors... This can take 10-30 seconds.")
        st.caption("Make sure your Govee sensors are nearby and powered on.")
        # Auto-refresh during detection
        schedule_autorefresh(DEFAULT_AUTOREFRESH_MS)
    else:
        st.success(f"✅ Found {detected_count} sensor(s)")
    
    # Step 3: Configure sensors
    st.markdown("### Step 3: Configure Sensors")
    if detected_count == 0:
        st.caption("Sensors will appear here once detected.")
    else:
        st.info("Go to the **Setup** tab to name your sensors and set alert thresholds.")
        if st.button("➡️ Go to Setup", key="onboard_setup"):
            st.session_state["_active_tab"] = 1
            st.rerun()


def get_user_timezone():
    """Get user timezone from config or default to Eastern."""
    if cfg.weather and cfg.weather.timezone:
        return ZoneInfo(cfg.weather.timezone)
    return ZoneInfo("America/New_York")


def convert_to_local(ts: float) -> datetime:
    """Convert unix timestamp to local datetime."""
    user_tz = get_user_timezone()
    return datetime.fromtimestamp(ts, tz=user_tz)


# ---------------------------------------------------------------------
# Cached weather queries (prevent hammering API on rerun)
# ---------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def get_cached_current_weather(lat, lon):
    return fetch_current_weather(lat, lon)


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_weather_series(lat, lon, start_iso, end_iso):
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
        # Section A/B: Add memory stats to health
        "memory_percent": 0.0,
        "memory_pressure": False,
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

    # Section A/B: Memory health check
    try:
        mem_stats = get_memory_stats()
        health["memory_percent"] = mem_stats.get("system_percent", 0.0)
        health["memory_pressure"] = check_memory_pressure()
    except Exception:
        pass

    return health

# ---------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------

st.title("🐕 Watchdog Environmental Monitor")
st.caption("Production Environmental Monitoring with Self-Healing")

# Top-level tabs
tab1, tab2, tab3, tab4 = st.tabs(["📊 Monitoring", "⚙️ Setup", "🔧 Settings", "🖥️ System Health"])

# ====================
# TAB 1: MONITORING (OSS-STYLE)
# ====================
with tab1:
    # === CRITICAL: Email system warning (must be first!) ===
    from alarm_ui import render_email_system_warning
    render_email_system_warning()
    
    # Check monitoring status
    status = get_cached_monitoring_status()
    is_running = bool(status.get("is_running", False))
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
        # Pull lightweight health snapshot for warnings (traffic lights live in System Health tab)
        try:
            health = get_system_health(window_s=300)
        except Exception:
            health = {}
        
        # Section B: Memory pressure warning
        if health.get("memory_pressure", False):
            mem_pct = health.get("memory_percent", 0)
            st.warning(
                f"⚠️ **System memory pressure: {mem_pct:.0f}% used.** "
                "Dashboard may be slow. Consider closing other applications or restarting the Pi."
            )

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
            pass

        st.divider()

        # --------------------
        # Control buttons
        # --------------------
        col_start, col_stop = st.columns(2)
        with col_start:
            if st.button("▶️ Start", use_container_width=True, disabled=is_running):
                with st.spinner("Starting monitoring..."):
                    success, message, pid = start_monitoring()
                if success:
                    st.success(message)
                    invalidate_monitoring_status_cache()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(message)
        
        with col_stop:
            if st.button("⏹️ Stop", use_container_width=True, disabled=not is_running):
                with st.spinner("Stopping monitoring..."):
                    success, message = stop_monitoring()
                if success:
                    st.success(message)
                    invalidate_monitoring_status_cache()
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(message)
        
        st.divider()

        # --------------------
        # Main sensor display
        # --------------------
        conn = None
        df_latest = pd.DataFrame()
        
        try:
            conn = get_connection()
            init_db(conn)

            cutoff = time.time() - cfg.stale_after_sec

            df_latest = pd.read_sql_query(
                """
                SELECT r.sensor_id, r.name, r.ts, r.temp_c, r.humidity, r.battery, r.rssi
                FROM readings r
                INNER JOIN (
                    SELECT sensor_id, MAX(ts) AS max_ts
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
                st.session_state.pop("scan_refresh", None)
                st.info("👆 Click **Start** to begin monitoring")
            else:
                # Auto-refresh while scanning
                if "scan_autorefresh_started_at" not in st.session_state:
                    st.session_state.scan_autorefresh_started_at = time.time()
                
                elapsed = time.time() - st.session_state.scan_autorefresh_started_at
                remaining = max(0, 300 - int(elapsed))
                
                if elapsed < 300:
                    st.warning("Waiting for sensor data... This can take 10-30 seconds after starting.")
                    # Section B: Use DEFAULT_AUTOREFRESH_MS (30s instead of 15s)
                    refresh_sec = DEFAULT_AUTOREFRESH_MS // 1000
                    st.caption(f"Auto-refresh in {refresh_sec}s ({remaining}s until timeout)...")
                    
                    # Streamlit-native auto-refresh (no full page reload)
                    schedule_autorefresh(DEFAULT_AUTOREFRESH_MS)
                    
                    if st.button("🔄 Refresh Now", key="manual_refresh_scanning"):
                        st.rerun()
                else:
                    st.info("Auto-refresh stopped after 5 minutes. Click below to refresh manually.")
                    if st.button("🔄 Refresh Page", key="manual_refresh_timeout"):
                        st.rerun()
        else:
            # We have data - render the main dashboard
            st.session_state.pop("scan_autorefresh_started_at", None)
            st.session_state.pop("scan_refresh", None)
            
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
                    # Section A: Log memory before chart data fetch (instrumentation point)
                    log_mem(f"before_chart_fetch_{time_window}")
                    
                    user_tz = ZoneInfo(cfg.weather.timezone if cfg.weather else "America/New_York")
                    now_dt = datetime.now(tz=user_tz)
                    
                    # Section B: Use reduced chart point limits for memory stability
                    window_config = {
                        "Hour": (timedelta(hours=1), 30, CHART_MAX_POINTS_HOUR),      # 1h, 30s buckets
                        "Day": (timedelta(days=1), 600, CHART_MAX_POINTS_DAY),        # 24h, 10m buckets
                        "Week": (timedelta(weeks=1), 3600, CHART_MAX_POINTS_WEEK),    # 7d, 1h buckets
                        "Month": (timedelta(days=30), 7200, CHART_MAX_POINTS_MONTH),  # 30d, 2h buckets
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
                    
                    # Section A: Log memory after chart data fetch (instrumentation point)
                    log_mem(f"after_chart_fetch_{time_window}")
                    
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
                        
                        # Section A: Log memory before chart creation (instrumentation point)
                        log_mem("before_chart_creation")
                        
                        # Temperature chart
                        st.subheader("Temperature History")
                        with MemoryProfileBlock("temp_chart"):
                            temp_chart = alt.Chart(df_history).mark_line().encode(
                                x=alt.X('datetime:T', title='Time'),
                                y=alt.Y('temp:Q', title=temp_label),
                                color=alt.Color('name:N', title='Sensor'),
                                tooltip=['name', 'datetime:T', 'temp:Q']
                            ).properties(height=300)
                            st.altair_chart(temp_chart, use_container_width=True)
                        
                        # Humidity chart
                        st.subheader("Humidity History")
                        with MemoryProfileBlock("humidity_chart"):
                            hum_chart = alt.Chart(df_history).mark_line().encode(
                                x=alt.X('datetime:T', title='Time'),
                                y=alt.Y('humidity:Q', title='Humidity (%)'),
                                color=alt.Color('name:N', title='Sensor'),
                                tooltip=['name', 'datetime:T', 'humidity:Q']
                            ).properties(height=300)
                            st.altair_chart(hum_chart, use_container_width=True)
                        
                        # Section A: Log memory after chart creation (instrumentation point)
                        log_mem("after_chart_creation")
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
    status = get_cached_monitoring_status()
    if not status.get("is_running", False):
        st.warning("Monitoring is not running. Click **Start** on the Monitoring tab to begin detection.")
    else:
        st.caption("Monitoring is running. Recent detections are shown below.")

    detected_df = pd.DataFrame()
    try:
        conn = get_connection()
        init_db(conn)

        # Use a wider window for setup so sensors have time to appear on first run
        cutoff = time.time() - SETUP_DETECTION_WINDOW_SEC  # last 15 minutes
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

            # Auto-refresh while scanning
            if "setup_autorefresh_started_at" not in st.session_state:
                st.session_state.setup_autorefresh_started_at = time.time()
            
            elapsed = time.time() - st.session_state.setup_autorefresh_started_at
            remaining = max(0, 300 - int(elapsed))
            
            if elapsed < 300:
                # Section B: Use DEFAULT_AUTOREFRESH_MS (30s instead of 15s)
                refresh_sec = DEFAULT_AUTOREFRESH_MS // 1000
                st.caption(f"Auto-refresh in {refresh_sec}s ({remaining}s until timeout)...")
                
                # Streamlit-native refresh (no full-page reload)
                schedule_autorefresh(DEFAULT_AUTOREFRESH_MS)
                
                if st.button("🔄 Refresh Now", key="setup_manual_refresh"):
                    st.rerun()
            else:
                st.info("Auto-refresh stopped. Click below to check for sensors.")
                if st.button("🔄 Refresh Page", key="setup_manual_timeout"):
                    st.rerun()       
        
        else:
            st.session_state.pop("setup_autorefresh_started_at", None)
            st.session_state.pop("setup_scan_refresh", None)
            st.info("Start monitoring to detect sensors.")
    else:
        st.session_state.pop("setup_autorefresh_started_at", None)
        st.session_state.pop("setup_scan_refresh", None)
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
            "**Select the sensors you want to configure and monitor:**"
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
                invalidate_app_config_cache()
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

    # Decide which sensors to plot
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
                    tmp = detected_df.set_index("sensor_id")["name"].to_dict() if not detected_df.empty else {}
                    label_map = tmp

                df_h["sensor_label"] = df_h["sensor_id"].map(label_map).fillna(df_h["sensor_id"])

                # Battery chart
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

                # Signal Strength chart
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
                        default_max = sensor_cfg.max_temp_c * 9/5 + 32 if sensor_cfg.max_temp_c is not None else 100.0
                        max_temp = st.number_input(
                            "Max Temperature (°F)",
                            value=float(default_max),
                            key=f"max_{sensor_id}_{cfg.units.upper()}"
                        )
                        sensor_cfg.max_temp_c = (max_temp - 32) * 5/9
                    else:
                        max_temp = st.number_input(
                            "Max Temperature (°C)",
                            value=float(sensor_cfg.max_temp_c) if sensor_cfg.max_temp_c is not None else 40.0,
                            key=f"max_{sensor_id}_{cfg.units.upper()}"
                        )
                        sensor_cfg.max_temp_c = max_temp

                # Update sensor config
                sensor_cfg.name = new_name

                col_save, col_remove = st.columns(2)
                with col_save:
                    if st.button("💾 Save", key=f"save_{sensor_id}"):
                        save_config(cfg)
                        invalidate_app_config_cache()
                        st.success("Saved!")
                        time.sleep(0.5)
                        st.rerun()
                
                with col_remove:
                    if st.button("🗑️ Remove", key=f"remove_{sensor_id}"):
                        del cfg.sensors[sensor_id]
                        save_config(cfg)
                        invalidate_app_config_cache()
                        st.success("Removed!")
                        time.sleep(0.5)
                        st.rerun()
    else:
        st.info("No sensors configured yet. Use the form above to add detected sensors.")

# ====================
# TAB 3: SETTINGS
# ====================
with tab3:
    st.header("Settings")
    
    # Temperature units
    st.subheader("Display Preferences")
    units = st.radio(
        "Temperature Units",
        ["Fahrenheit (°F)", "Celsius (°C)"],
        index=0 if cfg.units.upper() == "F" else 1
    )
    cfg.units = "F" if "Fahrenheit" in units else "C"
    
    st.divider()
    
    # Location settings
    st.subheader("Location")
    st.caption("Used for weather data and timezone.")
    
    if cfg.weather:
        st.success(f"📍 Current location: {cfg.weather.label}")
        
        # Timezone selector
        current_tz = cfg.weather.timezone if cfg.weather.timezone else "America/New_York"
        tz_options = [tz[0] for tz in US_TIMEZONES]
        tz_labels = [tz[1] for tz in US_TIMEZONES]
        
        current_idx = 0
        for i, tz in enumerate(tz_options):
            if tz == current_tz:
                current_idx = i
                break
        
        selected_tz = st.selectbox(
            "Timezone",
            options=tz_options,
            format_func=lambda x: dict(US_TIMEZONES).get(x, x),
            index=current_idx
        )
        cfg.weather.timezone = selected_tz
    
    new_zip = st.text_input("Set Location (US ZIP code)", placeholder="01234")
    if st.button("Update Location") and new_zip:
        with st.spinner("Looking up location..."):
            result = geocode_zip(new_zip)
        
        if result:
            cfg.weather = WeatherConfig(
                zipcode=new_zip,
                latitude=result['lat'],
                longitude=result['lon'],
                label=result['label'],
                timezone=result.get('timezone', "America/New_York")
            )
            save_config(cfg)
            invalidate_app_config_cache()
            st.success(f"Location set to: {result['label']}")
            st.rerun()
        else:
            st.error("Could not find that ZIP code. Please try again.")
    
    st.divider()
    
    # Email notifications
    st.subheader("Email Notifications")
    updated_email = render_email_settings(cfg.email)
    if updated_email is not None:
        cfg.email = updated_email
        save_config(cfg)
        invalidate_app_config_cache()
    
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
    
    status = get_cached_monitoring_status()
    
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
        invalidate_app_config_cache()
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
        get_system_health.clear()
        st.rerun()

# ====================
# TAB 4: SYSTEM HEALTH
# ====================

with tab4:
    st.markdown("### 🖥️ System Health")

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


    health_range = st.radio(
        "Time Range",
        ["5 min", "1 hour", "24 hours"],
        index=1,
        horizontal=True,
        key="health_time_range",
    )
    seconds_map = {"5 min": 300, "1 hour": 3600, "24 hours": 86400}
    seconds = seconds_map[health_range]

    latest, timeseries = _get_health_data(seconds=seconds, max_points=300, db_path_str=str(get_db_path()))

    if latest is None:
        st.warning("⏳ No health data yet. The health sampler starts collecting when the monitor service runs.")
        st.info("Health data is collected every 5 seconds and retained for 24 hours.")
    else:
        col1, col2, col3, col4 = st.columns(4)

        mem_pct = (latest["mem_used_mb"] / latest["mem_total_mb"] * 100) if latest["mem_total_mb"] else 0
        mem_status = "🟢" if mem_pct < 70 else "🟡" if mem_pct < 85 else "🔴"

        swap_pct = (latest["swap_used_mb"] / latest["swap_total_mb"] * 100) if latest["swap_total_mb"] else 0
        swap_status = "🟢" if swap_pct < 30 else "🟡" if swap_pct < 70 else "🔴"

        with col1:
            st.metric(f"{mem_status} Memory", f"{latest['mem_used_mb']:.0f} MB",
                      f"{mem_pct:.0f}% of {latest['mem_total_mb']:.0f} MB")
        with col2:
            st.metric(f"{swap_status} Swap", f"{latest['swap_used_mb']:.0f} MB",
                      f"{swap_pct:.0f}% of {latest['swap_total_mb']:.0f} MB")
        with col3:
            load_status = "🟢" if latest["load_1m"] < 1.0 else "🟡" if latest["load_1m"] < 2.0 else "🔴"
            st.metric(f"{load_status} CPU Load", f"{latest['load_1m']:.2f}",
                      f"5m: {latest['load_5m']:.2f} | 15m: {latest['load_15m']:.2f}")
        with col4:
            total_proc_mb = (latest["monitor_rss_mb"] or 0) + (latest["dashboard_rss_mb"] or 0)
            st.metric("🔧 Process RSS", f"{total_proc_mb:.0f} MB",
                      f"Mon: {latest['monitor_rss_mb']:.0f} | Dash: {latest['dashboard_rss_mb']:.0f}")

        if timeseries:
            df = pd.DataFrame(timeseries)
            # health_sampler buckets are "ts_bucket" in your current implementation
            ts_col = "ts_bucket" if "ts_bucket" in df.columns else "ts"
            df["datetime"] = pd.to_datetime(df[ts_col], unit="s")

            st.markdown("---")
            st.markdown("#### Memory Usage")
            st.area_chart(df.set_index("datetime")[["mem_used_mb"]], use_container_width=True, height=200)

            if latest["swap_total_mb"] > 0:
                st.markdown("#### Swap Usage")
                st.area_chart(df.set_index("datetime")[["swap_used_mb"]], use_container_width=True, height=150)

            st.markdown("#### CPU Load Average")
            st.line_chart(df.set_index("datetime")[["load_1m"]], use_container_width=True, height=200)

            if ("monitor_rss_mb" in df.columns) or ("dashboard_rss_mb" in df.columns):
                cols = [c for c in ["monitor_rss_mb", "dashboard_rss_mb"] if c in df.columns]
                if cols:
                    st.markdown("#### Process Memory (RSS)")
                    st.area_chart(df.set_index("datetime")[cols], use_container_width=True, height=150)

        st.markdown("---")
        st.caption(
            f"📊 Data points: {len(timeseries)} | Sample interval: 5s | Retention: 24h | "
            f"Last update: {datetime.fromtimestamp(latest['ts']).strftime('%H:%M:%S')}"
        )

# ---------------------------------------------------------------------
# Global autorefresh (single timer per rerun)
# ---------------------------------------------------------------------
_interval = st.session_state.get("_autorefresh_interval_ms")
if _interval:
    # limit=1 ensures we don't accumulate multiple JS timers
    st_autorefresh(interval=_interval, limit=1, key="global_autorefresh")

# ---------------------------------------------------------------------
# Section B: End-of-render cleanup
# Runs gc.collect() to free unreferenced objects and log memory state
# ---------------------------------------------------------------------
log_mem("script_end_before_cleanup")
cleanup_render()  # Always runs gc.collect(), logs only if WATCHDOG_MEM_PROBES=1
