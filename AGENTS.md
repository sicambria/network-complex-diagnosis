# AGENTS.md — Network Complex Diagnosis

## Project Overview

Platform-agnostic internet diagnostics suite that isolates local network issues from ISP/upstream problems, detects WiFi signal problems, interface errors, bufferbloat, and per-hop routing issues. Runs from a checkout as the `netdiag_core/` Python package, with `netdiag.py` kept as a thin entry-and-re-export shim. Python 3.12+. CLI mode: stdlib only (zero deps). GUI mode: 2 optional pip deps (fastapi + uvicorn).

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

Source lives in the `netdiag_core/` package; `netdiag.py` is a thin
entry-and-re-export shim (`python3 netdiag.py ...` and `from netdiag import
diagnose, full_diagnostic, ...` both still work). Every module is kept under 400
lines. The dependency graph is acyclic. `docs/architecture.md` is the canonical,
fuller description.

```
netdiag.py                       — thin entry + re-export shim
netdiag_core/
├── constants.py                 — host lists, ICMP_RATE_LIMITERS, WELLKNOWN_SITES, RELIABILITY_TARGETS, VERSION
├── runtime.py                   — IS_LINUX/IS_MACOS/IS_WINDOWS, run_cmd, has_tool, now_iso, activity log, package-manager detection, UserInterrupted
├── stats.py                     — percentile, series_stats, jitter_ms, clean_float
├── config.py                    — load/save config + session history (~/.netdiag/)
├── probes/                      — one concern per module; each has Linux/macOS/Windows branches + stdlib Plan B
│   ├── ping.py                  — ping_command, parse_rtt_ms, ping_once, ping_burst, resolve_all, classify_ping
│   ├── netinfo.py               — gateway / default-interface / wireless-iface detection, interface_stats, sysfs+procfs fallbacks, ethtool_info
│   ├── wifi.py                  — wifi_info (iw / airport / netsh) + /proc/net/wireless fallback
│   ├── sockets.py               — tcp_socket_stats (ss / nettop / netstat) + /proc/net/tcp fallback
│   ├── dns_tcp.py               — dns_test (getaddrinfo), tcp_test (create_connection)
│   ├── route.py                 — mtr_test, traceroute/TTL fallbacks, mtu_probe
│   ├── throughput.py            — speedtest_result, iperf3_test, bufferbloat_test
│   └── reliability.py           — reliability_test (intermittent-connection detector, per-phase DNS/TCP/TLS/TTFB timing, first-vs-retry, IPv4/IPv6 + concurrency A/B; Plan B urllib total-time), wellknown_sites_test 100-site reproducer + verdict, HTTP/download probes
├── analysis/                    — the severity authority
│   ├── reconcile.py             — reconcile_icmp / get_reconciliation: ICMP loss vs TCP/HTTP/DNS success so rate-limiting is never reported as packet loss
│   ├── diagnose.py              — diagnose(), the single source of truth for severity; engine concatenates per-layer helpers (physical→wifi→gateway→ISP→internet→meta)
│   └── score.py                 — health_score() 0-100 weighted composite (internet score ignores ICMP-filtered loss)
├── reporting.py                 — report.txt + diagnostics.json + CSVs, build_isp_report (isp_report.txt; export format=isp), console summary
├── orchestrate.py               — full_diagnostic() sequences all probes + cooperative cancellation
├── monitor.py                   — live-monitor sampling / state / verdict
├── cli.py                       — build_parser (--wellknown-test, --isp-report, ...), cli_main
├── server/                      — optional, lazy fastapi: build_app() assembles the FastAPI app + RunState and registers per-area route modules (routes_diag/monitor/reports/tools/config); /api/export/{file}?format=json|csv|html|isp + run/stop/status/monitor/history. page.py:assemble_index builds the page; /static is served from frontend/
└── frontend/                    — static files (NOT an embedded Python string): index.html shell, partials/*.html (one per tab), styles.css, js/app1.js,app2.js,app3.js. Renders diagnose() output verbatim (Findings = interpretation, Measurements = raw values). NEVER recompute severity in JS — see "Single source of truth" below.
```

