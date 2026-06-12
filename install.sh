#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$SCRIPT_DIR/netdiag.py"
HISTDIR="$HOME/.netdiag"
PLATFORM="$(uname -s)"

echo "=== NetDiag — all-in-one internet diagnostics suite ==="
echo ""

# -- System dependencies -------------------------------------------------------
echo "--- Step 1/4: System dependencies ---"
case "$PLATFORM" in
Linux)
    if command -v apt &>/dev/null; then
        echo "  Debian/Ubuntu: installing packages..."
        sudo apt update -qq 2>/dev/null || true
        sudo apt install -y -qq iputils-ping iproute2 iw mtr-tiny speedtest-cli ethtool iperf3 2>/dev/null || true
    elif command -v dnf &>/dev/null; then
        echo "  Fedora: installing packages..."
        sudo dnf install -y iputils iproute iw mtr speedtest-cli ethtool iperf3 2>/dev/null || true
    elif command -v pacman &>/dev/null; then
        echo "  Arch: installing packages..."
        sudo pacman -S --noconfirm iputils iproute2 iw mtr speedtest-cli ethtool iperf3 2>/dev/null || true
    elif command -v zypper &>/dev/null; then
        echo "  openSUSE: installing packages..."
        sudo zypper install -y iputils iproute2 iw mtr speedtest-cli ethtool iperf3 2>/dev/null || true
    else
        echo "  Warning: no known package manager. Install tools manually."
    fi
    ;;
Darwin)
    echo "  macOS: installing dependencies..."
    if command -v brew &>/dev/null; then
        brew install mtr speedtest-cli iperf3 2>/dev/null || true
    fi
    ;;
MINGW*|MSYS*|CYGWIN*)
    echo "  Windows: install manually:"
    echo "    speedtest-cli: https://www.speedtest.net/apps/cli"
    echo "    iperf3: https://iperf.fr/iperf-download.php"
    ;;
esac

# -- Python GUI dependencies ---------------------------------------------------
echo ""
echo "--- Step 2/4: Python GUI dependencies ---"
python3 -c "import fastapi" 2>/dev/null && echo "  fastapi already installed" || {
    python3 -m pip install --break-system-packages fastapi uvicorn 2>/dev/null ||
    python3 -m pip install --user fastapi uvicorn 2>/dev/null ||
    python3 -m pip install fastapi uvicorn 2>/dev/null ||
    echo "  Could not install fastapi/uvicorn. Run: pip install fastapi uvicorn"
}

# -- Core setup ----------------------------------------------------------------
echo ""
echo "--- Step 3/4: Core setup ---"
if [ ! -f "$SCRIPT" ]; then
    echo "Error: $SCRIPT not found"
    exit 1
fi
chmod +x "$SCRIPT"
mkdir -p "$HISTDIR"

if [ ! -L /usr/local/bin/netdiag ] && [ ! -f /usr/local/bin/netdiag ]; then
    sudo ln -sf "$SCRIPT" /usr/local/bin/netdiag 2>/dev/null || {
        echo "  Could not create /usr/local/bin/netdiag symlink."
        echo "  Run directly: python3 $SCRIPT"
    }
fi

python3 -m pip install pytest -q 2>/dev/null || true

# -- Desktop integration -------------------------------------------------------
echo ""
echo "--- Step 4/4: Desktop integration ---"
echo ""
echo "Would you like to install NetDiag in your start menu and desktop?"
echo "  This creates a clickable icon that launches the web UI."
echo ""
echo -n "Install desktop shortcut? [Y/n] "
read -r REPLY
if [[ "$REPLY" =~ ^[Nn] ]]; then
    echo "  Skipping desktop integration."
else
    SETUP_DIR="$SCRIPT_DIR/setup"
    if [ -f "$SETUP_DIR/install-desktop.sh" ]; then
        bash "$SETUP_DIR/install-desktop.sh"
    else
        echo "  Desktop integration script not found at $SETUP_DIR/install-desktop.sh"
    fi
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "CLI:      netdiag                  # run diagnostic"
echo "          netdiag --count 120      # long test"
echo "GUI:      netdiag --gui            # http://localhost:8080"
echo "Daemon:   netdiag --daemon         # continuous + web UI"
echo "Desktop:  Start menu / desktop icon added"
echo "Tests:    python3 -m pytest tests/"
echo ""
echo "Or run directly: python3 $SCRIPT"
