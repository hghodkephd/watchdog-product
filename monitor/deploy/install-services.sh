#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: This script must be run as root (use sudo)"
  exit 1
fi

if [[ "$1" == "--dry-run" ]]; then
  echo "[DRY RUN] Would install services with:"
  echo "  WATCHDOG_DIR=$WATCHDOG_DIR"
  echo "  WATCHDOG_USER=$WATCHDOG_USER"
  echo "  WATCHDOG_GROUP=$WATCHDOG_GROUP"
  exit 0
fi

# Absolute path to the monitor directory (parent of this deploy/ folder)
WATCHDOG_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Install-time user/group (assumes you run this via sudo as the target user)
WATCHDOG_USER="${SUDO_USER:-$(id -un)}"
WATCHDOG_GROUP="$(id -gn "$WATCHDOG_USER")"


SERVICE_DIR="/etc/systemd/system"

echo "[Watchdog] Ensuring Bluetooth is installed, enabled, and unblocked..."

# Ensure tools exist (safe even if already installed)
apt-get update -y
apt-get install -y bluetooth bluez rfkill

# Enable bluetooth daemon now + on boot
systemctl enable bluetooth --now || true

# Unblock at kernel level (softblock)
rfkill unblock bluetooth || true
rfkill unblock all || true

# Restart to apply cleanly
systemctl restart bluetooth || true
sleep 2

echo "[Watchdog] rfkill status:"
rfkill list || true


echo "[Watchdog] Installing watchdog-bt-unblock.service..."

cat >/etc/systemd/system/watchdog-bt-unblock.service <<'EOF'
[Unit]
Description=Watchdog: ensure Bluetooth is unblocked at boot
After=bluetooth.service
Wants=bluetooth.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/rfkill unblock bluetooth
ExecStart=/usr/sbin/rfkill unblock all
ExecStart=/bin/systemctl restart bluetooth
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable watchdog-bt-unblock.service
systemctl start watchdog-bt-unblock.service || true

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
