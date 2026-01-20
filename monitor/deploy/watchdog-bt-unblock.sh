#!/usr/bin/env bash
set -euo pipefail

# Unblock bluetooth if it was soft-blocked
rfkill unblock bluetooth || true

# Restart bluetooth service to apply cleanly
systemctl restart bluetooth || true

# Small delay can help the stack settle on some Pi images
sleep 1
