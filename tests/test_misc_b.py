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


class TestProcNetTcp:
    def test_parse_valid(self):
        content = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        content += "   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 00000000 100 0 0 10 0\n"
        content += "   1: 0100007F:0277 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 23456 1 00000000 100 0 0 10 0\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix="tcp") as f:
            f.write(content)
            f.flush()
            with patch("netdiag_core.probes.sockets.open", return_value=open(f.name, "r")):
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
            with patch("netdiag_core.probes.sockets.open", return_value=open(f.name, "r")):
                result = _proc_net_tcp_stats()
                assert result["connections"] == 1

    def test_file_not_found(self):
        with patch("netdiag_core.probes.sockets.open", side_effect=FileNotFoundError):
            assert _proc_net_tcp_stats() is None


class TestInterfaceStats:
    def test_no_iface(self):
        result = interface_stats(None)
        assert result["available"] is False
        assert "No interface" in result.get("reason", "")

    def test_macos(self):
        out = "\n".join(["en0: ...", "inet 192.168.1.2", "iperr: 0", "oerrors: 5"])
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("en0")
            assert result["available"] is True

    def test_macos_run_fails(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")),
        ):
            result = interface_stats("en0")
            assert result["available"] is False

    def test_windows(self):
        out = "\n".join(["", "Errors Received                    0", "Errors Sent                        3"])
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is True

    def test_windows_fails(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is False

    def test_linux_ip_fails_fallback_missing(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")),
            patch("netdiag_core.probes.netinfo._sysfs_interface_stats", return_value=None),
        ):
            result = interface_stats("eth0")
            assert result["available"] is False
            assert "ip command failed" in result.get("reason", "")

    def test_linux_ip_parse_exceptions(self):
        out = "RX: errors:a dropped:b overruns:c frame:d"
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
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
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = interface_stats("eth0")
            assert result["available"] is True
            assert result["tx"]["carrier"] == 0


class TestTcpSocketStats:
    def test_macos(self):
        out = "tcp   0      0 127.0.0.1:5432  *:*    LISTEN retransmit:0\n"
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True

    def test_windows(self):
        out = "Segments Retransmitted = 5\n"
        with (
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", False),
            patch("netdiag_core.runtime.IS_WINDOWS", True),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("eth0")
            assert result["available"] is True
            assert result["total_retransmits"] == 5

    def test_linux_ss_retrans_parse_exception(self):
        out = "ESTAB      0      0       127.0.0.1:5432      127.0.0.1:45678    retrans:abc rtt:x.y\n"
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", side_effect=lambda x: x == "ss"),
            patch("netdiag_core.runtime.run_cmd", return_value=(0, out, "")),
        ):
            result = tcp_socket_stats("lo")
            assert result["available"] is True


class TestGracefulDegradation:
    def test_detect_gateway_procfs_fallback(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
            # `ip route` must fail so the procfs fallback is actually reached
            # (detect_gateway tries the command first, regardless of has_tool).
            patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "no ip")),
            patch("netdiag_core.probes.netinfo._parse_proc_net_route",
                  return_value="192.168.1.1"),
        ):
            gw = detect_gateway()
            assert gw == "192.168.1.1"

    def test_get_interface_procfs_fallback(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
            patch("netdiag_core.probes.netinfo._parse_proc_net_route_iface",
                  return_value="eth0"),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(1, "", "command not found")),
        ):
            iface = get_default_interface()
            assert iface == "eth0"

    def test_interface_stats_sysfs_fallback(self):
        with (
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.runtime.has_tool", return_value=False),
            patch("netdiag_core.runtime.run_cmd",
                  return_value=(1, "", "command not found")),
            patch("netdiag_core.probes.netinfo._sysfs_interface_stats") as mock_sysfs,
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
            patch("netdiag_core.config.load_config", return_value={"ping_count": 20}),
            patch("netdiag_core.config.ensure_history_dir") as mock_ensure,
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
        # The CLI core (the shim + the whole netdiag_core package) must import only
        # stdlib + first-party netdiag_core. The optional GUI deps (fastapi/uvicorn)
        # may appear, but only inside function bodies — never at module top level.
        import ast
        from pathlib import Path as P
        stdlib_modules = {
            "argparse", "csv", "json", "os", "platform", "re", "shutil",
            "socket", "statistics", "subprocess", "sys", "time",
            "datetime", "pathlib", "logging", "ast", "textwrap",
            "collections", "math", "io", "itertools", "functools",
            "typing", "urllib", "http", "ssl", "threading", "concurrent",
            "tempfile", "signal", "html",
        }
        optional_allow = {"fastapi", "uvicorn", "asyncio", "httpx", "anyio"}
        first_party = {"netdiag_core"}
        files = [P("netdiag.py")] + sorted(P("netdiag_core").rglob("*.py"))

        def _check(top, where):
            if top in optional_allow or top in first_party or top == "__future__":
                return
            assert top in stdlib_modules, f"Non-stdlib import in {where}: {top}"

        toplevel_only = set()  # (file) -> fastapi/uvicorn must not be a module-level import
        for fpath in files:
            tree = ast.parse(fpath.read_text(encoding="utf-8"))
            toplevel = {id(n) for n in tree.body}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        _check(top, f"{fpath}:{node.lineno} ({alias.name})")
                        if top in optional_allow and id(node) in toplevel:
                            toplevel_only.add(f"{fpath}:{node.lineno} {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    _check(top, f"{fpath}:{node.lineno} ({node.module})")
                    if top in optional_allow and id(node) in toplevel:
                        toplevel_only.add(f"{fpath}:{node.lineno} {node.module}")
        assert not toplevel_only, (
            "Optional GUI deps imported at module top level (must be lazy): " + ", ".join(sorted(toplevel_only))
        )
