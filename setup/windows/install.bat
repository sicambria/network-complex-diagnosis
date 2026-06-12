@echo off
rem NetDiag Windows Installer
rem Installs shortcuts, copies scripts, creates Start Menu entry.
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0.."
set "PROJECT_DIR=%~dp0.."
set "SETUP_DIR=%PROJECT_DIR%\setup\windows"

echo === NetDiag Windows Setup ===
echo.

rem Check Python
python3 --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Python not found. Please install Python 3.12+ from https://python.org
    pause
    exit /b 1
)
echo Python: 
python3 --version

rem Install pip dependencies
echo Installing GUI dependencies...
python3 -m pip install --user fastapi uvicorn 2>nul
if %ERRORLEVEL% neq 0 (
    echo Warning: fastapi/uvicorn not installed. GUI mode requires them.
)

rem Create Start Menu shortcut via PowerShell
echo Creating Start Menu shortcut...
powershell -Command ^
    "$WshShell = New-Object -ComObject WScript.Shell; ^
     $Shortcut = $WshShell.CreateShortcut('%APPDATA%\Microsoft\Windows\Start Menu\Programs\NetDiag.lnk'); ^
     $Shortcut.TargetPath = 'powershell.exe'; ^
     $Shortcut.Arguments = '-WindowStyle Hidden -File \"%SETUP_DIR%\netdiag.ps1\"'; ^
     $Shortcut.WorkingDirectory = '%PROJECT_DIR%'; ^
     $Shortcut.Description = 'NetDiag - Internet Diagnostics Suite'; ^
     $Shortcut.Save()" 2>nul
if %ERRORLEVEL% equ 0 (echo   Start Menu: OK) else (echo   Start Menu: could not create)

rem Desktop shortcut
echo Creating Desktop shortcut...
powershell -Command ^
    "$WshShell = New-Object -ComObject WScript.Shell; ^
     $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\NetDiag.lnk'); ^
     $Shortcut.TargetPath = 'powershell.exe'; ^
     $Shortcut.Arguments = '-WindowStyle Hidden -File \"%SETUP_DIR%\netdiag.ps1\"'; ^
     $Shortcut.WorkingDirectory = '%PROJECT_DIR%'; ^
     $Shortcut.Save()" 2>nul
if %ERRORLEVEL% equ 0 (echo   Desktop: OK) else (echo   Desktop: could not create)

echo.
echo === Setup complete ===
echo.
echo Double-click the NetDiag icon on your desktop, or
echo find it in the Start Menu under "N".
echo Or run: python3 "%PROJECT_DIR%\netdiag.py"
echo.
pause
