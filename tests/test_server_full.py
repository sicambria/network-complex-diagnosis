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

    @patch("netdiag.IS_LINUX", False)
    @patch("netdiag.IS_MACOS", False)
    @patch("netdiag.IS_WINDOWS", False)
    @patch("netdiag.detect_gateway", return_value=None)
    @patch("netdiag.monitor_snapshot", return_value={"running": False, "sample_count": 0, "targets": {}, "events": [], "active_outages": [], "latest": None, "hints": []})
    @patch("netdiag.now_iso", return_value="2025-01-01T00:00:00")
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

    @patch("netdiag.IS_LINUX", False)
    @patch("netdiag.IS_MACOS", False)
    @patch("netdiag.IS_WINDOWS", False)
    @patch("netdiag.detect_gateway", return_value="192.168.1.1")
    @patch("netdiag.ping_once", return_value={"ok": True, "rtt_ms": 3.2})
    @patch("netdiag.monitor_snapshot", return_value={"running": True, "sample_count": 10, "targets": {}, "events": [], "active_outages": [], "latest": {}, "hints": []})
    @patch("netdiag.now_iso", return_value="2025-01-01T00:00:00")
    def test_api_monitor_with_gateway(self, *mocks):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gateway_latency_ms"] == 3.2

    @patch("netdiag.monitor_snapshot", side_effect=RuntimeError("boom"))
    def test_api_monitor_error(self, mock_snap):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/monitor")
        assert resp.status_code == 500

    @patch("netdiag.monitor_start", return_value=True)
    def test_api_monitor_start(self, mock_start):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is True

    @patch("netdiag.monitor_start", return_value=False)
    def test_api_monitor_start_already_running(self, mock_start):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is False

    @patch("netdiag.monitor_stop", return_value=True)
    def test_api_monitor_stop(self, mock_stop):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stopped"] is True

    @patch("netdiag.monitor_stop", return_value=False)
    def test_api_monitor_stop_not_running(self, mock_stop):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/monitor/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stopped"] is False

    @patch("netdiag.get_activity_log", return_value=[{"ts": "2025-01-01T00:00:00", "kind": "cmd", "label": "ping", "ok": True, "duration_ms": 10.0}])
    def test_api_activity(self, mock_log):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert "activity" in data
        assert len(data["activity"]) == 1

    @patch("netdiag.check_tools", return_value={"ping": True, "mtr": False})
    def test_api_tools(self, mock_check):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "ping" in data
        assert data["mtr"] is False

    @patch("netdiag.load_config", return_value={"ping_count": 20, "ping_interval": 0.5})
    def test_api_config_get(self, mock_load):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ping_count"] == 20

    @patch("netdiag.save_config", return_value={"ping_count": 30, "ping_interval": 1.0})
    def test_api_config_post(self, mock_save):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", json={"ping_count": 30})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ping_count"] == 30

    @patch("netdiag.save_config", return_value={"ping_count": 20, "ping_interval": 0.5})
    def test_api_config_post_bad_body(self, mock_save):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        mock_save.assert_called_once_with({})

    @patch("netdiag.save_config", return_value={"ping_count": 20, "ping_interval": 0.5})
    def test_api_config_post_empty_json(self, mock_save):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", json=None)
        assert resp.status_code == 200
        mock_save.assert_called_once_with({})

    @patch("netdiag.full_diagnostic", return_value={"health_score": 90, "diagnosis": []})
    @patch("netdiag.save_history", return_value="session_123.json")
    def test_api_run(self, mock_save, mock_diag):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "session_id" in data

    def test_api_run_already_running(self):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        state["status"] = "running"
        client = TestClient(app)
        resp = client.post("/api/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"

    @patch("netdiag.Path.is_dir")
    @patch("netdiag.Path.iterdir")
    def test_api_reports(self, mock_iterdir, mock_is_dir):
        from fastapi.testclient import TestClient
        mock_is_dir.return_value = True
        fake_file = MagicMock(spec=Path)
        fake_file.name = "report.txt"
        fake_file.stat.return_value.st_size = 123
        fake_file.stat.return_value.st_mtime = 1000000.0
        mock_iterdir.return_value = [fake_file]
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert "reports" in data
        assert data["reports"][0]["name"] == "report.txt"

    def test_api_reports_existing(self):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert "reports" in data
        assert "dir" in data

    @patch("netdiag.Path.exists")
    @patch("netdiag.Path.is_file")
    @patch("netdiag.Path.read_bytes", return_value=b"hello world")
    def test_api_report(self, mock_read, mock_is_file, mock_exists):
        from fastapi.testclient import TestClient
        mock_exists.return_value = True
        mock_is_file.return_value = True
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/report/some_file.txt")
        assert resp.status_code == 200
        assert resp.text == "hello world"

    def test_api_report_not_found(self):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/report/nonexistent.txt")
        assert resp.status_code == 404

    @patch("netdiag.load_history", return_value=[{"_file": "session_123.json", "health_score": 80}])
    def test_api_history(self, mock_load):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["_file"] == "session_123.json"

    @patch("netdiag.load_history", return_value=[{"_file": "s.json", "raw": "secret"}])
    def test_api_history_strips_raw(self, mock_load):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/history")
        data = resp.json()
        assert "raw" not in data["sessions"][0]

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
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 75}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/session/session_123.json")
            assert resp.status_code == 200
            data = resp.json()
            assert data["health_score"] == 75

    def test_api_session_not_found(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=False)
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/session/nonexistent.json")
            assert resp.status_code == 404

    def test_api_session_parse_error(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val="invalid{{json")
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/session/broken.json")
            assert resp.status_code == 500
            assert resp.json()["error"] == "Parse error"

    def test_api_export_json(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
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
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=False)
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/nonexistent.json")
            assert resp.status_code == 404

    def test_api_export_csv_no_ping_data(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 80}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/session_123.json?format=csv")
            assert resp.status_code == 404

    def test_api_export_parse_error(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val="invalid{json")
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/broken.json")
            assert resp.status_code == 500

    def test_api_export_invalid_format(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            self._mock_hdir(mock_ensure, exists=True, read_text_val=json.dumps({"health_score": 80}))
            app, _, _ = self._make_app()
            client = TestClient(app)
            resp = client.get("/api/export/session_123.json?format=pdf")
            assert resp.status_code == 400

    def test_api_export_csv(self):
        from fastapi.testclient import TestClient
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            with patch("netdiag.flatten_ping", return_value=[{"target": "gw", "rtt_ms": 5.0}]):
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
        with patch("netdiag.ensure_history_dir") as mock_ensure:
            with patch("netdiag.ping_summary_rows", return_value=[]):
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

    @patch("netdiag.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag.detect_gateway", return_value="192.168.1.1")
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

    @patch("netdiag.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag.detect_gateway", return_value="192.168.1.1")
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

    @patch("netdiag.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag.detect_gateway", return_value="192.168.1.1")
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

    @patch("netdiag.ping_once", return_value={"ok": True, "rtt_ms": 5.0})
    @patch("netdiag.detect_gateway", return_value="192.168.1.1")
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
        with patch("netdiag.ensure_history_dir") as mock_ensure:
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
        with patch("netdiag.ensure_history_dir") as mock_ensure:
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
