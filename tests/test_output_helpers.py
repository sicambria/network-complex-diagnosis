import csv
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from netdiag import flatten_ping, ping_summary_rows, compact_ping, write_csv, _diag_args_from_kw


GATEWAY_SAMPLES = [
    {"seq": 1, "rtt_ms": 1.0},
    {"seq": 2, "rtt_ms": 2.0},
]
INTERNET_SAMPLES_A = [
    {"seq": 1, "rtt_ms": 10.0},
]
INTERNET_SAMPLES_B = [
    {"seq": 2, "rtt_ms": 20.0},
]

PING_RESULTS = {
    "gateway_ping": {
        "host": "192.168.1.1",
        "min_ms": 1.0, "max_ms": 2.0, "avg_ms": 1.5,
        "p95_ms": 1.9, "loss_pct": 0, "jitter_ms": 0.5,
        "samples": GATEWAY_SAMPLES,
    },
    "internet_ping": [
        {"host": "1.1.1.1", "min_ms": 10.0, "max_ms": 20.0, "avg_ms": 15.0,
         "p95_ms": 19.0, "loss_pct": 0, "jitter_ms": 5.0,
         "samples": INTERNET_SAMPLES_A},
        {"host": "8.8.8.8", "min_ms": 12.0, "max_ms": 18.0, "avg_ms": 15.0,
         "p95_ms": 17.0, "loss_pct": 0, "jitter_ms": 4.0,
         "samples": INTERNET_SAMPLES_B},
    ],
}


class TestFlattenPing:
    def test_returns_samples_from_gateway_and_internet(self):
        rows = flatten_ping(PING_RESULTS)
        assert rows == GATEWAY_SAMPLES + INTERNET_SAMPLES_A + INTERNET_SAMPLES_B

    def test_returns_empty_list_when_no_ping_data(self):
        assert flatten_ping({}) == []
        assert flatten_ping({"gateway_ping": None}) == []

    def test_handles_missing_gateway_ping_key(self):
        results = {
            "internet_ping": [
                {"host": "1.1.1.1", "label": "WAN", "samples": [{"seq": 1}]},
            ],
        }
        assert flatten_ping(results) == [{"seq": 1}]

    def test_handles_missing_samples_key(self):
        results = {"gateway_ping": {"host": "gw", "min_ms": 1.0}}
        assert flatten_ping(results) == []

    def test_handles_none_samples(self):
        results = {"gateway_ping": {"host": "gw", "samples": None}}
        assert flatten_ping(results) == []

    def test_gateway_samples_empty_list(self):
        results = {"gateway_ping": {"host": "gw", "samples": []}}
        assert flatten_ping(results) == []

    def test_internet_ping_missing_samples_raises_key_error(self):
        results = {
            "internet_ping": [
                {"host": "1.1.1.1", "min_ms": 10.0},
            ],
        }
        try:
            flatten_ping(results)
            assert False, "expected KeyError"
        except KeyError:
            pass

    def test_internet_ping_not_present(self):
        results = {"gateway_ping": {"host": "gw", "min_ms": 1.0, "samples": [{"seq": 1}]}}
        assert flatten_ping(results) == [{"seq": 1}]


class TestPingSummaryRows:
    def test_returns_compact_rows_from_gateway_and_internet(self):
        rows = ping_summary_rows(PING_RESULTS)
        assert len(rows) == 3
        assert "samples" not in rows[0]
        assert rows[0]["host"] == "192.168.1.1"
        assert rows[1]["host"] == "1.1.1.1"
        assert rows[2]["host"] == "8.8.8.8"

    def test_removes_samples_key_from_all_entries(self):
        rows = ping_summary_rows(PING_RESULTS)
        for row in rows:
            assert "samples" not in row

    def test_returns_empty_list_when_no_ping_data(self):
        assert ping_summary_rows({}) == []

    def test_handles_missing_gateway_ping_key(self):
        results = {"internet_ping": [{"host": "1.1.1.1", "min_ms": 10.0}]}
        rows = ping_summary_rows(results)
        assert len(rows) == 1
        assert rows[0]["host"] == "1.1.1.1"

    def test_gateway_ping_none_is_skipped(self):
        results = {"gateway_ping": None, "internet_ping": []}
        assert ping_summary_rows(results) == []

    def test_preserves_non_samples_keys(self):
        rows = ping_summary_rows(PING_RESULTS)
        assert "min_ms" in rows[0]
        assert "max_ms" in rows[0]
        assert "loss_pct" in rows[0]
        assert "jitter_ms" in rows[0]

    def test_internet_ping_not_present(self):
        results = {"gateway_ping": {"host": "gw", "min_ms": 1.0}}
        rows = ping_summary_rows(results)
        assert len(rows) == 1
        assert rows[0]["host"] == "gw"


class TestCompactPing:
    FULL_ROW = {
        "label": "gateway", "host": "192.168.1.1", "ipv": "v4",
        "sent": 20, "received": 20, "loss_pct": 0.0,
        "min_ms": 1.0, "avg_ms": 1.5, "p95_ms": 1.9, "p99_ms": 2.5,
        "max_ms": 3.0, "jitter_ms": 0.5,
        "extra": "should not appear",
    }

    def test_returns_only_expected_keys(self):
        result = compact_ping(self.FULL_ROW)
        assert set(result.keys()) == {
            "label", "host", "ipv", "sent", "received", "loss_pct",
            "min_ms", "avg_ms", "p95_ms", "p99_ms", "max_ms", "jitter_ms",
        }

    def test_handles_missing_keys_gracefully(self):
        result = compact_ping({})
        for v in result.values():
            assert v is None

    def test_preserves_values_correctly(self):
        result = compact_ping(self.FULL_ROW)
        assert result["min_ms"] == 1.0
        assert result["max_ms"] == 3.0
        assert result["loss_pct"] == 0.0
        assert result["host"] == "192.168.1.1"
        assert result["label"] == "gateway"
        assert result["ipv"] == "v4"

    def test_partial_input_preserves_available_keys(self):
        row = {"host": "1.1.1.1", "min_ms": 10.0, "loss_pct": 5.0}
        result = compact_ping(row)
        assert result["host"] == "1.1.1.1"
        assert result["min_ms"] == 10.0
        assert result["loss_pct"] == 5.0
        assert result["label"] is None
        assert result["jitter_ms"] is None


