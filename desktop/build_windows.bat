@echo off
REM Watchdog Desktop App - Windows Build Script
REM Produces: Watchdog.exe (standalone)
REM Output: ..\releases\desktop\windows\
REM
REM Requirements:
REM   - Windows 10/11
REM   - Python 3.9+ in PATH

setlocal enabledelayedexpansion

echo ========================================
echo Watchdog Desktop - Windows Build
echo ========================================
echo.

REM Configuration
set APP_NAME=Watchdog
set APP_VERSION=1.0.0
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

set OUTPUT_DIR=%SCRIPT_DIR%..\releases\desktop\windows
set VENV_DIR=%SCRIPT_DIR%.build-venv

REM Clean previous builds
echo [1/7] Cleaning previous builds...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
if exist "*.spec" del /q *.spec
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

REM Check Python
echo [2/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    echo Install from https://www.python.org/
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do echo   Found Python %%i

REM Create build environment
echo [3/7] Creating build environment...
python -m venv "%VENV_DIR%"
call "%VENV_DIR%\Scripts\activate.bat"

REM Install dependencies
echo [4/7] Installing dependencies...
python -m pip install --upgrade pip wheel --quiet
python -m pip install pywebview pyinstaller --quiet

REM Check resources
echo [5/7] Checking resources...
set ICON_FLAG=
if exist "resources\icon.ico" (
    set ICON_FLAG=--icon=resources\icon.ico
    echo   [OK] Using icon: resources\icon.ico
) else if exist "resources\icon.png" (
    set ICON_FLAG=--icon=resources\icon.png
    echo   [WARN] Using icon.png instead of icon.ico
) else (
    echo   [WARN] No icon found
)

set DATA_FLAG=
if exist "resources" set DATA_FLAG=--add-data=resources;resources

REM Build with PyInstaller
echo [6/7] Building application...
python -m PyInstaller ^
    --name="%APP_NAME%" ^
    --windowed ^
    --onefile ^
    %ICON_FLAG% ^
    %DATA_FLAG% ^
    --noconfirm ^
    --clean ^
    --log-level=WARN ^
    --hidden-import=webview ^
    --hidden-import=webview.platforms.edgechromium ^
    --hidden-import=webview.platforms.mshtml ^
    --hidden-import=clr ^
    --collect-all=webview ^
    watchdog_app.py

REM Verify build
if not exist "dist\%APP_NAME%.exe" (
    echo.
    echo ERROR: Build failed - %APP_NAME%.exe not created
    call deactivate
    pause
    exit /b 1
)
echo   [OK] %APP_NAME%.exe created

REM Copy to output
echo [7/7] Packaging...
copy /y "dist\%APP_NAME%.exe" "%OUTPUT_DIR%\" >nul
for %%A in ("%OUTPUT_DIR%\%APP_NAME%.exe") do (
    set /a SIZE_MB=%%~zA / 1048576
    echo   [OK] Copied to output (!SIZE_MB! MB^)
)

REM Cleanup
call deactivate
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q *.spec

echo.
echo ========================================
echo [OK] Windows Build Complete!
echo ========================================
echo.
echo Output: %OUTPUT_DIR%
echo.
dir "%OUTPUT_DIR%"
echo.
echo To test: "%OUTPUT_DIR%\%APP_NAME%.exe"
echo.
pause
