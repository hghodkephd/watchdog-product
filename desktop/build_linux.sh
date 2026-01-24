#!/bin/bash
#
# Watchdog Desktop App - Linux Build Script
# Produces: watchdog executable + tarball
#
# Usage: cd desktop && ./build_linux.sh
# Output: ../releases/desktop/linux/
#
# Requirements:
#   - Ubuntu 20.04+ / Debian 11+ (or equivalent)
#   - Python 3.9+
#   - GTK WebKit: sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.0
#

set -e

echo "========================================"
echo "Watchdog Desktop - Linux Build"
echo "========================================"
echo ""

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Watchdog"
APP_NAME_LOWER="watchdog"
APP_VERSION="1.0.0"
OUTPUT_DIR="$SCRIPT_DIR/../releases/desktop/linux"
VENV_DIR="$SCRIPT_DIR/.build-venv"

# Clean previous builds
echo "[1/7] Cleaning previous builds..."
rm -rf build/ dist/ *.spec
rm -rf "$VENV_DIR"
mkdir -p "$OUTPUT_DIR"

# Check Python
echo "[2/7] Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Found Python $PYTHON_VERSION"

# Check GTK WebKit
echo "[3/7] Checking GTK WebKit..."
GTK_MISSING=false
if command -v dpkg &> /dev/null; then
    # Debian/Ubuntu
    if ! dpkg -s gir1.2-webkit2-4.0 &> /dev/null 2>&1 && \
       ! dpkg -s gir1.2-webkit2-4.1 &> /dev/null 2>&1; then
        GTK_MISSING=true
    fi
fi

if [ "$GTK_MISSING" = true ]; then
    echo "  WARNING: GTK WebKit may not be installed"
    echo "  Install with: sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.0"
    echo ""
    read -p "  Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "  ✓ GTK WebKit found"
fi

# Create build environment
echo "[4/7] Creating build environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Install dependencies
echo "[5/7] Installing dependencies..."
pip install --upgrade pip wheel --quiet
pip install pywebview pyinstaller --quiet

# Check resources
echo "[6/7] Checking resources..."
ICON_FLAG=""
if [ -f "resources/icon.png" ]; then
    ICON_FLAG="--icon=resources/icon.png"
    echo "  ✓ Using icon: resources/icon.png"
else
    echo "  ⚠ No icon found"
fi

DATA_FLAGS=""
if [ -d "resources" ]; then
    DATA_FLAGS="--add-data=resources:resources"
fi

# Build with PyInstaller
echo "[7/7] Building application..."
python -m PyInstaller \
    --name="$APP_NAME_LOWER" \
    --windowed \
    --onefile \
    $ICON_FLAG \
    $DATA_FLAGS \
    --noconfirm \
    --clean \
    --log-level=WARN \
    --hidden-import=webview \
    --hidden-import=webview.platforms.gtk \
    --hidden-import=gi \
    --collect-all=webview \
    watchdog_app.py

# Verify build
if [ ! -f "dist/$APP_NAME_LOWER" ]; then
    echo ""
    echo "ERROR: Build failed - $APP_NAME_LOWER not created"
    deactivate
    exit 1
fi
echo "  ✓ $APP_NAME_LOWER created"

# Copy to output
cp "dist/$APP_NAME_LOWER" "$OUTPUT_DIR/"
chmod +x "$OUTPUT_DIR/$APP_NAME_LOWER"
FILE_SIZE=$(du -sh "$OUTPUT_DIR/$APP_NAME_LOWER" | cut -f1)
echo "  ✓ Copied to $OUTPUT_DIR ($FILE_SIZE)"

# Create .desktop file
cat > "$OUTPUT_DIR/$APP_NAME_LOWER.desktop" << EOF
[Desktop Entry]
Name=$APP_NAME
Comment=Environmental Monitor Dashboard
Exec=$APP_NAME_LOWER
Icon=$APP_NAME_LOWER
Terminal=false
Type=Application
Categories=Utility;Monitor;
EOF
echo "  ✓ Desktop entry created"

# Create tarball
echo ""
echo "Creating tarball..."
TAR_NAME="$APP_NAME-$APP_VERSION-linux-x86_64.tar.gz"
cd "$OUTPUT_DIR"
tar -czf "$TAR_NAME" "$APP_NAME_LOWER" "$APP_NAME_LOWER.desktop"
cd "$SCRIPT_DIR"
TAR_SIZE=$(du -sh "$OUTPUT_DIR/$TAR_NAME" | cut -f1)
echo "  ✓ Tarball created: $TAR_NAME ($TAR_SIZE)"

# Cleanup
deactivate
rm -rf "$VENV_DIR" build/ dist/ *.spec

echo ""
echo "========================================"
echo "✅ Linux Build Complete!"
echo "========================================"
echo ""
echo "Output: $OUTPUT_DIR"
echo ""
ls -la "$OUTPUT_DIR"
echo ""
echo "To test: '$OUTPUT_DIR/$APP_NAME_LOWER'"
echo ""
echo "To install system-wide:"
echo "  sudo cp '$OUTPUT_DIR/$APP_NAME_LOWER' /usr/local/bin/"
echo "  cp '$OUTPUT_DIR/$APP_NAME_LOWER.desktop' ~/.local/share/applications/"
echo ""
