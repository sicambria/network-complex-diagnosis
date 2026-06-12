#!/bin/bash
# NetDiag — uninstall/recovery script
# Removes everything installed by install.sh or make install.
set -e

LOG_FILE="/tmp/netdiag_uninstall_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== NetDiag Uninstall ==="
echo "Log: $LOG_FILE"
echo ""

SILENT=false
[[ "$1" == "--silent" ]] && SILENT=true

confirm() {
    if ! $SILENT; then
        echo ""
        echo -n "$1 [y/N] "
        read -r REPLY
        if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
            echo "Skipped."
            return 1
        fi
    fi
    return 0
}

# 1. Remove symlink
if [ -L /usr/local/bin/netdiag ] || [ -f /usr/local/bin/netdiag ]; then
    if confirm "Remove /usr/local/bin/netdiag symlink?"; then
        sudo rm -f /usr/local/bin/netdiag
        echo "[OK] Removed /usr/local/bin/netdiag"
    fi
fi

# 2. Remove history directory
HISTDIR="$HOME/.netdiag"
if [ -d "$HISTDIR" ]; then
    if confirm "Remove $HISTDIR (diagnostic history)?"; then
        rm -rf "$HISTDIR"
        echo "[OK] Removed $HISTDIR"
    fi
fi

# 3. Uninstall system packages (Linux apt only)
if [ "$(uname -s)" = "Linux" ] && command -v apt &>/dev/null; then
    PKGS=(iputils-ping iproute2 iw mtr-tiny speedtest-cli ethtool iperf3)
    INSTALLED=$(dpkg-query -W -f='${Package} ${Status}\n' "${PKGS[@]}" 2>/dev/null | grep " installed$" | awk '{print $1}' || true)
    if [ -n "$INSTALLED" ]; then
        PKG_LIST=$(echo "$INSTALLED" | tr '\n' ' ')
        if confirm "Remove system packages: $PKG_LIST?"; then
            sudo apt remove -y $PKG_LIST
            sudo apt autoremove -y
            echo "[OK] Removed system packages"
        fi
    else
        echo "[SKIP] No NetDiag system packages installed by us"
    fi
fi

# 4. Uninstall Python packages
for pkg in fastapi uvicorn; do
    if python3 -c "import $pkg" 2>/dev/null; then
        if confirm "Uninstall Python package '$pkg'?"; then
            python3 -m pip uninstall -y "$pkg" 2>/dev/null || \
            python3 -m pip uninstall -y --break-system-packages "$pkg" 2>/dev/null || \
            echo "[WARN] Could not uninstall $pkg"
            echo "[OK] Uninstalled $pkg"
        fi
    fi
done

# 5. Remove systemd user service
SERVICE_FILE="$HOME/.config/systemd/user/netdiag.service"
if [ -f "$SERVICE_FILE" ]; then
    if confirm "Remove systemd user service?"; then
        systemctl --user stop netdiag 2>/dev/null || true
        systemctl --user disable netdiag 2>/dev/null || true
        rm -f "$SERVICE_FILE"
        systemctl --user daemon-reload
        echo "[OK] Removed systemd user service"
    fi
fi

echo ""
echo "=== Uninstall complete ==="
echo "Manual cleanup (if desired):"
echo "  rm -rf internet_diagnostics/  # output files in project directory"
echo "  rm -rf .venv                   # virtual environment"
echo ""
