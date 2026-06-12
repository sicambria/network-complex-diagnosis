#!/bin/bash
# NetDiag — Linux launcher
# Starts the GUI server (if not already running) and opens it in the browser.
# Used by netdiag.desktop so the start-menu entry actually shows the web UI.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
NETDIAG_PY="$PROJECT_DIR/netdiag.py"
PORT="${NETDIAG_PORT:-8080}"
URL="http://localhost:$PORT"
LOGDIR="$HOME/.netdiag"
LOGFILE="$LOGDIR/gui.log"

mkdir -p "$LOGDIR"

notify() {
    if command -v notify-send &>/dev/null; then
        notify-send "NetDiag" "$1" 2>/dev/null
    elif command -v zenity &>/dev/null; then
        zenity --error --text="$1" 2>/dev/null &
    else
        echo "$1" >&2
    fi
}

open_browser() {
    if command -v xdg-open &>/dev/null; then
        xdg-open "$URL" &>/dev/null &
    elif command -v sensible-browser &>/dev/null; then
        sensible-browser "$URL" &>/dev/null &
    else
        notify "NetDiag is running at $URL but no browser launcher was found."
    fi
}

wait_for_server() {
    for _ in $(seq 1 30); do
        if curl -fsS "$URL/api/status" -o /dev/null 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# Already running? Just open the browser.
if curl -fsS "$URL/api/status" -o /dev/null 2>/dev/null; then
    open_browser
    exit 0
fi

if [ ! -f "$NETDIAG_PY" ]; then
    notify "NetDiag: could not find netdiag.py at $NETDIAG_PY"
    exit 1
fi

if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
    notify "NetDiag: the web UI needs fastapi and uvicorn. Run: pip install --user fastapi uvicorn (or 'make install-gui')"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
nohup python3 "$NETDIAG_PY" --gui --port "$PORT" >>"$LOGFILE" 2>&1 &

if wait_for_server; then
    open_browser
else
    notify "NetDiag server did not start. See $LOGFILE for details."
    exit 1
fi
