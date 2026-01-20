#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi


# Absolute path to the monitor directory (parent of this deploy/ folder)
WATCHDOG_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Install-time user/group (assumes you run this via sudo as the target user)
WATCHDOG_USER="${SUDO_USER:-$(id -un)}"
WATCHDOG_GROUP="$(id -gn "$WATCHDOG_USER")"


SERVICE_DIR="/etc/systemd/system"

echo "Installing Watchdog systemd services..."

# Bake correct paths + user/group into systemd units
sed -e "s|__WATCHDOG_DIR__|$WATCHDOG_DIR|g" \
    -e "s|__WATCHDOG_USER__|$WATCHDOG_USER|g" \
    -e "s|__WATCHDOG_GROUP__|$WATCHDOG_GROUP|g" \
    deploy/watchdog-monitor.service > "$SERVICE_DIR/watchdog-monitor.service"

sed -e "s|__WATCHDOG_DIR__|$WATCHDOG_DIR|g" \
    -e "s|__WATCHDOG_USER__|$WATCHDOG_USER|g" \
    -e "s|__WATCHDOG_GROUP__|$WATCHDOG_GROUP|g" \
    deploy/watchdog-dashboard.service > "$SERVICE_DIR/watchdog-dashboard.service"


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
