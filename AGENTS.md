# AGENTS.md — Network Complex Diagnosis

## Project Overview

Platform-agnostic internet diagnostics suite (`netdiag.py`) that isolates local network issues from ISP/upstream problems, detects WiFi signal problems, interface errors, bufferbloat, and per-hop routing issues. Single-file Python 3.12 script. CLI mode: stdlib only (zero deps). GUI mode: 2 optional pip deps (fastapi + uvicorn).

Outputs machine-readable JSON + CSV in `internet_diagnostics/` and web GUI at `http://localhost:8080`.

Predecessor: `nettest.py` (1058 lines, Linux-only, simpler).

## Code Conventions

- **Python 3.12+**, stdlib only for CLI mode — `pip install fastapi uvicorn` optional for GUI
- No type hints, no docstrings, minimal comments
- `snake_case` for functions/variables, `SCREAMING_SNAKE_CASE` for constants
- Functions return plain `dict` everywhere (no dataclasses, no Pydantic)
- Subprocess calls via `run_cmd()` wrapper with 30s default timeout
- Output files go to `args.outdir` (default `internet_diagnostics/`)
- History sessions stored in `~/.netdiag/session_*.json`
- Console progress via `print(..., flush=True)`
- Platform-agnostic: all probes have Linux/macOS/Windows branches with graceful fallback

## Architecture

```
netdiag.py (~3340 lines, single file)
├── Platform detection      — IS_LINUX / IS_MACOS / IS_WINDOWS constants
├── run_cmd() / has_tool()  — subprocess wrapper + tool detection
├── ping()                  — platform-adaptive ping_command, parse_rtt_ms, ping_once, ping_burst
├── percentile/series_stats — statistics helpers (percentile, series_stats, jitter_ms, clean_float)
├── dns_test()              — socket.getaddrinfo() latency series
├── tcp_test()              — socket.create_connection() latency series
├── detect_gateway()        — ip route / route -n get / netstat -rn / procfs fallback
├── get_default_interface() — ip route / route -n get
├── detect_wireless_interface() — iw dev / airport / netsh wlan interface detection
├── interface_stats()       — ip -s link / ifconfig / netstat -e / sysfs fallback
├── wifi_info()             — iw survey dump / airport -I / netsh wlan / procfs fallback
├── tcp_socket_stats()      — ss -itp / nettop -J / netstat -s / procfs fallback
├── mtr_test()              — mtr -r / traceroute -n / tracert / native ping TTL sweep
├── speedtest_result()      — Ookla speedtest / speedtest-cli
├── iperf3_test()           — iperf3 -c server -t 10 -J
├── bufferbloat_test()      — tc -s qdisc + iperf3 concurrent ping (Linux enhanced)
├── ethtool_info()          — ethtool speed/duplex/link detection (Linux)
├── download_images_test()  — HTTP download latency (image URLs over time)
├── http_latency_test()     — HTTP request latency to multiple endpoints
├── mtu_probe()             — path MTU discovery via ping with varying packet size
├── classify_ping()         — loss→bad_loss→some_loss→bad_latency_spikes→latency_spikes→high_jitter→clean
├── diagnose()              — 5-layer rule engine: physical→wifi→gateway→ISP→internet
├── health_score()          — 0-100 composite from all layers
├── full_diagnostic()       — orchestrates all probes in sequence
├── write_report() / csv    — report.txt + diagnostics.json + CSVs
├── build_parser() / CLI    — argparse + default args
├── Server (FastAPI)         — 8 routes: /, /api/status, /api/monitor, /api/run, /api/history, /api/session/, /api/export/, /api/results/
└── Frontend (embedded HTML) — Dashboard + Troubleshoot + Live Monitor + History + Reports + About (Chart.js SPA)
```

### Classification Thresholds (`classify_ping`)
| Condition | Classification |
|-----------|---------------|
| loss >= 5% | `bad_loss` |
| 1% <= loss < 5% | `some_loss` |
| p95 >= 300ms | `bad_latency_spikes` |
| p95 >= 150ms | `latency_spikes` |
| jitter >= 80ms | `high_jitter` |
| else | `clean` |

