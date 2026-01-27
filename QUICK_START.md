# Watchdog Quick Start (v1.0)

This guide gets you from **power-on → reliable 24/7 monitoring with alerts** in a few minutes.

Watchdog runs locally on your Raspberry Pi. Once started, it continues monitoring even if the dashboard is closed.

---

## What You Need
- A **Raspberry Pi running Watchdog**
  - Pre-flashed SD card image **or**
  - Installed via `deploy.sh`
- Your computer on the **same Wi-Fi / LAN** as the Pi
- Optional: one or more **Bluetooth (BLE) sensors**
  - e.g. Govee temperature / humidity sensors

---

## 1) Power On + Find Watchdog

1. Power on the Raspberry Pi
2. Wait **~60–120 seconds** for startup
3. On your computer, open the **Watchdog Desktop App**

**If discovery succeeds:**  
You’ll be taken directly to the dashboard.

**If discovery fails:**  
Open a browser and try one of the following:
- `http://watchdog.local:8501`
- `http://<PI_IP>:8501`

> Tip: You can usually find the Pi’s IP address in your router’s “connected devices” list.

---

## 2) Start Monitoring

1. Open the **Monitoring** tab
2. Click **Start**

What you should see:
- Status changes to **Scanning…**
- Sensor readings begin appearing automatically
- Any alerts will appear as banners at the top

> Monitoring runs in the background once started.  
> You do **not** need to keep the dashboard open.

---

## 3) Add Sensors (First Time Only)

1. Go to **Setup**
2. Click **Detect Sensors**
3. Leave detection running for **30–60 seconds**
4. Select the sensors you want Watchdog to track
5. Click **Save**

If a sensor doesn’t appear:
- Move it closer to the Pi (BLE range is limited through walls)
- Confirm the sensor has battery and is actively reporting
- Make sure monitoring is running

---

## 4) Configure Email Alerts (Optional but Recommended)

1. Go to **Settings → Email Notifications**
2. Enter sender and recipient email addresses
3. Click **Test Email**
4. Configure temperature / humidity thresholds per sensor

### Common Email Setup Notes
- **Gmail** requires an **App Password** (not your normal password)
- Some providers block SMTP by default — check provider settings
- Watchdog includes a safety circuit breaker if email repeatedly fails

---

## 5) Confirm System Health

You can verify everything is running correctly in two ways:

- **Dashboard:** System Health section
- **Health endpoint (optional):**