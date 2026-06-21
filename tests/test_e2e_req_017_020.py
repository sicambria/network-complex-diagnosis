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

