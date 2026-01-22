# Watchdog — Raspberry Pi Installation Guide

This document describes a **clean, known-good installation** of Watchdog on a Raspberry Pi,
based on a full reinstall and end-to-end validation of system behavior.

If you follow this guide and see the expected outputs, **your system is working correctly**.

---

## Supported Platform

- Raspberry Pi Zero 2 W / Pi 4
- Raspberry Pi OS (Debian trixie / bookworm)
- Python 3.9+
- systemd

---

## 1. System Preparation

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git sqlite3 bluetooth bluez avahi-daemon
```

---

## 2. Clone Repository

```bash
cd ~
git clone https://github.com/hghodkephd/watchdog-product.git Watchdog
cd Watchdog
```

---

## 3. Python Environment

```bash
python3 -m venv monitor/venv
source monitor/venv/bin/activate
pip install --upgrade pip
pip install -r monitor/requirements.txt
```

---

## 4. Bluetooth Power (CRITICAL)

```bash
sudo bash deploy/watchdog-bt-unblock.sh
bluetoothctl show
```

Expected:
```
Powered: yes
```

---

## 5. Install Services

```bash
cd monitor/deploy
sudo ./install_services.sh
sudo systemctl daemon-reload
sudo systemctl enable watchdog-monitor watchdog-dashboard
```

---

## 6. Start Services

```bash
sudo systemctl start watchdog-monitor
sudo systemctl start watchdog-dashboard
```

---

## 7. systemd Behavior (Important)

Seeing this is **normal**:

```
Active: inactive (dead)
status=0/SUCCESS
```

This indicates a clean exit, not a crash.

---

## 8. Validate Monitor

```bash
journalctl -u watchdog-monitor -n 50 --no-pager
```

Look for:
```
Entering main loop
```

---

## 9. Dashboard

Open:
```
http://<pi-ip>:8501
```

Safari may require refresh once.

---

## 10. Validate Database

```bash
sqlite3 ~/Watchdog/monitor/data/data.sqlite3 ".schema readings"
sqlite3 ~/Watchdog/monitor/data/data.sqlite3 "SELECT COUNT(*) FROM readings;"
```

---

## 11. 5-Minute Health Check

```bash
python - << 'EOF'
import sqlite3, time
from pathlib import Path
db = Path.home() / "Watchdog/monitor/data/data.sqlite3"
conn = sqlite3.connect(db)
count = conn.execute(
    "SELECT COUNT(*) FROM readings WHERE ts > ?",
    (time.time() - 300,)
).fetchone()[0]
print(f"Readings in last 5 minutes: {count}")
EOF
```

---

## Installation Complete

If all steps succeed, Watchdog is correctly installed and operating.
