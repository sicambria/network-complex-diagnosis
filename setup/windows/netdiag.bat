@echo off
rem NetDiag Launcher for Windows
rem Installed by install.bat — launches GUI and opens browser.

setlocal
set "SCRIPT_DIR=%~dp0.."
set "SCRIPT=%SCRIPT_DIR%\netdiag.py"

if not exist "%SCRIPT%" (
    echo Error: netdiag.py not found at %SCRIPT%
    pause
    exit /b 1
)

echo Starting NetDiag web UI at http://localhost:8080
start "" pythonw "%SCRIPT%" --gui --port 8080
timeout /t 3 /nobreak >nul
start "" http://localhost:8080
echo Press any key to stop the server.
pause
taskkill /f /im pythonw.exe 2>nul
