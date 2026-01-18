#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

SERVICE_DIR="/etc/systemd/system"

echo "Removing Watchdog systemd services..."

systemctl stop watchdog-dashboard || true
systemctl stop watchdog-monitor || true

systemctl disable watchdog-dashboard || true
systemctl disable watchdog-monitor || true

rm -f \
  "$SERVICE_DIR/watchdog-monitor.service" \
  "$SERVICE_DIR/watchdog-dashboard.service"

systemctl daemon-reload

echo "🗑️  Watchdog services removed"
