# Watchdog Alert System Fix - Implementation Summary

## The Problem

The original Watchdog implementation had a **critical architectural flaw**: alerts and notifications only worked when the Streamlit dashboard was open in a browser.

```
BEFORE (Broken):
┌──────────────────┐          ┌──────────────────┐
│  ble_watchdog.py │────────▶ │     SQLite       │
│     (daemon)     │          │   (readings)     │
└──────────────────┘          └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │      app.py      │ ◀── ALERTS ONLY HERE
                              │   (dashboard)    │     (browser must be open)
                              └──────────────────┘
```

**Result:** If a user configured temperature alerts, tested them (with dashboard open), confirmed they worked, then closed their browser — **they would never receive another alert**.

For a monitoring product targeting dart frog breeders and vivarium keepers, this could mean dead animals.

---

## The Solution

Move alert processing into the daemon so it runs 24/7, regardless of dashboard state.

```
AFTER (Fixed):
┌──────────────────┐          ┌──────────────────┐
│  ble_watchdog.py │────────▶ │     SQLite       │
│     (daemon)     │          │  • readings      │
│                  │          │  • alarm_state   │ ◀── Persistent
│  + AlertEngine   │────────▶ │  • heartbeat     │
│    (background)  │          └────────┬─────────┘
└────────┬─────────┘                   │
         │                    ┌────────▼─────────┐
         │                    │      app.py      │ ◀── READ-ONLY
         ▼                    │   (dashboard)    │     (just displays)
  Email Notifications         └──────────────────┘
  (24/7, even when 
   dashboard closed)
```

---

## Files Changed/Created

### New Files

| File | Purpose |
|------|---------|
| `alert_engine.py` | Background alert processing engine. Runs as a thread in the daemon. Checks alerts every 60s, sends notifications, manages persistent alarm state. |
| `health_check.py` | System health monitoring. Provides health status for desktop app and external monitoring. Includes optional HTTP endpoint on port 8502. |
| `watchdog-health.service` | Systemd service for health check HTTP endpoint (optional). |

### Modified Files

| File | Changes |
|------|---------|
| `ble_watchdog.py` | Added AlertEngine startup/shutdown. Alert processing now happens here, not in dashboard. |
| `alarm_ui.py` | Now reads from SQLite-backed alarm store instead of in-memory state. Dashboard is read-only for alarm detection. |
| `discovery.py` (desktop) | Added health check support. Desktop app can now show "monitoring offline" warnings. |
| `watchdog_app.py` (desktop) | Shows health status during discovery and in UI. |

---

## New Database Tables

The alert engine adds three new tables to the existing SQLite database:

```sql
-- Persistent alarm state (survives daemon restarts)
CREATE TABLE alarm_state (
    alarm_key TEXT PRIMARY KEY,      -- "sensor_id:alert_type"
    sensor_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,        -- "temp_high", "temp_low", "low_battery", "offline"
    sensor_name TEXT,
    severity INTEGER NOT NULL,       -- 0=none, 1=warning, 2=critical
    message TEXT,
    first_seen_ts REAL,
    last_updated_ts REAL,
    last_notified_ts REAL,
    cleared_ts REAL,
    silenced_until REAL,             -- float('inf') for "until resolved"
    silenced_severity INTEGER
);

-- System heartbeat (for health monitoring)
CREATE TABLE system_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_heartbeat_ts REAL NOT NULL,
    last_reading_ts REAL,
    sensors_active INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running'
);

-- Notification audit log
CREATE TABLE alert_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_key TEXT NOT NULL,
    notification_type TEXT NOT NULL,  -- 'new', 'escalated', 'cleared'
    sent_ts REAL NOT NULL,
    success INTEGER NOT NULL,
    error_message TEXT
);
```

---

## How It Works

### Alert Engine Lifecycle

1. **Startup**: When `ble_watchdog.py` starts, it creates an `AlertEngine` instance and starts it as a daemon thread.

2. **Check Cycle** (every 60 seconds):
   - Query latest readings from SQLite
   - Check each reading against configured thresholds
   - Update alarm state in database
   - Send notifications for new/escalated alarms
   - Clear alarms that are no longer in violation

3. **Heartbeat** (every 5 minutes):
   - Write timestamp to `system_heartbeat` table
   - Record number of active sensors
   - Desktop app and health check use this to detect "monitoring offline"

4. **Shutdown**: Graceful stop, finish pending notifications, close connections.

### Notification Flow

```
Sensor reading exceeds threshold
         │
         ▼
    AlertEngine detects condition
         │
         ▼
    Check if alarm is new or escalated
         │
    ┌────┴────┐
    │         │
    ▼         ▼
  NEW     ESCALATED
    │         │
    └────┬────┘
         │
         ▼
    Check rate limit (default 30 min)
         │
         ▼
    Check if silenced
         │
         ▼
    Send email notification
         │
         ▼
    Log to alert_notifications table
         │
         ▼
    Update last_notified_ts
```

### Dashboard Integration

The dashboard (`app.py`) now:

1. **Reads** alarm state from SQLite (via `PersistentAlarmStore`)
2. **Displays** active alarms with banner UI
3. **Writes** silence/unsilence actions to SQLite
4. **Does NOT** process alerts (that's the daemon's job now)

