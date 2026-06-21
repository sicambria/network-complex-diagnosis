#!/usr/bin/env bash
#
# dev-setup.sh — one-shot, idempotent setup of the COMPLETE NetDiag dev + test
# environment. Installs everything needed to run every test (unit, integration,
# requirements e2e, and the Playwright browser e2e) and to exercise the real
# probes rather than only their stdlib fallbacks. Safe to re-run.
#
# What it does:
#   1. System diagnostic CLI tools (ping/ip/iw/mtr/traceroute/speedtest/ethtool/iperf3)
#   2. .venv with all dev + test Python deps (fastapi, uvicorn, pytest, httpx, playwright)
#   3. Playwright browser (chromium) + its OS libraries
#   4. Node check (used for frontend JS syntax checks)
#   5. Verification + a final status table
#
# Usage:
#   bash setup/dev-setup.sh            # full setup
#   bash setup/dev-setup.sh --check    # verify only, install nothing
#   bash setup/dev-setup.sh --no-sudo  # skip steps that need root (system pkgs / browser libs)
#
# SPDX-License-Identifier: AGPL-3.0-only

set -uo pipefail

# ── config ─────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv"
VPY="$VENV/bin/python"
PYBOOT="${PYTHON:-python3}"
CHECK_ONLY=0
USE_SUDO=1

