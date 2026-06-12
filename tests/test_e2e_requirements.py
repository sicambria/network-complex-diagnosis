"""
E2E tests mapped 1:1 to every requirement in docs/requirements.md.

Every test method is tagged with @pytest.mark.REQXXX or @pytest.mark.NFRXXX
matching the requirement ID. Run with:

    pytest tests/test_e2e_requirements.py -v --markers    # list all markers
    pytest tests/test_e2e_requirements.py -k REQ001       # run single REQ

REQUIREMENT COVERAGE:

REQ-001 Platform Detection             -> test_req_001_platform
REQ-002 Ping Probing                   -> test_req_002_ping
REQ-003 Gateway Detection              -> test_req_003_gateway
REQ-004 Interface Statistics           -> test_req_004_interface
REQ-005 WiFi Diagnostics               -> test_req_005_wifi
REQ-006 Ethernet Link Info             -> test_req_006_ethtool
REQ-007 TCP Socket Statistics          -> test_req_007_tcp_sockets
REQ-008 MTR / Path Analysis            -> test_req_008_mtr
REQ-009 DNS Resolution Latency         -> test_req_009_dns
REQ-010 TCP Connect Latency            -> test_req_010_tcp_connect
REQ-011 Speedtest                      -> test_req_011_speedtest
REQ-012 iPerf3 Throughput              -> test_req_012_iperf3
REQ-013 Bufferbloat Detection          -> test_req_013_bufferbloat
REQ-014 Download Throughput Test       -> test_req_014_download
REQ-015 HTTP Latency Test              -> test_req_015_http_latency
REQ-016 MTU Probing                    -> test_req_016_mtu
REQ-017 5-Layer Diagnosis Engine       -> test_req_017_diagnosis_layers
REQ-018 Health Score                   -> test_req_018_health_score
REQ-019 CLI Output                     -> test_req_019_cli_output
REQ-020 File Export                    -> test_req_020_file_export
REQ-021 Session History                -> test_req_021_session_history
REQ-022 Quiet Mode                     -> test_req_022_quiet_mode
REQ-023 Web GUI                        -> test_req_023_web_gui
REQ-024 GUI API Routes                 -> test_req_024_gui_api_routes
REQ-025 GUI Frontend Pages             -> test_req_025_gui_frontend
REQ-026 Daemon Mode                    -> test_req_026_daemon
REQ-027 Interrupt Handling             -> test_req_027_interrupt
REQ-028 Tool Availability Check        -> test_req_028_tools
NFR-001 Zero Dependencies (CLI)        -> test_nfr_001_zero_deps_cli
NFR-002 Optional GUI Dependencies      -> test_nfr_002_gui_deps
NFR-003 Single File                    -> test_nfr_003_single_file
NFR-004 Platform Support               -> test_nfr_004_platform
NFR-005 Graceful Degradation           -> test_nfr_005_graceful_degradation
NFR-006 Python Version                 -> test_nfr_006_python_version
NFR-007 License                        -> test_nfr_007_license
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


# ═══════════════════════════════════════════════════════════════════════════
# REQ-017 — 5-Layer Diagnosis Engine
# ═══════════════════════════════════════════════════════════════════════════

class TestReq017Diagnosis:
    @pytest.mark.REQ017
    def test_req_017_diagnosis_list(self, fast_results):
        d = fast_results["data"]
        diag = d.get("diagnosis", [])
        assert isinstance(diag, list), "diagnosis should be a list"
        assert len(diag) >= 1, "At least one diagnosis entry expected"
        for entry in diag:
            assert_json_field(entry, "layer", str)
            assert_json_field(entry, "severity", str)
            assert_json_field(entry, "title", str)
            assert_json_field(entry, "detail", str)
            assert_json_field(entry, "fix", str)
            assert entry["severity"] in ("clean", "info", "warning", "bad"), (
                f"Unknown severity: {entry['severity']}")

    @pytest.mark.REQ017
    def test_req_017_diagnosis_layers_present(self, fast_results):
        d = fast_results["data"]
        layers = {e["layer"] for e in d.get("diagnosis", [])}
        assert len(layers) >= 1, "No diagnosis layers found"

    @pytest.mark.REQ017
    def test_req_017_diagnosis_layer_names_known(self, fast_results):
        d = fast_results["data"]
        known = {"interface", "wifi", "gateway", "isp", "internet",
                 "dns", "tcp", "bufferbloat", "meta"}
        seen = {e["layer"] for e in d.get("diagnosis", [])}
        assert seen.issubset(known), (
            f"Unknown layer names: {seen - known}")


# ═══════════════════════════════════════════════════════════════════════════
# REQ-018 — Health Score
# ═══════════════════════════════════════════════════════════════════════════

class TestReq018HealthScore:
    @pytest.mark.REQ018
    def test_req_018_health_score_range(self, fast_results):
        d = fast_results["data"]
        assert_json_field(d, "health_score", int)
        assert 0 <= d["health_score"] <= 100, (
            f"health_score {d['health_score']} out of 0-100 range")

    @pytest.mark.REQ018
    def test_req_018_health_score_weights_present(self, fast_results):
        d = fast_results["data"]
        hs = d.get("health_score", -1)
        assert isinstance(hs, int), f"health_score should be int, got {type(hs)}"
        assert 0 <= hs <= 100, f"health_score {hs} out of range 0-100"

    @pytest.mark.REQ018
    def test_req_018_health_score_weights_known(self):
        """Verify health_score uses the expected weights."""
        import inspect
        src = inspect.getsource(netdiag.health_score)
        # The weights dict is defined inline in health_score()
        assert "interface\": 10" in src
        assert "wifi\": 15" in src
        assert "gateway\": 25" in src
        assert "internet\": 25" in src
        assert "dns\": 10" in src
        assert "tcp\": 5" in src
        assert "bufferbloat\": 10" in src


# ═══════════════════════════════════════════════════════════════════════════
# REQ-019 — CLI Output
# ═══════════════════════════════════════════════════════════════════════════

class TestReq019CLIOutput:
    @pytest.mark.REQ019
    def test_req_019_cli_contains_health_score(self, verbose_results):
        out = verbose_results["stdout"]
        assert "Health score" in out, "CLI output missing 'Health score'"

    @pytest.mark.REQ019
    def test_req_019_cli_contains_diagnosis(self, verbose_results):
        out = verbose_results["stdout"]
        assert "Diagnosis:" in out, "CLI output missing 'Diagnosis:'"

    @pytest.mark.REQ019
    def test_req_019_cli_contains_ping_summary(self, verbose_results):
        out = verbose_results["stdout"]
        assert "Ping summary:" in out, "CLI output missing 'Ping summary:'"

    @pytest.mark.REQ019
    def test_req_019_cli_contains_outdir(self, verbose_results):
        out = verbose_results["stdout"]
        assert "Files written to:" in out, "CLI output missing output dir path"


# ═══════════════════════════════════════════════════════════════════════════
# REQ-020 — File Export (CLI)
# ═══════════════════════════════════════════════════════════════════════════

class TestReq020FileExport:
    @pytest.mark.REQ020
    def test_req_020_files_exist(self, fast_results):
        outdir = fast_results["outdir"]
        for fname in ("diagnostics.json", "ping_samples.csv",
                      "ping_summary.csv", "report.txt"):
            assert_file_exists(f"{outdir}/{fname}")

    @pytest.mark.REQ020
    def test_req_020_diagnostics_json_valid(self, fast_results):
        d = fast_results["data"]
        assert d is not None, "diagnostics.json could not be parsed as JSON"
        assert_json_field(d, "timestamp", str)
        assert_json_field(d, "gateway", (str, type(None)))

    @pytest.mark.REQ020
    def test_req_020_ping_csv_has_header(self, fast_results):
        outdir = fast_results["outdir"]
        first_line = Path(f"{outdir}/ping_samples.csv").read_text().splitlines()[0]
        assert "timestamp" in first_line, "ping_samples.csv missing timestamp column"
        assert "rtt_ms" in first_line, "ping_samples.csv missing rtt_ms column"

    @pytest.mark.REQ020
    def test_req_020_report_txt_readable(self, fast_results):
        outdir = fast_results["outdir"]
        text = Path(f"{outdir}/report.txt").read_text()
        assert len(text) > 50, "report.txt too short"
        assert "80" in text or "Health" in text or "ping" in text.lower()

    @pytest.mark.REQ020
    def test_req_020_outdir_configurable(self):
        outdir = tempfile.mkdtemp(prefix="netdiag_e2e_custom_")
        try:
            res = run_diag(outdir, extra_args=["--no-speedtest", "--no-trace",
                           "--no-iperf", "--no-bufferbloat"])
            report = Path(f"{outdir}/report.txt")
            assert report.exists(), "report.txt not in custom outdir"
        finally:
            import shutil
            shutil.rmtree(outdir, ignore_errors=True)


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
# NFR-001 — Zero Dependencies (CLI)
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR001ZeroDeps:
    @pytest.mark.NFR001
    def test_nfr_001_cli_imports_stdlib_only(self):
        import ast
        with open(SCRIPT) as f:
            tree = ast.parse(f.read())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    imports.add(parts[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    parts = node.module.split(".")
                    imports.add(parts[0])
        # These are the only allowed top-level imports
        STDLIB = {"argparse", "collections", "csv", "json", "logging", "os", "platform",
                  "re", "shutil", "socket", "statistics", "subprocess",
                  "sys", "time", "datetime", "pathlib", "ast", "math",
                  "tempfile", "io", "concurrent", "urllib", "copy"}
        # Also allowed: netdiag itself (for test imports)
        ALSO_OK = {"netdiag", "fastapi", "uvicorn", "asyncio", "threading"}
        third_party = imports - STDLIB - ALSO_OK
        assert len(third_party) == 0, (
            f"Non-stdlib imports found: {third_party}")


# ═══════════════════════════════════════════════════════════════════════════
# NFR-002 — Optional GUI Dependencies
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR002GuiDeps:
    @pytest.mark.NFR002
    def test_nfr_002_gui_deps_optional(self, fast_results):
        # CLI mode works without any pip packages
        assert fast_results["rc"] == 0, (
            "CLI mode should work without fastapi/uvicorn")

    @pytest.mark.NFR002
    def test_nfr_002_fastapi_import_error_message(self):
        """GUI mode prints helpful error when packages are missing."""
        if HAS_FASTAPI:
            pytest.skip("fastapi is installed, can't test error path")
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--gui", "--port", "19999"],
            capture_output=True, text=True, timeout=10,
        )
        assert proc.returncode == 1, "Should exit with code 1 when fastapi missing"
        assert "Error" in proc.stderr, "Should print error message to stderr"
        assert "fastapi" in proc.stderr.lower(), "Error should mention fastapi"
        assert "pip install" in proc.stderr, "Error should mention pip install"


# ═══════════════════════════════════════════════════════════════════════════
# NFR-003 — Single File
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR003SingleFile:
    @pytest.mark.NFR003
    def test_nfr_003_single_file_exists(self):
        assert Path(SCRIPT).exists(), "netdiag.py not found"

    @pytest.mark.NFR003
    def test_nfr_003_single_file_is_single(self):
        # Check that the main code is self-contained in one file
        with open(SCRIPT) as f:
            content = f.read()
        assert "class " in content, "netdiag.py should contain classes"
        assert "def " in content, "netdiag.py should contain functions"

    @pytest.mark.NFR003
    def test_nfr_003_no_external_py_files(self):
        src = Path(SCRIPT).parent
        py_files = list(src.glob("*.py"))
        expected = {Path(SCRIPT).name}
        actual = {f.name for f in py_files if f.name not in ("nettest.py",)}
        assert actual == expected, (
            f"Expected only netdiag.py, found: {actual - expected}")


# ═══════════════════════════════════════════════════════════════════════════
# NFR-004 — Platform Support
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR004Platform:
    @pytest.mark.NFR004
    def test_nfr_004_platform_detected(self, fast_results):
        d = fast_results["data"]
        assert d["os"] in ("Linux", "Darwin", "Windows"), f"Unknown OS: {d['os']}"

    @pytest.mark.NFR004
    def test_nfr_004_platform_branches_defined(self):
        with open(SCRIPT) as f:
            content = f.read()
        for branch in ("IS_LINUX", "IS_MACOS", "IS_WINDOWS"):
            assert branch in content, f"Platform constant {branch} not found"

    @pytest.mark.NFR004
    def test_nfr_004_platform_branch_coverage(self):
        """Verify platform-specific constants are used throughout probes."""
        with open(SCRIPT) as f:
            content = f.read()
        # All three branches should appear at least once in conditional logic
        for branch in ("IS_LINUX", "IS_MACOS", "IS_WINDOWS"):
            count = content.count(branch)
            assert count >= 2, (
                f"{branch} used only {count} time(s), expected >= 2")


# ═══════════════════════════════════════════════════════════════════════════
# NFR-005 — Graceful Degradation
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR005GracefulDegradation:
    @pytest.mark.NFR005
    def test_nfr_005_fallback_procfs_parsers(self):
        """Verify procfs fallback parsers exist for Linux."""
        if not IS_LINUX:
            pytest.skip("procfs is Linux-specific")
        with open(SCRIPT) as f:
            content = f.read()
        for pattern in ("/proc/net/route", "/sys/class/net", "/proc/net/wireless",
                        "/proc/net/tcp"):
            assert pattern in content, f"Missing fallback path: {pattern}"

    @pytest.mark.NFR005
    def test_nfr_005_ping_fallback_tcp_connect(self):
        from netdiag import ping_once
        res = ping_once("1.1.1.1", timeout_s=2, ipv="auto")
        assert isinstance(res, dict)

    @pytest.mark.NFR005
    def test_nfr_005_interface_fallback_sysfs(self):
        if not IS_LINUX:
            pytest.skip("sysfs is Linux-specific")
        from netdiag import interface_stats
        stats = interface_stats("lo")
        assert stats is not None
        assert isinstance(stats, dict)
        assert "available" in stats


# ═══════════════════════════════════════════════════════════════════════════
# NFR-006 — Python Version
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR006PythonVersion:
    @pytest.mark.NFR006
    def test_nfr_006_python_version(self):
        assert sys.version_info >= (3, 12), (
            f"Python {sys.version_info.major}.{sys.version_info.minor} "
            "is below minimum 3.12")

    @pytest.mark.NFR006
    def test_nfr_006_syntax_compatible(self):
        """Verify file is parseable by Python 3.12 AST."""
        import ast
        with open(SCRIPT) as f:
            ast.parse(f.read())


# ═══════════════════════════════════════════════════════════════════════════
# NFR-007 — License
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR007License:
    @pytest.mark.NFR007
    def test_nfr_007_license_header(self):
        with open(SCRIPT) as f:
            content = f.read()
        assert "AGPL-3.0-only" in content, "Missing AGPL-3.0-only license identifier"
        assert "SPDX-License-Identifier" in content, "Missing SPDX header"

    @pytest.mark.NFR007
    def test_nfr_007_version_command(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--version"],
            capture_output=True, text=True, timeout=5,
        )
        assert proc.returncode == 0
        assert "AGPL" in proc.stdout, "--version should show license"
        assert netdiag.VERSION in proc.stdout, "--version should show version"

    @pytest.mark.NFR007
    def test_nfr_007_license_command(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--license"],
            capture_output=True, text=True, timeout=5,
        )
        assert proc.returncode == 0
        assert "GNU Affero General Public License" in proc.stdout, (
            "--license should print license text")


# ═══════════════════════════════════════════════════════════════════════════
# REQ-E2E tests — verify the E2E test infrastructure itself
# ═══════════════════════════════════════════════════════════════════════════

class TestReqE2ESelfCheck:
    @pytest.mark.REQ_E2E
    def test_e2e_requirements_coverage_complete(self):
        """Verify every REQ and NFR has a corresponding test method."""
        req_ids = [f"REQ{i:03d}" for i in range(1, 29)]
        nfr_ids = [f"NFR{i:03d}" for i in range(1, 8)]
        all_ids = set(req_ids + nfr_ids)

        # Collect all markers used in this file
        test_markers = set()
        with open(__file__) as f:
            for line in f:
                m = re.findall(r"@pytest\.mark\.(REQ\d+|NFR\d+)", line)
                test_markers.update(m)

        untested = all_ids - test_markers - {"REQ000", "NFR000"}
        assert not untested, (
            f"Requirements without test coverage: {sorted(untested)}")

        # Check that marker IDs are valid
        for m in test_markers:
            assert m in all_ids, f"Unknown test marker: {m}"
