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


class TestMtuProbe:
    def test_mtu_probe_available(self):
        with (
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, "1 received", "")),
        ):
            result = mtu_probe("1.1.1.1", max_size=100)
            assert result["available"] is True
            assert result["mtu"] >= 28
            assert result["payload_size"] > 0

    def test_mtu_probe_no_ping(self):
        with patch("netdiag_core.runtime.has_tool", return_value=False):
            result = mtu_probe("1.1.1.1")
            assert result["available"] is False
            assert "ping" in result.get("reason", "")

    def test_mtu_probe_macos(self):
        def run_cmd_side(*args, **kw):
            if "ping" in args[0][0] and "-D" in args[0]:
                if "fail" not in args[0]:
                    return (0, "64 bytes from 1.1.1.1: icmp_seq=1 ttl=53 time=10.0 ms", "")
            return (1, "", "")
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd", side_effect=run_cmd_side),
        ):
            result = mtu_probe("1.1.1.1", max_size=100)
            assert result["available"] is True

    def test_mtu_probe_windows(self):
        def run_cmd_side(*args, **kw):
            if "ping" in args[0][0] and "-i" in args[0]:
                if "fail" not in args[0]:
                    return (0, "Reply from 1.1.1.1: bytes=32 time=10ms TTL=53", "")
            return (1, "", "")
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.has_tool", return_value=True),
            patch("netdiag_core.runtime.run_cmd", side_effect=run_cmd_side),
        ):
            result = mtu_probe("1.1.1.1", max_size=80)
            assert result["available"] is True


class TestCheckTools:
    def test_all_tools_present_linux(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=True),
        ):
            result = check_tools()
            assert result["missing_required"] == []
            assert result["missing_optional"] == []

    def test_missing_tools_linux(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
        ):
            result = check_tools()
            assert "ping" in result["missing_required"]
            assert "ip" in result["missing_required"]
            assert result["install_hint_required"] is not None

    def test_non_linux_required_only_ping(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.has_tool", return_value=False),
        ):
            result = check_tools()
            assert "ping" in result["missing_required"]
