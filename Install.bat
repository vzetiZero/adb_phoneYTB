@echo off
setlocal

if not exist "requirements.txt" (
  echo requirements.txt not found.
  exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

endlocal
pause
