import threading
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from netdiag import (
    VERSION,
    UserInterrupted,
    cli_main,
    full_diagnostic,
    start_server,
)

DNS_HOSTS = ["google.com", "cloudflare.com", "quad9.net"]
TCP_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443), ("google.com", 443)]


class Args:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_GW_PING = {
    "label": "gateway", "host": "192.168.1.1",
    "sent": 3, "received": 3, "loss_pct": 0.0,
    "min_ms": 1.0, "avg_ms": 1.5, "p95_ms": 2.0, "p99_ms": 2.5, "max_ms": 3.0,
    "jitter_ms": 0.5, "samples": [],
}

_INT_PING = {
    "label": "1.1.1.1", "host": "1.1.1.1",
    "sent": 3, "received": 3, "loss_pct": 0.0,
    "min_ms": 10.0, "avg_ms": 12.0, "p95_ms": 15.0, "p99_ms": 18.0, "max_ms": 20.0,
    "jitter_ms": 2.0, "samples": [],
}

_DNS_RESULT = {"total": 2, "failures": 0, "avg_ms": 25.0}
_TCP_RESULT = {"total": 2, "failures": 0, "avg_ms": 30.0}
_TCP_SOCK = {"retransmit_pct": 1.0, "total_connections": 50}
_BUFFERBLOAT = {"ratio": 1.2, "available": True}
_MTR = {"hops": [{"loss_pct": 0.0, "avg_ms": 1.0}], "available": True}
_SPEEDTEST = {"download_mbps": 150.0, "available": True}
_IPERF3 = {"available": True, "retransmits": 2, "mbps": 50.0}
_DOWNLOAD = {"success": 95, "error": None, "avg_mbps": 30.0}
_HTTP_LAT = [{"failures": 0}, {"failures": 1}]
_MTU = {"available": True, "mtu": 1500}
_IFACE_STATS = {
    "available": True, "interface": "eth0",
    "rx": {"errors": 0, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0},
    "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0},
}
_WIFI = {"available": True, "signal_dbm": -50, "channel_util": 20}
_ETHTOOL = {"available": True, "speed": 1000, "duplex": "Full", "link": "yes"}


