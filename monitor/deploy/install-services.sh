#!/bin/bash
set -euo pipefail


# --------------------------------------------
# Watchdog - install systemd services
# Run: sudo ./install-services.sh
# Optional: sudo ./install-services.sh --dry-run
# --------------------------------------------

if [ "${EUID}" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

# Absolute path to the monitor directory (parent of this deploy/ folder)
WATCHDOG_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Install-time user/group (assumes you ran via sudo as the target user)
WATCHDOG_USER="${SUDO_USER:-$(id -un)}"
WATCHDOG_GROUP="$(id -gn "$WATCHDOG_USER")"

# --- Prerequisite checks ------------------------------------

# Check python3 exists
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.9+ and rerun."
  exit 1
fi

# Check Python version >= 3.9
if ! python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"; then
  echo "ERROR: Python 3.9+ required. Found: $(python3 --version 2>&1)"
  exit 1
fi

# Check venv exists (skip for dry run)
if [ "${DRY_RUN:-0}" -ne 1 ] && [ ! -f "$WATCHDOG_DIR/venv/bin/activate" ]; then
  echo "ERROR: Virtual environment not found at: $WATCHDOG_DIR/venv/"
  echo "Run setup.sh first, then rerun install-services.sh."
  exit 1
fi

# -----------------------------------------------------------

SERVICE_DIR="/etc/systemd/system"

echo "[Watchdog] Using WATCHDOG_DIR=$WATCHDOG_DIR"
echo "[Watchdog] Using WATCHDOG_USER=$WATCHDOG_USER"
echo "[Watchdog] Using WATCHDOG_GROUP=$WATCHDOG_GROUP"
echo

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[DRY RUN] Would install services into: $SERVICE_DIR"
  echo "[DRY RUN] Would bake templates from: $WATCHDOG_DIR/deploy"
  exit 0
fi

echo "[Watchdog] Ensuring Bluetooth tools exist..."
apt-get update -y
apt-get install -y bluetooth bluez rfkill

echo
echo "[Watchdog] Ensuring bluetooth daemon is enabled..."
systemctl enable bluetooth --now || true

echo
echo "[Watchdog] Ensuring bt-unblock helper script is executable..."
chmod +x "$WATCHDOG_DIR/deploy/watchdog-bt-unblock.sh" || true

echo
echo "[Watchdog] Installing systemd units..."

# Bake templates -> /etc/systemd/system/*.service
# IMPORTANT: templates MUST NOT contain stray trailing underscores.
sed -e "s|__WATCHDOG_DIR__|$WATCHDOG_DIR|g" \
    -e "s|__WATCHDOG_USER__|$WATCHDOG_USER|g" \
    -e "s|__WATCHDOG_GROUP__|$WATCHDOG_GROUP|g" \
    "$WATCHDOG_DIR/deploy/watchdog-monitor.service" \
    > "$SERVICE_DIR/watchdog-monitor.service"

sed -e "s|__WATCHDOG_DIR__|$WATCHDOG_DIR|g" \
    -e "s|__WATCHDOG_USER__|$WATCHDOG_USER|g" \
    -e "s|__WATCHDOG_GROUP__|$WATCHDOG_GROUP|g" \
    "$WATCHDOG_DIR/deploy/watchdog-dashboard.service" \
    > "$SERVICE_DIR/watchdog-dashboard.service"

sed -e "s|__WATCHDOG_DIR__|$WATCHDOG_DIR|g" \
    -e "s|__WATCHDOG_USER__|$WATCHDOG_USER|g" \
    -e "s|__WATCHDOG_GROUP__|$WATCHDOG_GROUP|g" \
    "$WATCHDOG_DIR/deploy/watchdog-health.service" \
    > "$SERVICE_DIR/watchdog-health.service"

sed -e "s|__WATCHDOG_DIR__|$WATCHDOG_DIR|g" \
    "$WATCHDOG_DIR/deploy/watchdog-bt-unblock.service" \
    > "$SERVICE_DIR/watchdog-bt-unblock.service"

chmod 644 \
  "$SERVICE_DIR/watchdog-monitor.service" \
  "$SERVICE_DIR/watchdog-dashboard.service" \
  "$SERVICE_DIR/watchdog-health.service" \
  "$SERVICE_DIR/watchdog-bt-unblock.service"

echo
echo "[Watchdog] Reloading systemd..."
systemctl daemon-reload

echo
echo "[Watchdog] Enabling services..."
systemctl enable watchdog-bt-unblock.service
systemctl enable watchdog-monitor.service watchdog-dashboard.service watchdog-health.service

echo
echo "[Watchdog] Starting services..."
systemctl start watchdog-bt-unblock.service || true
systemctl restart watchdog-monitor.service watchdog-dashboard.service watchdog-health.service

echo
echo "[Watchdog] Status:"
systemctl status watchdog-bt-unblock.service --no-pager -l || true
systemctl status watchdog-monitor.service --no-pager -l || true
systemctl status watchdog-dashboard.service --no-pager -l || true
systemctl status watchdog-health.service --no-pager -l || true

echo
echo "✅ Watchdog services installed."
echo "📊 Dashboard: http://<pi-hostname-or-ip>:8501"
echo "❤️  Health:    http://<pi-hostname-or-ip>:8502/health"
