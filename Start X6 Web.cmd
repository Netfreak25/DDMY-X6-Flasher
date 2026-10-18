@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 ist erforderlich. Bitte Python installieren und erneut starten.
  pause
  exit /b 1
)
python tools\x6_web.py
if errorlevel 1 pause
