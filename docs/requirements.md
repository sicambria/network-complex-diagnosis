# NetDiag — Product Requirements

## Overview

NetDiag is a platform-agnostic internet diagnostics suite that isolates local network issues from ISP/upstream problems. It runs from a checkout as the `netdiag_core/` Python package (with `netdiag.py` as a thin entry shim) and provides a 5-layer rule-based diagnosis engine, health scoring, CLI output, JSON/CSV export, and an optional web GUI.

---

## 1. Functional Requirements

### REQ-001: Platform Detection

The system shall:
- Auto-detect the host operating system (Linux, macOS, Windows)
- Branch all probes to platform-appropriate CLI tools or stdlib fallbacks
- Provide graceful degradation when platform-specific tools are unavailable

### REQ-002: Ping Probing

The system shall:
- Execute ICMP ping bursts to configurable target hosts with configurable count, interval, and timeout
- Support IPv4-only (`--ipv4`) and IPv6-only (`--ipv6`) modes
- Parse per-ping RTT in milliseconds from stdout
- Fall back to TCP connect RTT (`socket.create_connection`) when system `ping` is unavailable
- Compute per-host statistics: avg, min, max, p95, p99, jitter, loss percentage
- Classify each ping result as one of: `clean`, `high_jitter`, `latency_spikes`, `bad_latency_spikes`, `some_loss`, `bad_loss`

**Classification thresholds:**

| Condition | Classification |
|-----------|---------------|
| loss >= 5% | `bad_loss` |
| 1% <= loss < 5% | `some_loss` |
| p95 >= 300ms | `bad_latency_spikes` |
| p95 >= 150ms | `latency_spikes` |
| jitter >= 80ms | `high_jitter` |
| otherwise | `clean` |

### REQ-003: Gateway Detection

The system shall:
- Detect the default gateway IP via `ip route` (Linux), `route -n get default` (macOS), `netstat -rn` (Windows)
- Fall back to parsing `/proc/net/route` (Linux) when CLI tools are absent
- Detect the default network interface name via the same chain
- Ping the gateway and include results in the diagnosis

### REQ-004: Interface Statistics

The system shall:
- Collect per-interface RX/TX error counters, dropped packets, overruns, and carrier changes
- Use `ip -s link` (Linux), `ifconfig` (macOS), `netstat -e` (Windows)
- Fall back to `/sys/class/net/*/statistics/` (Linux) when CLI tools are absent

### REQ-005: WiFi Diagnostics

The system shall:
- Detect wireless interfaces via `iw dev` (Linux), `airport -I` (macOS), `netsh wlan` (Windows)
- Report signal strength in dBm, channel utilization percentage, and noise floor
- Fall back to `/proc/net/wireless` (Linux) when CLI tools are absent

### REQ-006: Ethernet Link Info

The system shall:
- Query ethtool for link status, speed, and duplex mode (Linux only)
- Report half-duplex and no-link conditions as severity `bad`

### REQ-007: TCP Socket Statistics

The system shall:
- Collect TCP retransmit counts and per-connection RTT via `ss -itp` (Linux), `nettop -J` (macOS), `netstat -s` (Windows)
- Fall back to parsing `/proc/net/tcp` (Linux) when CLI tools are absent

### REQ-008: MTR / Path Analysis

The system shall:
- Execute per-hop latency and loss analysis to the first configured host
- Use `mtr -r` as primary, `traceroute -n` as Plan B, `tracert` (Windows)
- Fall back to native `ping` TTL-sweep traceroute on all platforms when no traceroute tool is available
- Parse hop-by-hop loss percentages and average latencies
- Support `--no-trace` to skip

### REQ-009: DNS Resolution Latency

The system shall:
- Measure `socket.getaddrinfo()` latency over repeated calls to configurable hosts
- Report failure count, avg, min, max, and p95 latency per host
- Configurable query count via `--dns-count`

### REQ-010: TCP Connect Latency

