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
        with patch("netdiag.detect_package_manager", return_value="apt"), patch("netdiag.APT_PACKAGES", {"ping": "iputils-ping"}):
            hint = install_hint(["ping"])
            assert hint is not None
            assert "apt" in hint

    def test_dnf_hint(self):
        with patch("netdiag.detect_package_manager", return_value="dnf"):
            hint = install_hint(["ping"])
            assert "dnf" in hint

    def test_yum_hint(self):
        with patch("netdiag.detect_package_manager", return_value="yum"):
            hint = install_hint(["ping"])
            assert "yum" in hint

    def test_pacman_hint(self):
        with patch("netdiag.detect_package_manager", return_value="pacman"):
            hint = install_hint(["ping"])
            assert "pacman" in hint

    def test_zypper_hint(self):
        with patch("netdiag.detect_package_manager", return_value="zypper"):
            hint = install_hint(["ping"])
            assert "zypper" in hint

    def test_unknown_pm_hint(self):
        with patch("netdiag.detect_package_manager", return_value=None):
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
            with patch("netdiag.open", return_value=open(f.name, "r")):
                gw = _parse_proc_net_route()
                assert gw == "192.168.1.1"

    def test_no_gateway_file_not_found(self):
        with patch("netdiag.open") as mock_open:
            mock_open.side_effect = FileNotFoundError
            assert _parse_proc_net_route() is None

    def test_no_default_route(self):
        content = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        content += "eth0\t0101A8C0\t00000000\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="route") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                assert _parse_proc_net_route() is None


class TestProcNetRouteIface:
    def test_parse_valid_interface(self):
        content = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        content += "eth0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="route") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                iface = _parse_proc_net_route_iface()
                assert iface == "eth0"

    def test_file_not_found(self):
        with patch("netdiag.open", side_effect=FileNotFoundError):
            assert _parse_proc_net_route_iface() is None


