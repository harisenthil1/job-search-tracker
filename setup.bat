@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH.
  pause
  exit /b 1
)
if not exist "code\.venv\Scripts\python.exe" (
  echo Creating local Python environment...
  python -m venv "code\.venv"
  if errorlevel 1 goto :fail
)
echo Installing Search dependencies...
"code\.venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"code\.venv\Scripts\python.exe" -m pip install -r "code\requirements.txt"
if errorlevel 1 goto :fail
echo.
echo Setup complete. Double-click Start Search.vbs from now on.
pause
exit /b 0
:fail
echo.
echo Setup failed.
pause
exit /b 1
