@echo off
setlocal
cd /d "%~dp0"
if exist "code\.venv\Scripts\python.exe" (
  "code\.venv\Scripts\python.exe" "code\export_data.py"
) else (
  python "code\export_data.py"
)
pause
