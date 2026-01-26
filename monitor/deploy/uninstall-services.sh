#!/bin/bash
set -euo pipefail


if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

SERVICE_DIR="/etc/systemd/system"

echo "Removing Watchdog systemd services..."

systemctl stop watchdog-dashboard.service watchdog-monitor.service watchdog-health.service || true

systemctl disable watchdog-dashboard.service watchdog-monitor.service watchdog-health.service || true


rm -f \
  "$SERVICE_DIR/watchdog-monitor.service" \
  "$SERVICE_DIR/watchdog-dashboard.service" \
  "$SERVICE_DIR/watchdog-health.service"

systemctl disable --now watchdog-bt-unblock.service || true
rm -f "$SERVICE_DIR/watchdog-bt-unblock.service" || true

systemctl daemon-reload

echo "🗑️  Watchdog services removed"