The old `process_alerts_and_notify()` function in `alarm_ui.py` is now a no-op for backward compatibility.

---

## Deployment

### 1. Copy New Files to Pi

```bash
# From your development machine
scp alert_engine.py health_check.py pi@watchdog.local:~/Watchdog/monitor/
```

### 2. Replace Modified Files

```bash
# Backup originals first
ssh pi@watchdog.local "cd ~/Watchdog/monitor && cp ble_watchdog.py ble_watchdog.py.bak"
ssh pi@watchdog.local "cd ~/Watchdog/monitor && cp alarm_ui.py alarm_ui.py.bak"

# Copy new versions
scp ble_watchdog_new.py pi@watchdog.local:~/Watchdog/monitor/ble_watchdog.py
scp alarm_ui_new.py pi@watchdog.local:~/Watchdog/monitor/alarm_ui.py
```

### 3. Restart Services

```bash
ssh pi@watchdog.local "sudo systemctl restart watchdog-monitor watchdog-dashboard"
```

### 4. (Optional) Install Health Check Service

```bash
# Copy service file
scp watchdog-health.service pi@watchdog.local:~/Watchdog/monitor/deploy/

# Install and start
ssh pi@watchdog.local "cd ~/Watchdog/monitor/deploy && sudo ./install-services.sh"
```

### 5. Verify

```bash
# Check alert engine is running
ssh pi@watchdog.local "journalctl -u watchdog-monitor -n 50 | grep -i alert"

# Should see:
# "Starting background alert engine..."
# "Alert engine started (checking every 60s)"

# Check heartbeat
ssh pi@watchdog.local "sqlite3 ~/Watchdog/monitor/data/data.sqlite3 'SELECT * FROM system_heartbeat'"
```

---

## Desktop App Updates

The desktop app now:

1. Checks health status during discovery
2. Shows warning if monitoring is offline
3. Displays alarm count in UI

To deploy:

```bash
# Replace discovery.py and watchdog_app.py in desktop folder
cp discovery_new.py desktop/discovery.py
cp watchdog_app_new.py desktop/watchdog_app.py

# Rebuild desktop app
cd desktop
./build_mac.sh    # or build_linux.sh / build_windows.bat
```

---

## Testing

### Test 1: Alerts with Dashboard Closed

```bash
# 1. Configure email notifications in dashboard
# 2. Configure a temperature threshold (set it low so it triggers)
# 3. Close the dashboard browser tab
# 4. Wait 60-120 seconds
# 5. Check email - you should receive an alert
```

### Test 2: System Health Check

```bash
# On Pi
python3 ~/Watchdog/monitor/health_check.py

# Should show:
# ✅ System Status: HEALTHY
#    System healthy (X sensors active)
```

### Test 3: Desktop App Health Display

```bash
# Launch desktop app
# It should show health status during discovery
# If monitoring is stopped, it should show warning
```

### Test 4: Alarm Persistence

```bash
# 1. Trigger an alarm
# 2. Restart the monitor service
# 3. The alarm should still be active (survived restart)

sudo systemctl restart watchdog-monitor
sqlite3 ~/Watchdog/monitor/data/data.sqlite3 "SELECT * FROM alarm_state WHERE severity > 0"
```

---

## Configuration

The alert engine uses the same configuration as before (`~/.config/watchdog/config.json`), specifically:

```json
{
  "alerts": {
    "enabled": true,
    "low_battery_threshold": 20,
    "sensor_offline_minutes": 15,
    "temp_alerts_enabled": true
  },
  "email": {
    "enabled": true,
    "recipient": "you@example.com",
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "watchdog@gmail.com",
    "sender_password": "your-app-password",
    "rate_limit_minutes": 30,
    "notify_on_clear": true
  }
}
```

Changes to configuration are picked up automatically on the next check cycle (within 60 seconds).

---

## Key Behavioral Changes

| Behavior | Before | After |
|----------|--------|-------|
| Alerts when dashboard closed | ❌ No | ✅ Yes |
| Alarm state survives restart | ❌ No | ✅ Yes |
| Notification audit log | ❌ No | ✅ Yes |
| Desktop app health check | ❌ No | ✅ Yes |
| Rate limiting | In-memory | Persistent |
| Silence duration | In-memory | Persistent |

---

## Future Enhancements

Now that the architecture is correct, these become easy additions:

1. **Push notifications** (Pushover, ntfy.sh) - just add to `_send_alarm_notification()`
2. **Daily digest email** - add to heartbeat cycle
3. **Webhook integrations** - add to notification flow
4. **Remote monitoring API** - health check endpoint already exists

---

## Summary

The fix addresses the critical ship-blocker: **alerts now work 24/7, regardless of dashboard state**.

- Alert processing moved to daemon (`ble_watchdog.py` → `AlertEngine`)
- Alarm state persisted to SQLite (survives restarts)
- Dashboard becomes read-only for alarms (just displays, doesn't process)
- Health check added for desktop app and external monitoring
- Notification audit log for debugging

Total new/modified code: ~800 lines
Estimated integration time: 2-4 hours
