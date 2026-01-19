# Watchdog Desktop App

Cross-platform desktop launcher for Watchdog Environmental Monitor.

## Overview

This app provides a native-feeling launcher that:
- Auto-discovers your Watchdog on the local network
- Bypasses browser profile issues (especially Chrome)
- Remembers your Watchdog's IP address
- Works on macOS, Windows, and Linux

## Prerequisites

### macOS
```bash
pip install -r requirements.txt
```

### Windows
```batch
pip install -r requirements.txt
```

### Linux (Ubuntu/Debian)
```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.0
pip install -r requirements.txt
```

### Linux (Fedora)
```bash
sudo dnf install python3-gobject gtk3 webkit2gtk3
pip install -r requirements.txt
```

## Development

Run directly without building:
```bash
python watchdog_app.py
```

## Building

### macOS
```bash
chmod +x build_mac.sh
./build_mac.sh
# Output: dist/Watchdog.app
```

### Windows
```batch
build_windows.bat
# Output: dist\Watchdog.exe
```

### Linux
```bash
chmod +x build_linux.sh
./build_linux.sh
# Output: dist/watchdog
```

## Icon Generation

The `resources/icon.svg` is a placeholder. To create platform-specific icons:

### macOS (.icns)
```bash
# Using iconutil (built into macOS)
mkdir icon.iconset
sips -z 16 16 icon.png --out icon.iconset/icon_16x16.png
sips -z 32 32 icon.png --out icon.iconset/icon_16x16@2x.png
sips -z 32 32 icon.png --out icon.iconset/icon_32x32.png
sips -z 64 64 icon.png --out icon.iconset/icon_32x32@2x.png
sips -z 128 128 icon.png --out icon.iconset/icon_128x128.png
sips -z 256 256 icon.png --out icon.iconset/icon_128x128@2x.png
sips -z 256 256 icon.png --out icon.iconset/icon_256x256.png
sips -z 512 512 icon.png --out icon.iconset/icon_256x256@2x.png
sips -z 512 512 icon.png --out icon.iconset/icon_512x512.png
sips -z 1024 1024 icon.png --out icon.iconset/icon_512x512@2x.png
iconutil -c icns icon.iconset -o resources/icon.icns
rm -rf icon.iconset
```

### Windows (.ico)
```bash
# Using ImageMagick
convert icon.png -define icon:auto-resize=256,128,64,48,32,16 resources/icon.ico
```

### Quick method (both)
Use https://cloudconvert.com or similar to convert PNG to ICO/ICNS.

## Project Structure

```
watchdog-app/
├── watchdog_app.py      # Main application
├── config.py            # Configuration persistence
├── discovery.py         # Network discovery
├── requirements.txt     # Python dependencies
├── build_mac.sh         # macOS build script
├── build_windows.bat    # Windows build script
├── build_linux.sh       # Linux build script
├── resources/
│   ├── icon.svg         # Source icon
│   ├── icon.png         # PNG (256x256+)
│   ├── icon.icns        # macOS icon
│   └── icon.ico         # Windows icon
└── README.md
```

## How It Works

1. **Launch**: User double-clicks the app
2. **Discovery**: App tries to find Watchdog via:
   - Saved IP from previous session
   - mDNS (watchdog.local)
   - Common default IPs
3. **Connect**: If found, opens dashboard immediately
4. **Setup**: If not found, shows IP entry dialog
5. **Remember**: Saves working IP for next launch

## Troubleshooting

### "Watchdog not found"
- Ensure your computer is on the same WiFi as Watchdog
- Check your router for the Watchdog's IP address
- Enter the IP manually in the setup dialog

### Linux: "No module named 'gi'"
Install GTK WebKit bindings:
```bash
# Ubuntu/Debian
sudo apt install python3-gi python3-gi-cairo gir1.2-webkit2-4.0

# Fedora
sudo dnf install python3-gobject gtk3 webkit2gtk3
```

### Build fails with PyInstaller
Ensure you have the latest version:
```bash
pip install --upgrade pyinstaller
```
