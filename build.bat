@echo off
setlocal enabledelayedexpansion

REM ===========================================================
REM Build standalone .exe for BoxPhone Automation
REM Usage: just double-click build.bat (or run from terminal)
REM Output: dist\BoxPhone-Auto\BoxPhone-Auto.exe
REM ===========================================================

echo [build] PyInstaller check...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo [build] Installing PyInstaller...
  python -m pip install --upgrade pyinstaller || goto :err
)

echo [build] Cleaning previous build...
if exist build  rmdir /s /q build
if exist dist   rmdir /s /q dist

echo [build] Running PyInstaller... (Qt + OpenCV are big, may take 2-3 min)
python -m PyInstaller ^
  --noconfirm ^
  --windowed ^
  --name "BoxPhone-Auto" ^
  --collect-all PySide6 ^
  --hidden-import shiboken6 ^
  --hidden-import cv2 ^
  --hidden-import numpy ^
  gui_app.py
if errorlevel 1 goto :err

echo [build] Copying data files next to exe...
copy /Y tasks.txt           "dist\BoxPhone-Auto\tasks.txt"           >nul
copy /Y comments.txt        "dist\BoxPhone-Auto\comments.txt"        >nul
if not exist "dist\BoxPhone-Auto\like_templates" mkdir "dist\BoxPhone-Auto\like_templates"
if exist "like_templates\README.txt" copy /Y "like_templates\README.txt" "dist\BoxPhone-Auto\like_templates\README.txt" >nul
REM also ship any PNG templates the dev already placed
for %%f in (like_templates\*.png) do copy /Y "%%f" "dist\BoxPhone-Auto\like_templates\" >nul 2>&1

echo [build] Writing README.txt...
> "dist\BoxPhone-Auto\README.txt" (
  echo BoxPhone Automation - portable build
  echo.
  echo Usage:
  echo   1. Make sure ADB is installed and on PATH:
  echo        winget install Google.PlatformTools
  echo      Verify in a new terminal: adb version
  echo   2. Connect or "adb connect ip:port" your devices.
  echo   3. Double-click BoxPhone-Auto.exe
  echo.
  echo Files next to the exe ^(edit freely^):
  echo   tasks.txt        - advanced workflow definitions ^(CLI only^)
  echo   comments.txt     - YouTube comment pool
  echo   like_templates\  - drop a PNG crop of the YouTube Like icon here for
  echo                      OpenCV-based tap fallback ^(see README in folder^)
  echo   app.db           - SQLite ^(devices + history^), auto-created
  echo   debug_dumps\     - saved UI XML on Shorts/Like miss
)

echo.
echo [build] DONE.  dist\BoxPhone-Auto\BoxPhone-Auto.exe
goto :eof

:err
echo [build] FAILED.
exit /b 1
