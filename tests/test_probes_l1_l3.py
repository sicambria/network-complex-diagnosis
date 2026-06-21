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


class TestMtr:
    def test_mtr_with_mtr_tool(self):
        with (
            patch("netdiag_core.runtime.has_tool", return_value=True) as mock_has,
            patch("netdiag_core.runtime.run_cmd", return_value=(0, MTR_OUTPUT, "")) as mock_cmd,
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
            patch("netdiag_core.runtime.has_tool", side_effect=has_tool_side),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, TRACEROUTE_OUTPUT, "")),
        ):
            result = mtr_test("example.com")
            assert result["tool"] == "traceroute"
            assert len(result["hops"]) == 3

    def test_mtr_traceroute_malformed(self):
        def has_tool_side(name):
            return name == "traceroute"

        with (
            patch("netdiag_core.runtime.has_tool", side_effect=has_tool_side),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "garbage output", "")),
        ):
            result = mtr_test("example.com")
            assert result["tool"] == "traceroute"
            assert len(result["hops"]) == 0

    def test_mtr_ping_traceroute_fallback(self):
        with (
            patch("netdiag_core.runtime.has_tool", return_value=False),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, PING_TRACEROUTE_OUTPUT, "")),
        ):
            result = mtr_test("example.com")
            assert result["tool"] == "ping_traceroute"


class TestPingTraceroute:
    def test_linux_platform(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "64 bytes from 1.1.1.1: icmp_seq=1 ttl=53 time=10.0 ms", "")),
        ):
            result = _ping_traceroute("1.1.1.1", max_hops=5)
            assert result["tool"] == "ping_traceroute"
            assert len(result["hops"]) >= 1

    def test_macos_platform(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "64 bytes from 1.1.1.1: icmp_seq=1 ttl=53 time=10.0 ms", "")),
        ):
            result = _ping_traceroute("1.1.1.1", max_hops=3)
            assert result["tool"] == "ping_traceroute"

    def test_windows_platform(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "Reply from 1.1.1.1: bytes=32 time=10ms", "")),
        ):
            result = _ping_traceroute("1.1.1.1", max_hops=3)
            assert result["tool"] == "ping_traceroute"

    def test_max_hops_reached(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "From 10.0.0.1 icmp_seq=1 Time to live exceeded", "")),
        ):
            result = _ping_traceroute("1.1.1.1", max_hops=3)
            assert len(result["hops"]) == 3

    def test_no_hop_ip_rc_not_zero(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "timeout")),
        ):
            result = _ping_traceroute("1.1.1.1", max_hops=2)
            assert result["hops"][0].get("ip") is None
            assert result["hops"][0]["loss_pct"] == 100


class TestSpeedtest:
    def test_speedtest_success(self):
        with (
            patch("netdiag_core.runtime.has_tool",
                  side_effect=lambda x: x == "speedtest"),
            patch("netdiag_core.runtime.run_cmd",
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
            patch("netdiag_core.runtime.has_tool", side_effect=has_tool_side),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, SPEEDTESTCLI_JSON, "")),
        ):
            result = speedtest_result()
            assert result["available"] is True
            assert result["tool"] == "speedtest-cli"
            assert result["download_mbps"] == 15.0

    def test_speedtest_no_tool(self):
        with patch("netdiag_core.runtime.has_tool", return_value=False):
            result = speedtest_result()
            assert result["available"] is False
            assert "speedtest" in result.get("message", "")

    def test_speedtest_parse_fail(self):
        with (
            patch("netdiag_core.runtime.has_tool",
                  side_effect=lambda x: x == "speedtest"),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, "{invalid json", "")),
        ):
            result = speedtest_result()
            assert result.get("error") == "parse failed"

    def test_speedtest_rc_fail(self):
        with (
            patch("netdiag_core.runtime.has_tool",
                  side_effect=lambda x: x == "speedtest"),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(1, "error output", "")),
        ):
            result = speedtest_result()
            assert result.get("rc") == 1
            assert result.get("tool") == "speedtest"

    def test_speedtest_cli_parse_fail(self):
        def has_tool_side(name):
            return name == "speedtest-cli"

        with (
            patch("netdiag_core.runtime.has_tool", side_effect=has_tool_side),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, "{invalid json", "")),
        ):
            result = speedtest_result()
            assert result.get("error") == "parse failed"
            assert result.get("tool") == "speedtest-cli"

    def test_speedtest_cli_rc_fail(self):
        def has_tool_side(name):
            return name == "speedtest-cli"

        with (
            patch("netdiag_core.runtime.has_tool", side_effect=has_tool_side),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(1, "error", "")),
        ):
            result = speedtest_result()
            assert result.get("rc") == 1
            assert result.get("tool") == "speedtest-cli"


