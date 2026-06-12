from unittest.mock import patch, MagicMock

import pytest
import socket

from netdiag import dns_test, tcp_test, _tcp_ping


class TestDnsTest:
    def test_dns_success(self):
        with patch("netdiag.resolve_all") as mock_resolve:
            mock_resolve.return_value = {
                "ok": True,
                "addresses": [{"ip": "1.1.1.1", "version": 4}],
            }
            result = dns_test("cloudflare.com", count=3)
            assert result["host"] == "cloudflare.com"
            assert result["queries"] == 3
            assert result["failures"] == 0
            assert result["failure_pct"] == 0.0
            assert len(result["addresses"]) == 1

    def test_dns_all_fail(self):
        with patch("netdiag.resolve_all") as mock_resolve:
            mock_resolve.return_value = {"ok": False, "addresses": []}
            result = dns_test("bad.example", count=5)
            assert result["failures"] == 5
            assert result["failure_pct"] == 100.0
            assert result["addresses"] == []

    def test_dns_mixed(self):
        returns = [
            {"ok": True, "addresses": [{"ip": "1.1.1.1", "version": 4}]},
            {"ok": False, "addresses": []},
            {"ok": True, "addresses": [{"ip": "1.0.0.1", "version": 4}]},
        ]

        def side_effect(*a, **kw):
            return returns.pop(0) if returns else {"ok": False, "addresses": []}

        with patch("netdiag.resolve_all", side_effect=side_effect):
            result = dns_test("test.example", count=3)
            assert result["failures"] == 1
            assert result["failure_pct"] == pytest.approx(100 / 3, 0.01)

    def test_dns_deduplicates_addresses(self):
        with patch("netdiag.resolve_all") as mock_resolve:
            mock_resolve.return_value = {
                "ok": True,
                "addresses": [
                    {"ip": "1.1.1.1", "version": 4},
                    {"ip": "1.1.1.1", "version": 4},
                ],
            }
            result = dns_test("double.example", count=2)
            assert len(result["addresses"]) == 1


class TestTcpTest:
    def test_tcp_success(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = True
            result = tcp_test("1.1.1.1", 443, count=3, timeout_s=2)
            assert result["host"] == "1.1.1.1"
            assert result["port"] == 443
            assert result["attempts"] == 3
            assert result["failures"] == 0
            assert result["failure_pct"] == 0.0

    def test_tcp_all_timeout(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.side_effect = TimeoutError("timed out")
            result = tcp_test("1.1.1.1", 443, count=3, timeout_s=1)
            assert result["failures"] == 3
            assert result["failure_pct"] == 100.0
            assert "TimeoutError" in result["errors"]

    def test_tcp_partial_fail(self):
        side_effects = [True, TimeoutError("timeout"), True]

        def create_connection(*a, **kw):
            e = side_effects.pop(0)
            if isinstance(e, Exception):
                raise e
            m = MagicMock()
            m.__enter__.return_value = True
            return m

        with patch("socket.create_connection", side_effect=create_connection):
            result = tcp_test("1.1.1.1", 443, count=3)
            assert result["failures"] == 1
            assert result["failure_pct"] == pytest.approx(100 / 3, 0.01)

    def test_tcp_error_types(self):
        errors_to_raise = [ConnectionRefusedError, OSError, socket.gaierror]

        def side_effect(*a, **kw):
            e = errors_to_raise.pop(0)
            raise e("test")

        with patch("socket.create_connection", side_effect=side_effect):
            result = tcp_test("1.1.1.1", 443, count=3)
            assert result["failures"] == 3
            for name in ["ConnectionRefusedError", "OSError"]:
                assert any(name in k for k in result["errors"])


class TestTcpPing:
    def test_tcp_ping_success(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = True
            result = _tcp_ping("1.1.1.1", port=443, timeout_s=1)
            assert result["ok"] is True
            assert result["rtt_ms"] is not None
            assert result["_fallback"] == "tcp"

    def test_tcp_ping_fail(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.side_effect = OSError("connection failed")
            result = _tcp_ping("1.1.1.1", port=443, timeout_s=1)
            assert result["ok"] is False
            assert result["rtt_ms"] is None
            assert result["_fallback"] == "tcp"
