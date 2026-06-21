from unittest.mock import patch
import json

pytest_import_error = None
try:
    import fastapi
except ImportError:
    fastapi = None
    pytest_import_error = "fastapi not installed"


class TestServerRoutes:
    def _make_app(self):
        from netdiag import build_app
        app, state, parser = build_app()
        return app, state, parser

    def test_instance(self):
        if fastapi is None:
            return
        app, _, _ = self._make_app()
        assert app.title == "NetDiag"

    def test_status_endpoint_exists(self):
        if fastapi is None:
            return
        app, _, _ = self._make_app()
        routes = [r.path for r in app.routes]
        assert "/api/status" in routes

    def test_run_endpoint_exists(self):
        if fastapi is None:
            return
        app, _, _ = self._make_app()
        routes = [r.path for r in app.routes]
        assert "/api/run" in routes

    def test_history_endpoint_exists(self):
        if fastapi is None:
            return
        app, _, _ = self._make_app()
        routes = [r.path for r in app.routes]
        assert "/api/history" in routes

    def test_stop_endpoint_exists(self):
        if fastapi is None:
            return
        app, _, _ = self._make_app()
        routes = [r.path for r in app.routes]
        assert "/api/stop" in routes

    def test_stop_returns_ok_when_idle(self):
        if fastapi is None:
            return
        from fastapi.testclient import TestClient
        app, _, _ = self._make_app()
        client = TestClient(app)
        resp = client.post("/api/stop")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["stopping"] is False
