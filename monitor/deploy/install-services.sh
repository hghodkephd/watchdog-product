#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

WATCHDOG_DIR="/home/pi/watchdog"
SERVICE_DIR="/etc/systemd/system"

echo "Installing Watchdog systemd services..."

mkdir -p "$WATCHDOG_DIR"

cp deploy/watchdog-monitor.service "$SERVICE_DIR/"
cp deploy/watchdog-dashboard.service "$SERVICE_DIR/"

chmod 644 \
  "$SERVICE_DIR/watchdog-monitor.service" \
  "$SERVICE_DIR/watchdog-dashboard.service"

systemctl daemon-reload

systemctl enable watchdog-monitor watchdog-dashboard
systemctl start watchdog-monitor watchdog-dashboard

echo
systemctl status watchdog-monitor --no-pager
systemctl status watchdog-dashboard --no-pager

echo
echo "✅ Watchdog services installed successfully"
echo "📊 Dashboard available at: http://<pi-ip>:8501"
