@echo off
setlocal enabledelayedexpansion

echo [build] Building Vite frontend...
cd /d "%~dp0ui"
call npm run build
if errorlevel 1 goto :err
cd /d "%~dp0"

echo [build] Creating distribution folder...
if exist "dist\BoxPhone-Auto" rmdir /s /q "dist\BoxPhone-Auto"
mkdir "dist\BoxPhone-Auto"

echo [build] Copying Python backend...
copy /Y app.py              "dist\BoxPhone-Auto\app.py"              >nul
copy /Y db.py               "dist\BoxPhone-Auto\db.py"               >nul
copy /Y main.py             "dist\BoxPhone-Auto\main.py"             >nul
copy /Y google_login.py     "dist\BoxPhone-Auto\google_login.py"     >nul
copy /Y requirements.txt    "dist\BoxPhone-Auto\requirements.txt"    >nul
copy /Y config.json         "dist\BoxPhone-Auto\config.json"         >nul
copy /Y tasks.txt           "dist\BoxPhone-Auto\tasks.txt"           >nul
copy /Y comments.txt        "dist\BoxPhone-Auto\comments.txt"        >nul
xcopy /E /I /Y api               "dist\BoxPhone-Auto\api"               >nul
xcopy /E /I /Y adb_time_sync     "dist\BoxPhone-Auto\adb_time_sync"     >nul

echo [build] Copying Vite dist...
xcopy /E /I /Y ui\dist        "dist\BoxPhone-Auto\ui\dist"           >nul

echo [build] Copying Start.bat...
copy /Y Start.bat           "dist\BoxPhone-Auto\Start.bat"           >nul

echo [build] Writing Install.bat...
> "dist\BoxPhone-Auto\Install.bat" (
  echo @echo off
  echo echo Installing Python dependencies...
  echo pip install -r requirements.txt
  echo echo Done! Run Start.bat to launch.
  echo pause
)

echo [build] Writing README.txt...
> "dist\BoxPhone-Auto\README.txt" (
  echo BoxPhone Automation - portable build
  echo.
  echo Usage:
  echo   1. Make sure ADB is installed and on PATH
  echo   2. Connect or "adb connect ip:port" your devices
  echo   3. Run Install.bat ^(first time only^)
  echo   4. Run Start.bat to launch
  echo.
  echo The app opens in your browser at http://127.0.0.1:8765
)

echo.
echo [build] DONE. Output: dist\BoxPhone-Auto\
goto :eof

:err
echo [build] FAILED.
exit /b 1
