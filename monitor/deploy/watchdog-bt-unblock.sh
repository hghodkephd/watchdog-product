#!/usr/bin/env bash
set -euo pipefail

# Unblock bluetooth if it was soft-blocked
rfkill unblock bluetooth || true

# Restart bluetooth service to apply cleanly
systemctl restart bluetooth || true

# Small delay for stack to settle
sleep 2

# Power on the adapter (this was missing!)
bluetoothctl power on || true

# Verify
sleep 1
bluetoothctl show | grep -E "Powered|PowerState" || true