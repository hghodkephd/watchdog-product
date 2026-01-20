#!/bin/bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
  echo "[DRY RUN] Would install services with:"
  echo "  WATCHDOG_DIR=<auto-detected>"
  echo "  WATCHDOG_USER=<sudo user or current user>"
  echo "  WATCHDOG_GROUP=<primary group of user>"
  exit 0
fi

# Absolute path to the monitor directory (parent of this deploy/ folder)
WATCHDOG_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Install-time user/group (assumes you run this via sudo as the target user)
WATCHDOG_USER="${SUDO_USER:-$(id -un)}"
WATCHDOG_GROUP="$(id -gn "$WATCHDOG_USER")"

SERVICE_DIR="/etc/systemd/system"

echo "[Watchdog] Using WATCHDOG_DIR=$WATCHDOG_DIR"
echo "[Watchdog] Using WATCHDOG_USER=$WATCHDOG_USER"
echo "[Watchdog] Using WATCHDOG_GROUP=$WATCHDOG_GROUP"
echo

echo "[Watchdog] Ensuring Bluetooth tools exist..."
apt-get update -y
apt-get install -y bluetooth bluez rfkill

echo "[Watchdog] Installing systemd units..."

# Bake correct paths + user/group into systemd units (monitor + dashboard)
sed -e "s|_WATCHDOG_DIR_|$WATCHDOG_DIR|g" \
    -e "s|_WATCHDOG_USER_|$WATCHDOG_USER|g" \
    -e "s|_WATCHDOG_GROUP_|$WATCHDOG_GROUP|g" \
    "$WATCHDOG_DIR/deploy/watchdog-monitor.service" \
  > "$SERVICE_DIR/watchdog-monitor.service"

sed -e "s|_WATCHDOG_DIR_|$WATCHDOG_DIR|g" \
    -e "s|_WATCHDOG_USER_|$WATCHDOG_USER|g" \
    -e "s|_WATCHDOG_GROUP_|$WATCHDOG_GROUP|g" \
    "$WATCHDOG_DIR/deploy/watchdog-dashboard.service" \
  > "$SERVICE_DIR/watchdog-dashboard.service"

chmod 644 \
  "$SERVICE_DIR/watchdog-monitor.service" \
  "$SERVICE_DIR/watchdog-dashboard.service"

# ---- Bluetooth unblock safety net ----
# Ensure helper script is executable (runs from repo path)
chmod +x "$WATCHDOG_DIR/deploy/watchdog-bt-unblock.sh" || true

# Install bt-unblock unit with WATCHDOG_DIR baked in
sed -e "s|_WATCHDOG_DIR_|$WATCHDOG_DIR|g" \
    "$WATCHDOG_DIR/deploy/watchdog-bt-unblock.service" \
  > "$SERVICE_DIR/watchdog-bt-unblock.service"

chmod 644 "$SERVICE_DIR/watchdog-bt-unblock.service"

# Reload systemd units now that all unit files exist
systemctl daemon-reload

# Ensure bluetooth is enabled now + on boot (safe if already enabled)
systemctl enable bluetooth --now || true

# Enable + start the unblock safety net
systemctl enable watchdog-bt-unblock.service
systemctl start watchdog-bt-unblock.service || true

# Enable + start Watchdog services
systemctl enable watchdog-monitor watchdog-dashboard
systemctl start watchdog-monitor watchdog-dashboard

echo
echo "[Watchdog] Status:"
systemctl status watchdog-bt-unblock --no-pager -l || true
systemctl status watchdog-monitor --no-pager -l || true
systemctl status watchdog-dashboard --no-pager -l || true

echo
echo "✅ Watchdog services installed successfully"
echo "📊 Dashboard available at: http://<pi-ip>:8501"
