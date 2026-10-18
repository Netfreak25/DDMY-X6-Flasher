@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 oder neuer ist erforderlich.
  pause
  exit /b 1
)
python tools\x6_web.py --online
if errorlevel 1 pause
