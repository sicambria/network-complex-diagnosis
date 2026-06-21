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
# NFR-001 — Zero Dependencies (CLI)
# ═══════════════════════════════════════════════════════════════════════════

class TestNFR001ZeroDeps:
    @pytest.mark.NFR001
    def test_nfr_001_cli_imports_stdlib_only(self):
        # The CLI core (shim + entire netdiag_core package) imports only stdlib +
        # first-party netdiag_core. Optional GUI deps (fastapi/uvicorn) may appear
        # but are imported lazily inside functions, never as a hard CLI dependency.
        import ast
        imports = set()
        files = [Path(SCRIPT)] + sorted(CORE_DIR.rglob("*.py"))
        for fpath in files:
            tree = ast.parse(fpath.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    # Relative imports (level > 0) are intra-package, first-party.
                    if node.module and node.level == 0:
                        imports.add(node.module.split(".")[0])
        # These are the only allowed top-level imports
        STDLIB = {"argparse", "collections", "csv", "json", "logging", "os", "platform",
                  "re", "shutil", "socket", "statistics", "subprocess",
                  "sys", "time", "datetime", "pathlib", "ast", "math",
                  "tempfile", "io", "concurrent", "urllib", "copy", "ssl", "itertools", "html"}
        # Also allowed: first-party package + optional GUI deps (lazy)
        ALSO_OK = {"netdiag", "netdiag_core", "fastapi", "uvicorn", "asyncio", "threading"}
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

class TestNFR003PackageArchitecture:
    @pytest.mark.NFR003
    def test_nfr_003_entry_shim_exists(self):
        assert Path(SCRIPT).exists(), "netdiag.py entry shim not found"

    @pytest.mark.NFR003
    def test_nfr_003_shim_is_thin_reexport(self):
        # The entry point is a thin shim: it re-exports the package surface and
        # delegates to cli_main, rather than defining the implementation itself.
        content = Path(SCRIPT).read_text(encoding="utf-8")
        assert "from netdiag_core" in content, "shim should re-export netdiag_core"
        assert "cli_main()" in content, "shim should delegate to cli_main"

    @pytest.mark.NFR003
    def test_nfr_003_core_package_layout(self):
        # The implementation lives in the netdiag_core package, organised into the
        # documented layers, with every module kept under 400 lines.
        assert (CORE_DIR / "__init__.py").exists()
        for sub in ("probes", "analysis", "server", "frontend"):
            assert (CORE_DIR / sub).is_dir(), f"missing netdiag_core/{sub}"
        oversized = [str(p) for p in CORE_DIR.rglob("*.py")
                     if len(p.read_text(encoding="utf-8").splitlines()) > 400]
        assert not oversized, f"modules over 400 lines: {oversized}"


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
        """Verify platform-specific constants are used throughout the probes."""
        content = _package_source()
        # All three branches should appear at least once in conditional logic
        for branch in ("IS_LINUX", "IS_MACOS", "IS_WINDOWS"):
            count = content.count(branch)
            assert count >= 2, (
                f"{branch} used only {count} time(s), expected >= 2")