The system shall:
- Measure `socket.create_connection()` latency over repeated calls to configurable host:port pairs
- Report failure count, avg, min, max, and p95 latency per target
- Configurable attempt count via `--tcp-count`

### REQ-011: Speedtest

The system shall:
- Execute Ookla speedtest (`speedtest --format=json`) or fall back to `speedtest-cli --json`
- Report download Mbps, upload Mbps, and jitter
- Support `--no-speedtest` to skip

### REQ-012: iPerf3 Throughput

The system shall:
- Execute `iperf3 -c <server> -t 10 -J` to measure TCP throughput
- Parse JSON output for sent Mbps, retransmit count, and total bytes
- Report high retransmit percentage (>2%) as a warning
- Support `--no-iperf` to skip

### REQ-013: Bufferbloat Detection

The system shall:
- On Linux: inspect `tc -s qdisc` backlog and drops, then run concurrent ping during iPerf3 load
- On non-Linux: run iPerf3 throughput test with concurrent ping burst, comparing loaded vs unloaded latency
- Report latency ratio >2x as warning, >3x as severe
- Support `--no-bufferbloat` to skip

### REQ-014: Download Throughput Test

The system shall:
- Download up to 100 small images concurrently via HTTP (`requests`-less, using `http.client`)
- Report success/failure counts and aggregate throughput in Mbps
- Opt-in via `--download-test`

### REQ-015: HTTP Latency Test

The system shall:
- Measure HTTP request latencies to multiple endpoints
- Report p95 latency per host
- Opt-in via `--connection-test`

### REQ-016: MTU Probing

The system shall:
- Discover path MTU to a target host via ping with incrementally increasing packet sizes
- Report path MTU below 1400 as a warning
- Opt-in via `--connection-test`

### REQ-017: 5-Layer Diagnosis Engine

The system shall apply a rule engine across five layers, producing per-layer diagnoses with severity (`clean`, `info`, `warning`, `bad`), title, detail, and a fix recommendation:

1. **Physical** — Interface errors, drops, overruns, carrier changes, ethtool duplex/link
2. **WiFi** — Signal dBm (<-80 bad, <-70 warning, <-60 info), channel utilization (>60% warning)
3. **Gateway** — Ping classification + TCP retransmit count (>50) cross-correlated with WiFi signal
4. **ISP** — MTR per-hop loss localization (hops 1-2 = modem, hops 3+ = ISP upstream)
5. **Internet** — External ping instability, DNS failures, TCP connect failures, iPerf3 retransmits, speedtest

Bufferbloat and MTU diagnoses are cross-cutting (not tied to a single layer).

### REQ-018: Health Score

The system shall compute a 0-100 composite health score from all available diagnostic layers:

| Layer | Weight |
|-------|--------|
| Interface | 10% |
| WiFi | 15% |
| Gateway | 25% |
| Internet | 25% |
| DNS | 10% |
| TCP | 5% |
| Bufferbloat | 10% |

Missing layers (unavailable probes) contribute zero but are excluded from the weight denominator.

### REQ-019: CLI Output

The system shall print to console:
- Health score
- Diagnosis summary with layer, severity icon, title, detail, and fix recommendation
- Ping summary table: host, loss%, avg, p95, jitter per target
- Output directory path

### REQ-020: File Export (CLI)

The system shall write to the output directory (default `internet_diagnostics/`, configurable via `--outdir`):
- `diagnostics.json` — complete machine-readable results
- `ping_samples.csv` — every ping attempt with timestamp
- `ping_summary.csv` — per-host aggregated statistics
- `report.txt` — human-readable summary

### REQ-021: Session History

The system shall:
- Persist each CLI run to `~/.netdiag/session_YYYYMMDD_HHMMSS.json`
- Persist each GUI-triggered run to the same directory
- Configurable history directory via `--history-dir`

### REQ-022: Quiet Mode

The system shall support `--quiet` mode that suppresses per-ping progress output, printing only the final summary and diagnosis.

