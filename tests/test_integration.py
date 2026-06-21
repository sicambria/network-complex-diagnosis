"""Integration / functional tests — exercise the whole stack end-to-end with NO
internal mocking, complementing the mock-heavy unit suite.

Three angles:
  * GUI round-trip via FastAPI TestClient (real route modules + state + assembled
    static frontend), skipped if fastapi is absent.
  * A real `python3 netdiag.py` CLI subprocess that produces the output artifacts.
  * Cross-module package wiring through the `netdiag` shim.

Assertions are network-tolerant: probes degrade gracefully (Plan B), so we check
structure and well-formedness, not specific live measurements.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import netdiag

REPO = Path(__file__).resolve().parent.parent
SHIM = str(REPO / "netdiag.py")


# --------------------------------------------------------------------------- #
# Package wiring (no network, no fastapi)
# --------------------------------------------------------------------------- #
class TestPackageWiring:
    def test_shim_reexports_public_surface(self):
        for name in ["diagnose", "health_score", "reconcile_icmp", "full_diagnostic",
                     "build_parser", "cli_main", "build_app", "start_server",
                     "TOOLS_MENU", "reliability_verdict", "monitor_snapshot"]:
            assert hasattr(netdiag, name), f"shim missing {name}"

    def test_diagnose_health_integrate_through_shim(self):
        # A synthetic result flows through reconcile -> diagnose -> health_score,
        # all living in different modules, reached via the shim.
        results = {
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5, "avg_ms": 3, "sent": 50},
            "internet_ping": [{"host": "1.1.1.1", "label": "1.1.1.1", "loss_pct": 90,
                               "p95_ms": 20, "received": 2, "sent": 20}],
            "tcp": [{"host": "1.1.1.1", "port": 443, "failure_pct": 0, "attempts": 10}],
            "dns": [{"host": "google.com", "failure_pct": 0, "p95_ms": 20}],
        }
        diags = netdiag.diagnose(results)
        # ICMP rate-limiting must be recognised (single source of truth), not loss.
        titles = " ".join(d["title"] for d in diags)
        assert "rate-limiting" in titles.lower()
        score = netdiag.health_score(results)
        assert isinstance(score, int) and 0 <= score <= 100

    def test_tools_menu_run_closures_are_callable(self):
        # Every tool entry exposes the contract the server relies on.
        for t in netdiag.TOOLS_MENU:
            assert {"id", "name", "run"} <= set(t)
            assert callable(t["run"])


# --------------------------------------------------------------------------- #
# GUI round-trip via TestClient (real routes + assembled frontend)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    app, _current, _parser = netdiag.build_app()
    assert app is not None
    return TestClient(app)


class TestGuiRoundTrip:
    def test_index_serves_assembled_page(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.text
        # All seven tab sections assembled into the shell.
        for tab in ["dashboard", "troubleshoot", "monitor", "history",
                    "settings", "about", "tools"]:
            assert f'id="tab-{tab}"' in body
        assert "/static/styles.css" in body
        assert "/static/js/app1.js" in body

    def test_static_assets_served(self, client):
        css = client.get("/static/styles.css")
        assert css.status_code == 200 and ":root" in css.text
        for js in ["app1.js", "app2.js", "app3.js"]:
            r = client.get(f"/static/js/{js}")
            assert r.status_code == 200 and len(r.text) > 0

    def test_status_idle(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.json()["status"] in ("idle", "running", "done", "stopped", "error")

    def test_tools_menu_lists_all_tools(self, client):
        r = client.get("/api/tools/menu")
        assert r.status_code == 200
        tools = r.json()["tools"]
        assert len(tools) == len(netdiag.TOOLS_MENU)
        # The run closure must NOT be serialised into the JSON.
        assert all("run" not in t for t in tools)

    def test_config_get_and_tools_check(self, client):
        assert client.get("/api/config").status_code == 200
        assert client.get("/api/tools").status_code == 200

    def test_reports_listing(self, client):
        r = client.get("/api/reports")
        assert r.status_code == 200
        assert "reports" in r.json()

    def test_monitor_poll_degrades_gracefully(self, client):
        # Touches real wifi/gateway probes; must return a structured 200 (or a
        # clean 500 with an error message) — never crash the server.
        r = client.get("/api/monitor")
        assert r.status_code in (200, 500)
        assert "health_score" in r.json() or "error" in r.json()

    def test_history_listing(self, client):
        r = client.get("/api/history")
        assert r.status_code == 200
        assert "sessions" in r.json()


# --------------------------------------------------------------------------- #
# Real CLI subprocess — the full stdlib-only stack writes its artifacts
# --------------------------------------------------------------------------- #
class TestCliSubprocess:
    def test_cli_produces_artifacts(self, tmp_path):
        outdir = tmp_path / "diag"
        hist = tmp_path / "hist"
        proc = subprocess.run(
            [sys.executable, SHIM,
             "--count", "1", "--interval", "0.1",
             "--dns-count", "1", "--tcp-count", "1",
             "--no-speedtest", "--no-trace", "--no-iperf", "--no-bufferbloat",
             "--quiet", "--hosts", "1.1.1.1",
             "--outdir", str(outdir), "--history-dir", str(hist)],
            capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, f"CLI failed: {proc.stderr[-800:]}"

        for fname in ["diagnostics.json", "report.txt", "isp_report.txt",
                      "ping_samples.csv", "ping_summary.csv"]:
            assert (outdir / fname).is_file(), f"missing {fname}"

        data = json.loads((outdir / "diagnostics.json").read_text())
        assert isinstance(data.get("health_score"), int)
        assert isinstance(data.get("diagnosis"), list) and data["diagnosis"]
        assert data.get("timestamp")
        # A session was persisted to the history dir.
        assert list(hist.glob("session_*.json")), "no session history written"

    def test_cli_version_and_license(self):
        v = subprocess.run([sys.executable, SHIM, "--version"],
                           capture_output=True, text=True, timeout=30)
        assert v.returncode == 0 and "netdiag v" in v.stdout
        lic = subprocess.run([sys.executable, SHIM, "--license"],
                             capture_output=True, text=True, timeout=30)
        assert lic.returncode == 0 and "GNU Affero" in lic.stdout