class TestDetectWirelessInterface:
    def test_linux_procfs_fallback(self):
        import tempfile
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n face | tus | link quality | level         |   level\n wlp1s0: 0000   50.  -65.  -256\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with (
                patch("netdiag.IS_LINUX", True),
                patch("netdiag.IS_MACOS", False),
                patch("netdiag.IS_WINDOWS", False),
                patch("netdiag.has_tool", return_value=False),
                patch("pathlib.Path.exists", return_value=True),
                patch("pathlib.Path.read_text", return_value=content),
            ):
                result = detect_wireless_interface()
                assert result == "wlp1s0"

    def test_linux_procfs_exception(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.IS_MACOS", False),
            patch("netdiag.IS_WINDOWS", False),
            patch("netdiag.has_tool", return_value=False),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", side_effect=Exception("boom")),
        ):
            result = detect_wireless_interface()
            assert result is None

    def test_macos_returns_default_interface(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.get_default_interface", return_value="en0"),
        ):
            result = detect_wireless_interface()
            assert result == "en0"

    def test_windows_netsh_finds_name(self):
        out = "\n".join(["", "Name                   : Wi-Fi", "SSID                   : MyNet"])
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", False),
            patch("netdiag.IS_WINDOWS", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = detect_wireless_interface()
            assert result == "Wi-Fi"

    def test_windows_netsh_failure(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", False),
            patch("netdiag.IS_WINDOWS", True),
            patch("netdiag.run_cmd", return_value=(1, "", "error")),
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
            with patch("netdiag.open", return_value=open(f.name, "r")):
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
            with patch("netdiag.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_empty_line_skipped(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_value_error_signal_parse(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += " wlp1s0: 0000   50.  abc  def\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_index_error_short_line(self):
        content = "Inter-| sta-|   Quality        |   Signal       |   Noise\n"
        content += " face | tus | link quality | level         |   level\n"
        content += " wlp1s0:\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="wireless") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                result = _proc_net_wireless_any()
                assert result is None

    def test_file_not_found(self):
        with patch("netdiag.open", side_effect=FileNotFoundError):
            assert _proc_net_wireless_any() is None


class TestProcNetTcp:
    def test_parse_valid(self):
        content = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        content += "   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 00000000 100 0 0 10 0\n"
        content += "   1: 0100007F:0277 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 23456 1 00000000 100 0 0 10 0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="tcp") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                result = _proc_net_tcp_stats()
                assert result is not None
                assert result["available"] is True
                assert result["connections"] == 0  # both entries are LISTEN (0A), not ESTABLISHED (01)

    def test_established_connection_counted(self):
        content = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        content += "   0: 0100007F:0277 0100007F:1234 01 00000000:00000000 00:00000000 00000000     0        0 12345 1 00000000 100 0 0 10 0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="tcp") as f:
            f.write(content)
            f.flush()
            with patch("netdiag.open", return_value=open(f.name, "r")):
                result = _proc_net_tcp_stats()
                assert result["connections"] == 1

    def test_file_not_found(self):
        with patch("netdiag.open", side_effect=FileNotFoundError):
            assert _proc_net_tcp_stats() is None


class TestInterfaceStats:
    def test_no_iface(self):
        result = interface_stats(None)
        assert result["available"] is False
        assert "No interface" in result.get("reason", "")

    def test_macos(self):
        out = "\n".join(["en0: ...", "inet 192.168.1.2", "iperr: 0", "oerrors: 5"])
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("en0")
            assert result["available"] is True

    def test_macos_run_fails(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.run_cmd", return_value=(1, "", "error")),
        ):
            result = interface_stats("en0")
            assert result["available"] is False

    def test_windows(self):
        out = "\n".join(["", "Errors Received                    0", "Errors Sent                        3"])
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", False),
            patch("netdiag.IS_WINDOWS", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is True

    def test_windows_fails(self):
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", False),
            patch("netdiag.IS_WINDOWS", True),
            patch("netdiag.run_cmd", return_value=(1, "", "error")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is False

    def test_linux_ip_fails_fallback_missing(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.run_cmd", return_value=(1, "", "error")),
            patch("netdiag._sysfs_interface_stats", return_value=None),
        ):
            result = interface_stats("eth0")
            assert result["available"] is False
            assert "ip command failed" in result.get("reason", "")

    def test_linux_ip_parse_exceptions(self):
        out = "RX: errors:a dropped:b overruns:c frame:d"
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is True
            assert result["rx"]["errors"] == 0
            assert result["rx"]["dropped"] == 0
            assert result["rx"]["overruns"] == 0
            assert result["rx"]["frame"] == 0

    def test_linux_ip_tx_carrier_exception(self):
        out = "RX: errors:0 dropped:0 overruns:0 frame:0\nTX: errors:0 dropped:0 overruns:0 carrier:x"
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is True
            assert result["tx"]["carrier"] == 0


class TestTcpSocketStats:
    def test_macos(self):
        out = "tcp   0      0 127.0.0.1:5432  *:*    LISTEN retransmit:0\n"
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True

    def test_windows(self):
        out = "Segments Retransmitted = 5\n"
        with (
            patch("netdiag.IS_LINUX", False),
            patch("netdiag.IS_MACOS", False),
            patch("netdiag.IS_WINDOWS", True),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("eth0")
            assert result["available"] is True
            assert result["total_retransmits"] == 5

    def test_linux_ss_retrans_parse_exception(self):
        out = "ESTAB      0      0       127.0.0.1:5432      127.0.0.1:45678    retrans:abc rtt:x.y\n"
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", side_effect=lambda x: x == "ss"),
            patch("netdiag.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True


class TestGracefulDegradation:
    def test_detect_gateway_procfs_fallback(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=False),
            patch("netdiag._parse_proc_net_route",
                  return_value="192.168.1.1"),
        ):
            gw = detect_gateway()
            assert gw == "192.168.1.1"

    def test_get_interface_procfs_fallback(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=False),
            patch("netdiag._parse_proc_net_route_iface",
                  return_value="eth0"),
            patch("netdiag.run_cmd",
                  return_value=(1, "", "command not found")),
        ):
            iface = get_default_interface()
            assert iface == "eth0"

    def test_interface_stats_sysfs_fallback(self):
        with (
            patch("netdiag.IS_LINUX", True),
            patch("netdiag.has_tool", return_value=False),
            patch("netdiag.run_cmd",
                  return_value=(1, "", "command not found")),
            patch("netdiag._sysfs_interface_stats") as mock_sysfs,
        ):
            mock_sysfs.return_value = {
                "available": True, "interface": "eth0",
                "rx": {"errors": 0, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0},
                "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0},
            }
            result = interface_stats("eth0")
            assert result is not None
            assert result["available"] is True


class TestSaveConfig:
    def test_value_error_skips(self):
        from netdiag import save_config
        with (
            patch("netdiag.load_config", return_value={"ping_count": 20}),
            patch("netdiag.ensure_history_dir") as mock_ensure,
            patch("pathlib.Path.write_text"),
        ):
            d = MagicMock()
            mock_ensure.return_value = d
            result = save_config({"ping_count": "not-a-number"})
            assert result["ping_count"] == 20


class TestBuildApp:
    def test_import_error_returns_none(self):
        import builtins
        orig_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fastapi" or name.startswith("fastapi.") or name == "uvicorn":
                raise ImportError(f"No module named {name}")
            return orig_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            from netdiag import build_app
            app, state, parser = build_app()
            assert app is None
            assert state is None
            assert parser is None


class TestZeroDeps:
    def test_cli_imports_are_stdlib(self):
        import ast
        with open("netdiag.py") as f:
            tree = ast.parse(f.read())
        stdlib_modules = {
            "argparse", "csv", "json", "os", "platform", "re", "shutil",
            "socket", "statistics", "subprocess", "sys", "time",
            "datetime", "pathlib", "logging", "ast", "textwrap",
            "collections", "math", "io", "itertools", "functools",
            "typing", "urllib", "http", "ssl", "threading", "concurrent",
            "tempfile", "signal",
        }
        # optional GUI deps allowed inside function bodies
        optional_allow = {"fastapi", "uvicorn", "asyncio", "httpx", "anyio"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in optional_allow:
                        continue
                    assert top in stdlib_modules or top == "__future__", (
                        f"Non-stdlib import at line {node.lineno}: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    if top in optional_allow:
                        continue
                    assert top in stdlib_modules or top == "__future__", (
                        f"Non-stdlib import at line {node.lineno}: {node.module}"
                    )