### REQ-023: Web GUI

When started with `--gui`, the system shall:
- Serve a FastAPI web application on the configured port (default 8080)
- Serve a single-page HTML frontend with Chart.js for visualization
- Assemble the page from static files in `netdiag_core/frontend/` (`index.html` shell + per-tab `partials/*.html`) at request time, serving `/static` from that directory

### REQ-024: GUI API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Serve HTML frontend |
| `/api/status` | GET | Current run status and health score |
| `/api/monitor` | GET | Live polling snapshot (WiFi, gateway, health) |
| `/api/monitor/start` | POST | Start live monitor background thread |
| `/api/monitor/stop` | POST | Stop live monitor |
| `/api/activity` | GET | Recent command/socket activity log |
| `/api/tools` | GET | Tool availability check results |
| `/api/config` | GET | Load persistent config |
| `/api/config` | POST | Save persistent config |
| `/api/run` | POST | Trigger a new diagnostic run |
| `/api/reports` | GET | List report files in output directory |
| `/api/report/{name}` | GET | Serve individual report file |
| `/api/history` | GET | List past session files |
| `/api/session/{file}` | GET | Load full session data |
| `/api/export/{file}?format=` | GET | Export session as JSON, CSV, or HTML |
| `/api/tools/menu` | GET | OSI-layer-organized tool definitions |
| `/api/tool/run` | POST | Run a single tool by ID with params |
| `/api/tool/status` | GET | Last tool run result |
| `/api/results/{file}/json` | GET | Raw JSON of session file |

### REQ-025: GUI Frontend Pages

The frontend SPA shall provide:
- **Dashboard** — health score, diagnosis list, ping summary, last-run info
- **Troubleshoot** — trigger a new diagnostic run with configurable options
- **Live Monitor** — auto-refreshing WiFi signal, gateway latency, health score
- **History** — list and compare past sessions
- **Reports** — view and export saved report files
- **About** — version, license, platform info

### REQ-026: Daemon Mode

When started with `--daemon`, the system shall:
- Run a full diagnostic immediately
- Re-run every 10 minutes on a loop
- Serve the web GUI concurrently
- Provide a systemd service file for auto-start on Linux

### REQ-027: Interrupt Handling

The system shall:
- Catch `KeyboardInterrupt` (Ctrl+C) gracefully
- Record the interruption in the results dict
- Still write partial output files and history on interrupt
- Print an interruption message to stderr

### REQ-028: Tool Availability Check

The system shall audit which CLI tools are available on `PATH`:
- `ping`, `ip`, `iw`, `mtr`, `traceroute`/`tracert`, `ss`/`netstat`, `ethtool`, `iperf3`, `speedtest`
- Map missing tools to platform-specific install commands for `--install-missing` hints
- Pass tool availability into the results dict

### REQ-029: Intermittent Connection Reliability Detection

The system shall detect intermittent connection-establishment failures — the "internet works, but often not; first connection fails then a retry works; some addresses ok, some not; many small files/images trigger it" symptom class — via a stdlib-only probe (`reliability_test`) that:

- Makes many **fresh, cache-defeating** HTTPS connections so caching cannot mask the problem:
  - a unique cache-busting query string per request,
  - `Cache-Control: no-cache, no-store, max-age=0` and `Pragma: no-cache` request headers,
  - `Connection: close` plus a brand-new socket per attempt (no keepalive/pooling),
  - a fresh TLS context per attempt with session tickets disabled (`OP_NO_TICKET`) so TLS resumption cannot hide intermittent handshake failures,
  - rotation through the resolved A/AAAA addresses (connecting to a specific IP while still sending the correct SNI via `server_hostname`). The OS resolver cache cannot be fully bypassed in stdlib; this is mitigated by also testing bare-IP targets.
