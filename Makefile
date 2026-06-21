.PHONY: all install install-sys install-gui desktop-install test venv gui daemon run clean lint \
        e2e e2e-browser e2e-stress e2e-smoke install-e2e e2e-req dev-setup test-all check-js

PYTHON := python3
SCRIPT := netdiag.py
VENV := .venv
# GUI/daemon need fastapi+uvicorn. On PEP-668 (externally-managed) systems a
# system-wide pip install is blocked, so GUI mode runs from the venv python.
GUIPY := $(VENV)/bin/python
HISTDIR := $(HOME)/.netdiag

all: install

# -- One-click setup -----------------------------------------------------------

install: install-sys install-gui desktop-install
	chmod +x $(SCRIPT)
	mkdir -p $(HISTDIR)
	@echo ""
	@echo "NetDiag ready. Usage:"
	@echo "  $(PYTHON) $(SCRIPT)              # CLI diagnostic"
	@echo "  $(PYTHON) $(SCRIPT) --gui        # web UI at http://localhost:8080"
	@echo "  make gui                         # same"
	@echo "  make test                        # run tests"

install-sys:
ifeq ($(shell uname -s),Linux)
	@echo "[install-sys] Installing system dependencies..."
	command -v apt >/dev/null && sudo apt update -qq && sudo apt install -y -qq \
		iputils-ping iproute2 iw mtr-tiny speedtest-cli ethtool iperf3 2>/dev/null || true
	command -v dnf >/dev/null && sudo dnf install -y iputils iproute iw mtr speedtest-cli ethtool iperf3 2>/dev/null || true
	command -v pacman >/dev/null && sudo pacman -S --noconfirm iputils iproute2 iw mtr speedtest-cli ethtool iperf3 2>/dev/null || true
else ifeq ($(shell uname -s),Darwin)
	@echo "[install-sys] Installing macOS dependencies..."
	command -v brew >/dev/null && brew install mtr speedtest-cli iperf3 2>/dev/null || true
endif

install-gui:
	@echo "[install-gui] Installing GUI deps into venv ($(VENV))..."
	test -x $(GUIPY) || $(PYTHON) -m venv $(VENV)
	$(GUIPY) -m pip install --quiet --upgrade pip
	$(GUIPY) -m pip install --quiet fastapi uvicorn
	@$(GUIPY) -c "import fastapi, uvicorn" && echo "  GUI deps ready in $(VENV)"

desktop-install:
	@echo "[desktop] Installing start menu and desktop icon..."
	bash setup/install-desktop.sh
	@echo ""

# -- Virtual environment -------------------------------------------------------

venv:
	$(PYTHON) -m venv $(VENV)
	. $(VENV)/bin/activate && pip install --quiet fastapi uvicorn pytest httpx
	@echo "Virtual env ready: source $(VENV)/bin/activate"

# -- Development ----------------------------------------------------------------

test:
	$(GUIPY) -m pytest tests/ --ignore=tests/test_e2e_browser.py -v $(ARGS)

# One-shot: install EVERYTHING needed to develop and fully test (system tools,
# venv + all dev/test deps, Playwright browser + OS libs, node check).
dev-setup:
	bash setup/dev-setup.sh $(ARGS)

# The complete suite, including the Playwright browser e2e. Requires dev-setup.
test-all:
	$(GUIPY) -m pytest tests/ -v $(ARGS)

# Frontend JS syntax check (classic scripts loaded in order; needs node).
check-js:
	@command -v node >/dev/null 2>&1 || { echo "node not installed — run 'make dev-setup'"; exit 1; }
	@for f in netdiag_core/frontend/js/*.js; do node --check "$$f" && echo "  OK $$f"; done

lint:
	$(PYTHON) -c "import py_compile; py_compile.compile('$(SCRIPT)', doraise=True)"
	$(PYTHON) -m compileall -q netdiag_core && echo "Syntax OK"

run:
	$(PYTHON) $(SCRIPT)

gui: install-gui
	$(GUIPY) $(SCRIPT) --gui

daemon: install-gui
	$(GUIPY) $(SCRIPT) --daemon

clean:
	rm -rf internet_diagnostics/ output/ __pycache__/ .pytest_cache/
	find . -name '*.pyc' -delete
	@echo "Cleaned."

# -- Systemd (Linux daemon) ----------------------------------------------------

SERVICE_FILE := $(HOME)/.config/systemd/user/netdiag.service

install-service:
	mkdir -p $(HOME)/.config/systemd/user
	sed 's|/usr/bin/python3|$(shell which $(PYTHON))|g; s|/path/to/netdiag.py|$(CURDIR)/$(SCRIPT)|g' \
		netdiag.service > $(SERVICE_FILE)
	systemctl --user daemon-reload
	@echo "Service installed. Start with: systemctl --user start netdiag"
	@echo "Enable on login: systemctl --user enable netdiag"
	@echo "View logs: journalctl --user -u netdiag -f"

# -- E2E browser testing (Playwright) -------------------------------------------

install-e2e:
	$(PYTHON) -m pip install -q -r requirements-dev.txt 2>/dev/null || true
	$(PYTHON) -m playwright install chromium --with-deps 2>/dev/null || $(PYTHON) -m playwright install chromium 2>/dev/null || true
	@echo "E2E deps ready."

e2e-browser:
	$(PYTHON) -m pytest tests/test_e2e_browser.py::TestLiveMonitorBrowser -v -s $(ARGS)

e2e-stress:
	$(PYTHON) -m pytest tests/test_e2e_browser.py::TestMonitorServerStress -v $(ARGS)

e2e:
	$(PYTHON) -m pytest tests/test_e2e_browser.py -v -s $(ARGS)

e2e-smoke:
	NETDIAG_MONITOR_DURATION=15 $(PYTHON) -m pytest tests/test_e2e_browser.py -v -s $(ARGS)

e2e-req:
	$(PYTHON) -m pytest tests/test_e2e_requirements.py -v $(ARGS)
