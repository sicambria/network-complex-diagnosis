from unittest.mock import patch, MagicMock

from netdiag import (
    mtr_test, speedtest_result, iperf3_test,
    bufferbloat_test, ethtool_info, tcp_socket_stats,
    download_images_test, http_latency_test, mtu_probe,
    _ping_traceroute, classify_ping, has_tool, check_tools,
)


MTR_OUTPUT = """Start: 2026-06-10T12:00:00+0000
HOST: example.com                    Loss%   Snt   Last   Avg  Best  Wrst StDev
  1.|-- 192.168.1.1                   0.0%    10    1.2   1.3   1.0   2.0   0.3
  2.|-- 10.0.0.1                      0.0%    10    5.1   5.5   4.9   7.2   0.7
  3.|-- 1.1.1.1                      10.0%    10   10.0  12.5  10.0  18.1   2.5
"""

TRACEROUTE_OUTPUT = """ 1  192.168.1.1   1.234 ms  1.456 ms  1.567 ms
 2  10.0.0.1    5.123 ms  5.456 ms  5.789 ms
 3  * * *
"""

PING_TRACEROUTE_OUTPUT = """from 1.1.1.1 icmp_seq=1 ttl=1 time=1.0 ms
from 10.0.0.1 icmp_seq=1 ttl=2 time=5.0 ms
"""

SPEEDTEST_JSON = """{"download":{"bandwidth":12500000},"upload":{"bandwidth":2500000},"ping":{"latency":12.5,"jitter":2.3},"server":{"name":"Test"},"isp":"TestISP"}"""
SPEEDTESTCLI_JSON = """{"download":15000000,"upload":5000000,"ping":15.0,"server":{"name":"Old"},"client":{"isp":"OldISP"}}"""

IPERF3_JSON = """{"end":{"sum_sent":{"bits_per_second":50000000,"retransmits":3,"bytes":1000000},"sum_received":{"bits_per_second":48000000}}}"""

TC_OUTPUT = """qdisc pfifo_fast 0: dev eth0 root refcnt 2 bands 3 priomap  1 2 2 2 1 2 0 0 1 1 1 1 1 1 1 1
  backlog 42b 3p requeues 0
qdisc fq_codel 0: dev eth0 root refcnt 2 limit 10240p flows 1024 quantum 1514
  backlog 0b 0p drops 155 overlimits 0 requeues 0
  memory used: 1280K
"""


class TestEthtool:
    def test_ethtool_success(self):
        ETH_OUT = "Speed: 1000Mb/s\nDuplex: Full\nLink detected: yes\n"
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, ETH_OUT, "")),
        ):
            result = ethtool_info("eth0")
            assert result["available"] is True
            assert result["speed_mbps"] == 1000
            assert result["duplex"] == "Full"
            assert result["link_detected"] is True

    def test_ethtool_not_installed(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
        ):
            result = ethtool_info("eth0")
            assert result["available"] is False

    def test_ethtool_non_linux(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
        ):
            result = ethtool_info("eth0")
            assert result["available"] is False


