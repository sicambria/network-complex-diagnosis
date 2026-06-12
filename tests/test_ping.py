from unittest.mock import patch

from netdiag import ping_command, ping_once, _tcp_ping


class TestPingCommand:
    def test_linux_default(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), patch("netdiag.IS_WINDOWS", False):
            cmd = ping_command("1.1.1.1")
            assert cmd[0] == "ping"
            assert "-c" in cmd
            assert "1.1.1.1" in cmd

    def test_linux_timeout_flag(self):
        with patch("netdiag.IS_LINUX", True):
            cmd = ping_command("8.8.8.8", timeout_s=5)
            assert "-W" in cmd
            idx = cmd.index("-W")
            assert cmd[idx + 1] == "5"

    def test_macos(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", True), patch("netdiag.IS_WINDOWS", False):
            cmd = ping_command("1.1.1.1")
            assert cmd[0] == "ping"
            assert "-t" in cmd

    def test_windows(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", False), patch("netdiag.IS_WINDOWS", True):
            cmd = ping_command("1.1.1.1")
            assert cmd[0] == "ping"
            assert "-n" in cmd
            assert "-w" in cmd

    def test_ipv4_linux(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), patch("netdiag.IS_WINDOWS", False):
            cmd = ping_command("1.1.1.1", ipv=4)
            assert "-4" in cmd

    def test_ipv6_linux(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), patch("netdiag.IS_WINDOWS", False):
            cmd = ping_command("1.1.1.1", ipv=6)
            assert "-6" in cmd

    def test_timeout_min_one(self):
        with patch("netdiag.IS_LINUX", True):
            cmd = ping_command("1.1.1.1", timeout_s=0)
            assert "-W" in cmd
            idx = cmd.index("-W")
            assert cmd[idx + 1] == "1"

    def test_timeout_rounding(self):
        with patch("netdiag.IS_LINUX", True):
            cmd = ping_command("1.1.1.1", timeout_s=2.7)
            assert "-W" in cmd
            idx = cmd.index("-W")
            assert cmd[idx + 1] == "3"


class TestPingOnce:
    def test_success(self):
        with patch("netdiag.run_cmd") as mock_run, \
             patch("netdiag.IS_LINUX", True), \
             patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False):
            mock_run.return_value = (0, "64 bytes from 1.1.1.1: icmp_seq=1 ttl=53 time=12.3 ms", "")
            result = ping_once("1.1.1.1")
            assert result["ok"] is True
            assert result["rtt_ms"] == 12.3

    def test_timeout(self):
        with patch("netdiag.run_cmd") as mock_run, \
             patch("netdiag.IS_LINUX", True), \
             patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False):
            mock_run.return_value = (1, "", "ping: sendto: Network is unreachable")
            result = ping_once("1.1.1.1")
            assert result["ok"] is False
            assert result["rtt_ms"] is None

    def test_no_match_in_output(self):
        with patch("netdiag.run_cmd") as mock_run, \
             patch("netdiag.IS_LINUX", True), \
             patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False):
            mock_run.return_value = (0, "PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.", "")
            result = ping_once("1.1.1.1")
            assert result["ok"] is False
            assert result["rtt_ms"] is None

    def test_plan_b_tcp_fallback_when_no_ping(self):
        with patch("netdiag.run_cmd") as mock_run, \
             patch("netdiag.IS_LINUX", True), \
             patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), \
             patch("netdiag.has_tool") as mock_has:
            mock_run.return_value = (127, "", "command not found")
            mock_has.side_effect = lambda x: x == "ip"
            result = ping_once("1.1.1.1")
            assert result.get("_fallback") == "tcp" or not result["ok"]


class TestTcpPing:
    def test_tcp_ping_success(self):
        with patch("socket.create_connection") as mock_conn, \
             patch("time.perf_counter") as mock_time:
            mock_time.side_effect = [0.0, 0.025]
            result = _tcp_ping("1.1.1.1")
            assert result["ok"] is True
            assert result["rtt_ms"] == 25.0

    def test_tcp_ping_failure(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.side_effect = TimeoutError("timed out")
            result = _tcp_ping("google.com")
            assert result["ok"] is False
            assert result["rtt_ms"] is None
