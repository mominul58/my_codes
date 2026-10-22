@echo off
setlocal
cd /d "%~dp0"
call INSTALL_WINDOWS.bat --no-pause --if-needed
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --require-hashes -r requirements-build.lock
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean PaveForge.spec
if errorlevel 1 goto failed
echo Build complete. Open dist\PaveForge\PaveForge.exe.
echo Keep the entire PaveForge folder, including _internal, together.
pause
exit /b 0
:failed
echo Windows build failed. Read the error above.
pause
exit /b 1
