import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from netdiag import write_report, save_history, load_history, build_parser, print_console_summary


SAMPLE_RESULTS = {
    "timestamp": "2026-06-10T12:00:00+02:00",
    "platform": "Linux",
    "os": "Linux-6.8",
    "default_interface": "eth0",
    "gateway": "192.168.1.1",
    "health_score": 85,
    "diagnosis": [
        {"severity": "clean", "layer": "physical", "title": "Interface OK",
         "detail": "No errors", "fix": None},
        {"severity": "warning", "layer": "wifi", "title": "Weak signal",
         "detail": "Signal -72 dBm", "fix": "Move closer to router"},
    ],
    "mtr": {
        "hops": [
            {"hop": 1, "loss_pct": 0.0, "avg_ms": 1.2},
            {"hop": 2, "loss_pct": 5.0, "avg_ms": 10.0},
        ]
    },
    "dns": [{"host": "example.com", "failures": 0}],
    "tcp": [{"host": "1.1.1.1", "port": 443, "failures": 0}],
    "gateway_ping": {"min_ms": 1.0, "max_ms": 2.0, "avg_ms": 1.5, "p95_ms": 1.9, "loss_pct": 0},
    "internet_ping": [
        {"host": "1.1.1.1", "min_ms": 10.0, "max_ms": 20.0, "avg_ms": 15.0, "p95_ms": 19.0, "loss_pct": 0, "jitter_ms": 5.0},
    ],
}


class TestWriteReport:
    def test_write_report_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.txt"
            write_report(str(path), SAMPLE_RESULTS)
            assert path.exists()
            text = path.read_text()
            assert "Health score: 85/100" in text
            assert "Interface OK" in text
            assert "Weak signal" in text
            assert "Move closer to router" in text
            assert "192.168.1.1" in text

    def test_write_report_without_diagnosis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.txt"
            write_report(str(path), {"timestamp": "now", "platform": "X", "diagnosis": []})
            assert path.exists()
            text = path.read_text()
            assert "Diagnosis:" in text


class TestPrintConsoleSummary:
    def test_output_contains_health_score(self, capsys):
        print_console_summary(SAMPLE_RESULTS, "/tmp")
        captured = capsys.readouterr()
        assert "Health score: 85/100" in captured.out

    def test_output_contains_diagnosis(self, capsys):
        print_console_summary(SAMPLE_RESULTS, "/tmp")
        captured = capsys.readouterr()
        assert "Interface OK" in captured.out
        assert "Weak signal" in captured.out

    def test_output_contains_output_dir(self, capsys):
        print_console_summary(SAMPLE_RESULTS, "/output/dir")
        captured = capsys.readouterr()
        assert "/output/dir" in captured.out


class TestSaveHistory:
    def test_save_history_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist_dir = Path(tmp)
            fname = save_history(str(hist_dir), SAMPLE_RESULTS)
            assert fname is not None
            assert fname.startswith("session_")
            assert fname.endswith(".json")
            saved = json.loads((hist_dir / fname).read_text())
            assert saved["health_score"] == 85
            assert saved["gateway"] == "192.168.1.1"

    def test_save_history_auto_creates_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist_dir = Path(tmp) / "new_dir" / "sub"
            fname = save_history(str(hist_dir), SAMPLE_RESULTS)
            assert hist_dir.exists()
            assert (hist_dir / fname).exists()

    def test_load_history_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = load_history(str(tmp))
            assert sessions == []

    def test_load_history_with_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_history(str(tmp), SAMPLE_RESULTS)
            sessions = load_history(str(tmp))
            assert len(sessions) == 1
            assert sessions[0]["health_score"] == 85
            assert "_file" in sessions[0]

    def test_load_history_ignores_corrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "session_corrupt.json").write_text("{bad json")
            sessions = load_history(str(tmp))
            assert sessions == []


class TestBuildParser:
    def test_default_values(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.hosts == ["1.1.1.1", "8.8.8.8", "9.9.9.9", "google.com"]
        assert args.count == 20
        assert args.interval == 0.5
        assert args.quiet is False
        assert args.gui is False
        assert args.daemon is False
        assert args.port == 8080
        assert args.outdir == "internet_diagnostics"

    def test_custom_values(self):
        parser = build_parser()
        args = parser.parse_args(["--count", "5", "--interval", "0.2", "--quiet", "--gui", "--port", "3000"])
        assert args.count == 5
        assert args.interval == 0.2
        assert args.quiet is True
        assert args.gui is True
        assert args.port == 3000

    def test_no_speedtest_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--no-speedtest"])
        assert args.no_speedtest is True

    def test_no_trace_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--no-trace"])
        assert args.no_trace is True
