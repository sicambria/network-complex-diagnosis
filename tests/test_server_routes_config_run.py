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
    @patch("netdiag_core.config.load_config", return_value={"ping_count": 20, "ping_interval": 0.5})
    def test_api_config_get(self, mock_load):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ping_count"] == 20

    @patch("netdiag_core.config.save_config", return_value={"ping_count": 30, "ping_interval": 1.0})
    def test_api_config_post(self, mock_save):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", json={"ping_count": 30})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ping_count"] == 30

    @patch("netdiag_core.config.save_config", return_value={"ping_count": 20, "ping_interval": 0.5})
    def test_api_config_post_bad_body(self, mock_save):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        mock_save.assert_called_once_with({})

    @patch("netdiag_core.config.save_config", return_value={"ping_count": 20, "ping_interval": 0.5})
    def test_api_config_post_empty_json(self, mock_save):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", json=None)
        assert resp.status_code == 200
        mock_save.assert_called_once_with({})

    def test_api_config_post_not_dict(self):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/config", json=[1, 2, 3])
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    def test_api_status_with_list_gateway(self):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        state["status"] = "done"
        state["results"] = {"gateway_ping": [{"samples": [1, 2]}], "internet_ping": [{"samples": [3]}]}
        client = TestClient(app)
        resp = client.get("/api/status")
        data = resp.json()
        for item in data["results"]["gateway_ping"]:
            assert "samples" not in item
        for item in data["results"]["internet_ping"]:
            assert "samples" not in item

    @patch("netdiag_core.orchestrate.full_diagnostic", return_value={"health_score": 90, "diagnosis": []})
    @patch("netdiag_core.config.save_history", return_value="session_123.json")
    def test_api_run(self, mock_save, mock_diag):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "session_id" in data

    @patch("netdiag_core.runtime.IS_LINUX", False)
    @patch("netdiag_core.orchestrate.full_diagnostic", return_value={"health_score": 90, "diagnosis": []})
    @patch("netdiag_core.config.save_history", return_value="session_123.json")
    def test_api_run_non_linux(self, mock_save, mock_diag, *mocks):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/run", json={})
        assert resp.status_code == 200

    def test_api_run_already_running(self):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        state["status"] = "running"
        client = TestClient(app)
        resp = client.post("/api/run", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"

    @patch("netdiag_core.orchestrate.full_diagnostic", return_value={"health_score": 90, "diagnosis": []})
    @patch("netdiag_core.config.save_history", return_value="session_123.json")
    def test_api_run_bad_body(self, mock_save, mock_diag):
        from fastapi.testclient import TestClient
        app, state, _ = self._make_app()
        state["status"] = "idle"
        client = TestClient(app)
        resp = client.post("/api/run", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

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

    @patch("netdiag.Path.exists", return_value=True)
    @patch("netdiag.Path.is_file", return_value=True)
    @patch("netdiag.Path.read_bytes", return_value=b'{"a":1}')
    def test_api_report_json(self, mock_read, mock_is_file, mock_exists):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/report/data.json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    @patch("netdiag.Path.exists", return_value=True)
    @patch("netdiag.Path.is_file", return_value=True)
    @patch("netdiag.Path.read_bytes", return_value=b"a,b,c\n1,2,3")
    def test_api_report_csv(self, mock_read, mock_is_file, mock_exists):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/report/data.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    @patch("netdiag_core.config.load_history", return_value=[{"_file": "session_123.json", "health_score": 80}])
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

    @patch("netdiag_core.config.load_history", return_value=[{"_file": "s.json", "raw": "secret"}])
    def test_api_history_strips_raw(self, mock_load):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/history")
        data = resp.json()
        assert "raw" not in data["sessions"][0]

    @patch("netdiag_core.config.load_history", return_value=[{"_file": "s.json", "gateway_ping": {"samples": [1]}}])
    def test_api_history_strips_dict_samples(self, mock_load):
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.get("/api/history")
        data = resp.json()
        assert "samples" not in data["sessions"][0]["gateway_ping"]