- Times each connection **phase** (DNS → TCP connect → TLS handshake → first byte) and records **which phase the first attempt failed in**.
- Tracks **first-attempt** outcome separately from the **eventual** (post-retry) outcome, reporting `first_attempt_fail_pct`, `recovered_on_retry`, and `hard_failures`.
- Tests **IPv4 and IPv6 separately** (`by_family`) to surface broken-IPv6 / happy-eyeballs fallback.
- Compares **low vs high concurrency** (`by_concurrency`) to reproduce router NAT/conntrack-table exhaustion and rate-limiting (the many-small-files trigger).
- Breaks results down **per target** (`by_target`), including hostname vs bare-IP, to isolate DNS.
- Synthesizes a **localized verdict** (severity, title, detail, fix) rather than a raw dump, surfaced into the diagnosis list.
- Exposes configurable sample count, duration (time-bounded mode), concurrency, retries, timeout, IP mode, and target list via CLI (`--reliability-test`), the GUI config (`reliability_*` keys), the Tools tab, and a Troubleshoot checkbox.
- Falls back to a `urllib` total-time attempt (no phase breakdown) if the manual socket/ssl stack errors (graceful degradation).
- Does not alter the REQ-018 health score (stand-alone verdict).

**Known limitations:** The probe is deliberately scoped to connection *establishment* — an attempt that returns any HTTP response (including 4xx/5xx or a captive-portal redirect) counts as a success; HTTP status codes are not inspected. The OS DNS resolver cache cannot be fully bypassed from stdlib; this is mitigated by testing bare-IP targets alongside hostname targets.

---

## 2. Non-Functional Requirements

### NFR-001: Zero Dependencies (CLI)

The CLI mode shall use only Python 3.12+ standard library modules. No `pip install` required.

### NFR-002: Optional GUI Dependencies

The web GUI mode shall require only `fastapi` and `uvicorn`, installed via `pip install fastapi uvicorn`.

### NFR-003: Package Architecture

The implementation shall be organized as the `netdiag_core/` Python package (constants, runtime, stats, config, probes, analysis, reporting, orchestrate, monitor, cli, server, frontend), with each module kept under 400 lines and `netdiag.py` retained as a thin entry-and-re-export shim. The CLI core shall remain standard-library-only.

### NFR-004: Platform Support

The system shall run on Linux (93/100 feature coverage), macOS (82/100), and Windows (74/100).

### NFR-005: Graceful Degradation

Every probe shall have at least one fallback implementation when its primary CLI tool is missing, using only standard library I/O and sockets.

### NFR-006: Python Version

Minimum Python version: 3.12.

### NFR-007: License

AGPL-3.0-only.

---

## 3. CLI Options Reference

```
--hosts HOST [HOST ...]    Ping targets (default: 1.1.1.1 8.8.8.8 9.9.9.9 google.com)
--count N                  Pings per target (default: 20)
--interval SEC             Seconds between pings (default: 0.5)
--timeout SEC              Per-ping timeout (default: 2)
--ipv4                     Force IPv4
--ipv6                     Force IPv6
--dns-count N              DNS queries per host (default: 10)
--tcp-count N              TCP attempts per target (default: 10)
--outdir DIR               Output directory (default: internet_diagnostics/)
--no-speedtest             Skip Ookla speedtest
--no-trace                 Skip MTR/traceroute
--no-iperf                 Skip iPerf3 throughput
--no-bufferbloat           Skip bufferbloat test
--download-test            Download 100 images to measure throughput
--connection-test          HTTP latency + MTU probe
--quiet                    Suppress per-ping progress
--gui                      Start web GUI
--daemon                   Continuous monitoring + web GUI
--port PORT                Web server port (default: 8080)
--history-dir DIR          Session storage (default: ~/.netdiag/)
--version                  Show version and exit
--license                  Show license and exit
```

## 4. Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Runtime error |
| 2 | Invalid arguments |

---

## 5. E2E Browser Testing

### REQ-E2E-001: Live Monitor Crash Test

The system shall provide a headless browser test that:

