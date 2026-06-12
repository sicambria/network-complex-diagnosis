# NetDiag Launcher for Windows (PowerShell)
# Launches GUI and opens browser. Run: powershell -File netdiag.ps1

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path (Split-Path $ScriptDir -Parent) "netdiag.py"

if (-not (Test-Path $Script)) {
    Write-Error "netdiag.py not found at $Script"
    exit 1
}

Write-Host "Starting NetDiag web UI at http://localhost:8080"
Start-Process pythonw -ArgumentList "$Script --gui --port 8080" -NoNewWindow
Start-Sleep 3
Start-Process "http://localhost:8080"

Write-Host "Press Ctrl+C to stop."
try {
    while ($true) { Start-Sleep 10 }
} finally {
    Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process
}