### Diagnostic Layers (`diagnose`)
1. **Physical** — interface RX/TX errors, drops, overruns, carrier changes, ethtool duplex/link
2. **WiFi** — signal dBm, channel utilization, noise
3. **Gateway** — ping stability, TCP retransmits via ss, cross-correlate with WiFi
4. **ISP** — MTR per-hop loss localization (hop 1-2 = modem, hop 3+ = ISP), bufferbloat ratio
5. **Internet** — external ping, DNS failures, TCP connect failures, iPerf3 retransmits, speedtest

Each diagnosis includes: layer, severity (clean/info/warning/bad), title, detail, fix recommendation.

### Health Score (0-100)
Weighted composite: interface 10%, wifi 15%, gateway 25%, internet 25%, dns 10%, tcp 5%, bufferbloat 10%.

### Graceful Degradation (Plan B)

Every probe has a fallback chain if the primary tool is missing or fails:

| Probe | Primary | Plan B | Plan C |
|-------|---------|--------|--------|
| Ping | system `ping` | TCP connect RTT (`socket.create_connection`) | — |
| Gateway | `ip route` / `route -n get` / `netstat -rn` | `/proc/net/route` (Linux stdlib) | — |
| Interface stats | `ip -s link` / `ifconfig` / `netstat -e` | `/sys/class/net/*/statistics/*` (Linux stdlib) | — |
| WiFi info | `iw dev` / `airport` / `netsh wlan` | `/proc/net/wireless` (Linux stdlib) | — |
| TCP sockets | `ss -itp` / `nettop -J` / `netstat -s` | `/proc/net/tcp` (Linux stdlib, connection count only) | — |
| MTR | `mtr -r` | `traceroute -n` | Native `ping -t` TTL sweep (all platforms) |
| Bufferbloat | `tc -s qdisc` + `iperf3` | `iperf3` concurrent ping (non-Linux) | — |
| Ethtool | `ethtool` | — | — |
| iPerf3 | `iperf3` | — | — |
| Speedtest | `speedtest --format=json` | `speedtest-cli --json` | — |

Plan B probes use only stdlib (`open()`, `socket`) — no external CLI tools required. This ensures basic functionality even in minimal environments (containers, restricted shells, fresh systems without tool installation).

### Platform Probing
| Probe | Linux | macOS | Windows |
|-------|-------|-------|---------|
| Ping | ping -c 1 -W 2 | ping -c 1 -t 2 | ping -n 1 -w 2000 |
| Gateway | ip route / procfs | route -n get default | netstat -rn |
| Interface stats | ip -s link / sysfs | ifconfig | netstat -e |
| WiFi info | iw dev / procfs | airport -I | netsh wlan |
| TCP sockets | ss -itp / procfs | nettop -J tcp | netstat -s |
| MTR | mtr / traceroute / ping TTL | mtr / ping TTL | tracert / ping TTL |
| Bufferbloat | tc + iperf3 | iperf3 fallback | iperf3 fallback |
| Ethtool | ethtool | — | — |
| iPerf3 | iperf3 | iperf3 | iperf3 |
| Speedtest | speedtest --format=json | same | same |

## Commands

```bash
# One-click setup
make install                 # system pkgs + pip + symlink + desktop icon
bash install.sh              # same, shell-only (interactive)

# Desktop integration (start menu + desktop icon for all platforms)
make desktop-install
bash setup/install-desktop.sh

# Virtual env
make venv

# Tests (92 tests, pure functions + mock subprocess)
make test
python3 -m pytest tests/ -v
make test ARGS="-k diagnose"  # run specific test class
bash test_e2e.sh              # full end-to-end verification

# Lint (syntax check only)
make lint

# Clean output artifacts
make clean

# Web GUI
make gui
python3 netdiag.py --gui --port 3000

# Daemon mode
make daemon
python3 netdiag.py --daemon

# Systemd service (Linux)
make install-service
systemctl --user start netdiag

# Uninstall / recovery
bash uninstall.sh             # interactive removal
bash uninstall.sh --silent    # auto-confirm removal
```