class TestTcpSocketStats:
    def test_tcp_socket_stats_linux_ss(self):
        SS_OUTPUT = "State      Recv-Q Send-Q  Local Address:Port   Peer Address:Port  \n"
        SS_OUTPUT += "ESTAB      0      0       127.0.0.1:5432      127.0.0.1:45678    retrans:0/1 rtt:0.5\n"

        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", side_effect=lambda x: x == "ss"),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, SS_OUTPUT, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True
            assert result["connections"] >= 1
            assert result["total_retransmits"] >= 0

    def test_tcp_socket_stats_proc_fallback(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
            patch("netdiag_core.probes.sockets._proc_net_tcp_stats") as mock_proc,
        ):
            mock_proc.return_value = {
                "available": True, "connections": 5,
                "total_retransmits": 0, "avg_rtt_ms": None,
                "details": [], "_source": "/proc/net/tcp",
            }
            result = tcp_socket_stats("lo")
            assert result["available"] is True
            assert result["connections"] == 5

    def test_tcp_socket_stats_non_linux(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
        ):
            result = tcp_socket_stats("en0")
            assert result["available"] is False

    def test_tcp_socket_stats_linux_no_ss_no_proc(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
            patch("netdiag_core.probes.sockets._proc_net_tcp_stats", return_value=None),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is False
            assert "/proc/net/tcp" in result.get("reason", "")

    def test_tcp_socket_stats_macos_with_data(self):
        out = "tcp   0      0 127.0.0.1:5432  *:*    LISTEN retransmit:0\n"
        out += "tcp   0      0 127.0.0.1:22    *:*    LISTEN\n"
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True
            assert result["connections"] > 0

    def test_tcp_socket_stats_windows(self):
        out = "Segments Retransmitted = 3\n"
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("eth0")
            assert result["available"] is True
            assert result["total_retransmits"] == 3

    def test_tcp_socket_stats_windows_fails(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")),
        ):
            result = tcp_socket_stats("eth0")
            assert result["available"] is False

    def test_tcp_socket_stats_macos_nettop_fails(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.run_cmd", return_value=(2, "", "error")),
        ):
            result = tcp_socket_stats("en0")
            assert result["available"] is False


class TestClassifyPing:
    def test_clean(self):
        assert classify_ping({"loss_pct": 0, "p95_ms": 50, "jitter_ms": 20}) == "clean"
        assert classify_ping({"loss_pct": 0.5, "p95_ms": 100, "jitter_ms": 30}) == "clean"

    def test_bad_loss(self):
        assert classify_ping({"loss_pct": 5, "p95_ms": 50, "jitter_ms": 20}) == "bad_loss"
        assert classify_ping({"loss_pct": 50, "p95_ms": 50, "jitter_ms": 20}) == "bad_loss"

    def test_some_loss(self):
        assert classify_ping({"loss_pct": 3, "p95_ms": 50, "jitter_ms": 20}) == "some_loss"
        assert classify_ping({"loss_pct": 1, "p95_ms": 50, "jitter_ms": 20}) == "some_loss"

    def test_bad_latency_spikes(self):
        assert classify_ping({"loss_pct": 0, "p95_ms": 300, "jitter_ms": 20}) == "bad_latency_spikes"
        assert classify_ping({"loss_pct": 0, "p95_ms": 500, "jitter_ms": 20}) == "bad_latency_spikes"

    def test_latency_spikes(self):
        result = classify_ping({"loss_pct": 0, "p95_ms": 150, "jitter_ms": 20})
        assert result == "latency_spikes", f"got {result}"

    def test_high_jitter(self):
        assert classify_ping({"loss_pct": 0, "p95_ms": 50, "jitter_ms": 80}) == "high_jitter"

    def test_handles_missing_keys(self):
        assert classify_ping({}) == "clean"


class TestDownloadImages:
    def test_download_images_simple(self):
        mock_future = MagicMock()
        mock_future.result.return_value = {"ok": True, "bytes": 1024, "latency_ms": 50.0, "idx": 0}
        mock_executor = MagicMock()
        mock_executor.__enter__.return_value.submit.return_value = mock_future
        with (
            patch("concurrent.futures.ThreadPoolExecutor",
                  return_value=mock_executor),
            patch("concurrent.futures.as_completed",
                  side_effect=lambda fs: fs),
        ):
            result = download_images_test(count=2, timeout_s=5)
            assert result["available"] is True

    def test_download_images_all_fail(self):
        mock_future = MagicMock()
        mock_future.result.return_value = {"ok": False, "error": "timeout", "idx": 0}
        mock_executor = MagicMock()
        mock_executor.__enter__.return_value.submit.return_value = mock_future
        with (
            patch("concurrent.futures.ThreadPoolExecutor",
                  return_value=mock_executor),
            patch("concurrent.futures.as_completed",
                  side_effect=lambda fs: fs),
        ):
            result = download_images_test(count=1, timeout_s=1)
            assert result["available"] is True
            assert result["success"] == 0
            assert result["failures"] > 0
            assert result["error"] == "All downloads failed"


class TestHttpLatency:
    def test_http_latency_basic(self):
        with (
            patch("http.client.HTTPSConnection") as mock_http,
            patch("ssl.create_default_context"),
        ):
            mock_http_instance = MagicMock()
            mock_http.return_value = mock_http_instance
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.read.return_value = b"OK"
            mock_http_instance.getresponse.return_value = mock_response
            result = http_latency_test(["example.com"], count=1, timeout_s=2)
            assert isinstance(result, list)
            if len(result) > 0:
                assert result[0].get("available") is True

    def test_http_latency_all_fail(self):
        with (
            patch("urllib.request.urlopen") as mock_urlopen,
        ):
            mock_urlopen.side_effect = Exception("connection failed")
            result = http_latency_test(["fail.example"], count=2, timeout_s=1)
            assert len(result) == 1
            assert result[0]["failures"] == 2
            assert result[0].get("avg_ms") is None


