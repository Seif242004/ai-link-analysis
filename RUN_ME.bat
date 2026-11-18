@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"
echo.
echo Finished. See the docs folder. Press any key to close.
pause >nul
