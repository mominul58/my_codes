@echo off
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Please install Python 3.12 from python.org first, including the Python launcher.
  goto failed
)
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto failed
)
set "PIP_CACHE_DIR=%CD%\.cache\pip"
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements.lock
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --no-deps -e .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
echo Installation complete. Open START_WINDOWS.bat to use PaveForge.
if not "%~1"=="--no-pause" pause
exit /b 0
:failed
echo Installation failed. Copy the error above for troubleshooting.
pause
exit /b 1
