from unittest.mock import patch, MagicMock

from netdiag import (
    mtr_test, speedtest_result, iperf3_test,
    bufferbloat_test, ethtool_info, tcp_socket_stats,
    download_images_test, http_latency_test, mtu_probe,
    classify_ping, has_tool, check_tools,
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


class TestMtr:
    def test_mtr_with_mtr_tool(self):
        with (
            patch("netdiag.has_tool", return_value=True) as mock_has,
            patch("netdiag.run_cmd", return_value=(0, MTR_OUTPUT, "")) as mock_cmd,
        ):
            result = mtr_test("example.com", count=10)
            assert result["tool"] == "mtr"
            assert len(result["hops"]) == 3
            assert result["hops"][0]["hop"] == 1
            assert result["hops"][0]["loss_pct"] == 0.0
            assert result["hops"][0]["avg_ms"] == 10.0
            assert result["hops"][2]["loss_pct"] == 10.0

    def test_mtr_uses_traceroute_fallback(self):
        def has_tool_side(name):
            return name == "traceroute"

        with (
            patch("netdiag.has_tool", side_effect=has_tool_side),
            patch("netdiag.run_cmd", return_value=(0, TRACEROUTE_OUTPUT, "")),
        ):
            result = mtr_test("example.com")
            assert result["tool"] == "traceroute"
            assert len(result["hops"]) == 3

    def test_mtr_ping_traceroute_fallback(self):
        with (
            patch("netdiag.has_tool", return_value=False),
            patch("netdiag.run_cmd", return_value=(0, PING_TRACEROUTE_OUTPUT, "")),
        ):
            result = mtr_test("example.com")
            assert result["tool"] == "ping_traceroute"


class TestSpeedtest:
    def test_speedtest_success(self):
        with (
            patch("netdiag.has_tool",
                  side_effect=lambda x: x == "speedtest"),
            patch("netdiag.run_cmd",
                  return_value=(0, SPEEDTEST_JSON, "")),
        ):
            result = speedtest_result()
            assert result["available"] is True
            assert result["tool"] == "speedtest"
            assert result["download_mbps"] == 100.0  # 12500000 * 8 / 1e6
            assert result["upload_mbps"] == 20.0
            assert result["latency_ms"] == 12.5
            assert result["jitter_ms"] == 2.3

    def test_speedtest_cli_fallback(self):
        def has_tool_side(name):
            return name == "speedtest-cli"

        with (
            patch("netdiag.has_tool", side_effect=has_tool_side),
            patch("netdiag.run_cmd",
                  return_value=(0, SPEEDTESTCLI_JSON, "")),
        ):
            result = speedtest_result()
            assert result["available"] is True
            assert result["tool"] == "speedtest-cli"
            assert result["download_mbps"] == 15.0

    def test_speedtest_no_tool(self):
        with patch("netdiag.has_tool", return_value=False):
            result = speedtest_result()
            assert result["available"] is False
            assert "speedtest" in result.get("message", "")

    def test_speedtest_parse_fail(self):
        with (
            patch("netdiag.has_tool",
                  side_effect=lambda x: x == "speedtest"),
            patch("netdiag.run_cmd",
                  return_value=(0, "{invalid json", "")),
        ):
            result = speedtest_result()
            assert result.get("error") == "parse failed"


class TestIperf3:
    def test_iperf3_success(self):
        with (
            patch("netdiag.has_tool", return_value=True),
            patch("netdiag.run_cmd",
                  return_value=(0, IPERF3_JSON, "")),
            patch("netdiag.IPERF_SERVER", "iperf.example"),
        ):
            result = iperf3_test("iperf.example", duration=5)
            assert result["available"] is True
            assert result["server"] == "iperf.example"
            assert result["download_mbps"] == 48.0
            assert result["retransmits"] == 3

    def test_iperf3_not_installed(self):
        with patch("netdiag.has_tool", return_value=False):
            result = iperf3_test()
            assert result["available"] is False

    def test_iperf3_timeout(self):
        with (
            patch("netdiag.has_tool", return_value=True),
            patch("netdiag.run_cmd",
                  return_value=(1, "", "Connection timed out")),
        ):
            result = iperf3_test("iperf.example", duration=5)
            assert result["available"] is True
            assert result["rc"] != 0


class TestBufferbloat:
    def test_bufferbloat_non_linux(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.has_tool", return_value=False),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is False

    def test_bufferbloat_linux_tc_success(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", side_effect=lambda x: x == "tc"),
            patch("netdiag.run_cmd",
                  return_value=(0, TC_OUTPUT, "")),
            patch("netdiag.ping_once", return_value={"rtt_ms": 10.0}),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is True
            assert result["interface"] == "eth0"
            assert result["backlog_bytes"] == 42
            assert result["drops"] == 155

    def test_bufferbloat_no_interface(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.run_cmd", return_value=(1, "", "")),
        ):
            result = bufferbloat_test(None)
            assert result["available"] is False


class TestEthtool:
    def test_ethtool_success(self):
        ETH_OUT = "Speed: 1000Mb/s\nDuplex: Full\nLink detected: yes\n"
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=True),
            patch("netdiag.run_cmd",
                  return_value=(0, ETH_OUT, "")),
        ):
            result = ethtool_info("eth0")
            assert result["available"] is True
            assert result["speed_mbps"] == 1000
            assert result["duplex"] == "Full"
            assert result["link_detected"] is True

    def test_ethtool_not_installed(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=False),
        ):
            result = ethtool_info("eth0")
            assert result["available"] is False

    def test_ethtool_non_linux(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
        ):
            result = ethtool_info("eth0")
            assert result["available"] is False


class TestTcpSocketStats:
    def test_tcp_socket_stats_linux_ss(self):
        SS_OUTPUT = "State      Recv-Q Send-Q  Local Address:Port   Peer Address:Port  \n"
        SS_OUTPUT += "ESTAB      0      0       127.0.0.1:5432      127.0.0.1:45678    retrans:0/1 rtt:0.5\n"

        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", side_effect=lambda x: x == "ss"),
            patch("netdiag.run_cmd",
                  return_value=(0, SS_OUTPUT, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True
            assert result["connections"] >= 1
            assert result["total_retransmits"] >= 0

    def test_tcp_socket_stats_proc_fallback(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=False),
            patch("netdiag._proc_net_tcp_stats") as mock_proc,
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
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.has_tool", return_value=False),
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


class TestMtuProbe:
    def test_mtu_probe_available(self):
        with (
            patch("netdiag.has_tool", return_value=True),
            patch("netdiag.run_cmd", return_value=(0, "1 received", "")),
        ):
            result = mtu_probe("1.1.1.1", max_size=100)
            assert result["available"] is True
            assert result["mtu"] >= 28
            assert result["payload_size"] > 0

    def test_mtu_probe_no_ping(self):
        with patch("netdiag.has_tool", return_value=False):
            result = mtu_probe("1.1.1.1")
            assert result["available"] is False
            assert "ping" in result.get("reason", "")


class TestCheckTools:
    def test_all_tools_present_linux(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=True),
        ):
            result = check_tools()
            assert result["missing_required"] == []
            assert result["missing_optional"] == []

    def test_missing_tools_linux(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=False),
        ):
            result = check_tools()
            assert "ping" in result["missing_required"]
            assert "ip" in result["missing_required"]
            assert result["install_hint_required"] is not None

    def test_non_linux_required_only_ping(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.IS_WINDOWS", False),
            patch("netdiag.has_tool", return_value=False),
        ):
            result = check_tools()
            assert "ping" in result["missing_required"]
