import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from netdiag import (
    has_tool, check_tools, install_hint,
    _parse_proc_net_route, _parse_proc_net_route_iface,
    _proc_net_wireless_any, _proc_net_tcp_stats,
    interface_stats, detect_gateway, get_default_interface, detect_wireless_interface,
    wifi_info, tcp_socket_stats, parse_rtt_ms,
    IS_LINUX, IS_MACOS, IS_WINDOWS, ping_once, run_cmd,
)


class TestHasTool:
    def test_known_tool(self):
        assert has_tool("sh") is True
        assert has_tool("python3") is True

    def test_unknown_tool(self):
        assert has_tool("this_tool_does_not_exist_xyz") is False

    def test_empty_name(self):
        assert has_tool("") is False


class TestInstallHint:
    def test_no_missing(self):
        assert install_hint([]) is None

    def test_none_missing(self):
        assert install_hint(None) is None

    def test_apt_hint(self):
        with patch("netdiag_core.runtime.detect_package_manager", return_value="apt"), patch("netdiag.APT_PACKAGES", {"ping": "iputils-ping"}):
            hint = install_hint(["ping"])
            assert hint is not None
            assert "apt" in hint

    def test_dnf_hint(self):
        with patch("netdiag_core.runtime.detect_package_manager", return_value="dnf"):
            hint = install_hint(["ping"])
            assert "dnf" in hint

    def test_yum_hint(self):
        with patch("netdiag_core.runtime.detect_package_manager", return_value="yum"):
            hint = install_hint(["ping"])
            assert "yum" in hint

    def test_pacman_hint(self):
        with patch("netdiag_core.runtime.detect_package_manager", return_value="pacman"):
            hint = install_hint(["ping"])
            assert "pacman" in hint

    def test_zypper_hint(self):
        with patch("netdiag_core.runtime.detect_package_manager", return_value="zypper"):
            hint = install_hint(["ping"])
            assert "zypper" in hint

    def test_unknown_pm_hint(self):
        with patch("netdiag_core.runtime.detect_package_manager", return_value=None):
            hint = install_hint(["ping"])
            assert "manually" in hint


class TestParseRttMs:
    def test_valid_match(self):
        assert parse_rtt_ms("time=12.3 ms") == 12.3
        assert parse_rtt_ms("time<12.3 ms") == 12.3

    def test_summary_match(self):
        assert parse_rtt_ms("rtt min/avg/max/mdev = 10.0/12.5/15.0/2.5") == 12.5

    def test_no_match(self):
        assert parse_rtt_ms("no ping data") is None

    def test_value_error_on_float_parse(self):
        val = parse_rtt_ms("time=NaN ms")
        assert val is None


class TestProcNetRoute:
    def test_gateway_parsed(self):
        content = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        content += "eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="route") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.netinfo.open", return_value=open(f.name, "r")):
                gw = _parse_proc_net_route()
                assert gw == "192.168.1.1"

    def test_no_gateway_file_not_found(self):
        with patch("netdiag_core.probes.netinfo.open") as mock_open:
            mock_open.side_effect = FileNotFoundError
            assert _parse_proc_net_route() is None

    def test_no_default_route(self):
        content = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        content += "eth0\t0101A8C0\t00000000\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="route") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.netinfo.open", return_value=open(f.name, "r")):
                assert _parse_proc_net_route() is None


class TestProcNetRouteIface:
    def test_parse_valid_interface(self):
        content = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        content += "eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="route") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.netinfo.open", return_value=open(f.name, "r")):
                iface = _parse_proc_net_route_iface()
                assert iface == "eth0"

    def test_file_not_found(self):
        with patch("netdiag_core.probes.netinfo.open", side_effect=FileNotFoundError):
            assert _parse_proc_net_route_iface() is None


class TestDetectWirelessInterface:
    def test_linux_procfs_fallback(self):
        import tempfile
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n face | tus | link quality | level         |   level\n wlp1s0: 0000   50.  -65.  -256\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with (
                patch("netdiag_core.runtime.IS_LINUX", True),
                patch("netdiag_core.runtime.IS_MACOS", False),
                patch("netdiag_core.runtime.IS_WINDOWS", False),
                patch("netdiag_core.runtime.has_tool", return_value=False),
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.read_text", return_value=content),
            ):
                result = detect_wireless_interface()
                assert result == "wlp1s0"

    def test_linux_procfs_exception(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", False),
            patch("netdiag_core.runtime.has_tool", return_value=False),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", side_effect=Exception("boom")),
        ):
            result = detect_wireless_interface()
            assert result is None

    def test_macos_returns_default_interface(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.probes.netinfo.get_default_interface", return_value="en0"),
        ):
            result = detect_wireless_interface()
            assert result == "en0"

    def test_windows_netsh_finds_name(self):
        out = "\n".join(["", "Name                   : Wi-Fi", "SSID                   : MyNet"])
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = detect_wireless_interface()
            assert result == "Wi-Fi"

    def test_windows_netsh_failure(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")),
        ):
            result = detect_wireless_interface()
            assert result is None


class TestProcNetWireless:
    def test_parse_valid(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += " wlp1s0: 0000   50.  -65.  -256\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.wifi.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is not None
                assert result["available"] is True
                assert result["interface"] == "wlp1s0"
                assert result["signal_dbm"] == -65

    def test_zero_signal_ignored(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += " wlp1s0: 0000   50.  0.  0.\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.wifi.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_empty_line_skipped(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.wifi.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_value_error_signal_parse(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += " wlp1s0: 0000   50.  abc  def\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.wifi.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_index_error_short_line(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += " wlp1s0:\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.wifi.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_file_not_found(self):
        with patch("netdiag_core.probes.wifi.open", side_effect=FileNotFoundError):
            assert _proc_net_wireless_any() is None


