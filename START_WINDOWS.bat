@echo off
setlocal
cd /d "%~dp0"
call INSTALL_WINDOWS.bat --no-pause --if-needed
if errorlevel 1 goto failed
".venv\Scripts\python.exe" desktop.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo PaveForge could not start. Read the error above before closing this window.
pause
exit /b 1
