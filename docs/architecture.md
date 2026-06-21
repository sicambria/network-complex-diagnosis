# NetDiag Architecture

NetDiag is a platform-agnostic internet diagnostics suite that isolates local
network issues from ISP/upstream problems. It runs from a checkout: the CLI core
is **stdlib-only** (Python 3.12+); the optional web GUI adds `fastapi` + `uvicorn`.

Entry points (both unchanged for users):

- `python3 netdiag.py [--gui|--daemon|...]` — CLI / GUI / daemon
- `from netdiag import diagnose, full_diagnostic, ...` — library use & tests

## Package layout

The source lives in the `netdiag_core/` package; `netdiag.py` is a thin
entry-and-re-export shim. Every module is kept under 400 lines. The dependency
graph is acyclic — arrows point "depends on":

```
constants ─┐
runtime ───┼─> stats ─> probes/* ─> analysis/* ─> reporting ─> orchestrate ─> cli
           │                                  └─> monitor                      │
config ────┘                                                                   │
                                                          server/* (lazy fastapi) <─ orchestrate, monitor, reporting, config
                                                          frontend/* (static html/css/js)
```

### Leaf modules (no intra-package dependencies)

- **`runtime.py`** — process/OS runtime: platform flags (`IS_LINUX`/`IS_MACOS`/
  `IS_WINDOWS`/`OS_NAME`), `run_cmd`, `has_tool`, `now_iso`, the activity log
  (`ACTIVITY_LOG`/`log_activity`/`get_activity_log`), package-manager detection
  (`detect_package_manager`/`install_hint`/`check_tools`), and `UserInterrupted`.
  These are the hot, heavily-mocked primitives — see "Patch convention".
- **`constants.py`** — pure data: host lists, `ICMP_RATE_LIMITERS`,
  `WELLKNOWN_SITES`, `RELIABILITY_TARGETS`, `APT_PACKAGES`, `VERSION`.
- **`stats.py`** — `percentile`, `clean_float`, `series_stats`, `jitter_ms`.
- **`config.py`** — config + history persistence (`load_config`/`save_config`/
  `save_history`/`load_history`/`ensure_history_dir`, `CONFIG_DEFAULTS`/`_LIMITS`).

### Probes (`netdiag_core/probes/`)

One concern per module; each has Linux/macOS/Windows branches + stdlib Plan B:

| Module | Probes |
|--------|--------|
| `ping.py` | `ping_command`, `parse_rtt_ms`, `ping_once`, `ping_burst`, `resolve_all`, `classify_ping` |
| `netinfo.py` | gateway / interface / wireless-iface detection, `interface_stats`, sysfs + procfs fallbacks, `ethtool_info` |
| `wifi.py` | `wifi_info` + `/proc/net/wireless` fallbacks |
| `sockets.py` | `tcp_socket_stats` + `/proc/net/tcp` fallback |
| `dns_tcp.py` | `dns_test`, `tcp_test` |
| `route.py` | `mtr_test`, traceroute/TTL fallbacks, `mtu_probe` |
| `throughput.py` | `speedtest_result`, `iperf3_test`, `bufferbloat_test` |
| `reliability.py` | reliability probe + verdict, 100-site reproducer, HTTP/download probes |

### Analysis (`netdiag_core/analysis/`) — the severity authority

- **`reconcile.py`** — `reconcile_icmp` / `get_reconciliation`: cross-references
  ICMP loss against TCP/HTTP/DNS success so ICMP rate-limiting is never reported
  as packet loss.
- **`diagnose.py`** — `diagnose()`, the **single source of truth for severity**.
  Built from per-layer helpers (`_diag_interface/_wifi/_gateway/_isp/_internet/
  _meta`), each returning a list of diagnosis dicts that `diagnose()` concatenates.
- **`score.py`** — `health_score()` weighted composite.

### Reporting / orchestration / CLI / monitor

- **`reporting.py`** — report.txt, CSV, `build_isp_report`, console summary.
- **`orchestrate.py`** — `full_diagnostic()` sequences all probes + cancellation.
- **`monitor.py`** — live-monitor sampling/state/verdict.
- **`cli.py`** — `build_parser`, `cli_main`.

### Server (`netdiag_core/server/`) — optional, lazy `fastapi`

`fastapi`/`uvicorn` are imported **inside functions only**, so `import netdiag`
works on a stdlib-only box. `build_app()` is a thin assembler: it builds the
`FastAPI` app, creates a `RunState` holder, and calls `register_*` functions
from the per-area route modules (`routes_diag`, `routes_monitor`,
`routes_reports`, `routes_tools`, `routes_config`). The GUI Stop button is
cooperative cancellation via a `threading.Event` closure (never a serializable
field on the status dict).

### Frontend (`netdiag_core/frontend/`)

The previously-embedded HTML is now real static files: `index.html` shell,
per-tab partials in `partials/`, `styles.css`, and `js/*.js`. The server
assembles them and serves the result; there is no "regenerate the template"
step anymore — the static files are the source of truth.

## Patch convention (load-bearing for tests)

The test suite is mock-heavy and patches by string. `mock.patch("module.name")`
replaces the name **in the namespace you target**, so a function resolves a
mocked symbol only if it reads it from the patched namespace. Rule:

> Cross-module calls to mockable symbols go through the **module object**
> (`rt.run_cmd(...)`, `ping.ping_once(...)`), giving exactly **one** canonical
> patch target per symbol. Pure helpers may be imported by name.

The canonical patch target for each symbol is its defining module
(`netdiag_core.runtime.run_cmd`, `netdiag_core.probes.netinfo.detect_gateway`,
...). The re-export shim keeps `import netdiag` and `netdiag.X` *attribute
access* working, but patching `netdiag.X` no longer affects internal call sites —
tests target the symbol's home module.

## Baseline test oracle

Fast deterministic subset (~2s, excludes network-touching e2e):
`440 passed`. Six failures are **pre-existing and environmental**, not
behavioral: `test_detect_gateway_procfs_fallback` (machine has `ip`, so the
procfs fallback is never reached) and five `TestStartServer` tests (port 8080
held by an unrelated dev server). The refactor must introduce **no new
failures** beyond these.
