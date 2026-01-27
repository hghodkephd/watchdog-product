#!/usr/bin/env bash
set -euo pipefail

echo "⚠️  Watchdog n-1 Factory Reset"
echo "This will REMOVE all Watchdog code, data, services, and state."
echo
read -p "Type RESET to continue: " confirm
[[ "$confirm" == "RESET" ]] || { echo "Aborted."; exit 1; }

USER_HOME="${HOME}"
WATCHDOG_DIR="${USER_HOME}/Watchdog"

echo "Stopping Watchdog services..."
sudo systemctl stop watchdog-monitor.service watchdog-dashboard.service || true
sudo systemctl disable watchdog-monitor.service watchdog-dashboard.service || true

echo "Removing systemd units..."
sudo rm -f /etc/systemd/system/watchdog-monitor.service
sudo rm -f /etc/systemd/system/watchdog-dashboard.service
sudo systemctl daemon-reload

echo "Killing residual processes..."
pkill -f ble_watchdog || true
pkill -f streamlit || true

if [[ -d "${WATCHDOG_DIR}" ]]; then
  echo "Removing ${WATCHDOG_DIR}"
  rm -rf "${WATCHDOG_DIR}"
else
  echo "No Watchdog directory found."
fi

echo "Restarting Bluetooth..."
sudo systemctl restart bluetooth || true

echo
echo "✅ Watchdog n-1 baseline restored."
echo "The Watchdog repository has been removed."
echo "Reboot recommended."