The analysis layer keeps the same behavior as before — only the file locations
moved: `classify_ping` is pure (`loss→bad_loss→some_loss→bad_latency_spikes→
latency_spikes→high_jitter→clean`), and the internet verdict routes through
`reconcile_icmp`, not raw `classify_ping`.

### Stop button / cooperative cancellation (GUI)
A running diagnostic is a `daemon` thread — you cannot signal it from outside, so
Stop is **cooperative**. `POST /api/stop` sets a `threading.Event` (`stop_event`),
which `full_diagnostic(args, callback, should_stop)` polls two ways:
(1) `_stopcheck()` at every slow-probe boundary, so no new probe starts after Stop;
(2) the GUI progress `cb` raises `UserInterrupted` when the flag is set, which
unwinds the long callback-driven probes mid-run (ping bursts, reliability/wellknown
rounds — so the ~2.5 min 100-site reproducer aborts in seconds, not minutes).
Both land in `full_diagnostic`'s `except UserInterrupted`, which still runs
reconcile/diagnose/health on the partial data → `interrupted=True`, a "Test was
interrupted" finding, and `run_state["status"]="stopped"`. The frontend treats
`stopped` as terminal (alongside `done`/`error`) and renders the partial report.
Two foot-guns, both load-bearing:
- **Keep `stop_event` a closure variable, NOT a key in `current_run`.** `api_status`
  does `dict(current_run)` then JSON-encodes it; a `threading.Event` isn't
  serializable, so stashing it there 500s *every* status poll and freezes the UI.
- Clear the flag in `api_run` under the lock (with the run reset), not in the
  worker — otherwise a click during thread startup is lost, or a stale Stop from a
  prior run cancels the new one.

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

Patch the canonical module target, not `netdiag.X`. Internal callers use qualified module references (`rt.run_cmd(...)`, `netinfo.detect_gateway(...)`), so a symbol has exactly one patch home — its defining module (e.g. `netdiag_core.runtime.run_cmd`, `netdiag_core.probes.netinfo.detect_gateway`). The shim keeps `netdiag.X` *attribute access* working, but patching `netdiag.X` no longer affects internal call sites. See `docs/architecture.md` "Patch convention".

**End-to-end**: `test_e2e.sh` — runs syntax checks, pytest, CLI diagnostic, GUI server test, install script validation, and uninstall script validation. Writes timestamped log to `/tmp/netdiag_e2e_*.log`.

### Full test environment (one-shot)
`make dev-setup` (→ `setup/dev-setup.sh`) installs EVERYTHING needed to fully
test: system diagnostic tools, `.venv` with all dev/test deps (fastapi, uvicorn,
pytest, httpx, playwright), the Playwright chromium browser + OS libs, and a
node check for `make check-js`. It is idempotent; `--check` verifies only,
`--no-sudo` skips root steps. Then:
- `make test` — unit + integration (no browser); the default fast suite.
- `make test-all` — everything, **including the Playwright browser e2e**.
- `make check-js` — `node --check` on the split frontend JS.

The browser e2e (`tests/test_e2e_browser.py`) launches a real `python3
netdiag.py --gui` and drives the page with chromium — this is the only check
that actually *executes* the frontend JavaScript. `make lint`/pytest validate
Python only.

### Refactor / package foot-guns (learned the hard way)
- **Module-vs-local name clash.** `full_diagnostic` and `api_monitor` keep a
  local variable named `wifi`, so the wifi probe **module** must be imported
  aliased (`from netdiag_core.probes import wifi as wifi_probe`). Importing it
  as `wifi` makes Python treat `wifi` as a local everywhere in the function →
  `UnboundLocalError` on `wifi = wifi.wifi_info(...)`. Watch this for any module
  whose name matches a local.
