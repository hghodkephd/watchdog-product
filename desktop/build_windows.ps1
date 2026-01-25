<#
.SYNOPSIS
    Watchdog Desktop App - Windows Build Script
    
.DESCRIPTION
    Produces: Watchdog.exe (standalone, no installer needed)
    Output: ..\releases\desktop\windows\
    
.NOTES
    Requirements:
    - Windows 10/11
    - Python 3.9+ in PATH
    
.EXAMPLE
    .\build_windows.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "========================================"
Write-Host "Watchdog Desktop - Windows Build"
Write-Host "========================================"
Write-Host ""

# Configuration
$APP_NAME = "Watchdog"
$APP_VERSION = "1.0.1"
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $SCRIPT_DIR

$OUTPUT_DIR = Join-Path $SCRIPT_DIR "..\releases\desktop\windows"
$VENV_DIR = Join-Path $SCRIPT_DIR ".build-venv"

# Clean previous builds
Write-Host "[1/7] Cleaning previous builds..."
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist") { Remove-Item -Recurse -Force "dist" }
if (Test-Path $VENV_DIR) { Remove-Item -Recurse -Force $VENV_DIR }
Get-ChildItem -Filter "*.spec" | Remove-Item -Force -ErrorAction SilentlyContinue
if (-not (Test-Path $OUTPUT_DIR)) { 
    New-Item -ItemType Directory -Path $OUTPUT_DIR -Force | Out-Null 
}

# Check Python
Write-Host "[2/7] Checking Python..."
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Found $pythonVersion"
} catch {
    Write-Host "ERROR: Python not found in PATH"
    Write-Host "Install from https://www.python.org/"
    exit 1
}

# Create isolated build environment
Write-Host "[3/7] Creating build environment..."
python -m venv $VENV_DIR
$activateScript = Join-Path $VENV_DIR "Scripts\Activate.ps1"
. $activateScript

# Install dependencies
Write-Host "[4/7] Installing dependencies..."
python -m pip install --upgrade pip wheel --quiet
python -m pip install pywebview pyinstaller --quiet

# Check resources
Write-Host "[5/7] Checking resources..."
$ICON_FLAG = @()
$iconPath = Join-Path $SCRIPT_DIR "resources\icon.ico"
$iconPngPath = Join-Path $SCRIPT_DIR "resources\icon.png"

if (Test-Path $iconPath) {
    $ICON_FLAG = @("--icon=resources\icon.ico")
    Write-Host "  OK Using icon: resources\icon.ico"
} elseif (Test-Path $iconPngPath) {
    Write-Host "  WARN icon.ico not found, using icon.png"
    $ICON_FLAG = @("--icon=resources\icon.png")
} else {
    Write-Host "  WARN No icon found, building without icon"
}

$DATA_FLAGS = @()
$resourcesPath = Join-Path $SCRIPT_DIR "resources"
if (Test-Path $resourcesPath) {
    $DATA_FLAGS = @("--add-data=resources;resources")
}

# Build with PyInstaller
Write-Host "[6/7] Building application with PyInstaller..."

$pyinstallerArgs = @(
    "-m", "PyInstaller",
    "--name=$APP_NAME",
    "--windowed",
    "--onefile",
    "--noconfirm",
    "--clean",
    "--log-level=WARN",
    "--hidden-import=webview",
    "--hidden-import=webview.platforms.edgechromium",
    "--hidden-import=webview.platforms.mshtml",
    "--hidden-import=clr",
    "--collect-all=webview",
    "watchdog_app.py"
)

if ($ICON_FLAG) { $pyinstallerArgs += $ICON_FLAG }
if ($DATA_FLAGS) { $pyinstallerArgs += $DATA_FLAGS }

& python @pyinstallerArgs

# Verify build
$exePath = Join-Path "dist" "$APP_NAME.exe"
if (-not (Test-Path $exePath)) {
    Write-Host ""
    Write-Host "ERROR: Build failed - $APP_NAME.exe not created"
    deactivate
    exit 1
}
Write-Host "  OK $APP_NAME.exe created"

# Copy to output
Write-Host "[7/7] Packaging..."
$outputExe = Join-Path $OUTPUT_DIR "$APP_NAME.exe"
Copy-Item $exePath $outputExe -Force

$fileSize = (Get-Item $outputExe).Length
$fileSizeMB = [math]::Round($fileSize / 1MB, 1)
Write-Host "  OK Copied to $OUTPUT_DIR ($fileSizeMB MB)"

# Create ZIP
Write-Host ""
Write-Host "Creating ZIP archive..."
$zipPath = Join-Path $OUTPUT_DIR "$APP_NAME-$APP_VERSION-windows.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $outputExe -DestinationPath $zipPath -Force
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "  OK ZIP created: $zipPath ($zipSize MB)"

# Cleanup
deactivate
Remove-Item -Recurse -Force $VENV_DIR -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue
Get-ChildItem -Filter "*.spec" | Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "========================================"
Write-Host "OK Windows Build Complete!"
Write-Host "========================================"
Write-Host ""
Write-Host "Output: $OUTPUT_DIR"
Write-Host ""
Get-ChildItem $OUTPUT_DIR | Format-Table Name, Length -AutoSize
Write-Host ""
Write-Host "To test: Start-Process '$outputExe'"
Write-Host ""
