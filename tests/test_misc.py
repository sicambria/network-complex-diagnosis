import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from netdiag import (
    has_tool, check_tools, install_hint,
    _parse_proc_net_route, _parse_proc_net_route_iface,
    _proc_net_wireless_any, _proc_net_tcp_stats,
    interface_stats, detect_gateway, get_default_interface,
    IS_LINUX, ping_once, run_cmd,
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

    def test_apt_hint(self):
        with patch("netdiag.has_tool", return_value=True), patch("netdiag.APT_PACKAGES", {"ping": "iputils-ping"}):
            hint = install_hint(["ping"])
            assert hint is not None
            assert "apt" in hint or "install" in hint


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
