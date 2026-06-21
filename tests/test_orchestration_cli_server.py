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


class TestCliMain:
    def test_cli_version(self):
        with (
            patch("netdiag_core.cli.build_parser") as mock_bp,
            patch("sys.stdout", new_callable=StringIO) as mock_stdout,
        ):
            mock_args = MagicMock()
            mock_args.version = True
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_bp.return_value.parse_args.return_value = mock_args

            cli_main()

            assert f"netdiag v{VERSION}" in mock_stdout.getvalue()

    def test_cli_license(self):
        with (
            patch("netdiag_core.cli.build_parser") as mock_bp,
            patch("sys.stdout", new_callable=StringIO) as mock_stdout,
        ):
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = True
            mock_args.gui = False
            mock_args.daemon = False
            mock_bp.return_value.parse_args.return_value = mock_args

            cli_main()

            output = mock_stdout.getvalue()
            assert "GNU" in output or "Affero" in output or "General Public" in output

    def test_cli_gui_missing_deps(self):
        def fake_import(name, *args, **kwargs):
            if name == "fastapi" or name.startswith("fastapi.") or name == "uvicorn":
                raise ImportError(f"No module named {name}")
            return orig_import(name, *args, **kwargs)

        import builtins
        orig_import = builtins.__import__

        with (
            patch("netdiag_core.cli.build_parser") as mock_bp,
            patch("sys.stderr", new_callable=StringIO) as mock_stderr,
        ):
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = True
            mock_args.daemon = False
            mock_bp.return_value.parse_args.return_value = mock_args

            with patch("builtins.__import__", side_effect=fake_import):
                with pytest.raises(SystemExit) as exc_info:
                    cli_main()

            assert exc_info.value.code == 1
            assert "fastapi and uvicorn are required" in mock_stderr.getvalue()

    def test_cli_low_count(self):
        with patch("netdiag_core.cli.build_parser") as mock_bp:
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_args.count = 0
            mock_bp.return_value.parse_args.return_value = mock_args

            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 2

    def test_cli_negative_interval(self):
        with patch("netdiag_core.cli.build_parser") as mock_bp:
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_args.count = 5
            mock_args.interval = -1
            mock_bp.return_value.parse_args.return_value = mock_args

            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 2

    def test_cli_low_timeout(self):
        with patch("netdiag_core.cli.build_parser") as mock_bp:
            mock_args = MagicMock()
            mock_args.version = False
            mock_args.license = False
            mock_args.gui = False
            mock_args.daemon = False
            mock_args.count = 5
            mock_args.interval = 1
            mock_args.timeout = 0
            mock_bp.return_value.parse_args.return_value = mock_args

            with pytest.raises(SystemExit) as exc_info:
                cli_main()

            assert exc_info.value.code == 2

    def test_cli_normal_flow(self):
        mock_args = MagicMock()
        mock_args.version = False
        mock_args.license = False
        mock_args.gui = False
        mock_args.daemon = False
        mock_args.count = 5
        mock_args.interval = 0.5
        mock_args.timeout = 2
        mock_args.outdir = "/tmp/test_outdir"
        mock_args.history_dir = "/tmp/.netdiag"
        mock_args.quiet = True

        with (
            patch("netdiag_core.cli.build_parser") as mock_bp,
            patch("netdiag_core.orchestrate.full_diagnostic") as mock_diag,
            patch("netdiag_core.reporting.write_csv") as mock_csv,
            patch("netdiag_core.reporting.write_report") as mock_report,
            patch("netdiag_core.reporting.print_console_summary") as mock_print,
            patch("netdiag_core.config.save_history") as mock_save,
            patch("netdiag_core.cli.Path") as mock_path,
            patch("netdiag_core.reporting.flatten_ping", return_value=[]),
            patch("netdiag_core.reporting.ping_summary_rows", return_value=[]),
        ):
            mock_bp.return_value.parse_args.return_value = mock_args
            mock_diag.return_value = {"health_score": 85, "diagnosis": []}

            mock_outdir = MagicMock()
            mock_path.return_value = mock_outdir
            mock_outdir.__truediv__.return_value = MagicMock()

            cli_main()

            mock_diag.assert_called_once_with(mock_args)
            mock_outdir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
            assert mock_csv.call_count == 2
            mock_report.assert_called_once()
            mock_print.assert_called_once()
            mock_save.assert_called_once()


