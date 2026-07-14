@echo off
echo ============================================
echo   BoxPhone Automation - Install
echo ============================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install Python dependencies
    pause
    exit /b 1
)

echo [2/3] Installing frontend dependencies...
cd /d "%~dp0ui"
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install npm dependencies
    pause
    exit /b 1
)

echo [3/3] Building frontend...
call npm run build
if %errorlevel% neq 0 (
    echo [ERROR] Failed to build frontend
    pause
    exit /b 1
)

cd /d "%~dp0"

echo.
echo ============================================
echo   Install complete! Run Start.bat to launch.
echo ============================================
pause
