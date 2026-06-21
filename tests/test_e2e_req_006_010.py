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
# REQ-006 — Ethernet Link Info (ethtool, Linux-only)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq006Ethtool:
    @pytest.mark.REQ006
    def test_req_006_ethtool_structure(self, fast_results):
        d = fast_results["data"]
        eth = d.get("ethtool")
        if eth is None:
            pytest.skip("ethtool not available on this system")
        assert isinstance(eth, dict)
        if eth.get("available"):
            for field in ("speed_mbps", "duplex", "link_detected"):
                assert field in eth, f"ethtool missing '{field}'"

    @pytest.mark.REQ006
    def test_req_006_ethtool_linux_only(self):
        from netdiag import ethtool_info
        ei = ethtool_info("lo")
        if IS_LINUX:
            assert isinstance(ei, dict)
        else:
            assert ei is None or ei.get("available") is False


# ═══════════════════════════════════════════════════════════════════════════
# REQ-007 — TCP Socket Statistics
# ═══════════════════════════════════════════════════════════════════════════

class TestReq007TcpSockets:
    @pytest.mark.REQ007
    def test_req_007_tcp_sockets_structure(self, fast_results):
        d = fast_results["data"]
        ts = d.get("tcp_sockets")
        if ts and ts.get("available"):
            for field in ("connections", "total_retransmits", "avg_rtt_ms"):
                assert field in ts, f"tcp_sockets missing '{field}'"
                assert isinstance(ts.get(field), (int, float, type(None)))


# ═══════════════════════════════════════════════════════════════════════════
# REQ-008 — MTR / Path Analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestReq008MTR:
    @pytest.mark.REQ008
    def test_req_008_mtr_structure(self, trace_results):
        d = trace_results["data"]
        mtr = d.get("mtr")
        if mtr is None:
            pytest.skip("MTR not available")
        assert isinstance(mtr, dict)
        assert_json_field(mtr, "tool", str)
        assert_json_field(mtr, "host", str)
        hops = mtr.get("hops", [])
        if hops:
            for h in hops:
                assert_json_field(h, "hop", int)
                assert_json_field(h, "loss_pct", (int, float))
                assert_json_field(h, "avg_ms", (float, type(None)))

    @pytest.mark.REQ008
    def test_req_008_no_trace_skips_mtr(self, fast_results):
        d = fast_results["data"]
        assert d["mtr"] is None, "MTR should be None when --no-trace is passed"

    @pytest.mark.REQ008
    def test_req_008_mtr_no_host_configurable(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_mtr_")
        try:
            res = run_diag(outdir, extra_args=["--hosts", "8.8.8.8",
                           "--no-speedtest", "--no-iperf", "--no-bufferbloat"])
            if res["data"] and res["data"].get("mtr"):
                assert "8.8.8.8" in str(res["data"]["mtr"].get("host", ""))
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-009 — DNS Resolution Latency
# ═══════════════════════════════════════════════════════════════════════════

class TestReq009DNS:
    @pytest.mark.REQ009
    def test_req_009_dns_structure(self, fast_results):
        d = fast_results["data"]
        dns_list = d.get("dns", [])
        assert isinstance(dns_list, list)
        assert len(dns_list) >= 1, "No DNS test results"
        for entry in dns_list:
            assert_json_field(entry, "host", str)
            assert_json_field(entry, "queries", int)
            assert_json_field(entry, "failures", int)
            assert_json_field(entry, "failure_pct", (int, float))
            assert_json_field(entry, "avg_ms", (float, type(None)))
            assert_json_field(entry, "p95_ms", (float, type(None)))

    @pytest.mark.REQ009
    def test_req_009_dns_error_handling(self):
        from netdiag import dns_test
        host = "nonexistent-domain-xyz123.test"
        res = dns_test(host, count=3)
        assert res["failures"] >= 1 or res["failure_pct"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# REQ-010 — TCP Connect Latency
# ═══════════════════════════════════════════════════════════════════════════

class TestReq010TCPConnect:
    @pytest.mark.REQ010
    def test_req_010_tcp_structure(self, fast_results):
        d = fast_results["data"]
        tcp_list = d.get("tcp", [])
        assert isinstance(tcp_list, list)
        assert len(tcp_list) >= 1, "No TCP test results"
        for entry in tcp_list:
            assert_json_field(entry, "host", str)
            assert_json_field(entry, "port", int)
            assert_json_field(entry, "attempts", int)
            assert_json_field(entry, "failures", int)
            assert_json_field(entry, "failure_pct", (int, float))
            assert_json_field(entry, "avg_ms", (float, type(None)))
            assert_json_field(entry, "p95_ms", (float, type(None)))

    @pytest.mark.REQ010
    def test_req_010_tcp_count_configurable(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_tcp_")
        try:
            res = run_diag(outdir, extra_args=["--tcp-count", "2",
                           "--no-speedtest", "--no-trace", "--no-iperf",
                           "--no-bufferbloat"])
            for entry in res["data"].get("tcp", []):
                assert entry["attempts"] == 2, (
                    f"Expected 2 TCP attempts, got {entry['attempts']}")
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


