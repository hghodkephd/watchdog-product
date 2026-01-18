#!/bin/bash
# Automated deployment script for Watchdog Environmental Monitor
# PROPRIETARY - Commercial Product
#
# This script sets up the Watchdog system on Raspberry Pi

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Watchdog Environmental Monitor Setup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if running on Raspberry Pi
if [ ! -f /proc/cpuinfo ] || ! grep -q "Raspberry Pi" /proc/cpuinfo; then
    echo -e "${YELLOW}Warning: This doesn't appear to be a Raspberry Pi${NC}"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Update system
echo -e "${GREEN}[1/8] Updating system packages...${NC}"
sudo apt update
sudo apt upgrade -y

# Install dependencies
echo -e "${GREEN}[2/8] Installing dependencies...${NC}"
sudo apt install -y \
    python3-pip \
    python3-venv \
    git \
    sqlite3 \
    bluez \
    bluez-tools

# Add user to bluetooth group
echo -e "${GREEN}[3/8] Configuring Bluetooth permissions...${NC}"
sudo usermod -a -G bluetooth $USER

# Create virtual environment
echo -e "${GREEN}[4/8] Creating Python virtual environment...${NC}"
if [ ! -d "$HOME/watchdog-env" ]; then
    python3 -m venv $HOME/watchdog-env
fi

# Activate and install packages
echo -e "${GREEN}[5/8] Installing Python packages...${NC}"
source $HOME/watchdog-env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# Create project directories
echo -e "${GREEN}[6/8] Creating project directories...${NC}"
mkdir -p $HOME/watchdog/data
mkdir -p $HOME/watchdog/data/archive
mkdir -p $HOME/.streamlit

# Configure Streamlit
echo -e "${GREEN}[7/8] Configuring Streamlit...${NC}"
cat > $HOME/.streamlit/config.toml << 'TOML_EOF'
[server]
address = "0.0.0.0"
port = 8501
headless = true
fileWatcherType = "none"

[browser]
gatherUsageStats = false

[theme]
primaryColor = "#FF4B4B"
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#262730"
textColor = "#FAFAFA"
font = "sans serif"
TOML_EOF

# Install systemd services
echo -e "${GREEN}[8/8] Installing systemd services...${NC}"
sudo cp watchdog-monitor.service /etc/systemd/system/
sudo cp watchdog-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable watchdog-monitor.service
sudo systemctl enable watchdog-dashboard.service

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Next steps:"
echo "1. Copy your Python files to $HOME/watchdog/"
echo "2. Start services:"
echo "   sudo systemctl start watchdog-monitor.service"
echo "   sudo systemctl start watchdog-dashboard.service"
echo "3. Check status:"
echo "   sudo systemctl status watchdog-monitor.service"
echo "   sudo systemctl status watchdog-dashboard.service"
echo "4. View logs:"
echo "   sudo journalctl -u watchdog-monitor.service -f"
echo "5. Access dashboard:"
echo "   http://$(hostname -I | awk '{print $1}'):8501"
echo ""
echo "Health check: ./check_health.sh"
echo ""
