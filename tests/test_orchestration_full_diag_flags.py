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
    def test_no_default_interface(self):
        args = Args(
            hosts=["1.1.1.1"], count=3, interval=0.1, timeout=2,
            dns_count=2, tcp_count=2, quiet=True,
            no_trace=False, no_speedtest=False, no_iperf=False,
            no_bufferbloat=False, download_test=False, connection_test=False,
        )
        with (
            patch("netdiag_core.runtime.check_tools", return_value={}),
            patch("netdiag_core.probes.netinfo.detect_gateway", return_value="192.168.1.1"),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value=None),
            patch("netdiag_core.probes.netinfo.interface_stats") as mock_iface,
            patch("netdiag_core.probes.wifi.wifi_info") as mock_wifi,
            patch("netdiag_core.probes.netinfo.ethtool_info") as mock_ethtool,
            patch("netdiag_core.probes.ping.ping_burst", return_value=_GW_PING),
            patch("netdiag_core.probes.dns_tcp.dns_test", return_value=_DNS_RESULT),
            patch("netdiag_core.probes.dns_tcp.tcp_test", return_value=_TCP_RESULT),
            patch("netdiag_core.probes.sockets.tcp_socket_stats") as mock_sock,
            patch("netdiag_core.probes.throughput.bufferbloat_test") as mock_bb,
            patch("netdiag_core.probes.route.mtr_test", return_value=_MTR),
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.probes.route.mtr_test") as mock_mtr,
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.probes.throughput.speedtest_result") as mock_st,
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.probes.throughput.iperf3_test") as mock_iperf,
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.probes.throughput.bufferbloat_test") as mock_bb,
            patch("netdiag_core.probes.route.mtr_test", return_value=_MTR),
            patch("netdiag_core.probes.throughput.speedtest_result", return_value=_SPEEDTEST),
            patch("netdiag_core.probes.throughput.iperf3_test", return_value=_IPERF3),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.probes.webprobes.download_images_test", return_value=_DOWNLOAD),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
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
            patch("netdiag_core.probes.webprobes.http_latency_test", return_value=_HTTP_LAT),
            patch("netdiag_core.probes.route.mtu_probe", return_value=_MTU),
            patch("netdiag_core.analysis.diagnose", return_value=[]),
            patch("netdiag_core.analysis.health_score", return_value=0),
        ):
            results = full_diagnostic(args)
            assert results["connection_test"] is not None
            assert results["connection_test"]["http_latency"] == _HTTP_LAT
            assert results["connection_test"]["mtu"] == _MTU

