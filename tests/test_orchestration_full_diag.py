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
            patch("netdiag_core.runtime.check_tools") as mock_ct,
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="eth0"),
            patch("netdiag_core.probes.netinfo.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag_core.probes.wifi.wifi_info", return_value=_WIFI),
            patch("netdiag_core.probes.netinfo.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag_core.probes.ping.ping_burst", return_value=_GW_PING),
            patch("netdiag_core.probes.dns_tcp.dns_test", return_value=_DNS_RESULT),
            patch("netdiag_core.probes.dns_tcp.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag_core.probes.sockets.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag_core.probes.throughput.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag_core.probes.route.mtr_test", return_value=_MTR),
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose") as mock_diag,
            patch("netdiag_core.analysis.health_score") as mock_score,
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
            patch("netdiag_core.runtime.check_tools", return_value={}),
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="eth0"),
            patch("netdiag_core.probes.netinfo.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag_core.probes.wifi.wifi_info", return_value=_WIFI),
            patch("netdiag_core.probes.netinfo.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag_core.probes.ping.ping_burst", return_value=_GW_PING),
            patch("netdiag_core.probes.dns_tcp.dns_test", return_value=_DNS_RESULT),
            patch("netdiag_core.probes.dns_tcp.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag_core.probes.sockets.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag_core.probes.throughput.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag_core.probes.route.mtr_test", return_value=_MTR),
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.runtime.check_tools", return_value={}),
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="eth0"),
            patch("netdiag_core.probes.netinfo.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag_core.probes.wifi.wifi_info", return_value=_WIFI),
            patch("netdiag_core.probes.netinfo.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag_core.probes.ping.ping_burst", side_effect=UserInterrupted("user stopped")),
            patch("netdiag_core.probes.dns_tcp.dns_test", return_value=_DNS_RESULT),
            patch("netdiag_core.probes.dns_tcp.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag_core.probes.sockets.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.runtime.check_tools", return_value={}),
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="eth0"),
            patch("netdiag_core.probes.netinfo.interface_stats", side_effect=_raise_kb),
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
            patch("netdiag_core.runtime.check_tools", return_value={}),
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value=None),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="eth0"),
            patch("netdiag_core.probes.netinfo.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag_core.probes.wifi.wifi_info", return_value=_WIFI),
            patch("netdiag_core.probes.netinfo.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag_core.probes.ping.ping_burst", return_value=_INT_PING),
            patch("netdiag_core.probes.dns_tcp.dns_test", return_value=_DNS_RESULT),
            patch("netdiag_core.probes.dns_tcp.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag_core.probes.sockets.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag_core.probes.throughput.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag_core.probes.route.mtr_test", return_value=_MTR),
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["gateway"] is None
            assert results["gateway_ping"] is None

    def test_no_gateway_not_quiet_prints(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=False,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag_core.runtime.check_tools", return_value={}),
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value=None),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="eth0"),
            patch("netdiag_core.probes.netinfo.interface_stats", return_value=_IFACE_STATS),
            patch("netdiag_core.probes.wifi.wifi_info", return_value=_WIFI),
            patch("netdiag_core.probes.netinfo.ethtool_info", return_value=_ETHTOOL),
            patch("netdiag_core.probes.ping.ping_burst", return_value=_INT_PING),
            patch("netdiag_core.probes.dns_tcp.dns_test", return_value=_DNS_RESULT),
            patch("netdiag_core.probes.dns_tcp.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag_core.probes.sockets.tcp_socket_stats", return_value=_TCP_SOCK),
            patch("netdiag_core.probes.throughput.bufferbloat_test", return_value=_BUFFERBLOAT),
            patch("netdiag_core.probes.route.mtr_test", return_value=_MTR),
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
            patch("builtins.print") as mock_print,
        ):
            full_diagnostic(args)
            mock_print.assert_any_call("No gateway detected.", flush=True)