class TestIperf3:
    def test_iperf3_success(self):
        with (
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, IPERF3_JSON, "")),
            patch("netdiag.IPERF_SERVER", "iperf.example"),
        ):
            result = iperf3_test("iperf.example", duration=5)
            assert result["available"] is True
            assert result["server"] == "iperf.example"
            assert result["download_mbps"] == 48.0
            assert result["retransmits"] == 3

    def test_iperf3_not_installed(self):
        with patch("netdiag_core.runtime.has_tool", return_value=False):
            result = iperf3_test()
            assert result["available"] is False

    def test_iperf3_timeout(self):
        with (
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(1, "", "Connection timed out")),
        ):
            result = iperf3_test("iperf.example", duration=5)
            assert result["available"] is True
            assert result["rc"] != 0

    def test_iperf3_parse_fail(self):
        with (
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, "{invalid json", "")),
        ):
            result = iperf3_test()
            assert result.get("error") == "parse failed"


class TestBufferbloat:
    def test_bufferbloat_non_linux(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is False

    def test_bufferbloat_linux_tc_success(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", side_effect=lambda x: x == "tc"),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(0, TC_OUTPUT, "")),
            patch("netdiag_core.probes.ping.ping_once", return_value={"rtt_ms": 10.0}),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is True
            assert result["interface"] == "eth0"
            assert result["backlog_bytes"] == 42
            assert result["drops"] == 155

    def test_bufferbloat_no_interface(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "")),
        ):
            result = bufferbloat_test(None)
            assert result["available"] is False

    def test_bufferbloat_non_linux_with_iperf3(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.probes.ping.ping_once", side_effect=[
                {"rtt_ms": 10.0}, {"rtt_ms": 50.0},
            ]),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "", "")),
        ):
            result = bufferbloat_test("en0")
            assert result["available"] is False
            assert result.get("ratio") == 5.0

    def test_bufferbloat_non_linux_with_iperf3_no_ratio(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.probes.ping.ping_once", side_effect=[
                {"rtt_ms": None}, {"rtt_ms": 50.0},
            ]),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "", "")),
        ):
            result = bufferbloat_test("en0")
            assert result.get("ratio") is None

    def test_bufferbloat_tc_failed(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is False
            assert "tc failed" in result.get("reason", "")

    def test_bufferbloat_linux_with_iperf3_ratio(self):
        tc_out = "backlog 42b drops 155 overlimits 0"
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", side_effect=lambda x: x == "tc" or x == "iperf3"),
            patch("netdiag_core.runtime.run_cmd", side_effect=[
                (0, tc_out, ""),
                (0, "", ""),
            ]),
            patch("netdiag_core.probes.ping.ping_once", side_effect=[
                {"rtt_ms": 10.0}, {"rtt_ms": 30.0},
            ]),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is True
            assert result["drops"] == 155
            assert result["ratio"] == 3.0

    def test_bufferbloat_drop_parse_exception(self):
        tc_out = "backlog 42b drops abc overlimits def"
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", side_effect=lambda x: x == "tc"),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, tc_out, "")),
        ):
            result = bufferbloat_test("eth0")
            assert result["available"] is True
            assert result["drops"] == 0
            assert result["overlimits"] == 0


