@echo off
setlocal
cd /d "%~dp0"
if exist "code\.venv\Scripts\python.exe" (
  set "PY=code\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
start "Search Server" cmd /k "%PY%" "code\app.py"
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765
"%PY%" "code\overlay.py"
