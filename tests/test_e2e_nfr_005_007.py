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
# NFR-005 — Graceful Degradation
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR005GracefulDegradation:
    @pytest.mark.NFR005
    def test_nfr_005_fallback_procfs_parsers(self):
        """Verify procfs fallback parsers exist for Linux."""
        if not IS_LINUX:
            pytest.skip("procfs is Linux-specific")
        content = _package_source()
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
        req_ids = [f"REQ{i:03d}" for i in range(1, 30)]
        nfr_ids = [f"NFR{i:03d}" for i in range(1, 8)]
        all_ids = set(req_ids + nfr_ids)

        # Collect all REQ/NFR markers across the (now split) e2e requirement files.
        test_markers = set()
        for f in sorted(Path(__file__).resolve().parent.glob("test_e2e_*.py")):
            for m in re.findall(r"@pytest\.mark\.(REQ\d+|NFR\d+)", f.read_text(encoding="utf-8")):
                test_markers.add(m)

        untested = all_ids - test_markers - {"REQ000", "NFR000"}
        assert not untested, (
            f"Requirements without test coverage: {sorted(untested)}")

        # Check that marker IDs are valid
        for m in test_markers:
            assert m in all_ids, f"Unknown test marker: {m}"
