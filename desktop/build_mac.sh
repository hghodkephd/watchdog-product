#!/bin/bash
#
# Watchdog Desktop App - macOS Build Script
# Produces: Watchdog.app + Watchdog.dmg (or .zip fallback)
#
# Usage: cd desktop && ./build_mac.sh
# Output: ../releases/desktop/macos/
#
# Requirements:
#   - macOS 10.15+ (Catalina or later)
#   - Python 3.9+ installed
#   - Optional: create-dmg for DMG creation (brew install create-dmg)
#

set -e

echo "========================================"
echo "Watchdog Desktop - macOS Build"
echo "========================================"
echo ""

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Watchdog"
APP_VERSION="1.0.0"
BUNDLE_ID="com.watchdog.desktop"
OUTPUT_DIR="$SCRIPT_DIR/../releases/desktop/macos"
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
    echo "Install from https://www.python.org/ or via Homebrew"
    exit 1
fi
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  Found Python $PYTHON_VERSION"

# Create isolated build environment
echo "[3/7] Creating build environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# Upgrade pip and install build dependencies
echo "[4/7] Installing dependencies..."
pip install --upgrade pip wheel --quiet
pip install pywebview pyinstaller --quiet

# Determine icon
echo "[5/7] Checking resources..."
ICON_FLAG=""
if [ -f "resources/icon.icns" ]; then
    ICON_FLAG="--icon=resources/icon.icns"
    echo "  ✓ Using icon: resources/icon.icns"
elif [ -f "resources/icon.png" ]; then
    echo "  ⚠ icon.icns not found, using icon.png (may not display correctly)"
    ICON_FLAG="--icon=resources/icon.png"
else
    echo "  ⚠ No icon found, building without app icon"
fi

# Add resources if present
DATA_FLAGS=""
if [ -d "resources" ]; then
    DATA_FLAGS="--add-data=resources:resources"
fi

# Build with PyInstaller
echo "[6/7] Building application with PyInstaller..."
python -m PyInstaller \
    --name="$APP_NAME" \
    --windowed \
    --onedir \
    $ICON_FLAG \
    $DATA_FLAGS \
    --osx-bundle-identifier="$BUNDLE_ID" \
    --noconfirm \
    --clean \
    --log-level=WARN \
    --hidden-import=webview \
    --hidden-import=webview.platforms.cocoa \
    --collect-all=webview \
    watchdog_app.py

# Verify build
if [ ! -d "dist/$APP_NAME.app" ]; then
    echo ""
    echo "ERROR: Build failed - $APP_NAME.app not created"
    deactivate
    exit 1
fi
echo "  ✓ $APP_NAME.app created"

# Copy to output
echo "[7/7] Packaging..."
rm -rf "$OUTPUT_DIR/$APP_NAME.app"
cp -R "dist/$APP_NAME.app" "$OUTPUT_DIR/"
APP_SIZE=$(du -sh "$OUTPUT_DIR/$APP_NAME.app" | cut -f1)
echo "  ✓ App copied to $OUTPUT_DIR ($APP_SIZE)"

# Create DMG if create-dmg is available
if command -v create-dmg &> /dev/null; then
    echo ""
    echo "Creating DMG installer..."
    DMG_PATH="$OUTPUT_DIR/$APP_NAME-$APP_VERSION.dmg"
    rm -f "$DMG_PATH"
    
    create-dmg \
        --volname "$APP_NAME" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "$APP_NAME.app" 150 185 \
        --app-drop-link 450 185 \
        --hide-extension "$APP_NAME.app" \
        "$DMG_PATH" \
        "$OUTPUT_DIR/$APP_NAME.app" \
        2>/dev/null && {
            DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)
            echo "  ✓ DMG created: $DMG_PATH ($DMG_SIZE)"
        } || echo "  ⚠ DMG creation failed, ZIP will be created instead"
fi

# Always create ZIP as fallback/alternative
echo ""
echo "Creating ZIP archive..."
ZIP_PATH="$OUTPUT_DIR/$APP_NAME-$APP_VERSION-macos.zip"
rm -f "$ZIP_PATH"
cd "$OUTPUT_DIR"
zip -r -q "$APP_NAME-$APP_VERSION-macos.zip" "$APP_NAME.app"
cd "$SCRIPT_DIR"
ZIP_SIZE=$(du -sh "$ZIP_PATH" | cut -f1)
echo "  ✓ ZIP created: $ZIP_PATH ($ZIP_SIZE)"

# Cleanup
deactivate
rm -rf "$VENV_DIR" build/ dist/ *.spec

echo ""
echo "========================================"
echo "✅ macOS Build Complete!"
echo "========================================"
echo ""
echo "Output: $OUTPUT_DIR"
echo ""
ls -la "$OUTPUT_DIR"
echo ""
echo "To test: open '$OUTPUT_DIR/$APP_NAME.app'"
echo ""
if ! command -v create-dmg &> /dev/null; then
    echo "TIP: Install create-dmg for DMG installers:"
    echo "  brew install create-dmg"
    echo ""
fi
