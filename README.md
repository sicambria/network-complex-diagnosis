# NetDiag

All-in-one internet diagnostics suite. Python 3.12+, zero deps for CLI mode.

Detects WiFi signal problems, interface errors, bufferbloat, per-hop routing issues, and isolates local vs ISP problems.

```
Health score: 86/100

Diagnosis:
  ![info][wifi] Fair WiFi signal
      Signal strength: -64 dBm
  !! [gateway] Gateway instability detected
      Latency spikes: p95=23.77ms; TCP retransmits: 490
  !! [tcp] TCP connection instability
      Affected: google.com:443
```

## Quick Start

### Linux / macOS
```bash
make install                    # system deps + GUI + desktop icon
# or
bash install.sh                 # same, interactive

# CLI diagnostic (zero deps)
python3 netdiag.py

# Quick smoke test
python3 netdiag.py --count 3 --interval 0.2 --no-speedtest --no-trace --no-iperf --no-bufferbloat

# Web GUI (optional: pip install fastapi uvicorn)
python3 netdiag.py --gui

# Continuous monitoring + web UI
python3 netdiag.py --daemon

# Run tests
make test
```

### Windows
```batch
:: Double-click install.bat  or  run from cmd:
install.bat

:: CLI diagnostic
python3 netdiag.py

:: Web GUI
python3 netdiag.py --gui

:: Desktop shortcuts will be in Start Menu > NetDiag
```

## Features

| Feature | CLI | GUI |
|---------|-----|-----|
| Ping burst (4 hosts, configurable count/interval) | Yes | Yes |
| Gateway detection + ping | Yes | Yes |
| WiFi signal strength, channel utilization | Yes | Yes |
| Interface errors (RX/TX drops, overruns) | Yes | Yes |
| Ethtool speed/duplex/link (Linux) | Yes | Yes |
| TCP socket stats (retransmits, RTT) | Yes | Yes |
| MTR per-hop loss/latency | Yes | Yes |
| DNS resolution latency | Yes | Yes |
| TCP connect latency | Yes | Yes |
| Speedtest (Ookla) | Yes | Yes |
| iPerf3 throughput | Yes | Yes |
| Bufferbloat detection (Linux enhanced) | Yes | Yes |
| Intermittent connection detector (cache-defeating, first-vs-retry, IPv4/IPv6, concurrency A/B) | Yes | Yes |
| 5-layer diagnosis engine | Yes | Yes |
| Health score 0–100 | Yes | Yes |
| JSON/CSV/HTML export | Yes | Yes |
| GUI dashboard + history | — | Yes |
| Daemon mode | — | Yes |

## Architecture

NetDiag runs from a checkout as the `netdiag_core/` Python package, with
`netdiag.py` kept as a thin entry-and-re-export shim (`python3 netdiag.py ...`
and `from netdiag import diagnose` both still work). Every module is under 400
lines. See `docs/architecture.md` for the full breakdown.

```
netdiag.py                — thin entry + re-export shim
netdiag_core/
├── constants.py          — host lists, ICMP_RATE_LIMITERS, WELLKNOWN_SITES, VERSION
├── runtime.py            — platform flags, run_cmd, has_tool, activity log, UserInterrupted
├── stats.py              — percentile, series_stats, jitter_ms, clean_float
├── config.py             — config + session-history persistence
├── probes/               — one concern per module, each with Plan B stdlib fallback
│                           (ping, netinfo, wifi, sockets, dns_tcp, route,
│                            throughput, reliability)
├── analysis/             — severity authority: reconcile.py (ICMP-vs-TCP),
│                           diagnose.py (single source of truth), score.py
├── reporting.py          — report.txt, CSV, build_isp_report, console summary
├── orchestrate.py        — full_diagnostic() sequencing + cooperative cancellation
├── monitor.py            — live-monitor sampling/state/verdict
├── cli.py                — build_parser, cli_main
├── server/               — optional, lazy fastapi (build_app + per-area route modules)
└── frontend/             — static files: index.html shell, partials/*.html,
                            styles.css, js/*.js (assembled server-side, no template step)

Platform scoring: Linux 93/100, macOS 82/100, Windows 74/100
```

## CLI Options

```
--hosts HOSTS           Ping targets (default: 1.1.1.1 8.8.8.8 9.9.9.9 google.com)
--count N               Pings per target (default: 20)
--interval SEC          Seconds between pings (default: 0.5)
--timeout SEC           Per-ping timeout (default: 2)
--ipv4                  Force IPv4 for all pings
--ipv6                  Force IPv6 for all pings
--dns-count N           DNS queries per resolver (default: 10)
--tcp-count N           TCP attempts per target (default: 10)
--outdir DIR            Output directory (default: internet_diagnostics/)
--quiet                 Suppress per-ping progress
--no-speedtest          Skip Ookla speedtest
--no-trace              Skip MTR/traceroute
--no-iperf              Skip iPerf3 throughput test
--no-bufferbloat        Skip bufferbloat test
--download-test         Download 100 images to measure throughput
--connection-test       HTTP latency + MTU probe
--reliability-test      Intermittent connection detector (cache-defeating fresh-connection probe)
--gui                   Start web UI at http://localhost:8080
--daemon                Continuous monitoring + web UI
--port PORT             Web server port (default: 8080)
--history-dir DIR       Session storage (default: ~/.netdiag/)
```

