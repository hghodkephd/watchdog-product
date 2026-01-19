@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Building Watchdog for Windows
echo ========================================

:: Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found
    echo Please install Python from https://www.python.org/
    pause
    exit /b 1
)

:: Install dependencies
echo.
echo Installing dependencies...
python -m pip install -r requirements.txt --quiet

:: Clean previous builds
echo.
echo Cleaning previous builds...
if exist "build" rmdir /s /q build
if exist "dist" rmdir /s /q dist
if exist "*.spec" del /q *.spec

:: Determine icon flag
set ICON_FLAG=
if exist "resources\icon.ico" (
    set ICON_FLAG=--icon=resources\icon.ico
    echo Using icon: resources\icon.ico
) else if exist "resources\icon.png" (
    echo Warning: icon.ico not found, using icon.png
    set ICON_FLAG=--icon=resources\icon.png
) else (
    echo Warning: No icon found, building without icon
)

:: Build with PyInstaller
echo.
echo Building application...
python -m PyInstaller ^
    --name="Watchdog" ^
    --windowed ^
    --onefile ^
    %ICON_FLAG% ^
    --add-data="resources;resources" ^
    --noconfirm ^
    --clean ^
    watchdog_app.py

:: Check if build succeeded
if exist "dist\Watchdog.exe" (
    echo.
    echo ========================================
    echo Build successful!
    echo ========================================
    echo.
    echo Output: dist\Watchdog.exe
    echo.
    
    :: Show size
    for %%A in ("dist\Watchdog.exe") do echo Size: %%~zA bytes
) else (
    echo.
    echo ========================================
    echo Build failed!
    echo ========================================
    pause
    exit /b 1
)

echo.
pause