- **Stdlib-singleton patches go through the shim.** Tests do
  `patch.object(netdiag.socket, ...)`, `patch("netdiag.time.sleep")`,
  `patch("netdiag.Path.is_dir")`. The shim therefore `import`s `socket`, `time`,
  `threading`, and `Path` so those names exist and patching them mutates the
  global singletons the probes call. BUT a whole-**name** rebind like
  `patch("netdiag.Path")` only affects the shim's name — code that uses `Path`
  in another module (e.g. `cli_main`) must be patched at *its* module
  (`netdiag_core.cli.Path`). Class-**method** patches (`Path.is_dir`) are global
  and work via the shim; whole-name rebinds are per-module.
- **Frontend files are SOURCE, not generated.** `netdiag_core/frontend/**` must
  be committed; there is no runtime regeneration. A clone missing them 500s on
  `/static`.
- **Browser provisioning on bleeding-edge OS/Python.** Playwright won't
  auto-download a chromium build for an unreleased Ubuntu, and old Playwright
  won't install on Python 3.14 (greenlet build). The browser e2e falls back to a
  **cached** browser via `launch_chromium()` (`tests/server_helpers.py`,
  `executable_path=`) and skips cleanly if none is available — so `make
  test-all` is green whether or not a browser could be provisioned.
- **Baseline oracle, not zero failures.** Record the pre-existing failure set
  before refactoring; "green" means *no NEW* failures. (This box had an
  unrelated `node` server on :8080 — never blind-kill it; tests must mock the
  socket bind / use a free port instead of assuming :8080 is free.)
- **Prove the risky bits byte-for-byte.** `diagnose()` was split across files
  only after an equivalence harness confirmed identical output on every branch.
  When restructuring the single source of truth, build that oracle first.

## Makefile Targets

| Target | What it does |
|--------|-------------|
| `dev-setup` | One-shot FULL dev+test env: system tools + venv + all dev deps + Playwright browser + node check (`setup/dev-setup.sh`) |
| `install` | Full one-click: system deps + pip deps + symlink + desktop icon |
| `install-sys` | System deps (apt/dnf/pacman/brew) |
| `install-gui` | fastapi + uvicorn via pip |
| `desktop-install` | Start menu + desktop icon (all platforms) |
| `venv` | Python virtual environment with dev deps |
| `test` | Run pytest, unit + integration, no browser (pass ARGS="-k diagnose") |
| `test-all` | Complete suite incl. the Playwright browser e2e |
| `check-js` | `node --check` on the split frontend JS |
| `lint` | Syntax check (shim + whole `netdiag_core` package) |
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
- **`pkill -f` can kill your own shell.** `pkill -f "netdiag.py.*--gui"` matches the *full command line* of every process — including the `bash -c "..."` running your kill command, because that pattern string is literally in its argv. pkill spares its own PID but not the parent shell, so the script dies mid-run (exit 144 = SIGSTKFLT). When you need a clean kill inside a scripted step, kill by PID instead: `ss -ltnp 'sport = :PORT'` → `kill <pid>`. The bare `pkill -f "netdiag.py.*--gui"` line is safe only when it's the whole command, not embedded in a larger compound command that echoes the pattern.
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

- **Frontend changes**: The UI is now static files under `netdiag_core/frontend/`
  (`index.html` shell, `partials/*.html` one per tab, `styles.css`,
  `js/app1.js`/`app2.js`/`app3.js`). Edit those directly — the server assembles
  `index.html` + partials at request time (`server/page.py:assemble_index`) and
  serves `/static` from `netdiag_core/frontend`. There is no `INDEX_HTML` Python
  string and no `templates/index.html` regeneration step anymore; just restart the
  server to pick up changes:
  ```bash
  pkill -f "netdiag.py.*--gui" 2>/dev/null; sleep 0.5   # scoped: never kill unrelated servers on :8080
  python3 netdiag.py --gui --port 8080 &
  sleep 2
  curl -s http://localhost:8080/ | head -5  # verify the updated page is served
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
