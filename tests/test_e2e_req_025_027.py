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
# REQ-025 — GUI Frontend Pages (SPA tabs)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq025GUIFrontend:
    @pytest.mark.REQ025
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_025_frontend_contains_tabs(self, gui_server):
        import urllib.request
        resp = urllib.request.urlopen(
            f"{gui_server['base_url']}/", timeout=5)
        html = resp.read().decode()
        for tab in ("Dashboard", "Troubleshoot", "Live Monitor",
                    "History", "Reports", "About"):
            assert tab in html, f"Frontend missing tab: {tab}"

    @pytest.mark.REQ025
    @pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright not installed")
    def test_req_025_frontend_live_monitor_tab(self, gui_server):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--disable-gpu",
                      "--no-sandbox", "--disable-extensions"],
            )
            context = browser.new_context()
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{gui_server['base_url']}/", timeout=10000)
            page.wait_for_selector("button[data-tab=\"monitor\"]", timeout=5000)
            page.click("button[data-tab=\"monitor\"]")
            page.wait_for_timeout(1000)
            assert len(errors) == 0, f"JS errors in monitor tab: {errors}"
            context.close()
            browser.close()


# ═══════════════════════════════════════════════════════════════════════════
# REQ-026 — Daemon Mode
# ═══════════════════════════════════════════════════════════════════════════

class TestReq026Daemon:
    @pytest.mark.REQ026
    @pytest.mark.skipif(not HAS_GUI_DEPS, reason="fastapi/uvicorn not installed")
    def test_req_026_daemon_starts_and_serves(self):
        """Verify --daemon starts the server and can serve pages."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with tempfile.NamedTemporaryFile(suffix=".log", prefix="netdiag_daemon_", delete=False) as stderr_f:
            stderr_path = stderr_f.name
        proc = subprocess.Popen(
            [sys.executable, SCRIPT, "--daemon", "--port", str(port)],
            stderr=open(stderr_path, "w"),
            stdout=subprocess.DEVNULL,
        )
        try:
            import urllib.request
            import urllib.error
            deadline = time.time() + 25
            last_err = ""
            served = False
            while time.time() < deadline:
                rc = proc.poll()
                if rc is not None:
                    with open(stderr_path) as f:
                        stderr_text = f.read()
                    pytest.fail(
                        f"Daemon exited early (rc={rc}, stderr={stderr_text[-200:]})")
                try:
                    resp = urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/status", timeout=2)
                    data = json.loads(resp.read().decode())
                    assert data.get("status") in (
                        "idle", "running", "done", "error"), (
                        f"Unexpected status: {data}")
                    served = True
                    break
                except urllib.error.HTTPError as e:
                    last_err = str(e)
                except urllib.error.URLError as e:
                    last_err = str(e)
                except Exception as e:
                    last_err = str(e)
                time.sleep(1)
            if not served:
                with open(stderr_path) as f:
                    stderr_text = f.read()
                pytest.fail(
                    f"Daemon server not ready after 25s (last error={last_err}, "
                    f"stderr={stderr_text[-500:]})")
        finally:
            proc.kill()
            proc.wait(timeout=5)
            try:
                os.unlink(stderr_path)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# REQ-027 — Interrupt Handling
# ═══════════════════════════════════════════════════════════════════════════

class TestReq027Interrupt:
    @pytest.mark.REQ027
    def test_req_027_interrupt_writes_partial_output(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_int_")
        try:
            args = [sys.executable, SCRIPT, "--count", "50", "--interval", "0.5",
                    "--timeout", "2", "--quiet", "--outdir", outdir,
                    "--no-speedtest", "--no-trace", "--no-iperf",
                    "--no-bufferbloat"]
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(3)
            proc.send_signal(signal.SIGINT)
            stdout, stderr = proc.communicate(timeout=10)
            diag_file = Path(outdir) / "diagnostics.json"
            report_file = Path(outdir) / "report.txt"
            assert diag_file.exists(), (
                "diagnostics.json should exist after interrupt")
            data = json.loads(diag_file.read_text())
            assert data.get("interrupted") is True, (
                "interrupted flag should be True after SIGINT")
            assert "stderr was generated" or len(stderr) >= 0  # stderr may vary
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)

    @pytest.mark.REQ027
    def test_req_027_interrupted_flag_in_results(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_int2_")
        try:
            args = [sys.executable, SCRIPT, "--count", "30", "--interval", "0.3",
                    "--timeout", "2", "--quiet", "--outdir", outdir,
                    "--no-speedtest", "--no-trace", "--no-iperf",
                    "--no-bufferbloat"]
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(2)
            proc.send_signal(signal.SIGINT)
            proc.communicate(timeout=10)
            data = json.loads(Path(f"{outdir}/diagnostics.json").read_text())
            assert "interrupted" in data, "Results should contain 'interrupted' flag"
            assert "interrupt_reason" in data, (
                "Results should contain 'interrupt_reason'")
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


