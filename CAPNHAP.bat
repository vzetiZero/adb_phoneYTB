@echo off
echo ============================================
echo   BoxPhone Automation - Cap Nhat
echo ============================================
echo.

echo [1/3] Git pull...
cd /d "%~dp0"
git pull
if %errorlevel% neq 0 (
    echo [ERROR] Git pull failed
    pause
    exit /b 1
)

echo [2/3] Install UI dependencies...
cd /d "%~dp0ui"
call npm install
if %errorlevel% neq 0 (
    echo [ERROR] npm install failed
    pause
    exit /b 1
)

echo [3/3] Building frontend...
call npm run build
if %errorlevel% neq 0 (
    echo [ERROR] Build failed
    pause
    exit /b 1
)

cd /d "%~dp0"

echo.
echo ============================================
echo   Cap nhat thanh cong! Chay Start.bat de mo app.
echo ============================================
pause
