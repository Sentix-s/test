@echo off
rem JARVIS launcher. First run installs everything (scripts\install.ps1).
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install.ps1"
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m jarvis %*
