#!/bin/bash
# Govee Monitor - Raspberry Pi Deployment Script
# This script automates the deployment process for first-time setup

set -e  # Exit on any error

echo "=========================================="
echo "  Govee Monitor Deployment Script"
echo "=========================================="
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Check if running on Raspberry Pi
print_info "Checking system..."
if [[ ! -f /proc/device-tree/model ]] || ! grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    print_error "This script is designed for Raspberry Pi. Detected: $(uname -a)"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    PI_MODEL=$(cat /proc/device-tree/model)
    print_success "Detected: $PI_MODEL"
fi

# Variables
PROJECT_DIR="$HOME/govee-monitor"
VENV_DIR="$HOME/govee-env"
STREAMLIT_CONFIG="$HOME/.streamlit"

# Step 1: Update system
print_info "Updating system packages (this may take several minutes)..."
if sudo apt update && sudo apt upgrade -y; then
    print_success "System updated"
else
    print_error "Failed to update system"
    exit 1
fi

# Step 2: Install dependencies
print_info "Installing system dependencies..."
if sudo apt install -y git python3-pip python3-venv sqlite3 bluez; then
    print_success "System dependencies installed"
else
    print_error "Failed to install dependencies"
    exit 1
fi

# Step 3: Add user to bluetooth group
print_info "Configuring Bluetooth permissions..."
if sudo usermod -a -G bluetooth "$USER"; then
    print_success "Added $USER to bluetooth group"
    print_info "Note: You may need to log out and back in for group changes to take effect"
else
    print_error "Failed to add user to bluetooth group"
fi

# Step 4: Create virtual environment
if [ ! -d "$VENV_DIR" ]; then
    print_info "Creating Python virtual environment..."
    if python3 -m venv "$VENV_DIR"; then
        print_success "Virtual environment created at $VENV_DIR"
    else
        print_error "Failed to create virtual environment"
        exit 1
    fi
else
    print_success "Virtual environment already exists at $VENV_DIR"
fi

# Step 5: Activate virtual environment and install Python packages
print_info "Installing Python packages..."
source "$VENV_DIR/bin/activate"

if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    if pip install --upgrade pip && pip install -r "$PROJECT_DIR/requirements.txt"; then
        print_success "Python packages installed"
    else
        print_error "Failed to install Python packages"
        exit 1
    fi
else
    print_info "requirements.txt not found. Installing packages manually..."
    if pip install --upgrade pip && pip install bleak streamlit pandas plotly platformdirs psutil requests; then
        print_success "Python packages installed"
    else
        print_error "Failed to install Python packages"
        exit 1
    fi
fi

# Step 6: Create project directory structure
print_info "Setting up project structure..."
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/data/archive"
print_success "Project directories created"

# Step 7: Configure Streamlit
print_info "Configuring Streamlit for network access..."
mkdir -p "$STREAMLIT_CONFIG"
cat > "$STREAMLIT_CONFIG/config.toml" << 'EOF'
[server]
# Allow connections from any network device
address = "0.0.0.0"
port = 8501

# Disable automatic browser opening (Pi has no browser)
headless = true

# Disable file watcher to reduce CPU usage
fileWatcherType = "none"

# Increase timeout for stability
maxUploadSize = 5
maxMessageSize = 50

[browser]
# Disable browser gathering usage stats
gatherUsageStats = false

[theme]
# Optional: Set a nice default theme
primaryColor = "#FF4B4B"
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#262730"
textColor = "#FAFAFA"
EOF
print_success "Streamlit configured at $STREAMLIT_CONFIG/config.toml"

# Step 8: Install systemd service files
print_info "Installing systemd service files..."

# Check if service files exist in project directory
if [ -f "$PROJECT_DIR/govee-monitor.service" ] && [ -f "$PROJECT_DIR/govee-dashboard.service" ]; then
    sudo cp "$PROJECT_DIR/govee-monitor.service" /etc/systemd/system/
    sudo cp "$PROJECT_DIR/govee-dashboard.service" /etc/systemd/system/
    print_success "Service files copied to /etc/systemd/system/"
else
    print_error "Service files not found in $PROJECT_DIR"
    print_info "Please ensure govee-monitor.service and govee-dashboard.service are in the project directory"
    exit 1
fi

# Step 9: Reload systemd and enable services
print_info "Enabling services..."
if sudo systemctl daemon-reload; then
    print_success "Systemd reloaded"
else
    print_error "Failed to reload systemd"
    exit 1
fi

if sudo systemctl enable govee-monitor.service govee-dashboard.service; then
    print_success "Services enabled for automatic startup"
else
    print_error "Failed to enable services"
    exit 1
fi

# Step 10: Start services
print_info "Starting services..."
if sudo systemctl start govee-monitor.service; then
    print_success "BLE monitoring service started"
else
    print_error "Failed to start BLE monitoring service"
    print_info "Check logs with: sudo journalctl -u govee-monitor.service -n 50"
fi

sleep 2  # Give BLE service time to start

if sudo systemctl start govee-dashboard.service; then
    print_success "Dashboard service started"
else
    print_error "Failed to start dashboard service"
    print_info "Check logs with: sudo journalctl -u govee-dashboard.service -n 50"
fi

# Step 11: Display status and access information
echo ""
echo "=========================================="
echo "  Deployment Complete!"
echo "=========================================="
echo ""

# Get IP addresses
IP_ADDR=$(hostname -I | awk '{print $1}')
print_success "Dashboard is accessible at: http://$IP_ADDR:8501"
echo ""

# Check service status
print_info "Service Status:"
sudo systemctl status govee-monitor.service --no-pager | grep -E "(Active|Loaded)" || true
sudo systemctl status govee-dashboard.service --no-pager | grep -E "(Active|Loaded)" || true
echo ""

# Useful commands
echo "=========================================="
echo "  Useful Commands"
echo "=========================================="
echo ""
echo "View BLE monitoring logs:"
echo "  sudo journalctl -u govee-monitor.service -f"
echo ""
echo "View dashboard logs:"
echo "  sudo journalctl -u govee-dashboard.service -f"
echo ""
echo "Restart services:"
echo "  sudo systemctl restart govee-monitor.service"
echo "  sudo systemctl restart govee-dashboard.service"
echo ""
echo "Check service status:"
echo "  sudo systemctl status govee-monitor.service"
echo "  sudo systemctl status govee-dashboard.service"
echo ""
echo "Stop services:"
echo "  sudo systemctl stop govee-monitor.service"
echo "  sudo systemctl stop govee-dashboard.service"
echo ""

# Final notes
echo "=========================================="
echo "  Post-Deployment Notes"
echo "=========================================="
echo ""
print_info "1. Test the dashboard by opening http://$IP_ADDR:8501 in a browser"
print_info "2. Verify sensors are being detected (may take a few minutes)"
print_info "3. Check logs if you encounter any issues"
print_info "4. Consider setting a static IP address for consistent access"
print_info "5. Read RASPBERRY_PI_DEPLOYMENT.md for detailed documentation"
echo ""
print_success "Deployment complete! Your Govee Monitor is now running 24/7."