## File Output

After a CLI run, files are written to `internet_diagnostics/` (or `--outdir`):

- `diagnostics.json` — Full results, all probes, diagnosis, health score
- `ping_samples.csv` — Every ping attempt with timestamp
- `ping_summary.csv` — Per-host ping statistics
- `report.txt` — Human-readable summary

Sessions are also saved to `~/.netdiag/session_*.json` for GUI history.

## Platform Support

| Probe | Linux | macOS | Windows |
|-------|-------|-------|---------|
| Ping | ping -c -W | ping -c -t | ping -n -w |
| Gateway | ip route | route -n get | netstat -rn |
| Interface stats | ip -s link | ifconfig | netstat -e |
| WiFi info | iw survey dump | airport -I | netsh wlan |
| TCP sockets | ss -itp | nettop -J | netstat -s |
| MTR | mtr -r | mtr -r | tracert |
| Bufferbloat | tc + iperf3 | iperf3 fallback | iperf3 fallback |
| Ethtool | ethtool | — | — |
| iPerf3 | iperf3 | iperf3 | iperf3 |
| Speedtest | speedtest | speedtest | speedtest |

## Install

### Linux
```bash
make install                              # automated (system deps + GUI + desktop icon)
# or
bash install.sh                           # interactive, same result
pip install fastapi uvicorn               # optional, for GUI
sudo ln -sf "$(pwd)/netdiag.py" /usr/local/bin/netdiag  # symlink to PATH
```

### macOS
```bash
bash install.sh                           # Homebrew + pip + desktop .app bundle
pip install fastapi uvicorn               # optional, for GUI
sudo ln -sf "$(pwd)/netdiag.py" /usr/local/bin/netdiag  # symlink to PATH
```

### Windows
```batch
:: Double-click install.bat (at project root)  or run from cmd:
install.bat

:: Or run the setup script directly:
setup\windows\install.bat

:: Optional system tools (install manually):
::   speedtest-cli: https://www.speedtest.net/apps/cli
::   iperf3:        https://iperf.fr/iperf-download.php

:: Python GUI deps (if skipped during install):
pip install fastapi uvicorn
```

## Tests

```bash
make test
# or
python3 -m pytest tests/ -v
```

**516 tests** across 20 test files (514 pass, 2 skip as expected — MTR tool not found, fastapi installed).

| File | Coverage |
|------|----------|
| `test_parsers.py` | Ping output parsing, `classify_ping`, procfs parsers |
| `test_stats.py` | `percentile`, `series_stats`, `jitter_ms`, `clean_float` |
| `test_diagnose.py` | `diagnose()` and `health_score()` base cases |
| `test_diagnose_full.py` | All diagnose branches + health_score edge cases |
| `test_ping.py` | `ping_command`, `ping_once`, `_tcp_ping` |
| `test_ping_burst.py` | `ping_burst`, `resolve_all`, `now_iso`, `detect_package_manager` |
| `test_platform.py` | `detect_gateway`, `get_default_interface` per platform |
| `test_dns_tcp.py` | `dns_test`, `tcp_test` with mocked sockets |
| `test_probes.py` | mtr, speedtest, iperf3, bufferbloat, ethtool, tcp_sockets |
| `test_probes_full.py` | Platform branches, MTU binary search, run_cmd edge cases |
| `test_wireless_sysfs.py` | `detect_wireless_interface`, `_sysfs_interface_stats`, `wifi_info` |
| `test_output.py` | `write_report`, `save/load_history`, `build_parser` |
| `test_output_helpers.py` | `flatten_ping`, `ping_summary_rows`, `write_csv`, `_diag_args_from_kw` |
| `test_misc.py` | `has_tool`, `install_hint`, procfs parsers, graceful degradation |
| `test_live_monitor.py` | Activity log, monitor sample, outages, snapshot, diagnose, config |
| `test_live_monitor_full.py` | `monitor_targets`, `monitor_loop`, `monitor_start`, `monitor_stop` |
| `test_server.py` | Route existence checks |
| `test_server_full.py` | All 19 API routes with FastAPI TestClient |
| `test_orchestration.py` | `full_diagnostic`, `cli_main`, `start_server` |
| `test_e2e_requirements.py` | 1:1 mapping to 28 functional + 7 non-functional requirements |
| `test_e2e_browser.py` | Playwright browser crash test + httpx stress test |

## Systemd Service

```bash
make install-service
systemctl --user start netdiag
systemctl --user enable netdiag   # auto-start on login
journalctl --user -u netdiag -f   # tail logs
```

## Related

- `nettest.py` — predecessor (Linux-only, simpler, kept for reference)
- `docs/oss-network-diagnostics.md` — OSS tool survey and scoring
