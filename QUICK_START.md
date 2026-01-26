# Watchdog Quick Start (v1.0)

This is the fastest path from “out of the box” to reliable 24/7 monitoring + email alerts.

## What you need
- A powered **Raspberry Pi running Watchdog** (pre-flashed SD card image or installed via `deploy.sh`)
- Your computer on the **same Wi‑Fi / LAN** as the Pi
- Optional: one or more **Govee BLE sensors** (or supported BLE sensors)

---

## 1) Power on + find Watchdog
1. Power on the Pi and wait ~60–120 seconds.
2. On your computer, open the **Watchdog Desktop App**.

**If discovery works:** you’ll be taken to the dashboard automatically.

**If discovery fails:** open the dashboard in a browser:
- Try: `http://watchdog.local:8501`
- Or: `http://<PI_IP>:8501`

> Tip: You can usually find the Pi’s IP in your router’s “connected devices” list.

---

## 2) Start monitoring
In the dashboard, go to **Monitoring** and click **Start**.

- You should see “Scanning…” while readings begin to populate.
- If you see alarms/banners, they’re actionable—click through or silence as needed.

---

## 3) Add sensors (first time only)
1. Go to **Setup**.
2. Click **Detect Sensors** (leave it running for ~30–60 seconds).
3. Select the sensors you want Watchdog to track and **Save**.

If a sensor doesn’t show up:
- Move it closer to the Pi (BLE range can be short through walls).
- Confirm the sensor has battery and is actively reporting.
- Make sure monitoring is running.

---

## 4) Configure email alerts
1. Go to **Settings → Email Notifications**.
2. Enter sender email + recipient email.
3. Click **Test Email**.
4. Set your **temperature/humidity thresholds** per sensor.

### Common email setup notes
- **Gmail** typically requires an **App Password** (not your normal password).
- Some providers block SMTP by default; check your provider’s “app password” / “SMTP” settings.

---

## 5) Confirm the system is healthy
- Dashboard: **System Health** section
- Health endpoint (optional): `http://<PI_IP>:8502/health`

---

# Troubleshooting (fast)
## “Desktop app can’t find Watchdog”
- Confirm computer and Pi are on the **same Wi‑Fi/LAN**
- Try opening: `http://watchdog.local:8501`
- Or use the Pi’s IP: `http://<PI_IP>:8501`
- Reboot the Pi if needed

## “No sensors detected”
- Ensure monitoring is running
- Move sensors closer
- Wait 30–60 seconds and re-run **Detect Sensors**
- BLE can be affected by distance, walls, metal racks, and water (aquariums)

## “Email test failed” / “No alerts arriving”
- Double-check sender credentials
- For Gmail: use an **App Password**
- Verify your SMTP host/port settings (if configurable)
- Watchdog will surface an alarm if email is repeatedly failing (circuit breaker)

## “Dashboard loads but looks empty”
- Click **Start** on Monitoring
- Add sensors in **Setup**
- Wait for first readings (auto-refresh may be active)

---

## Support info to include when reporting a bug
- Pi model + OS image (or install method)
- Watchdog version
- Desktop OS (macOS/Windows/Linux)
- A screenshot of **System Health** and any alarms