- Starts a `netdiag --gui` server on a random available port
- Launches headless Chromium via Playwright
- Navigates to the GUI frontend
- Clicks the "Live Monitor" tab and activates monitoring
- Polls the page for a configurable duration (default 30s, smoke 10s)
- Collects:
  - All browser console messages (info, warning, error)
  - Page error events (JS exceptions)
  - Page crash events (renderer process crash)
  - Live monitor signal and latency values from the DOM
- After polling, asserts:
  - No page crash occurred
  - No JS console errors (except benign ones like favicon 404)
  - At least one `poll ok` console message (from client-side logging)
  - Live monitor UI updated (signal or latency values present)
  - Server process still alive at end (liveness check)

### REQ-E2E-002: Rapid Poll Stress Test

The system shall provide an HTTP stress test that:

- Starts a `netdiag --gui` server on a random available port
- Sends 100 sequential `GET /api/monitor` requests via httpx
- Records status codes and response times for each request
- Asserts:
  - All 100 requests return HTTP 200
  - Median request latency < 5000ms
  - P95 request latency < 15000ms
  - Server process still alive after all requests

### REQ-E2E-003: Graceful Skip

Both e2e tests shall skip with `pytest.mark.skipif` when their dependencies are not installed:

- `TestLiveMonitorBrowser` skips if `playwright` package is not installed
- `TestMonitorServerStress` skips if `httpx` package is not installed

This ensures `make test` never fails due to missing e2e deps.

### REQ-E2E-004: Sandboxed Execution

The browser test shall sandbox headless Chromium through:

- `headless=True` — no display server required, no windowing
- Process isolation via Playwright's Chromium subprocess management
- Chromium launch flags for memory safety:
  - `--disable-dev-shm-usage` — uses `/tmp` instead of `/dev/shm` (avoids OOM on containers)
  - `--disable-gpu` — no hardware acceleration (safe on headless systems)
  - `--no-sandbox` — bypasses Chrome's kernel sandbox when running inside unprivileged containers
  - `--disable-extensions` — prevents extension interference
- Server process started via `subprocess.Popen` with separate PID
- Server stderr captured to a temp file and dumped on test failure
- Both processes killed in `finally` block — no orphan processes on failure

### REQ-E2E-005: Server Lifecycle Fixture

The system shall provide shared test helpers in `tests/server_helpers.py`:

- `init_netdiag_server()` — binds to port 0 for OS-assigned random port, starts server subprocess, polls `/api/status` until healthy (8s timeout)
- `shutdown_netdiag_server(srv, test_failed=False)` — kills server, dumps stderr if test failed, returns exit info

This fixture is used by both e2e tests and runs the real `netdiag.py --gui` process, not a mocked version.

### REQ-E2E-006: Make Targets

| Target | Action |
|--------|--------|
| `make install-e2e` | `pip install -r requirements-dev.txt && playwright install chromium` |
| `make e2e-browser` | Run Playwright crash test only |
| `make e2e-stress` | Run httpx server stress test only |
| `make e2e` | Run both browser and stress tests (full suite) |
| `make e2e-smoke` | Run both tests at 10s monitor duration via `NETDIAG_MONITOR_DURATION=10` |

### REQ-E2E-007: Dependencies

| Dependency | Purpose | File |
|------------|---------|------|
| `requirements-dev.txt` | All e2e dev deps | `requirements-dev.txt` |
| `pytest>=8.0` | Test runner (existing) | `requirements-dev.txt` |
| `fastapi>=0.100` | GUI runtime (existing) | `requirements-dev.txt` |
| `uvicorn>=0.23` | GUI runtime (existing) | `requirements-dev.txt` |
| `httpx>=0.27` | HTTP client for server stress test | `requirements-dev.txt` |
| `playwright>=1.45` | Browser automation for crash test | `requirements-dev.txt` |
| Chromium binary | Browser runtime (installed via `playwright install chromium`) | ~175MB |

---

*Derived from netdiag.py v1.0.0, AGENTS.md, docs/usage.md, and docs/oss-network-diagnostics.md.*
