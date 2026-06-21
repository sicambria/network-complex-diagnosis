"""Shared server run-state (replaces build_app's closure variables).

RunState carries the diagnostic run dict, the cooperative-cancellation Event,
the tools run-state, and the report directory. The stop Event is a plain
attribute (NOT a key in current_run) so it never reaches api_status's JSON
encoder — a threading.Event isn't serializable and would 500 every status poll.
"""

import threading

from netdiag_core import runtime as rt
from netdiag_core import config
from netdiag_core import orchestrate


class RunState:
    def __init__(self, report_dir):
        self.lock = threading.Lock()
        self.current_run = {"status": "idle", "progress": {}, "results": None, "error": None}
        # Cooperative-cancellation flag for the GUI Stop button. Kept off
        # current_run so it never reaches api_status's JSON encoder.
        self.stop_event = threading.Event()
        self.tools_run_state = {"running": False, "tool_id": None, "result": None, "error": None}
        self.report_dir = report_dir

    def run_diag(self, args):
        run_state = self.current_run
        try:
            run_state["status"] = "running"
            run_state["progress"] = {}
            run_state["results"] = None
            run_state["error"] = None

            def cb(label, seq, total, ok, rtt, status_override=None):
                st = status_override or ("running" if seq < total else "done")
                with self.lock:
                    run_state["progress"][label] = {
                        "seq": seq, "total": total, "ok": ok,
                        "rtt_ms": rtt, "status": st, "label": label}
                # Mid-probe cancellation: raising here unwinds the long callback-
                # driven probes (ping bursts, reliability/wellknown rounds) so Stop
                # is responsive even inside the ~2.5 min 100-site reproducer.
                if self.stop_event.is_set():
                    raise rt.UserInterrupted("Stopped by user")

            results = orchestrate.full_diagnostic(args, callback=cb, should_stop=self.stop_event.is_set)
            with self.lock:
                run_state["status"] = "stopped" if results.get("interrupted") else "done"
                run_state["results"] = results
                config.save_history(args.history_dir, results)
        except Exception as e:
            with self.lock:
                run_state["status"] = "error"
                run_state["error"] = str(e)