for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --no-sudo) USE_SUDO=0 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ── pretty output ──────────────────────────────────────────────────────────
if [ -t 1 ]; then G='\033[0;32m'; Y='\033[0;33m'; R='\033[0;31m'; B='\033[0;36m'; N='\033[0m'
else G=''; Y=''; R=''; B=''; N=''; fi
info()  { printf "${B}[dev-setup]${N} %s\n" "$*"; }
ok()    { printf "  ${G}OK${N}   %s\n" "$*"; }
warn()  { printf "  ${Y}WARN${N} %s\n" "$*"; }
fail()  { printf "  ${R}FAIL${N} %s\n" "$*"; }
have()  { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$USE_SUDO" -eq 1 ] && [ "$(id -u)" -ne 0 ] && have sudo; then SUDO="sudo"; fi

OS="$(uname -s)"

# ── 1. system diagnostic tools ─────────────────────────────────────────────
install_system_tools() {
  info "1/5 System diagnostic tools"
  if [ "$CHECK_ONLY" -eq 1 ]; then warn "skipped (--check)"; return; fi
  if [ "$OS" = "Linux" ]; then
    if have apt; then
      $SUDO apt-get update -qq 2>/dev/null || true
      $SUDO apt-get install -y -qq iputils-ping iproute2 iw mtr-tiny traceroute \
        speedtest-cli ethtool iperf3 2>/dev/null && ok "apt packages installed" \
        || warn "some apt packages failed (need root? offline?)"
    elif have dnf; then
      $SUDO dnf install -y iputils iproute iw mtr traceroute speedtest-cli ethtool iperf3 \
        >/dev/null 2>&1 && ok "dnf packages installed" || warn "some dnf packages failed"
    elif have pacman; then
      $SUDO pacman -S --noconfirm iputils iproute2 iw mtr traceroute speedtest-cli ethtool iperf3 \
        >/dev/null 2>&1 && ok "pacman packages installed" || warn "some pacman packages failed"
    else
      warn "no supported package manager (apt/dnf/pacman) — install tools manually"
    fi
  elif [ "$OS" = "Darwin" ]; then
    if have brew; then
      brew install mtr speedtest-cli iperf3 >/dev/null 2>&1 && ok "brew packages installed" \
        || warn "some brew packages failed"
    else
      warn "Homebrew not found — install from https://brew.sh then re-run"
    fi
  else
    warn "unsupported OS '$OS' for auto tool install (Windows: install tools via choco/winget)"
  fi
}

# ── 2. venv + python dev/test deps ─────────────────────────────────────────
setup_python() {
  info "2/5 Python venv + dev/test dependencies"
  if [ "$CHECK_ONLY" -eq 1 ]; then warn "skipped (--check)"; return; fi
  if [ ! -x "$VPY" ]; then
    "$PYBOOT" -m venv "$VENV" && ok "created $VENV" || { fail "could not create venv"; return; }
  else
    ok "venv exists"
  fi
  "$VPY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
    "$VPY" -m pip install --quiet -r "$PROJECT_DIR/requirements-dev.txt" \
      && ok "requirements-dev.txt installed" || fail "pip install requirements-dev.txt failed"
  else
    "$VPY" -m pip install --quiet fastapi uvicorn pytest httpx playwright \
      && ok "core dev deps installed" || fail "pip install failed"
  fi
}

# ── 3. playwright browser + OS libs ────────────────────────────────────────
install_playwright_browser() {
  info "3/5 Playwright browser (chromium) + OS libraries"
  if [ "$CHECK_ONLY" -eq 1 ]; then warn "skipped (--check)"; return; fi
  if ! "$VPY" -c "import playwright" >/dev/null 2>&1; then
    warn "playwright python package missing — skipping browser install (step 2 must succeed first)"
    return
  fi
  # --with-deps installs the OS libraries (needs root); fall back to browser-only.
  if [ -n "$SUDO" ] || [ "$(id -u)" -eq 0 ]; then
    $SUDO "$VPY" -m playwright install-deps chromium >/dev/null 2>&1 && ok "browser OS libs installed" \
      || warn "playwright install-deps failed (browser may still run headless)"
  else
    warn "no sudo — skipping browser OS libs (run with sudo if the browser fails to launch)"
  fi
  "$VPY" -m playwright install chromium >/dev/null 2>&1 && ok "chromium browser ready" \
    || fail "playwright install chromium failed"
}

# ── 4. node (frontend JS syntax checks) ────────────────────────────────────
check_node() {
  info "4/5 Node.js (optional — frontend JS syntax checks)"
  if have node; then ok "node $(node --version)"; else
    warn "node not found — 'make check-js' will be skipped (install Node 18+ to enable)"
  fi
}

# ── 5. verification ────────────────────────────────────────────────────────
SUMMARY=""
record() { SUMMARY="${SUMMARY}$(printf '  %-28s %s' "$1" "$2")\n"; }

verify() {
  info "5/5 Verification"
  # diagnostic tools
  local missing=""
  for t in ping ip iw mtr traceroute ethtool iperf3; do
    if have "$t"; then record "tool: $t" "${G}present${N}"; else record "tool: $t" "${Y}missing${N}"; missing="$missing $t"; fi
  done
  if have speedtest || have speedtest-cli; then record "tool: speedtest" "${G}present${N}"; else record "tool: speedtest" "${Y}missing${N}"; fi
  # python deps
  if [ -x "$VPY" ]; then
    for mod in fastapi uvicorn pytest httpx playwright; do
      if "$VPY" -c "import $mod" >/dev/null 2>&1; then record "py: $mod" "${G}importable${N}"; else record "py: $mod" "${R}MISSING${N}"; fi
    done
    # Launch chromium the same way the browser e2e does: normal provisioning,
    # falling back to a cached browser binary (handles too-new OSes Playwright
    # has no published build for).
    if "$VPY" -c "import playwright" >/dev/null 2>&1 && PYTHONPATH="$PROJECT_DIR" "$VPY" - <<'PY' >/dev/null 2>&1
from playwright.sync_api import sync_playwright
from tests.server_helpers import find_cached_chromium
with sync_playwright() as p:
    try:
        b = p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
    except Exception:
        exe = find_cached_chromium()
        assert exe, "no cached chromium"
        b = p.chromium.launch(headless=True, executable_path=exe,
                              args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
    b.close()
PY
    then record "playwright: chromium launch" "${G}works${N}"; else record "playwright: chromium launch" "${Y}unavailable (OS has no PW build + none cached)${N}"; fi
  else
    record "venv python" "${R}MISSING${N}"
  fi
  # core CLI works zero-dep on system python
  if "$PYBOOT" "$PROJECT_DIR/netdiag.py" --version >/dev/null 2>&1; then record "CLI (zero-dep)" "${G}runs${N}"; else record "CLI (zero-dep)" "${R}FAIL${N}"; fi
  if have node; then record "node (js checks)" "${G}present${N}"; else record "node (js checks)" "${Y}missing${N}"; fi
}

# ── run ────────────────────────────────────────────────────────────────────
info "NetDiag full dev+test environment setup (OS=$OS, sudo=${SUDO:-none}, check-only=$CHECK_ONLY)"
install_system_tools
setup_python
install_playwright_browser
check_node
verify

printf "\n${B}=== Environment summary ===${N}\n"
printf "$SUMMARY"
printf "\n${B}Run the full test suite:${N}\n"
printf "  make test-all       # everything incl. Playwright browser e2e\n"
printf "  make test           # unit + integration (no browser)\n"
printf "  make check-js       # frontend JS syntax check (needs node)\n\n"
