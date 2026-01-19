#!/bin/bash
set -e

echo "========================================"
echo "Building Watchdog for macOS"
echo "========================================"

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 not found"
    exit 1
fi

# Check for pip
if ! python3 -m pip --version &> /dev/null; then
    echo "ERROR: pip not found"
    exit 1
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
python3 -m pip install -r requirements.txt --quiet

# Clean previous builds
echo ""
echo "Cleaning previous builds..."
rm -rf build/ dist/ *.spec

# Determine icon flag
ICON_FLAG=""
if [ -f "resources/icon.icns" ]; then
    ICON_FLAG="--icon=resources/icon.icns"
    echo "Using icon: resources/icon.icns"
elif [ -f "resources/icon.png" ]; then
    echo "Warning: icon.icns not found, using icon.png (may not display correctly)"
    ICON_FLAG="--icon=resources/icon.png"
else
    echo "Warning: No icon found, building without icon"
fi

# Build with PyInstaller
echo ""
echo "Building application..."
python3 -m PyInstaller \
    --name="Watchdog" \
    --windowed \
    --onedir \
    $ICON_FLAG \
    --osx-bundle-identifier="com.watchdog.app" \
    --add-data="resources:resources" \
    --noconfirm \
    --clean \
    watchdog_app.py

# Check if build succeeded
if [ -d "dist/Watchdog.app" ]; then
    echo ""
    echo "========================================"
    echo "✅ Build successful!"
    echo "========================================"
    echo ""
    echo "Output: dist/Watchdog.app"
    echo ""
    echo "To test: open dist/Watchdog.app"
    echo ""
    
    # Show size
    SIZE=$(du -sh dist/Watchdog.app | cut -f1)
    echo "Size: $SIZE"
    
    # Create DMG if create-dmg is available
    if command -v create-dmg &> /dev/null; then
        echo ""
        echo "Creating DMG installer..."
        create-dmg \
            --volname "Watchdog" \
            --window-size 600 400 \
            --icon-size 128 \
            --icon "Watchdog.app" 150 200 \
            --app-drop-link 450 200 \
            "dist/Watchdog.dmg" \
            "dist/Watchdog.app" \
            2>/dev/null || echo "DMG creation skipped (create-dmg error)"
        
        if [ -f "dist/Watchdog.dmg" ]; then
            echo "DMG created: dist/Watchdog.dmg"
        fi
    else
        echo ""
        echo "Tip: Install 'create-dmg' to generate a DMG installer:"
        echo "  brew install create-dmg"
    fi
else
    echo ""
    echo "========================================"
    echo "❌ Build failed!"
    echo "========================================"
    exit 1
fi
