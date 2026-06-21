"""NetDiag — all-in-one internet diagnostics suite.

Platform-agnostic, Linux-enhanced. Reuses existing CLI tools.
Zero deps for CLI mode. Optional fastapi+uvicorn for web GUI.

Usage:
  python3 netdiag.py                    # CLI mode
  python3 netdiag.py --gui              # start web UI on http://localhost:8080
  python3 netdiag.py --daemon           # continuous monitoring + web UI
  python3 netdiag.py --count 120 --int 1 # long test

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

import argparse
import json
import sys
from pathlib import Path

from netdiag_core import config
from netdiag_core import orchestrate
from netdiag_core import reporting


def build_parser():
    cfg = config.load_config()
    parser = argparse.ArgumentParser(description="NetDiag — all-in-one internet diagnostics suite")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--license", action="store_true", help="Show license information")
    parser.add_argument("--hosts", nargs="*", default=cfg["hosts"])
    parser.add_argument("--count", type=int, default=cfg["ping_count"])
    parser.add_argument("--interval", type=float, default=cfg["ping_interval"])
    parser.add_argument("--timeout", type=int, default=cfg["ping_timeout"])
    parser.add_argument("--dns-count", type=int, default=cfg["dns_count"])
    parser.add_argument("--tcp-count", type=int, default=cfg["tcp_count"])
    parser.add_argument("--outdir", default="internet_diagnostics")
    parser.add_argument("--ipv4", action="store_true")
    parser.add_argument("--ipv6", action="store_true")
    parser.add_argument("--no-speedtest", action="store_true")
    parser.add_argument("--no-trace", action="store_true")
    parser.add_argument("--no-iperf", action="store_true")
    parser.add_argument("--no-bufferbloat", action="store_true")
    parser.add_argument("--download-test", action="store_true", help="Download 100 images to measure throughput")
    parser.add_argument("--connection-test", action="store_true", help="HTTP latency + MTU probe")
    parser.add_argument("--reliability-test", action="store_true", help="Intermittent connection detector (cache-defeating fresh-connection probe)")
    parser.add_argument("--wellknown-test", action="store_true", help="Reproduce intermittent issues by fetching small images from ~100 well-known sites for ~2.5 min")
    parser.add_argument("--isp-report", action="store_true", help="Also print the detailed ISP evidence report to the console (always written to isp_report.txt)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-ping progress output")
    parser.add_argument("--gui", action="store_true", help="Start web GUI at http://localhost:8080")
    parser.add_argument("--daemon", action="store_true", help="Continuous monitoring + web GUI")
    parser.add_argument("--port", type=int, default=8080, help="Web server port (default: 8080)")
    parser.add_argument("--history-dir", default=cfg["history_dir"], help="Directory for history and persistent data")
    return parser


def cli_main():
    args = build_parser().parse_args()

    if args.version:
        print(f"netdiag v{config.VERSION} — AGPLv3")
        return

    if args.license:
        print(__doc__.split("SPDX-License-Identifier: AGPL-3.0-only")[1].strip())
        return

    if args.gui or args.daemon:
        try:
            from fastapi import FastAPI, Request, Response
            from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
            from fastapi.staticfiles import StaticFiles
            import asyncio
            import threading
            import uvicorn
        except ImportError:
            print("Error: fastapi and uvicorn are required for GUI mode.", file=sys.stderr)
            print("Install with: pip install fastapi uvicorn", file=sys.stderr)
            sys.exit(1)
        from netdiag_core.server.app import start_server
        start_server(args)
        return

    if args.count < 1:
        print("Error: --count must be at least 1", file=sys.stderr)
        sys.exit(2)
    if args.interval < 0:
        print("Error: --interval must be 0 or greater", file=sys.stderr)
        sys.exit(2)
    if args.timeout < 1:
        print("Error: --timeout must be at least 1 second", file=sys.stderr)
        sys.exit(2)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = orchestrate.full_diagnostic(args)

    results["diagnosis"] = results.get("diagnosis", [])
    results["health_score"] = results.get("health_score", 0)

    (outdir / "diagnostics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    reporting.write_csv(outdir / "ping_samples.csv", reporting.flatten_ping(results))
    reporting.write_csv(outdir / "ping_summary.csv", reporting.ping_summary_rows(results))
    reporting.write_report(outdir / "report.txt", results)
    (outdir / "isp_report.txt").write_text(reporting.build_isp_report(results), encoding="utf-8")
    reporting.print_console_summary(results, outdir)
    if getattr(args, "isp_report", False):
        print("\n" + "=" * 72)
        print(reporting.build_isp_report(results))

    config.save_history(args.history_dir, results)
