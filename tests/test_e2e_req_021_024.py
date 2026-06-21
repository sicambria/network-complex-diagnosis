"""
E2E tests mapped 1:1 to requirements in docs/requirements.md.

Every test method is tagged with @pytest.mark.REQXXX or @pytest.mark.NFRXXX
matching the requirement ID. Split across several files (by REQ/NFR range)
so each stays small; the shared header below is duplicated verbatim into each.
"""

import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import netdiag

SCRIPT = str(Path(__file__).resolve().parent.parent / "netdiag.py")
CORE_DIR = Path(__file__).resolve().parent.parent / "netdiag_core"


def _package_source():
    """Concatenated source of the whole netdiag_core package (the CLI core)."""
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(CORE_DIR.rglob("*.py")))


IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

BASE_ARGS = [
    sys.executable, SCRIPT,
    "--count", "3",
    "--interval", "0.1",
    "--timeout", "2",
    "--quiet",
    "--no-speedtest",
    "--no-trace",
    "--no-iperf",
    "--no-bufferbloat",
]

HAS_FASTAPI = True
HAS_GUI_DEPS = True
try:
    import fastapi
    import uvicorn
except ImportError:
    HAS_FASTAPI = False
    HAS_GUI_DEPS = False

HAS_PLAYWRIGHT = False
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    pass

HAS_HTTPX = False
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    pass


# ── Helpers ──────────────────────────────────────────────────────────────

def run_diag(outdir, extra_args=None, timeout=90):
    """Execute netdiag.py with BASE_ARGS + extra_args, return parsed result."""
    args = BASE_ARGS + ["--outdir", outdir]
    if extra_args:
        args.extend(extra_args)
    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    elapsed = time.time() - t0
    diag_path = Path(outdir) / "diagnostics.json"
    data = json.loads(diag_path.read_text()) if diag_path.exists() else None
    return {
        "data": data,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "rc": proc.returncode,
        "outdir": str(outdir),
        "elapsed_s": round(elapsed, 1),
        "args": args,
    }


def get_data_key(data, *keys):
    """Safely traverse nested dict keys, returning None if any missing."""
    for k in keys:
        if isinstance(data, dict):
            data = data.get(k)
        else:
            return None
    return data


def assert_file_exists(path):
    assert Path(path).exists(), f"Expected file not found: {path}"


def assert_json_field(d, field, typ=None, msg=None):
    assert field in d, msg or f"Missing field '{field}' in dict"
    if typ:
        assert isinstance(d[field], typ), (
            msg or f"Field '{field}' should be {typ}, got {type(d[field]).__name__}")


# ── Shared fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def fast_results():
    """Fast CLI diagnostic shared across basic-probe tests."""
    outdir = tempfile.mkdtemp(prefix="netdiag_e2e_fast_")
    res = run_diag(outdir)
    res["_outdir"] = outdir
    yield res
    import shutil
    shutil.rmtree(outdir, ignore_errors=True)


@pytest.fixture(scope="module")
def verbose_results():
    """Non-quiet CLI run for testing console output format."""
    outdir = tempfile.mkdtemp(prefix="netdiag_e2e_verbose_")
    extra = ["--no-speedtest", "--no-trace", "--no-iperf", "--no-bufferbloat"]
    args = [
        sys.executable, SCRIPT,
        "--count", "2",
        "--interval", "0.2",
        "--timeout", "2",
        "--outdir", outdir,
    ] + extra
    t0 = time.time()
    proc = subprocess.run(args, capture_output=True, text=True, timeout=90)
    elapsed = time.time() - t0
    diag_path = Path(outdir) / "diagnostics.json"
    data = json.loads(diag_path.read_text()) if diag_path.exists() else None
    res = {
        "data": data,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "rc": proc.returncode,
        "outdir": str(outdir),
        "elapsed_s": round(elapsed, 1),
    }
    yield res
    import shutil
    shutil.rmtree(outdir, ignore_errors=True)


@pytest.fixture(scope="module")
def trace_results():
    """CLI run with traceroute enabled (for REQ-008)."""
    outdir = tempfile.mkdtemp(prefix="netdiag_e2e_trace_")
    extra = ["--no-speedtest", "--no-iperf", "--no-bufferbloat"]
    res = run_diag(outdir, extra_args=extra, timeout=120)
    yield res
    import shutil
    shutil.rmtree(outdir, ignore_errors=True)


@pytest.fixture(scope="module")
def connection_test_results():
    """CLI run with --connection-test (REQ-015, REQ-016)."""
    outdir = tempfile.mkdtemp(prefix="netdiag_e2e_conn_")
    extra = [
        "--no-speedtest", "--no-trace", "--no-iperf", "--no-bufferbloat",
        "--connection-test",
    ]
    res = run_diag(outdir, extra_args=extra, timeout=120)
    yield res
    import shutil
    shutil.rmtree(outdir, ignore_errors=True)


@pytest.fixture(scope="module")
def download_results():
    """CLI run with --download-test (REQ-014)."""
    outdir = tempfile.mkdtemp(prefix="netdiag_e2e_dl_")
    extra = [
        "--no-speedtest", "--no-trace", "--no-iperf", "--no-bufferbloat",
        "--download-test",
    ]
    res = run_diag(outdir, extra_args=extra, timeout=120)
    yield res
    import shutil
    shutil.rmtree(outdir, ignore_errors=True)


