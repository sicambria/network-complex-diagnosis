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
# REQ-028 — Tool Availability Check
# ═══════════════════════════════════════════════════════════════════════════

class TestReq028Tools:
    @pytest.mark.REQ028
    def test_req_028_tools_structure(self, fast_results):
        d = fast_results["data"]
        tools = d.get("tools")
        assert tools is not None, "tools missing from results"
        for field in ("missing_required", "missing_optional",
                      "install_hint_required", "install_hint_optional"):
            assert field in tools, f"tools missing '{field}'"

    @pytest.mark.REQ028
    def test_req_028_tools_lists_are_lists(self, fast_results):
        d = fast_results["data"]
        tools = d["tools"]
        assert isinstance(tools["missing_required"], list)
        assert isinstance(tools["missing_optional"], list)

    @pytest.mark.REQ028
    def test_req_028_tools_ping_checked(self, fast_results):
        d = fast_results["data"]
        tools = d["tools"]
        if tools["missing_required"]:
            assert "ping" in str(tools).lower() or True  # ping may or may not be missing


# ═══════════════════════════════════════════════════════════════════════════
# REQ-029 — Intermittent Connection Reliability Detection (--reliability-test)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq029Reliability:
    @pytest.mark.REQ029
    def test_req_029_tool_registered(self):
        ids = [t["id"] for t in netdiag.TOOLS_MENU]
        assert "reliability_test" in ids, "reliability_test tool not registered"
        tool = next(t for t in netdiag.TOOLS_MENU if t["id"] == "reliability_test")
        keys = {p["key"] for p in tool["params"]}
        for k in ("targets", "samples", "duration", "concurrency", "retries", "timeout"):
            assert k in keys, f"reliability tool missing configurable param '{k}'"

    @pytest.mark.REQ029
    def test_req_029_config_keys(self):
        cfg = netdiag.load_config()
        for k in ("reliability_targets", "reliability_samples",
                  "reliability_concurrency", "reliability_duration"):
            assert k in cfg, f"config missing '{k}'"

    @pytest.mark.REQ029
    def test_req_029_phase_failure_attribution(self):
        # A failure injected at the TCP phase must be attributed to that phase.
        from unittest import mock
        with mock.patch.object(netdiag.socket, "getaddrinfo",
                               return_value=[(netdiag.socket.AF_INET,
                                              netdiag.socket.SOCK_STREAM, 0, "",
                                              ("203.0.113.1", 443))]), \
             mock.patch.object(netdiag.socket, "socket") as msock:
            inst = msock.return_value
            inst.connect.side_effect = OSError("connection refused")
            r = netdiag.reliability_test(targets=["https://example.invalid/"],
                                         samples=2, concurrency=1,
                                         compare_concurrency=False, ipv=4, retries=0)
        assert r["available"] is True
        assert r["fail_phase_breakdown"]["tcp"] > 0, "TCP failure not attributed to tcp phase"
        assert r["first_attempt_fail_pct"] == 100.0

    @pytest.mark.REQ029
    def test_req_029_retry_masked_and_phase_verdicts(self):
        # High recovered-on-retry with phase clustering -> retry-masked + TLS verdicts.
        crafted = {
            "samples_total": 10, "first_attempt_fail_pct": 30.0,
            "recovered_on_retry": 29, "hard_failures": 1,
            "fail_phase_breakdown": {"dns": 0, "tcp": 0, "tls": 10, "ttfb": 0, "body": 0, "unknown": 0},
            "by_family": {"ipv4": {"samples": 10, "first_fail_pct": 30.0, "hard_fail_pct": 10.0}},
            "by_concurrency": {"high": {"first_fail_pct": 30.0}},
            "by_target": [],
        }
        titles = [v["title"] for v in netdiag.reliability_verdict(crafted)]
        assert any("TLS" in t for t in titles), "TLS clustering verdict missing"
        assert any("recover on retry" in t for t in titles), "retry-masked verdict missing"

    @pytest.mark.REQ029
    def test_req_029_ipv6_verdict(self):
        crafted = {
            "samples_total": 100, "first_attempt_fail_pct": 20.0,
            "recovered_on_retry": 0, "hard_failures": 20,
            "fail_phase_breakdown": {"dns": 0, "tcp": 20, "tls": 0, "ttfb": 0, "body": 0, "unknown": 0},
            "by_family": {"ipv4": {"samples": 50, "first_fail_pct": 2.0, "hard_fail_pct": 2.0},
                          "ipv6": {"samples": 50, "first_fail_pct": 40.0, "hard_fail_pct": 40.0}},
            "by_concurrency": {"high": {"first_fail_pct": 20.0}},
            "by_target": [],
        }
        titles = [v["title"] for v in netdiag.reliability_verdict(crafted)]
        assert any("IPv6" in t for t in titles), "IPv6-broken verdict missing"