## Test Architecture

```
tests/
├── conftest.py           — sys.path setup for netdiag import
├── test_parsers.py       — ping output parsing, classify_ping, procfs parsers (23 tests)
├── test_stats.py         — percentile, series_stats, jitter_ms, clean_float (16 tests)
├── test_diagnose.py      — diagnose() 5-layer rules, health_score (23 tests)
├── test_ping.py          — ping_command platform branches, ping_once mocks (14 tests)
├── test_platform.py      — detect_gateway, get_default_interface per platform (12 tests)
└── test_server.py        — FastAPI route presence (4 tests, requires fastapi)
```

All tests use `unittest.mock` to avoid real subprocess/socket calls. Server tests skip if fastapi not installed.

**End-to-end**: `test_e2e.sh` — runs syntax checks, pytest, CLI diagnostic, GUI server test, install script validation, and uninstall script validation. Writes timestamped log to `/tmp/netdiag_e2e_*.log`.

## Makefile Targets

| Target | What it does |
|--------|-------------|
| `install` | Full one-click: system deps + pip deps + symlink + desktop icon |
| `install-sys` | System deps (apt/dnf/pacman/brew) |
| `install-gui` | fastapi + uvicorn via pip |
| `desktop-install` | Start menu + desktop icon (all platforms) |
| `venv` | Python virtual environment with dev deps |
| `test` | Run pytest (pass ARGS="-k diagnose" to filter) |
| `lint` | Syntax check via py_compile |
| `run` | Plain CLI mode |
| `gui` | Web UI mode |
| `daemon` | Continuous monitoring + web UI |
| `install-service` | User systemd service for daemon auto-start |
| `clean` | Remove output dirs and caches |

## Workflow Rules

- Always test tasks end-to-end before returning — run lint, typecheck, pytest, or applicable verification. Do not return half-finished work.
- If a task involves code changes, verify with the relevant test suite and fix any failures before reporting done.
- Ban "should work" / "should be fine" / speculative language. There is evidence or there isn't. Test it, show the evidence, or don't claim it.

- **Server restart**: After any code change, kill the old GUI process and start a fresh one:
  ```bash
  kill $(lsof -ti:8080) 2>/dev/null; sleep 0.5
  python3 netdiag.py --gui --port 8080 &
  sleep 2
  ```
  Verify endpoints respond correctly:
  ```bash
  curl -s http://localhost:8080/api/status | python3 -m json.tool | head -10
  curl -s http://localhost:8080/api/monitor | python3 -m json.tool
  curl -s http://localhost:8080/ | head -5  # frontend loads
  curl -s http://localhost:8080/api/reports | python3 -m json.tool
  ```

- **Frontend changes**: After modifying `INDEX_HTML` in `netdiag.py`, delete `templates/index.html` and restart the server so it regenerates from source:
  ```bash
  rm -f templates/index.html
  kill $(lsof -ti:8080) 2>/dev/null; sleep 0.5
  python3 netdiag.py --gui --port 8080 &
  sleep 2
  curl -s http://localhost:8080/ | head -5  # verify fresh template served
  ```

## Git Guardrails

Pre-commit and pre-push hooks in `.githooks/` block commits containing:
- Absolute local paths (`/home/`, `/Users/`, `C:\Users\`)
- Hostname (`venividivici`) or username (`arsvivendi`)
- API tokens / secrets (`gho_`, `ghp_`, `sk-`, `AKIA`, etc.)
- Unintentionally large text files (>1 MB)

The hooks are auto-activated via `git config core.hooksPath .githooks` (set globally or per-repo).

To bypass temporarily (e.g. when you mean to commit a legitimate reference):
```bash
git commit --no-verify -m "message"
git push --no-verify
```

## No-Go Rules

- Never add pip dependencies — keep stdlib-only for the core script (fastapi/uvicorn optional for GUI)
- Never add type hints or docstrings unless asked
- Never create new scripts/files without explicit request
- Never add emoji to code or docs
- Never commit without explicit request
- Never commit personal data, local paths, hostnames, or secrets