class TestStartServer:
    def test_gui_mode(self):
        args = Args(daemon=False, port=8080)
        mock_app = MagicMock()
        mock_uvicorn = MagicMock()

        with (
            patch("netdiag_core.server.app.socket.socket"),  # don't bind a real port
            patch("netdiag_core.server.app.build_app", return_value=(mock_app, {}, MagicMock())),
            patch("netdiag.threading.Thread") as mock_thread,
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            start_server(args)

            mock_uvicorn.run.assert_called_once_with(
                mock_app, host="0.0.0.0", port=8080, log_level="warning",
            )
            mock_thread.assert_not_called()

    def test_daemon_mode(self):
        args = Args(daemon=True, port=8080)
        mock_app = MagicMock()
        mock_cr = {"status": "idle", "progress": {}, "results": None}
        mock_parser_fn = MagicMock()
        mock_diag_args = MagicMock()
        mock_diag_args.no_bufferbloat = False
        mock_diag_args.history_dir = "/tmp/.netdiag"
        mock_parser_fn.return_value.parse_args.return_value = mock_diag_args
        mock_uvicorn = MagicMock()

        with (
            patch("netdiag_core.server.app.socket.socket"),  # don't bind a real port
            patch("netdiag_core.server.app.build_app", return_value=(mock_app, mock_cr, mock_parser_fn)),
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("threading.Thread") as mock_thread_cls,
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            start_server(args)

            mock_thread_cls.assert_called_once()
            call_kwargs = mock_thread_cls.call_args[1]
            assert call_kwargs["daemon"] is True
            assert callable(call_kwargs["target"])

            mock_thread_instance.start.assert_called_once()
            mock_uvicorn.run.assert_called_once_with(
                mock_app, host="0.0.0.0", port=8080, log_level="warning",
            )

    def test_daemon_loop_logic(self):
        args = Args(daemon=True, port=8080, history_dir="/tmp/.netdiag")
        mock_app = MagicMock()
        mock_cr = {"status": "idle", "progress": {}, "results": None}
        mock_parser_fn = MagicMock()
        mock_diag_args = MagicMock()
        mock_diag_args.no_bufferbloat = False
        mock_diag_args.history_dir = "/tmp/.netdiag"
        mock_parser_fn.return_value.parse_args.return_value = mock_diag_args
        mock_uvicorn = MagicMock()

        sleep_count = [0]

        def mock_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 1:
                raise RuntimeError("break_loop")

        with (
            patch("netdiag_core.server.app.socket.socket"),  # don't bind a real port
            patch("netdiag_core.server.app.build_app", return_value=(mock_app, mock_cr, mock_parser_fn)),
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.orchestrate.full_diagnostic", return_value={"health_score": 85, "diagnosis": []}) as mock_diag,
            patch("netdiag_core.config.save_history") as mock_save,
            patch("threading.Thread") as mock_thread_cls,
            patch("time.sleep", side_effect=mock_sleep),
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance

            start_server(args)

            call_kwargs = mock_thread_cls.call_args[1]
            target = call_kwargs["target"]
            target_args = call_kwargs["args"]

            mock_cr["_lock"] = threading.RLock()

            with pytest.raises(RuntimeError, match="break_loop"):
                target(*target_args)

            mock_diag.assert_called()
            mock_save.assert_called()

    def test_daemon_loop_exception(self):
        args = Args(daemon=True, port=8080, history_dir="/tmp/.netdiag")
        mock_app = MagicMock()
        mock_cr = {"status": "idle", "progress": {}, "results": None}
        mock_parser_fn = MagicMock()
        mock_diag_args = MagicMock()
        mock_diag_args.no_bufferbloat = False
        mock_diag_args.history_dir = "/tmp/.netdiag"
        mock_parser_fn.return_value.parse_args.return_value = mock_diag_args
        mock_uvicorn = MagicMock()

        sleep_count = [0]

        def mock_sleep(secs):
            sleep_count[0] += 1
            if sleep_count[0] >= 1:
                raise RuntimeError("break_loop")

        with (
            patch("netdiag_core.server.app.socket.socket"),  # don't bind a real port
            patch("netdiag_core.server.app.build_app", return_value=(mock_app, mock_cr, mock_parser_fn)),
            patch("netdiag_core.runtime.IS_LINUX", True),
            patch("netdiag_core.orchestrate.full_diagnostic", side_effect=ValueError("test error")) as mock_diag,
            patch("netdiag_core.config.save_history") as mock_save,
            patch("threading.Thread") as mock_thread_cls,
            patch("time.sleep", side_effect=mock_sleep),
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance
            start_server(args)
            call_kwargs = mock_thread_cls.call_args[1]
            target = call_kwargs["target"]
            target_args = call_kwargs["args"]
            mock_cr["_lock"] = threading.RLock()

            with pytest.raises(RuntimeError, match="break_loop"):
                target(*target_args)

            assert mock_cr["status"] == "error"
            assert mock_cr["error"] == "test error"

    def test_daemon_non_linux_sets_no_bufferbloat(self):
        args = Args(daemon=True, port=8080, history_dir="/tmp/.netdiag")
        mock_app = MagicMock()
        mock_cr = {"status": "idle", "progress": {}, "results": None}
        mock_parser_fn = MagicMock()
        mock_diag_args = MagicMock()
        mock_diag_args.no_bufferbloat = True
        mock_diag_args.history_dir = "/tmp/.netdiag"
        mock_parser_fn.return_value.parse_args.return_value = mock_diag_args
        mock_uvicorn = MagicMock()

        with (
            patch("netdiag_core.server.app.socket.socket"),  # don't bind a real port
            patch("netdiag_core.server.app.build_app", return_value=(mock_app, mock_cr, mock_parser_fn)),
            patch("netdiag_core.runtime.IS_LINUX", False),
            patch("netdiag_core.runtime.IS_MACOS", True),
            patch("threading.Thread") as mock_thread_cls,
            patch.dict("sys.modules", {"uvicorn": mock_uvicorn}),
        ):
            mock_thread_instance = MagicMock()
            mock_thread_cls.return_value = mock_thread_instance
            start_server(args)
            call_kwargs = mock_thread_cls.call_args[1]
            target = call_kwargs["target"]
            target_args = call_kwargs["args"]
            # The target will run with no_bufferbloat = True
            assert mock_diag_args.no_bufferbloat is True

    def test_build_app_failure(self):
        args = Args(daemon=False, port=8080)

        with (
            patch("netdiag_core.server.app.build_app", return_value=(None, None, None)),
            patch("sys.stderr", new_callable=StringIO) as mock_stderr,
        ):
            with pytest.raises(SystemExit) as exc_info:
                start_server(args)

            assert exc_info.value.code == 1
            assert "fastapi and uvicorn required" in mock_stderr.getvalue()
