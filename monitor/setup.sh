#!/bin/bash
# Watchdog Environmental Monitor - Setup Script
# Creates virtual environment and installs dependencies

set -e  # Exit on error

echo "=========================================="
echo "Watchdog Monitor - Setup"
echo "=========================================="
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
echo "✓ Found Python $PYTHON_VERSION"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip > /dev/null 2>&1
echo "✓ pip upgraded"

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -r requirements.txt
echo "✓ Dependencies installed"

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "To activate the environment:"
echo "  source venv/bin/activate"
echo ""
echo "To test the monitoring service:"
echo "  python ble_watchdog.py"
echo ""
echo "To test the dashboard:"
echo "  streamlit run app.py"
echo ""
echo "To deploy as systemd services (full deployment):"
echo "  sudo ./deploy.sh"
echo ""
