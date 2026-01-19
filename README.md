# Watchdog Product

This repository contains the full Watchdog product:

- **monitor/** — Raspberry Pi monitoring system (BLE scanning + SQLite + Streamlit dashboard)
- **desktop/** — desktop launcher app for discovering/launching the dashboard

## Repository layout
watchdog-product/
├── monitor/     # Raspberry Pi monitor + dashboard
├── desktop/     # Desktop launcher
├── docs/        # (optional) unified docs
└── releases/    # (optional) packaged builds

## Quick start (local/developer use)

### Monitor (Pi / local dev)
```bash
cd monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run BLE monitor (Pi required for BLE)
python ble_watchdog.py

# Run dashboard
streamlit run app.py

cd desktop
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python watchdog_app.py
