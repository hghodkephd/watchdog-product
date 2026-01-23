#!/bin/bash
# Watchdog Environmental Monitor - Full Deployment Script
# This script automates the complete deployment process for Raspberry Pi
#
# Usage: ./deploy.sh
#
# This consolidates the previous deploy_pi.sh and deploy_watchdog.sh scripts.

set -e  # Exit on any error

echo "=========================================="
echo "  Watchdog Environmental Monitor"
echo "  Full Deployment Script"
echo "=========================================="
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored messages
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${YELLOW}➜ $1${NC}"
}

print_step() {
    echo -e "${BLUE}[$1/$TOTAL_STEPS] $2${NC}"
}

TOTAL_STEPS=8

# Detect script location (works even when called from different directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
VENV_DIR="$PROJECT_DIR/venv"

echo "Project directory: $PROJECT_DIR"
echo ""

# Check if running on Raspberry Pi
print_step 1 "Checking system..."
if [[ -f /proc/device-tree/model ]]; then
    PI_MODEL=$(tr -d '\0' < /proc/device-tree/model)
    if [[ "$PI_MODEL" == *"Raspberry Pi"* ]]; then
        print_success "Detected: $PI_MODEL"
    else
        print_info "Not a Raspberry Pi: $PI_MODEL"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
else
    print_info "Could not detect hardware model"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 2: Update system
print_step 2 "Updating system packages..."
if sudo apt update && sudo apt upgrade -y; then
    print_success "System updated"
else
    print_error "Failed to update system"
    exit 1
fi

# Step 3: Install dependencies
print_step 3 "Installing system dependencies..."
if sudo apt install -y git python3-pip python3-venv sqlite3 bluez bluez-tools rfkill avahi-daemon; then
    print_success "System dependencies installed"
else
    print_error "Failed to install dependencies"
    exit 1
fi

# Step 4: Add user to bluetooth group
print_step 4 "Configuring Bluetooth permissions..."
if sudo usermod -a -G bluetooth "$USER"; then
    print_success "Added $USER to bluetooth group"
    print_info "Note: You may need to log out and back in for group changes to take effect"
else
    print_error "Failed to add user to bluetooth group"
fi

# Step 5: Create virtual environment
print_step 5 "Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    print_info "Creating virtual environment..."
    if python3 -m venv "$VENV_DIR"; then
        print_success "Virtual environment created at $VENV_DIR"
    else
        print_error "Failed to create virtual environment"
        exit 1
    fi
else
    print_success "Virtual environment already exists at $VENV_DIR"
fi

# Activate and install Python packages
print_info "Installing Python packages..."
source "$VENV_DIR/bin/activate"

if pip install --upgrade pip && pip install -r "$PROJECT_DIR/requirements.txt"; then
    print_success "Python packages installed"
else
    print_error "Failed to install Python packages"
    exit 1
fi

deactivate

# Step 6: Create data directories
print_step 6 "Creating data directories..."
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/data/archive"
mkdir -p "$PROJECT_DIR/data/logs"
print_success "Data directories created"

# Step 7: Configure Streamlit
print_step 7 "Configuring Streamlit..."
STREAMLIT_CONFIG="$HOME/.streamlit"
mkdir -p "$STREAMLIT_CONFIG"
cat > "$STREAMLIT_CONFIG/config.toml" << 'EOF'
[server]
address = "0.0.0.0"
port = 8501
headless = true
fileWatcherType = "none"
maxUploadSize = 5
maxMessageSize = 50

[browser]
gatherUsageStats = false

[theme]
primaryColor = "#FF4B4B"
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#262730"
textColor = "#FAFAFA"
font = "sans serif"
EOF
print_success "Streamlit configured"

# Step 8: Install systemd services
print_step 8 "Installing systemd services..."
if [ -f "$PROJECT_DIR/deploy/install-services.sh" ]; then
    print_info "Running service installer..."
    sudo bash "$PROJECT_DIR/deploy/install-services.sh"
    print_success "Services installed"
else
    print_error "Service installer not found at $PROJECT_DIR/deploy/install-services.sh"
    print_info "You can install services manually later with:"
    print_info "  sudo bash $PROJECT_DIR/deploy/install-services.sh"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}  Deployment Complete!${NC}"
echo "=========================================="
echo ""

# Get IP addresses
IP_ADDR=$(hostname -I | awk '{print $1}')

echo "Next steps:"
echo ""
echo "1. Verify Bluetooth is powered on:"
echo "   bluetoothctl show | grep Powered"
echo ""
echo "2. If Bluetooth is off, run:"
echo "   sudo bash $PROJECT_DIR/deploy/watchdog-bt-unblock.sh"
echo ""
echo "3. Check service status:"
echo "   sudo systemctl status watchdog-monitor.service"
echo "   sudo systemctl status watchdog-dashboard.service"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u watchdog-monitor.service -f"
echo ""
echo "5. Access dashboard:"
echo -e "   ${GREEN}http://$IP_ADDR:8501${NC}"
echo ""
echo "6. Run health check:"
echo "   bash $PROJECT_DIR/check_health.sh"
echo ""
echo "=========================================="
