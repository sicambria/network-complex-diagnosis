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
# REQ-001 — Platform Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestReq001Platform:
    @pytest.mark.REQ001
    def test_req_001_platform(self, fast_results):
        d = fast_results["data"]
        assert_json_field(d, "os", str)
        assert d["os"] in ("Linux", "Darwin", "Windows"), f"Unknown OS: {d['os']}"
        assert_json_field(d, "platform", str)
        assert len(d["platform"]) > 0

    @pytest.mark.REQ001
    def test_req_001_platform_constants_match(self):
        assert netdiag.OS_NAME == platform.system()
        assert netdiag.IS_LINUX == (platform.system() == "Linux")
        assert netdiag.IS_MACOS == (platform.system() == "Darwin")
        assert netdiag.IS_WINDOWS == (platform.system() == "Windows")


# ═══════════════════════════════════════════════════════════════════════════
# REQ-002 — Ping Probing
# ═══════════════════════════════════════════════════════════════════════════

class TestReq002Ping:
    @pytest.mark.REQ002
    def test_req_002_ping_gateway_present(self, fast_results):
        d = fast_results["data"]
        gp = get_data_key(d, "gateway_ping")
        assert gp is not None, "gateway_ping is missing"
        for field in ("sent", "received", "loss_pct", "avg_ms", "min_ms",
                      "max_ms", "p95_ms", "p99_ms", "jitter_ms"):
            assert_json_field(gp, field, msg=f"gateway_ping missing '{field}'")
        assert gp["sent"] >= 1
        assert isinstance(gp["loss_pct"], (int, float))

    @pytest.mark.REQ002
    def test_req_002_ping_internet_present(self, fast_results):
        d = fast_results["data"]
        ip = get_data_key(d, "internet_ping")
        assert isinstance(ip, list), "internet_ping should be a list"
        assert len(ip) >= 1, "No internet ping results"
        row = ip[0]
        for field in ("host", "sent", "received", "loss_pct", "avg_ms", "p95_ms", "jitter_ms"):
            assert_json_field(row, field, msg=f"internet_ping[0] missing '{field}'")

    @pytest.mark.REQ002
    def test_req_002_ping_classify(self, fast_results):
        from netdiag import classify_ping
        d = fast_results["data"]
        valid = {"clean", "high_jitter", "latency_spikes", "bad_latency_spikes",
                 "some_loss", "bad_loss"}
        gp = d.get("gateway_ping", {})
        if gp:
            cls = classify_ping(gp)
            assert cls in valid, f"classify_ping returned '{cls}' not in {valid}"
        for ip in d.get("internet_ping", []):
            cls = classify_ping(ip)
            assert cls in valid, f"classify_ping returned '{cls}' not in {valid}"

    @pytest.mark.REQ002
    def test_req_002_ping_samples(self, fast_results):
        d = fast_results["data"]
        outdir = fast_results["outdir"]
        assert_file_exists(f"{outdir}/ping_samples.csv")
        assert_file_exists(f"{outdir}/ping_summary.csv")
        lines = Path(f"{outdir}/ping_samples.csv").read_text().strip().splitlines()
        assert len(lines) >= 2, "ping_samples.csv should have header + data rows"

    @pytest.mark.REQ002
    def test_req_002_ipv4_flag_does_not_crash(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_ipv_")
        try:
            r4 = run_diag(outdir, extra_args=["--ipv4", "--no-speedtest",
                          "--no-trace", "--no-iperf", "--no-bufferbloat"])
            assert r4["rc"] == 0, f"--ipv4 mode failed with rc={r4['rc']}"
            assert r4["data"] is not None
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)

    @pytest.mark.REQ002
    def test_req_002_ipv6_flag_graceful(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_ipv_")
        try:
            r6 = run_diag(outdir, extra_args=["--ipv6", "--no-speedtest",
                          "--no-trace", "--no-iperf", "--no-bufferbloat"],
                          timeout=30)
            # --ipv6 may fail to resolve on systems without IPv6, but must not crash
            assert r6["rc"] in (0,), f"--ipv6 mode exited with rc={r6['rc']}"
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════
# REQ-003 — Gateway Detection
# ═══════════════════════════════════════════════════════════════════════════

class TestReq003Gateway:
    @pytest.mark.REQ003
    def test_req_003_gateway_ip(self, fast_results):
        d = fast_results["data"]
        assert_json_field(d, "gateway", (str, type(None)))
        assert_json_field(d, "default_interface", (str, type(None)))

    @pytest.mark.REQ003
    def test_req_003_gateway_ping(self, fast_results):
        d = fast_results["data"]
        gp = get_data_key(d, "gateway_ping")
        if d.get("gateway") and gp:
            assert gp.get("host") == d["gateway"], (
                f"gateway_ping host {gp.get('host')} != gateway {d['gateway']}")

    @pytest.mark.REQ003
    def test_req_003_detect_gateway_stdlib_fallback(self):
        gw = netdiag.detect_gateway()
        if gw:
            assert re.match(r"^\d+\.\d+\.\d+\.\d+$", gw), (
                f"Gateway '{gw}' not a valid IPv4 address")
        iface = netdiag.get_default_interface()
        if iface:
            assert isinstance(iface, str) and len(iface) > 0


# ═══════════════════════════════════════════════════════════════════════════
# REQ-004 — Interface Statistics
# ═══════════════════════════════════════════════════════════════════════════

class TestReq004Interface:
    @pytest.mark.REQ004
    def test_req_004_interface_stats_structure(self, fast_results):
        d = fast_results["data"]
        iface = d.get("interface")
        if iface and iface.get("available"):
            for side in ("rx", "tx"):
                assert side in iface, f"interface missing '{side}'"
                for field in ("errors", "dropped", "overruns", "carrier"):
                    assert field in iface[side], (
                        f"interface.{side} missing '{field}'")
                    assert isinstance(iface[side][field], int), (
                        f"interface.{side}.{field} should be int")


# ═══════════════════════════════════════════════════════════════════════════
# REQ-005 — WiFi Diagnostics
# ═══════════════════════════════════════════════════════════════════════════

class TestReq005WiFi:
    @pytest.mark.REQ005
    def test_req_005_wifi_structure(self, fast_results):
        d = fast_results["data"]
        wifi = d.get("wifi")
        if wifi and wifi.get("available"):
            for field in ("signal_dbm", "frequency", "channel_util", "noise_dbm"):
                assert field in wifi, f"wifi missing '{field}'"
        # When not available, must have reason
        if wifi and not wifi.get("available"):
            assert "reason" in wifi, "wifi not available but missing 'reason'"

    @pytest.mark.REQ005
    def test_req_005_wifi_procfs_fallback(self):
        if IS_LINUX:
            from netdiag import wifi_info
            wi = wifi_info(None)
            if wi:
                assert isinstance(wi, dict)


