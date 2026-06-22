"""FastAPI app assembly and server entry point.

build_app() is a thin assembler: it creates the app, a RunState, mounts the
static frontend, and registers the per-area route groups. fastapi/uvicorn are
imported here (inside functions) so importing the package never requires them.
"""

import logging
import socket
import sys
import threading
import time
from pathlib import Path

from netdiag_core import runtime as rt
from netdiag_core import config
from netdiag_core import orchestrate
from netdiag_core import cli
from netdiag_core.server import page
from netdiag_core.server.state import RunState
from netdiag_core.server import routes_diag, routes_monitor, routes_reports, routes_tools

log = rt.log


def build_app():
    try:
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
    except ImportError:
        return None, None, None

    app = FastAPI(title="NetDiag")
    state = RunState(report_dir=Path.cwd() / "internet_diagnostics")
    app.mount("/static", StaticFiles(directory=str(page.FRONTEND_DIR)), name="static")

    routes_diag.register(app, state)
    routes_monitor.register(app, state)
    routes_reports.register(app, state)
    routes_tools.register(app, state)

    return app, state.current_run, cli.build_parser


def start_server(args):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    app, current_run, parser = build_app()
    if app is None:
        print("Error: fastapi and uvicorn required.", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    if args.daemon:
        diag_args = parser().parse_args([])
        # The daemon re-runs this every 10 min, so it must use only fast,
        # self-contained probes. The heavy ones (speedtest/iperf3/bufferbloat/mtr)
        # reach out to public servers that routinely hang with no hard timeout — a
        # single stuck iperf3 wedges the loop so history never persists (the bug
        # this fixes). They add nothing to a rolling trend; deep runs stay on-demand
        # via the CLI / Tools tab.
        diag_args.no_speedtest = True
        diag_args.no_iperf = True
        diag_args.no_bufferbloat = True
        diag_args.no_trace = True

        def daemon_loop(diag_args, current_run):
            while True:
                # Claim the run under the lock, then RELEASE it before the long
                # diagnostic. Holding it across full_diagnostic deadlocks: the
                # progress callback below re-acquires the same (non-reentrant) lock
                # on the first probe, so the loop would wedge forever and never
                # persist history. Only the quick state mutations take the lock.
                with current_run["_lock"]:
                    busy = current_run.get("status") == "running"
                    if not busy:
                        current_run["status"] = "running"
                        current_run["progress"] = {}
                if not busy:
                    def cb(label, seq, total, ok, rtt, status_override=None):
                        st2 = status_override or ("running" if seq < total else "done")
                        with current_run["_lock"]:
                            current_run["progress"][label] = {"seq": seq, "total": total, "ok": ok, "rtt_ms": rtt, "status": st2}

                    try:
                        res = orchestrate.full_diagnostic(diag_args, callback=cb)
                        with current_run["_lock"]:
                            current_run["status"] = "done"
                            current_run["results"] = res
                            config.save_history(diag_args.history_dir, res)
                    except Exception as e:
                        with current_run["_lock"]:
                            current_run["status"] = "error"
                            current_run["error"] = str(e)
                time.sleep(600)

        current_run["_lock"] = threading.Lock()
        t = threading.Thread(target=daemon_loop, args=(diag_args, current_run), daemon=True)
        t.start()

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", args.port))
    except OSError:
        print("Error: port %s is already in use by another process." % args.port, file=sys.stderr)
        print("Another server may be running there. Start NetDiag on a free port: --port <N>", file=sys.stderr)
        sys.exit(1)
    finally:
        probe.close()

    log.info("NetDiag web UI starting at http://localhost:%s", args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
