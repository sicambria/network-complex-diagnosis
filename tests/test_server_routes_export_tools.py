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

    def _mock_hdir(self, mock_ensure, exists=True, read_text_val="{}"):
        d = MagicMock()
        f = MagicMock()
        d.__truediv__.return_value = f
        f.exists.return_value = exists
        f.read_text.return_value = read_text_val
        mock_ensure.return_value = d
        return d, f

    def test_api_session(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 75}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/session/session_123.json")
            assert resp.status_code == 200
            data = resp.json()
            assert data["health_score"] == 75

    def test_api_session_not_found(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=False)
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/session/nonexistent.json")
            assert resp.status_code == 404

    def test_api_session_parse_error(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val="invalid{{json")
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/session/broken.json")
            assert resp.status_code == 500
            assert resp.json()["error"] == "Parse error"

    def test_api_export_json(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True,
                            read_text_val=json.dumps({"health_score": 80, "gateway_ping": {"rtt_ms": 5.0}}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/session_123.json?format=json")
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
            data = resp.json()
            assert data["health_score"] == 80

    def test_api_export_not_found(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=False)
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/nonexistent.json")
            assert resp.status_code == 404

    def test_api_export_csv_no_ping_data(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 80}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/session_123.json?format=csv")
            assert resp.status_code == 404

    def test_api_export_parse_error(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val="invalid{json")
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/broken.json")
            assert resp.status_code == 500

    def test_api_export_invalid_format(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 80}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/session_123.json?format=pdf")
            assert resp.status_code == 400

    def test_api_export_csv(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            with patch("netdiag_core.reporting.flatten_ping", return_value=[{"target": "gw", "rtt_ms": 5.0}]):
                self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({
                    "health_score": 80,
                    "gateway_ping": {"rtt_ms": 5.0, "ok": True},
                    "internet_ping": {"rtt_ms": 20.0, "ok": True}
                }))
                app, _, _ = self._make_app()
                client = TestClient(app)
                resp = client.get("/api/export/session_123.json?format=csv")
                assert resp.status_code == 200
                assert "text/csv" in resp.headers["content-type"]

    def test_api_export_html(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            with patch("netdiag_core.reporting.ping_summary_rows", return_value=[]):
                self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({
                    "health_score": 80, "diagnosis": [],
                    "timestamp": "2025-01-01", "platform": "linux"
                }))
                app, _, _ = self._make_app()
                client = TestClient(app)
                resp = client.get("/api/export/session_123.json?format=html")
                assert resp.status_code == 200
                assert "text/html" in resp.headers["content-type"]
                assert "NetDiag Report" in resp.text

    def test_api_tools_menu(self):
        from fastapi.testclient import TestClient
        from netdiag import TOOLS_MENU
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/tools/menu")
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert len(data["tools"]) == len(TOOLS_MENU)
        tool_ids = [t["id"] for t in data["tools"]]
        assert "ping_test" in tool_ids
        assert "mtr_test" in tool_ids
        assert "full_diagnostic" in tool_ids
        for t in data["tools"]:
            assert "run" not in t
            assert "id" in t
            assert "name" in t
            assert "layer" in t
            assert "desc" in t

    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    def test_api_tool_run(self, mock_gw, mock_ping):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/tool/run", json={"tool_id": "quick_ping", "params": {"host": "1.1.1.1"}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["tool_id"] == "quick_ping"

    def test_api_tool_run_invalid_json(self):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/tool/run", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_api_tool_run_not_found(self):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/tool/run", json={"tool_id": "nonexistent_tool"})
        assert resp.status_code == 404

    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    def test_api_tool_run_already_running(self, mock_gw, mock_ping):
        from fastapi.testclient import TestClient
        import threading
        block = threading.Event()
        mock_ping.side_effect = lambda *a, **kw: block.wait() or {"ok": True, "rtt_ms": 5.0}
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp1 = client.post("/api/tool/run", json={"tool_id": "quick_ping", "params": {"host": "1.1.1.1"}})
        assert resp1.status_code == 200
        resp2 = client.post("/api/tool/run", json={"tool_id": "quick_ping", "params": {"host": "1.1.1.1"}})
        assert resp2.status_code == 409
        block.set()

    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    def test_api_tool_status_after_run(self, mock_gw, mock_ping):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/tool/run", json={"tool_id": "quick_ping", "params": {"host": "1.1.1.1"}})
        assert resp.status_code == 200
        time.sleep(0.3)
        resp = client.get("/api/tool/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["tool_id"] == "quick_ping"
        assert data["result"]["ok"] is True
        assert data["result"]["rtt_ms"] == 5.0

    @patch("netdiag_core.probes.ping.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1")
    def test_api_tool_status_running(self, mock_gw, mock_ping):
        from fastapi.testclient import TestClient
        import threading
        block = threading.Event()
        mock_ping.side_effect = lambda *a, **kw: block.wait() or {"ok": True, "rtt_ms": 5.0}
        app, _, _ = self._make_app()
        client = TestClient(app)
        client.post("/api/tool/run", json={"tool_id": "quick_ping", "params": {"host": "1.1.1.1"}})
        time.sleep(0.1)
        resp = client.get("/api/tool/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["tool_id"] == "quick_ping"
        block.set()

    def test_api_results_json(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 85}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/results/session_123.json/json")
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
            data = resp.json()
            assert data["health_score"] == 85

    def test_api_results_json_not_found(self):
        from fastapi.testclient import TestClient
        with patch("netdiag_core.config.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=False)
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/results/nonexistent.json/json")
            assert resp.status_code == 404

    def test_routes_exist(self):
        from netdiag import build_app
        app, _, _ = build_app()
        paths = [r.path for r in app.routes]
        expected = [
            "/", "/api/status", "/api/monitor",
            "/api/monitor/start", "/api/monitor/stop",
            "/api/activity", "/api/tools",
            "/api/config", "/api/run",
            "/api/reports", "/api/report/{name}",
            "/api/history", "/api/session/{file}",
            "/api/export/{file}", "/api/tools/menu",
            "/api/tool/run", "/api/tool/status",
            "/api/results/{file}/json",
        ]
        for route in expected:
            assert route in paths, f"Route {route} not found in app routes"
