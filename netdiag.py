#!/usr/bin/env python3
"""
NetDiag — all-in-one internet diagnostics suite.

Platform-agnostic, Linux-enhanced. Reuses existing CLI tools.
Zero deps for CLI mode. Optional fastapi+uvicorn for web GUI.

Usage:
  python3 netdiag.py                    # CLI mode
  python3 netdiag.py --gui              # start web UI on http://localhost:8080
  python3 netdiag.py --daemon           # continuous monitoring + web UI
  python3 netdiag.py --count 120 --int 1 # long test

This module is a thin entry-and-re-export shim over the netdiag_core package
(see docs/architecture.md). It keeps `python3 netdiag.py` and `from netdiag
import diagnose` working; the implementation lives in netdiag_core/.

SPDX-License-Identifier: AGPL-3.0-only
Copyright (C) 2024  Sicambria

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

# Stdlib modules some tests patch through the netdiag namespace
# (e.g. patch.object(netdiag.socket, ...), patch("netdiag.time.sleep")). These are
# singletons, so patching them here patches the same objects the probes call.
import socket  # noqa: F401
import time  # noqa: F401
import threading  # noqa: F401
from pathlib import Path  # noqa: F401

# Runtime primitives + platform flags
from netdiag_core.runtime import (
    log, OS_NAME, IS_LINUX, IS_MACOS, IS_WINDOWS, UserInterrupted,
    now_iso, ACTIVITY_LOG, ACTIVITY_LOCK, log_activity, get_activity_log,
    run_cmd, has_tool, detect_package_manager, install_hint, check_tools,
)
# Static data
from netdiag_core.constants import (
    DEFAULT_HOSTS, DNS_HOSTS, TCP_TARGETS, ICMP_RATE_LIMITERS, IPERF_SERVER,
    RELIABILITY_TARGETS, WELLKNOWN_SITES, APT_PACKAGES,
)
# Statistics
from netdiag_core.stats import percentile, clean_float, series_stats, jitter_ms
# Config / history
from netdiag_core.config import (
    VERSION, CONFIG_DEFAULTS, CONFIG_LIMITS, config_path, load_config, save_config,
    ensure_history_dir, save_history, load_history,
)
# Probes
from netdiag_core.probes.ping import (
    ping_command, parse_rtt_ms, _tcp_ping, ping_once, ping_burst, resolve_all, classify_ping,
)
from netdiag_core.probes.netinfo import (
    _parse_proc_net_route, _parse_proc_net_route_iface, detect_gateway, get_default_interface,
    detect_wireless_interface, _sysfs_interface_stats, interface_stats, ethtool_info,
)
from netdiag_core.probes.dns_tcp import dns_test, tcp_test
from netdiag_core.probes.wifi import _proc_net_wireless, _proc_net_wireless_any, wifi_info
from netdiag_core.probes.sockets import _proc_net_tcp_stats, tcp_socket_stats
from netdiag_core.probes.route import _ping_traceroute, mtr_test, mtu_probe
from netdiag_core.probes.throughput import speedtest_result, iperf3_test, bufferbloat_test
from netdiag_core.probes.reliability import _reliability_host_info, reliability_test
from netdiag_core.probes.verdicts import reliability_verdict
from netdiag_core.probes.webprobes import (
    download_images_test, http_latency_test, wellknown_sites_test, wellknown_verdict,
)
# Analysis (severity authority)
from netdiag_core.analysis import reconcile_icmp, get_reconciliation, diagnose, health_score
# Reporting
from netdiag_core.reporting import (
    flatten_ping, ping_summary_rows, write_csv, compact_ping, write_report,
    _sev_label, build_isp_report, print_console_summary,
)
# Orchestration + CLI
from netdiag_core.orchestrate import full_diagnostic
from netdiag_core.cli import build_parser, cli_main
# Live monitor
from netdiag_core.monitor import (
    MONITOR_WINDOW, MONITOR_LOCK, MONITOR_STATE, monitor_targets, monitor_sample,
    _flatten_sample, _update_outages, monitor_loop, monitor_start, monitor_stop,
    _target_stats, monitor_snapshot, monitor_diagnose,
)
# Server (fastapi imported lazily inside these — safe to import on a stdlib box)
from netdiag_core.server.app import build_app, start_server
from netdiag_core.server.tools_menu import TOOLS_MENU, _diag_args_from_kw


if __name__ == "__main__":
    cli_main()