class TestWriteCsv:
    def test_writes_csv_file_with_header(self):
        rows = [
            {"host": "1.1.1.1", "loss_pct": 0.0, "min_ms": 10.0},
            {"host": "8.8.8.8", "loss_pct": 5.0, "min_ms": 12.0},
        ]
        expected_keys = sorted({"host", "loss_pct", "min_ms"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.csv"
            write_csv(str(path), rows)
            text = path.read_text()
            reader = csv.DictReader(text.splitlines())
            assert reader.fieldnames == expected_keys
            assert len(list(reader)) == 2

    def test_writes_multiple_data_rows(self):
        rows = [
            {"host": "a", "val": 1},
            {"host": "b", "val": 2},
            {"host": "c", "val": 3},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "multi.csv"
            write_csv(str(path), rows)
            lines = path.read_text().strip().splitlines()
            assert len(lines) == 4
            assert lines[0] == "host,val"

    def test_handles_empty_rows_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.csv"
            write_csv(str(path), [])
            assert not path.exists()

    def test_fieldnames_are_sorted(self):
        rows = [{"z": 1, "a": 2, "m": 3}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sorted.csv"
            write_csv(str(path), rows)
            lines = path.read_text().strip().splitlines()
            assert lines[0] == "a,m,z"

    def test_uses_proper_csv_format(self):
        rows = [{"host": "1.1.1.1", "loss_pct": 0.0, "min_ms": 10.0}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "format.csv"
            write_csv(str(path), rows)
            content = path.read_text()
            assert "\r\n" not in content
            with path.open() as f:
                reader = csv.reader(f)
                header = next(reader)
                data = next(reader)
                assert "loss_pct" in header
                assert "0.0" in data


class TestDiagArgsFromKw:
    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_returns_object_with_expected_attributes(self, mock_load_config):
        mock_load_config.return_value = {
            "hosts": ["1.1.1.1", "8.8.8.8"],
            "ping_count": 20, "ping_interval": 0.5, "ping_timeout": 2,
            "dns_count": 10, "tcp_count": 10,
            "history_dir": "~/.netdiag",
        }
        a = _diag_args_from_kw({})
        assert a.hosts == ["1.1.1.1", "8.8.8.8"]
        assert a.count == 20
        assert a.interval == 0.5
        assert a.timeout == 2
        assert a.dns_count == 10
        assert a.tcp_count == 10
        assert a.quiet is True
        assert a.outdir == "internet_diagnostics"
        assert a.history_dir == "~/.netdiag"

    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_string_hosts_split_by_comma(self, mock_load_config):
        mock_load_config.return_value = {}
        a = _diag_args_from_kw({"hosts": "1.1.1.1,8.8.8.8, google.com"})
        assert a.hosts == ["1.1.1.1", "8.8.8.8", "google.com"]

    @patch("netdiag.load_config")
    def test_boolean_flags_set_correctly(self, mock_load_config):
        mock_load_config.return_value = {}
        with patch("netdiag.IS_LINUX", True):
            a = _diag_args_from_kw({"bufferbloat": False})
            assert a.no_bufferbloat is True
            a = _diag_args_from_kw({"bufferbloat": True})
            assert a.no_bufferbloat is False
            a = _diag_args_from_kw({})
            assert a.no_bufferbloat is False

    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_no_bufferbloat_true_on_non_linux(self, mock_load_config):
        mock_load_config.return_value = {}
        with patch("netdiag.IS_LINUX", False):
            a = _diag_args_from_kw({"bufferbloat": True})
            assert a.no_bufferbloat is True

    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_speedtest_iperf_flags(self, mock_load_config):
        mock_load_config.return_value = {}
        a = _diag_args_from_kw({})
        assert a.no_speedtest is True
        assert a.no_iperf is True
        a = _diag_args_from_kw({"speedtest": True, "iperf3": True})
        assert a.no_speedtest is False
        assert a.no_iperf is False

    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_no_trace_and_download_connection_flags(self, mock_load_config):
        mock_load_config.return_value = {}
        a = _diag_args_from_kw({})
        assert a.no_trace is False
        assert a.download_test is False
        assert a.connection_test is False
        a = _diag_args_from_kw({"trace": False, "download_test": True, "connection_test": True})
        assert a.no_trace is True
        assert a.download_test is True
        assert a.connection_test is True

    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_numeric_parameters_parsed_correctly(self, mock_load_config):
        mock_load_config.return_value = {}
        a = _diag_args_from_kw({
            "count": "10", "interval": "0.3", "timeout": "5",
            "dns_count": "3", "tcp_count": "7",
        })
        assert a.count == 10
        assert a.interval == 0.3
        assert a.timeout == 5
        assert a.dns_count == 3
        assert a.tcp_count == 7

    @patch("netdiag.load_config")
    @patch("netdiag.IS_LINUX", True)
    def test_uses_load_config_for_defaults(self, mock_load_config):
        mock_load_config.return_value = {
            "ping_count": 50,
            "ping_interval": 1.0,
            "history_dir": "/custom/path",
        }
        a = _diag_args_from_kw({})
        assert a.count == 50
        assert a.interval == 1.0
        assert a.history_dir == "/custom/path"
