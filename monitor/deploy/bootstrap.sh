#!/usr/bin/env bash
set -euo pipefail

echo "🚀 Watchdog bootstrap starting..."
echo "This will prepare the system to run Watchdog."
echo

# -------------------------
# Sanity checks
# -------------------------
if [[ ! -f "monitor/requirements.txt" ]]; then
  echo "❌ ERROR: Must be run from the Watchdog repo root."
  exit 1
fi

# -------------------------
# OS-level dependencies
# -------------------------
echo "📦 Installing system dependencies..."

sudo apt update
sudo apt install -y \
  python3-venv \
  python3-pip \
  bluetooth \
  bluez \
  libbluetooth-dev \
  git

# -------------------------
# Virtual environment
# -------------------------
VENV_DIR="venv"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "🐍 Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
else
  echo "🐍 Virtual environment already exists."
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# -------------------------
# Python dependencies
# -------------------------
echo "⬆️  Upgrading pip..."
python -m pip install --upgrade pip

echo "📦 Installing Python requirements..."
python -m pip install -r monitor/requirements.txt

# -------------------------
# Summary
# -------------------------
echo
echo "✅ Bootstrap complete."
echo
echo "Next steps:"
echo "  1) sudo bash monitor/deploy/install-services.sh"
echo "  2) Reboot (recommended)"
echo "  3) Open the Watchdog dashboard"