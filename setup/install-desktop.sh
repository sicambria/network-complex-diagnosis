#!/bin/bash
# NetDiag — Desktop integration installer
# Installs start menu entries and desktop icons for the current user.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SCRIPT="$PROJECT_DIR/netdiag.py"
SVG_PATH="$SCRIPT_DIR/netdiag.svg"

echo "=== NetDiag Desktop Integration ==="
echo ""

case "$(uname -s)" in
Linux)
    echo "[Linux] Installing start menu entry..."
    mkdir -p "$HOME/.local/share/applications"
    mkdir -p "$HOME/.local/share/icons/hicolor/64x64/apps"

    chmod +x "$SCRIPT_DIR/linux/netdiag-launcher.sh"

    DESKTOP_FILE="$HOME/.local/share/applications/netdiag.desktop"
    sed "s|%PROJECT_DIR%|$PROJECT_DIR|g" "$SCRIPT_DIR/linux/netdiag.desktop" > "$DESKTOP_FILE"

    if [ -f "$SVG_PATH" ]; then
        cp "$SVG_PATH" "$HOME/.local/share/icons/hicolor/64x64/apps/netdiag.svg"
    else
        sed -i 's|Icon=.*|Icon=utilities-terminal|' "$DESKTOP_FILE"
    fi

    chmod 644 "$DESKTOP_FILE"
    echo "  Start menu: $DESKTOP_FILE"

    # Desktop shortcut
    DESKTOP_ICON="$HOME/Desktop/netdiag.desktop"
    if [ -d "$HOME/Desktop" ]; then
        cp "$DESKTOP_FILE" "$DESKTOP_ICON"
        chmod 755 "$DESKTOP_ICON"
        echo "  Desktop icon: $DESKTOP_ICON"
        gio set "$DESKTOP_ICON" metadata::trusted true 2>/dev/null || true
    fi

    # Update desktop database
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    fi
    echo "[OK] Linux desktop integration complete."
    ;;

Darwin)
    echo "[macOS] Installing .app bundle..."
    APP_DIR="$HOME/Applications/NetDiag.app"
    mkdir -p "$APP_DIR/Contents/MacOS"
    mkdir -p "$APP_DIR/Contents/Resources"

    cp "$SCRIPT_DIR/macos/NetDiag.app/Contents/Info.plist" "$APP_DIR/Contents/Info.plist"
    cp "$SCRIPT_DIR/macos/NetDiag.app/Contents/MacOS/NetDiag" "$APP_DIR/Contents/MacOS/NetDiag"
    chmod 755 "$APP_DIR/Contents/MacOS/NetDiag"

    if [ -f "$SVG_PATH" ]; then
        cp "$SVG_PATH" "$APP_DIR/Contents/Resources/icon.svg"
    fi

    # Add to Dock (optional)
    if command -v dockutil &>/dev/null; then
        dockutil --add "$APP_DIR" 2>/dev/null || true
    fi

    echo "  .app bundle: $APP_DIR"
    # Also add alias to Desktop
    if [ -d "$HOME/Desktop" ]; then
        osascript -e "tell application \"Finder\" to make alias file to POSIX file \"$APP_DIR\" at POSIX file \"$HOME/Desktop\"" 2>/dev/null || true
    fi
    echo "[OK] macOS desktop integration complete."
    ;;

MINGW*|MSYS*|CYGWIN*)
    echo "[Windows] Installing Start Menu shortcut..."
    POWERSHELL_CMD='
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\NetDiag.lnk")
        $Shortcut.TargetPath = "powershell.exe"
        $Shortcut.Arguments = "-WindowStyle Hidden -File \"'"$PROJECT_DIR"'\setup\windows\netdiag.ps1\""
        $Shortcut.WorkingDirectory = "'"$PROJECT_DIR"'"
        $Shortcut.Description = "NetDiag — Internet Diagnostics Suite"
        $Shortcut.Save()
    '
    powershell -Command "$POWERSHELL_CMD" 2>/dev/null || echo "  Start menu shortcut could not be created (run as normal user)."

    # Desktop shortcut
    DESKTOP_PS='
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut("$env:USERPROFILE\Desktop\NetDiag.lnk")
        $Shortcut.TargetPath = "powershell.exe"
        $Shortcut.Arguments = "-WindowStyle Hidden -File \"'"$PROJECT_DIR"'\setup\windows\netdiag.ps1\""
        $Shortcut.WorkingDirectory = "'"$PROJECT_DIR"'"
        $Shortcut.Save()
    '
    powershell -Command "$DESKTOP_PS" 2>/dev/null || echo "  Desktop shortcut could not be created."
    echo "[OK] Windows desktop integration complete."
    ;;

*)
    echo "Unknown platform: $(uname -s)"
    exit 1
    ;;
esac

echo ""
echo "NetDiag added to your start menu and desktop."
echo "Double-click the icon to launch the web UI."
