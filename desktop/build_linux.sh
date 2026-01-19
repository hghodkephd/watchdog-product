#!/bin/bash
set -e

echo "========================================"
echo "Building Watchdog for Linux"
echo "========================================"

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Check for GTK WebKit (required by pywebview on Linux)
echo ""
echo "Checking GTK WebKit dependencies..."

# Try to detect the package manager and check for required packages
if command -v dpkg &> /dev/null; then
    # Debian/Ubuntu
    MISSING=""
    for pkg in python3-gi python3-gi-cairo gir1.2-webkit2-4.0 gir1.2-webkit2-4.1; do
        if dpkg -s "$pkg" &> /dev/null 2>&1; then
            echo "  ✓ $pkg installed"
            break
        fi
    done
    
    # Check if at least one webkit package is installed
    if ! dpkg -s gir1.2-webkit2-4.0 &> /dev/null 2>&1 && \
       ! dpkg -s gir1.2-webkit2-4.1 &> /dev/null 2>&1; then
        echo ""
        echo "WARNING: GTK WebKit not found. Please install:"
        echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.0"
        echo ""
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
elif command -v rpm &> /dev/null; then
    # Fedora/RHEL
    echo "  Fedora/RHEL detected"
    echo "  Required packages: python3-gobject gtk3 webkit2gtk3"
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
python3 -m pip install -r requirements.txt --quiet

# Clean previous builds
echo ""
echo "Cleaning previous builds..."
rm -rf build/ dist/ *.spec

# Determine icon flag
ICON_FLAG=""
if [ -f "resources/icon.png" ]; then
    ICON_FLAG="--icon=resources/icon.png"
    echo "Using icon: resources/icon.png"
else
    echo "Warning: No icon found, building without icon"
fi

# Build with PyInstaller
echo ""
echo "Building application..."
python3 -m PyInstaller \
    --name="watchdog" \
    --windowed \
    --onefile \
    $ICON_FLAG \
    --add-data="resources:resources" \
    --noconfirm \
    --clean \
    watchdog_app.py

# Check if build succeeded
if [ -f "dist/watchdog" ]; then
    echo ""
    echo "========================================"
    echo "✅ Build successful!"
    echo "========================================"
    echo ""
    echo "Output: dist/watchdog"
    echo ""
    echo "To test: ./dist/watchdog"
    echo ""
    
    # Show size
    SIZE=$(du -sh dist/watchdog | cut -f1)
    echo "Size: $SIZE"
    
    # Make executable
    chmod +x dist/watchdog
    
    echo ""
    echo "To install system-wide:"
    echo "  sudo cp dist/watchdog /usr/local/bin/"
    
    # Create .desktop file for application menu
    echo ""
    echo "Creating .desktop file..."
    cat > dist/watchdog.desktop << 'EOF'
[Desktop Entry]
Name=Watchdog
Comment=Environmental Monitor Dashboard
Exec=/usr/local/bin/watchdog
Icon=watchdog
Terminal=false
Type=Application
Categories=Utility;Monitor;
EOF
    echo "Desktop entry created: dist/watchdog.desktop"
    echo "  Install with: cp dist/watchdog.desktop ~/.local/share/applications/"
    
else
    echo ""
    echo "========================================"
    echo "❌ Build failed!"
    echo "========================================"
    exit 1
fi
