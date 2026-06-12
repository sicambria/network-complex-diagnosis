#!/bin/bash
# NetDiag Launcher for macOS — standalone script (alternative to .app bundle)
# Usage: bash macos-launcher.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/../netdiag.py"

if [ ! -f "$SCRIPT" ]; then
    echo "Error: netdiag.py not found at $SCRIPT"
    exit 1
fi

echo "Starting NetDiag..."
cd "$(dirname "$SCRIPT")"
python3 netdiag.py --gui --port 8080 &
sleep 2
open "http://localhost:8080"
echo "NetDiag running at http://localhost:8080"
echo "Press Ctrl+C to stop."
wait
