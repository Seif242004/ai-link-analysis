@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local2.ps1"
echo Finished.
timeout /t 5 >nul
