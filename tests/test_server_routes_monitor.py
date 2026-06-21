import pytest
pytest.importorskip("fastapi")

import json
import time
from unittest.mock import patch, MagicMock
from pathlib import Path


class TestServerFull:

    def setup_method(self):
        import netdiag
        netdiag.MONITOR_STATE["running"] = False
        netdiag.MONITOR_STATE["samples"].clear()
        netdiag.MONITOR_STATE["events"].clear()
        netdiag.MONITOR_STATE["outages"].clear()
        netdiag.MONITOR_STATE["started_at"] = None
        netdiag.MONITOR_STATE["thread"] = None
        netdiag.ACTIVITY_LOG.clear()

    def _make_app(self):
        from netdiag import build_app
        app, state, parser = build_app()
        return app, state, parser

    def test_index(self):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_api_status_idle(self):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"

    def test_api_status_with_results(self):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        state["status"] = "done"
        state["results"] = {"health_score": 85, "gateway_ping": {"samples": [1, 2]}}
        client = TestClient(app)
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert "samples" not in data["results"].get("gateway_ping", {})

    @patch("netdiag_core.runtime.IS_LINUX", False)
    @patch("netdiag_core.runtime.IS_MACOS", False)
    @patch("netdiag_core.runtime.IS_WINDOWS", False)
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value=None)
    @patch("netdiag_core.monitor.monitor_snapshot", return_value={"running": False, "sample_count": 0, "targets": {}, "events": [], "active_outages": [], "latest": None, "hints": []})
    @patch("netdiag_core.runtime.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor(self, mock_now, mock_snap, mock_gw, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert "wifi" in data
        assert "gateway_latency_ms" in data
        assert "health_score" in data
        assert data["health_score"] == 50

    @patch("netdiag_core.runtime.IS_LINUX", True)
    @patch("netdiag_core.probes.wifi._proc_net_wireless_any", return_value=None)
    @patch("netdiag_core.runtime.has_tool", return_value=True)
    @patch("netdiag_core.probes.netinfo.detect_wireless_interface", return_value="wlan0")
    @patch("netdiag_core.probes.wifi.wifi_info", return_value={"signal_dbm": -50, "available": True})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 3.0})
    @patch("netdiag_core.monitor.monitor_snapshot", return_value={"running": True})
    @patch("netdiag_core.runtime.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor_linux_wifi(self, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wifi"]["signal_dbm"] == -50
        assert data["gateway_latency_ms"] == 3.0

    @patch("netdiag_core.runtime.IS_LINUX", False)
    @patch("netdiag_core.runtime.IS_MACOS", True)
    @patch("netdiag_core.probes.netinfo.detect_wireless_interface", return_value="en0")
    @patch("netdiag_core.probes.wifi.wifi_info", return_value={"signal_dbm": -45, "available": True})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag_core.monitor.monitor_snapshot", return_value={"running": True})
    @patch("netdiag_core.runtime.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor_macos_wifi(self, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wifi"]["signal_dbm"] == -45

    @patch("netdiag_core.runtime.IS_LINUX", False)
    @patch("netdiag_core.runtime.IS_MACOS", False)
    @patch("netdiag_core.runtime.IS_WINDOWS", True)
    @patch("netdiag_core.probes.netinfo.detect_wireless_interface", return_value="Wi-Fi")
    @patch("netdiag_core.probes.wifi.wifi_info", return_value={"signal_dbm": -60, "available": True})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 7.0})
    @patch("netdiag_core.monitor.monitor_snapshot", return_value={"running": True})
    @patch("netdiag_core.runtime.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor_windows_wifi(self, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["wifi"]["signal_dbm"] == -60

    @patch("netdiag_core.runtime.IS_LINUX", False)
    @patch("netdiag_core.runtime.IS_MACOS", False)
    @patch("netdiag_core.runtime.IS_WINDOWS", True)
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": False, "rtt_ms": None})
    @patch("netdiag_core.probes.ping._tcp_ping", return_value={"ok": True, "rtt_ms": 15.0})
    @patch("netdiag_core.monitor.monitor_snapshot", return_value={"running": True})
    @patch("netdiag_core.runtime.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor_tcp_fallback(self, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gateway_latency_ms"] == 15.0

    @patch("netdiag_core.runtime.IS_LINUX", False)
    @patch("netdiag_core.runtime.IS_MACOS", False)
    @patch("netdiag_core.runtime.IS_WINDOWS", False)
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 3.2})
    @patch("netdiag_core.monitor.monitor_snapshot", return_value={"running": True, "sample_count": 10, "targets": {}, "events": [], "active_outages": [], "latest": {}, "hints": []})
    @patch("netdiag_core.runtime.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor_with_gateway(self, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gateway_latency_ms"] == 3.2

    @patch("netdiag_core.monitor.monitor_snapshot", side_effect=RuntimeError("boom"))
    def test_api_monitor_error(self, mock_snap):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 500

    @patch("netdiag_core.monitor.monitor_start", return_value=True)
    def test_api_monitor_start(self, mock_start):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is True

    @patch("netdiag_core.monitor.monitor_start", return_value=False)
    def test_api_monitor_start_already_running(self, mock_start):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is False

    @patch("netdiag_core.monitor.monitor_stop", return_value=True)
    def test_api_monitor_stop(self, mock_stop):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stopped"] is True

    @patch("netdiag_core.monitor.monitor_stop", return_value=False)
    def test_api_monitor_stop_not_running(self, mock_stop):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stopped"] is False

    @patch("netdiag_core.runtime.get_activity_log", return_value=[{"ts": "2025-01-01T00:00:00", "kind": "cmd", "label": "ping", "ok": True, "duration_ms": 10.0}])
    def test_api_activity(self, mock_log):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert "activity" in data
        assert len(data["activity"]) == 1

    @patch("netdiag_core.runtime.check_tools", return_value={"ping": True, "mtr": False})
    def test_api_tools(self, mock_check):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "ping" in data
        assert data["mtr"] is False