class TestFullDiagnostic:
    def test_basic_run(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools") as mock_ct,
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose") as mock_diag,
            patch("netdiag.health_score") as mock_score,
        ):
            mock_ct.return_value = {"platform": "Linux", "missing_required": []}
            mock_diag.return_value = [{"layer": "meta", "severity": "clean"}]
            mock_score.return_value = 95

            results = full_diagnostic(args)

            assert results["interrupted"] is False
            assert results["gateway"] == "192.168.1.1"
            assert results["default_interface"] == "eth0"
            assert results["interface"] == _IFACE_STATS
            assert results["wifi"] == _WIFI
            assert results["ethtool"] == _ETHTOOL
            assert results["gateway_ping"] == _GW_PING
            assert results["internet_ping"] == [_GW_PING]
            assert results["tcp_sockets"] == _TCP_SOCK
            assert results["bufferbloat"] == _BUFFERBLOAT
            assert results["mtr"] == _MTR
            assert results["speedtest"] == _SPEEDTEST
            assert results["iperf3"] == _IPERF3
            assert results["download_test"] is None
            assert results["connection_test"] is None
            assert results["diagnosis"] == mock_diag.return_value
            assert results["health_score"] == mock_score.return_value
            assert results["tools"]["platform"] == "Linux"

    def test_with_callback(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        callback = MagicMock()
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            full_diagnostic(args, callback=callback)

        assert callback.call_count >= 4
        callback.assert_any_call("interface", 0, 1, None, None, "running")
        callback.assert_any_call("interface", 1, 1, 1, 0, "done")

    def test_interrupted_by_userinterrupted(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=True, no_speedtest=True, no_iperf=True,
            no_bufferbloat=True, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", side_effect=UserInterrupted("user stopped")),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["interrupted"] is True
            assert results["interrupt_reason"] == "user stopped"

    def test_interrupted_by_keyboardinterrupt(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=True, no_speedtest=True, no_iperf=True,
            no_bufferbloat=True, download_test=False, connection_test=False,
        )

        def _raise_kb(*a, **kw):
            raise KeyboardInterrupt()

        # detect_gateway is before the try block; use interface_stats instead
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", side_effect=_raise_kb),
        ):
            results = full_diagnostic(args)
            assert results["interrupted"] is True
            assert results["interrupt_reason"] == "Interrupted by user"

    def test_no_gateway(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value=None),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_INT_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["gateway"] is None
            assert results["gateway_ping"] is None

    def test_no_default_interface(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value=None),
            patch("netdiag.interface_stats") as mock_iface,
            patch("netdiag.wifi_info") as mock_wifi,
            patch("netdiag.ethtool_info") as mock_ethtool,
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats") as mock_sock,
            patch("netdiag.bufferbloat_test") as mock_bb,
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["default_interface"] is None
            assert results["interface"] is None
            assert results["wifi"] is None
            assert results["ethtool"] is None
            assert results["tcp_sockets"] is None
            assert results["bufferbloat"] is None
            assert results["gateway_ping"] == _GW_PING
            assert len(results["internet_ping"]) == 1
            mock_iface.assert_not_called()
            mock_wifi.assert_not_called()
            mock_ethtool.assert_not_called()
            mock_sock.assert_not_called()
            mock_bb.assert_not_called()

    def test_no_trace_flag(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=True, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test") as mock_mtr,
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["mtr"] is None
            mock_mtr.assert_not_called()

    def test_no_speedtest_flag(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=True, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result") as mock_st,
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["speedtest"] is None
            mock_st.assert_not_called()

    def test_no_iperf_flag(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=True,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test") as mock_iperf,
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["iperf3"] is None
            mock_iperf.assert_not_called()

    def test_no_bufferbloat_flag(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=True, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test") as mock_bb,
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["bufferbloat"] is None
            mock_bb.assert_not_called()

    def test_download_test_flag(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=True, connection_test=False,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.download_images_test", return_value=_DOWNLOAD),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["download_test"] == _DOWNLOAD
            assert results["connection_test"] is None

    def test_connection_test_flag(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=True,
        )
        with (
            patch("netdiag.check_tools", return_value={}),
            patch("netdiag.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag.get_default_interface", return_value="eth0"),
            patch("netdiag.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag.wifi_info", return_value=_WIFI),
            patch("netdiag.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag.ping_burst", return_value=_GW_PING),
            patch("netdiag.dns_test", return_value=_DNS_RESULT),
            patch("netdiag.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag.mtr_test", return_value=_MTR),
            patch("netdiag.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag.iperf3_test", return_value=_IPERF3),
            patch("netdiag.http_latency_test", return_value=_HTTP_LAT),
            patch("netdiag.mtu_probe", return_value=_MTU),
            patch("netdiag.diagnose", return_value=[]),
            patch("netdiag.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["connection_test"] is not None
            assert results["connection_test"]["http_latency"] == _HTTP_LAT
            assert results["connection_test"]["mtu"] == _MTU


class TestCliMain:
    def test_cli_version(self):
        with (
            patch("netdiag.build_parser") as mock_bp,
            patch("sys.stdout", new_callable=StringIO) as mock_stdout,
        ):
            mock_args = MagicMock()
            mock_args.version = True
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_bp.return_value.parse_args.return_value = mock_args

            cli_main()

            assert f"netdiag v{VERSION}" in mock_stdout.getvalue()

    def test_cli_license(self):
        with (
            patch("netdiag.build_parser") as mock_bp,
            patch("sys.stdout", new_callable=StringIO) as mock_stdout,
        ):
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = True
            mock_args.gui = False
            mock_args.daemon = False
            mock_bp.return_value.parse_args.return_value = mock_args

            cli_main()

            output = mock_stdout.getvalue()
            assert "GNU" in output or "Affero" in output or "General Public" in output

    def test_cli_gui_missing_deps(self):
        def fake_import(name, *args, **kwargs):
            if name == "fastapi" or name.startswith("fastapi.") or name == "uvicorn":
                raise ImportError(f"No module named {name}")
            return orig_import(name, *args, **kwargs)

        import builtins
        orig_import = builtins.__import__

        with (
            patch("netdiag.build_parser") as mock_bp,
            patch("sys.stderr", new_callable=StringIO) as mock_stderr,
        ):
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = True
            mock_args.daemon = False
            mock_bp.return_value.parse_args.return_value = mock_args

            with patch("builtins.__import__", side_effect=fake_import):
                with pytest.raises(SystemExit) as exc_info:
                    cli_main()

            assert exc_info.value.code == 1
            assert "fastapi and uvicorn are required" in mock_stderr.getvalue()

    def test_cli_low_count(self):
        with patch("netdiag.build_parser") as mock_bp:
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_args.count = 0
            mock_bp.return_value.parse_args.return_value = mock_args

            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 2

    def test_cli_negative_interval(self):
        with patch("netdiag.build_parser") as mock_bp:
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_args.count = 5
            mock_args.interval = -1
            mock_bp.return_value.parse_args.return_value = mock_args

            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 2

    def test_cli_low_timeout(self):
        with patch("netdiag.build_parser") as mock_bp:
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_args.count = 5
            mock_args.interval = 1
            mock_args.timeout = 0
            mock_bp.return_value.parse_args.return_value = mock_args

            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 2

    def test_cli_normal_flow(self):
        mock_args = MagicMock()
        mock_args.version = False
        mock_args.license = False
        mock_args.gui = False
        mock_args.daemon = False
        mock_args.count = 5
        mock_args.interval = 0.5
        mock_args.timeout = 2
        mock_args.outdir = "/tmp/test_outdir"
        mock_args.history_dir = "/tmp/.netdiag"
        mock_args.quiet = True

        with (
            patch("netdiag.build_parser") as mock_bp,
            patch("netdiag.full_diagnostic") as mock_diag,
            patch("netdiag.write_csv") as mock_csv,
            patch("netdiag.write_report") as mock_report,
            patch("netdiag.print_console_summary") as mock_print,
            patch("netdiag.save_history") as mock_save,
            patch("netdiag.Path") as mock_path,
            patch("netdiag.flatten_ping", return_value=[]),
            patch("netdiag.ping_summary_rows", return_value=[]),
        ):
            mock_bp.return_value.parse_args.return_value = mock_args
            mock_diag.return_value = {"health_score": 85, "diagnosis": []}

            mock_outdir = MagicMock()
            mock_path.return_value = mock_outdir
            mock_outdir.__truediv__.return_value = MagicMock()

            cli_main()

            mock_diag.assert_called_once_with(mock_args)
            mock_outdir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
            assert mock_csv.call_count == 2
            mock_report.assert_called_once()
            mock_print.assert_called_once()
            mock_save.assert_called_once()


class TestStartServer:
    def test_gui_mode(self):
        args = Args(daemon=False, port=8080)
        mock_app = MagicMock()
        mock_uvicorn = MagicMock()

        with (
            patch("netdiag.build_app", return_value=(mock_app, {}, MagicMock())),
            patch("netdiag.threading.Thread") as mock_thread,
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            start_server(args)

            mock_uvicorn.run.assert_called_once_with(
                mock_app, host="0.0.0.0", port=8080, log_level="warning",
            )
            mock_thread.assert_not_called()

    def test_daemon_mode(self):
        args = Args(daemon=True, port=8080)
        mock_app = MagicMock()
        mock_cr = {"status": "idle", "progress": {}, "results": None}
        mock_parser_fn = MagicMock()
        mock_diag_args = MagicMock()
        mock_diag_args.no_bufferbloat = False
        mock_diag_args.history_dir = "/tmp/.netdiag"
        mock_parser_fn.return_value.parse_args.return_value = mock_diag_args
        mock_uvicorn = MagicMock()

        with (
            patch("netdiag.build_app", return_value=(mock_app, mock_cr, mock_parser_fn)),
            patch("netdiag.IS_LINUX", True),
            patch("threading.Thread") as mock_thread_cls,
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            start_server(args)

            mock_thread_cls.assert_called_once()
            call_kwargs = mock_thread_cls.call_args[1]
            assert call_kwargs["daemon"] is True
            assert callable(call_kwargs["target"])

            mock_thread_instance.start.assert_called_once()
            mock_uvicorn.run.assert_called_once_with(
                mock_app, host="0.0.0.0", port=8080, log_level="warning",
            )

    def test_daemon_loop_logic(self):
        args = Args(daemon=True, port=8080, history_dir="/tmp/.netdiag")
        mock_app = MagicMock()
        mock_cr = {"status": "idle", "progress": {}, "results": None}
        mock_parser_fn = MagicMock()
        mock_diag_args = MagicMock()
        mock_diag_args.no_bufferbloat = False
        mock_diag_args.history_dir = "/tmp/.netdiag"
        mock_parser_fn.return_value.parse_args.return_value = mock_diag_args
        mock_uvicorn = MagicMock()

        sleep_count = [0]

        def mock_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 1:
                raise RuntimeError("break_loop")

        with (
            patch("netdiag.build_app", return_value=(mock_app, mock_cr, mock_parser_fn)),
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.full_diagnostic", return_value={"health_score": 85, "diagnosis": []}) as mock_diag,
            patch("netdiag.save_history") as mock_save,
            patch("threading.Thread") as mock_thread_cls,
            patch("time.sleep", side_effect=mock_sleep),
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            start_server(args)

            call_kwargs = mock_thread_cls.call_args[1]
            target = call_kwargs["target"]
            target_args = call_kwargs["args"]

            mock_cr["_lock"] = threading.RLock()

            with pytest.raises(RuntimeError, match="break_loop"):
                target(*target_args)

            mock_diag.assert_called()
            mock_save.assert_called()

    def test_build_app_failure(self):
        args = Args(daemon=False, port=8080)

        with (
            patch("netdiag.build_app", return_value=(None, None, None)),
            patch("sys.stderr", new_callable=StringIO) as mock_stderr,
        ):
            with pytest.raises(SystemExit) as exc_info:
                start_server(args)

            assert exc_info.value.code == 1
            assert "fastapi and uvicorn required" in mock_stderr.getvalue()
