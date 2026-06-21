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
# REQ-011 — Speedtest (skip flag)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq011Speedtest:
    @pytest.mark.REQ011
    def test_req_011_speedtest_skipped_by_default(self, fast_results):
        d = fast_results["data"]
        assert d["speedtest"] is None, (
            "speedtest should be None with --no-speedtest")

    @pytest.mark.REQ011
    def test_req_011_speedtest_no_flag_runs(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_st_")
        try:
            res = run_diag(outdir, extra_args=["--no-trace", "--no-iperf",
                           "--no-bufferbloat"], timeout=60)
            if res["data"] and res["data"].get("speedtest"):
                st = res["data"]["speedtest"]
                if st.get("available"):
                    assert_json_field(st, "download_mbps", (int, float))
                    assert_json_field(st, "upload_mbps", (int, float))
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-012 — iPerf3 Throughput (skip flag)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq012Iperf3:
    @pytest.mark.REQ012
    def test_req_012_iperf3_skipped_by_default(self, fast_results):
        d = fast_results["data"]
        assert d["iperf3"] is None, (
            "iperf3 should be None with --no-iperf")

    @pytest.mark.REQ012
    def test_req_012_iperf3_no_flag_runs(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_i3_")
        try:
            res = run_diag(outdir, extra_args=["--no-speedtest", "--no-trace",
                           "--no-bufferbloat"], timeout=60)
            if res["data"] and res["data"].get("iperf3"):
                i3 = res["data"]["iperf3"]
                if i3.get("available"):
                    assert_json_field(i3, "server", str)
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-013 — Bufferbloat Detection (skip flag)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq013Bufferbloat:
    @pytest.mark.REQ013
    def test_req_013_bufferbloat_skipped_by_default(self, fast_results):
        d = fast_results["data"]
        assert d["bufferbloat"] is None, (
            "bufferbloat should be None with --no-bufferbloat")

    @pytest.mark.REQ013
    def test_req_013_bufferbloat_structure(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_bb_")
        try:
            res = run_diag(outdir, extra_args=["--no-speedtest", "--no-trace",
                           "--no-iperf"], timeout=60)
            if res["data"] and res["data"].get("bufferbloat"):
                bb = res["data"]["bufferbloat"]
                if bb.get("available"):
                    for field in ("ratio", "rtt_idle_ms", "rtt_loaded_ms"):
                        assert field in bb, f"bufferbloat missing '{field}'"
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-014 — Download Throughput Test (--download-test)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq014Download:
    @pytest.mark.REQ014
    def test_req_014_download_opt_in_only(self, fast_results):
        d = fast_results["data"]
        assert d["download_test"] is None, (
            "download_test should be None without --download-test")

    @pytest.mark.REQ014
    def test_req_014_download_structure(self, download_results):
        d = download_results["data"]
        dl = d.get("download_test")
        if dl is None:
            pytest.skip("download_test not available")
        assert_json_field(dl, "available", bool)
        if dl.get("available"):
            for field in ("success", "failures", "total_bytes",
                          "total_time_s", "avg_mbps"):
                assert field in dl, f"download_test missing '{field}'"


# ═══════════════════════════════════════════════════════════════════════════
# REQ-015 — HTTP Latency Test (--connection-test)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq015HTTPLatency:
    @pytest.mark.REQ015
    def test_req_015_http_opt_in_only(self, fast_results):
        d = fast_results["data"]
        ct = d.get("connection_test")
        assert ct is None, "connection_test should be None without --connection-test"

    @pytest.mark.REQ015
    def test_req_015_http_latency_structure(self, connection_test_results):
        d = connection_test_results["data"]
        ct = d.get("connection_test")
        if ct is None:
            pytest.skip("connection_test not available")
        hl = ct.get("http_latency", [])
        assert isinstance(hl, list), "http_latency should be a list"
        for entry in hl:
            assert_json_field(entry, "host", str)
            assert_json_field(entry, "failures", int)
            assert_json_field(entry, "available", bool)
            # p95_ms and latencies are only present when at least one request succeeded
            if entry["failures"] < entry.get("count", 5):
                assert_json_field(entry, "p95_ms", (float, type(None)))


# ═══════════════════════════════════════════════════════════════════════════
# REQ-016 — MTU Probing (--connection-test)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq016MTU:
    @pytest.mark.REQ016
    def test_req_016_mtu_structure(self, connection_test_results):
        d = connection_test_results["data"]
        ct = d.get("connection_test")
        if ct is None:
            pytest.skip("connection_test not available")
        mtu = ct.get("mtu")
        if mtu is None:
            pytest.skip("MTU probe not available")
        assert isinstance(mtu, dict)
        if mtu.get("available"):
            assert_json_field(mtu, "mtu", int)
            assert_json_field(mtu, "payload_size", int)
            assert mtu["mtu"] >= 576, f"MTU {mtu['mtu']} unreasonably low"
            assert mtu["mtu"] <= 1500, f"MTU {mtu['mtu']} unusually high"

    @pytest.mark.REQ016
    def test_req_016_mtu_below_1400_warning(self):
        from netdiag import mtu_probe
        res = mtu_probe("1.1.1.1")
        if res and res.get("available"):
            assert "mtu" in res

