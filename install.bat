@echo off
rem NetDiag Windows Installer
rem Double-click this file or run from cmd to set up NetDiag.
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%netdiag.py"

echo === NetDiag -- all-in-one internet diagnostics suite ===
echo.

rem -- Step 1/3: Check Python -----------------------------------------------
echo --- Step 1/3: Checking Python ---
python3 --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    python --version >nul 2>&1
    if !ERRORLEVEL! neq 0 (
        echo Python not found. Install Python 3.12+ from https://python.org
        pause
        exit /b 1
    )
    set PY=python
) else (
    set PY=python3
)
%PY% --version

rem -- Step 2/3: Install GUI dependencies ------------------------------------
echo.
echo --- Step 2/3: Python GUI dependencies ---
%PY% -c "import fastapi" 2>nul && echo   fastapi already installed || (
    %PY% -m pip install --user fastapi uvicorn 2>nul || (
        %PY% -m pip install fastapi uvicorn 2>nul || (
            echo   Could not install fastapi/uvicorn. Run: %PY% -m pip install fastapi uvicorn
        )
    )
)

rem -- Step 3/3: Desktop shortcuts -------------------------------------------
echo.
echo --- Step 3/3: Desktop shortcuts ---
if exist "%SCRIPT_DIR%setup\windows\install.bat" (
    call "%SCRIPT_DIR%setup\windows\install.bat"
) else (
    echo   setup\windows\install.bat not found -- skipping shortcut creation.
    echo   You can run netdiag.py directly: %PY% "%SCRIPT%"
)

rem -- Optional tools --------------------------------------------------------
echo.
echo --- Optional tools (install manually) ---
echo   speedtest-cli: https://www.speedtest.net/apps/cli
echo   iperf3:        https://iperf.fr/iperf-download.php

echo.
echo === Installation complete ===
echo.
echo CLI:      %PY% "%SCRIPT%"
echo           %PY% "%SCRIPT%" --count 120
echo GUI:      %PY% "%SCRIPT%" --gui     (http://localhost:8080)
echo Daemon:   %PY% "%SCRIPT%" --daemon  (continuous + web UI)
echo Desktop:  Start Menu ^> NetDiag  or  Desktop icon
echo Tests:    %PY% -m pytest tests/
echo.
pause
