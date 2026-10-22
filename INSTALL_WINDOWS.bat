@echo off
setlocal
cd /d "%~dp0"
set "PAVEFORGE_PYTHON=py -3.13"
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 goto selected
set "PAVEFORGE_PYTHON=py -3.12"
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 goto selected
set "PAVEFORGE_PYTHON=python"
python -c "import sys; raise SystemExit(sys.version_info[:2] not in ((3,12),(3,13)))" >nul 2>&1
if not errorlevel 1 goto selected
echo Python 3.12 or 3.13 was not found. Install standard 64-bit Python, including the Python launcher.
goto failed
:selected
if "%~2"=="--if-needed" (
  %PAVEFORGE_PYTHON% scripts\install_windows.py --if-needed
) else (
  %PAVEFORGE_PYTHON% scripts\install_windows.py
)
if errorlevel 1 goto failed
if not "%~1"=="--no-pause" pause
exit /b 0
:failed
echo Installation failed. The window stays open so you can read the error.
if not "%~1"=="--no-pause" pause
exit /b 1
