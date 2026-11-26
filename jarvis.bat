@echo off
rem JARVIS launcher. The first run (or an interrupted install) runs scripts\install.ps1.
rem The window never closes silently on an error: it shows where the log is and waits.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\.jarvis-installed" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install.ps1" %JARVIS_INSTALL_ARGS%
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m jarvis %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo [JARVIS] Something went wrong. Logs:
echo          %LOCALAPPDATA%\JARVIS\logs\install.log
echo          %LOCALAPPDATA%\JARVIS\logs\jarvis.log
echo          Diagnostics: jarvis.bat doctor
if not defined JARVIS_NO_PAUSE pause
exit /b 1
