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
├── reliability_test()      — intermittent-connection detector: fresh cache-defeating HTTPS conns, per-phase timing (DNS/TCP/TLS/TTFB), first-vs-retry, IPv4/IPv6 + concurrency A/B, localized verdict (Plan B: urllib total-time). `label=` param namespaces its progress callbacks.
├── wellknown_sites_test()  — intermittent-issue REPRODUCER: reliability_test pointed at ~100 WELLKNOWN_SITES favicons, ~2.5 min duration, IPv4, high concurrency (recreates "page with many small images"). wellknown_verdict() names worst-offending sites.
├── reconcile_icmp()        — cross-references ICMP ping loss vs TCP/HTTP/DNS success to the SAME hosts; flags ICMP rate-limiting (1.1.1.1/8.8.8.8/9.9.9.9 etc.) so phantom "95% loss" is never reported as packet loss. Cached on results["icmp_reconciliation"]; get_reconciliation() reads-or-computes.
├── mtu_probe()             — path MTU discovery via ping with varying packet size
├── classify_ping()         — loss→bad_loss→some_loss→bad_latency_spikes→latency_spikes→high_jitter→clean (pure; the internet verdict routes through reconcile_icmp, not raw classify_ping)
├── diagnose()              — 5-layer rule engine: physical→wifi→gateway→ISP→internet
├── health_score()          — 0-100 composite from all layers (internet score ignores ICMP-filtered loss)
├── full_diagnostic()       — orchestrates all probes in sequence
├── write_report() / csv    — report.txt + diagnostics.json + CSVs
├── build_isp_report()      — detailed plain-text evidence report for ISP tickets (isp_report.txt; export format=isp). Leads with the ICMP-vs-TCP method note; separates local vs upstream; side-by-side ICMP/TCP table.
├── build_parser() / CLI    — argparse + default args (--wellknown-test, --isp-report)
├── Server (FastAPI)         — /api/export/{file}?format=json|csv|html|isp + the run/status/monitor/history routes
└── Frontend (embedded HTML) — renders diagnose() output verbatim (Findings = interpretation, Measurements = raw values). NEVER recompute severity in JS — see "Single source of truth" below.
```

### Diagnosis schema & single source of truth (IMPORTANT)
Each diagnosis dict is `{layer, severity, title, detail, fix}` PLUS optional
`facts` (list of measured strings), `assumption` (the inference + why), and
`confidence` ("high"/"medium"/"low"). All consumers (console, report.txt, HTML
export, ISP report, web UI) render these uniformly — separating measured fact
from inference is a product requirement, not decoration.

`diagnose()` is the ONLY severity authority. The web frontend (`ndFindingsHtml`/
`ndMeasurementsHtml`) renders its output directly. Do NOT re-derive severity in
JavaScript — the old per-card recompute disagreed with `diagnose()` and produced
contradictions (a red ✗ card footed with "No specific fix needed", an ISP-route
✗ when the trace was clean). If you add a probe, emit a diagnosis for it and let
the UI render that; never add a parallel JS severity rule.

### ICMP-vs-TCP reconciliation (the "really 95% packet loss?" fix)
A genuine high packet-loss rate CANNOT coexist with a near-100% TCP handshake
rate (a handshake needs several consecutive round trips). Public resolvers
(1.1.1.1/8.8.8.8/9.9.9.9, set `ICMP_RATE_LIMITERS`) rate-limit ICMP echo, so high
ping "loss" to them is a measurement artifact, not packet loss, when TCP/HTTP to
the same host succeed. `reconcile_icmp()` encodes this per-host (direct TCP match)
and globally (TCP/HTTP works + DNS resolves). The SAME rule applies to MTR: loss
at a middle hop that clears by the destination is that router rate-limiting its
own ICMP — only loss reaching the final hop is real. Watch the `(x or default)`
trap: `failure_pct` of 0 is falsy, so use explicit `is None` checks.

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
4. **ISP** — MTR per-hop loss localization, but only loss that PERSISTS to the destination hop is real (mid-hop loss that clears = ICMP rate-limiting), bufferbloat ratio
5. **Internet** — external ping reconciled against TCP/HTTP (ICMP rate-limiting detection), DNS failures, TCP connect failures, iPerf3 retransmits/inconclusive, speedtest, small-image fetch (NOT a bandwidth test — low Mbps with 0 failures is clean), HTTP intermittent failures

Each diagnosis includes: layer, severity (clean/info/warning/bad), title, detail, fix — plus optional `facts` (measured), `assumption` (inference + why), `confidence`.

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
| Reliability | manual `socket`+`ssl` per-phase timing | `urllib` total-time (no phase breakdown) | — |
| 100-site reproducer | `wellknown_sites_test` (reuses reliability_test over ~100 favicons) | inherits reliability_test's urllib Plan B | — |

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
- **Setup must fully work first time.** Every install/launch path (`make` targets, scripts, the snippets in these docs) must succeed from a clean checkout on a fresh machine — no manual venv/dep steps assumed. This host's system Python is PEP-668 externally-managed, so a system-wide `pip install` is blocked: GUI/test deps (`fastapi`, `uvicorn`, `httpx`, `pytest`) go in `.venv`, never system-wide. Prove it from a clean state (`rm -rf .venv` then the documented path) before claiming done.
- **Never blind-kill by port.** `kill $(lsof -ti:8080)` murders whatever holds the port — often an unrelated dev server (e.g. another project on :8080). Always scope kills to netdiag (`pkill -f "netdiag.py.*--gui"`) and check what is listening before touching a port.
- **Capture operational insights into AGENTS.md and CLAUDE.md.** When a session surfaces a non-obvious lesson — a foot-gun, an environment gotcha (PEP-668, missing test dep), a setup fix, a process-safety rule — record it in these guides so it does not recur. Treat that as part of finishing the task, not optional.

- **Server restart**: After any code change, kill the old GUI process and start a fresh one:
  ```bash
  pkill -f "netdiag.py.*--gui" 2>/dev/null; sleep 0.5   # scoped: never kill unrelated servers on :8080
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
  pkill -f "netdiag.py.*--gui" 2>/dev/null; sleep 0.5   # scoped: never kill unrelated servers on :8080
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
- Never blind-kill a port (`kill $(lsof -ti:PORT)`) — scope process kills to netdiag (`pkill -f "netdiag.py.*--gui"`) so unrelated servers survive
- Never claim setup works without proving it from a clean state (`rm -rf .venv`)
- Never add emoji to code or docs
- Never commit without explicit request
- Never commit personal data, local paths, hostnames, or secrets
