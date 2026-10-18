@echo off
setlocal
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 oder neuer erforderlich. Alternativ die portable EXE verwenden.
  pause
  exit /b 1
)
start "" pythonw tools\x6_helper.py