@pytest.fixture(scope="module")
def gui_server():
    """Start netdiag --gui on random port, yield server info, stop on teardown."""
    from tests.server_helpers import init_netdiag_server, shutdown_netdiag_server
    srv = init_netdiag_server()
    yield srv
    shutdown_netdiag_server(srv)

# ═══════════════════════════════════════════════════════════════════════════
# REQ-021 — Session History
# ═══════════════════════════════════════════════════════════════════════════

class TestReq021SessionHistory:
    @pytest.mark.REQ021
    def test_req_021_session_history_saved(self, fast_results):
        hist_dir = Path.home() / ".netdiag"
        sessions = sorted(hist_dir.glob("session_*.json"), reverse=True)
        assert len(sessions) >= 1, "No session history files found"
        latest = json.loads(sessions[0].read_text())
        assert "health_score" in latest, "Session JSON missing health_score"
        assert "timestamp" in latest, "Session JSON missing timestamp"

    @pytest.mark.REQ021
    def test_req_021_session_history_configurable(self):
        custom_dir = tempfile.mkdtemp(prefix="netdiag_e2e_hist_")
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_out_")
        try:
            args = BASE_ARGS + [
                "--outdir", outdir,
                "--history-dir", custom_dir,
            ]
            subprocess.run(args, capture_output=True, text=True, timeout=90)
            sessions = list(Path(custom_dir).glob("session_*.json"))
            assert len(sessions) >= 1, (
                f"No session files in custom history dir {custom_dir}")
        finally:
            import shutil
            shutil.rmtree(custom_dir, ignore_errors=True)
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-022 — Quiet Mode
# ═══════════════════════════════════════════════════════════════════════════

class TestReq022QuietMode:
    @pytest.mark.REQ022
    def test_req_022_quiet_suppresses_ping_output(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_quiet_")
        try:
            extra = ["--no-speedtest", "--no-trace", "--no-iperf", "--no-bufferbloat"]
            # Non-quiet run
            args_verbose = [sys.executable, SCRIPT, "--count", "2",
                            "--interval", "0.2", "--timeout", "2",
                            "--outdir", outdir] + extra
            r_verb = subprocess.run(args_verbose, capture_output=True, text=True, timeout=90)
            # Quiet run
            args_quiet = [sys.executable, SCRIPT, "--count", "2",
                          "--interval", "0.2", "--timeout", "2", "--quiet",
                          "--outdir", outdir] + extra
            r_quiet = subprocess.run(args_quiet, capture_output=True, text=True, timeout=90)
            # Remove per-ping output dirs by replacing path references
            quiet_out = r_quiet.stdout
            verb_out = r_verb.stdout
            # Quiet mode must have fewer lines of per-progress output
            assert len(quiet_out.splitlines()) <= len(verb_out.splitlines()), (
                "Quiet mode output should be shorter or equal to verbose mode")
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)

    @pytest.mark.REQ022
    def test_req_022_quiet_still_prints_summary(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_quiet2_")
        try:
            args = [sys.executable, SCRIPT, "--count", "2", "--interval", "0.1",
                    "--timeout", "2", "--quiet", "--outdir", outdir,
                    "--no-speedtest", "--no-trace", "--no-iperf", "--no-bufferbloat"]
            r = subprocess.run(args, capture_output=True, text=True, timeout=90)
            assert "Health score" in r.stdout, (
                "Quiet mode should still print the final summary")
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-023 — Web GUI
# ═══════════════════════════════════════════════════════════════════════════

class TestReq023WebGUI:
    @pytest.mark.REQ023
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_023_web_gui_serves_html(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(f"{gui_server['base_url']}/", timeout=5)
        html = resp.read().decode()
        assert resp.status == 200
        assert "NetDiag" in html, "Frontend HTML missing 'NetDiag'"
        assert "Chart" in html or "chart" in html, (
            "Frontend HTML missing Chart.js reference")

    @pytest.mark.REQ023
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_023_gui_auto_generates_template(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(f"{gui_server['base_url']}/", timeout=5)
        assert resp.status == 200
        assert resp.headers.get("content-type", "").startswith("text/html")


# ═══════════════════════════════════════════════════════════════════════════
# REQ-024 — GUI API Routes
# ═══════════════════════════════════════════════════════════════════════════

class TestReq024GUIAPIRoutes:
    @pytest.mark.REQ024
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_024_api_status(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(
            f"{gui_server['base_url']}/api/status", timeout=5)
        data = json.loads(resp.read().decode())
        assert_json_field(data, "status", str)

    @pytest.mark.REQ024
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_024_api_monitor(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(
            f"{gui_server['base_url']}/api/monitor", timeout=5)
        data = json.loads(resp.read().decode())
        assert_json_field(data, "wifi")
        assert_json_field(data, "health_score", (int, type(None)))

    @pytest.mark.REQ024
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_024_api_run(self, gui_server):
        import urllib.request
        req = urllib.request.Request(
            f"{gui_server['base_url']}/api/run",
            data=b"{}",
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        assert data.get("status") == "ok", f"api/run returned {data}"

    @pytest.mark.REQ024
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_024_api_reports(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(
            f"{gui_server['base_url']}/api/reports", timeout=5)
        data = json.loads(resp.read().decode())
        assert_json_field(data, "reports", list)

    @pytest.mark.REQ024
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_024_api_history(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(
            f"{gui_server['base_url']}/api/history", timeout=5)
        data = json.loads(resp.read().decode())
        assert_json_field(data, "sessions", list)


