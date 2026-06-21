from unittest.mock import patch, MagicMock, call
import threading
import time

from netdiag import (
    monitor_targets, monitor_loop, monitor_start, monitor_stop, monitor_diagnose,
    MONITOR_STATE, MONITOR_LOCK,
)


class TestMonitorStart:
    def setup_method(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = False
            MONITOR_STATE["samples"].clear()
            MONITOR_STATE["events"].clear()
            MONITOR_STATE["outages"] = {}
            MONITOR_STATE["started_at"] = None
            MONITOR_STATE["thread"] = None

    def test_sets_running_true(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = monitor_start()
        assert result is True
        with MONITOR_LOCK:
            assert MONITOR_STATE["running"] is True

    def test_clears_samples_and_events(self):
        with MONITOR_LOCK:
            MONITOR_STATE["samples"].append({"ts": "old"})
            MONITOR_STATE["events"].append({"target": "gw"})
            MONITOR_STATE["outages"] = {"gw": {"count": 1}}
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            monitor_start()
        with MONITOR_LOCK:
            assert len(MONITOR_STATE["samples"]) == 0
            assert len(MONITOR_STATE["events"]) == 0
            assert MONITOR_STATE["outages"] == {}

    def test_sets_started_at(self):
        with patch("netdiag.threading.Thread") as mock_thread, \
                patch("netdiag_core.runtime.now_iso") as mock_now:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            mock_now.return_value = "2026-06-11T12:00:00"
            monitor_start()
        with MONITOR_LOCK:
            assert MONITOR_STATE["started_at"] == "2026-06-11T12:00:00"

    def test_creates_and_starts_daemon_thread(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            monitor_start()
        mock_thread.assert_called_once()
        _, kwargs = mock_thread.call_args
        assert kwargs["daemon"] is True
        assert kwargs["target"] == monitor_loop
        assert kwargs["args"] == (MONITOR_STATE,)
        mock_t.start.assert_called_once()

    def test_stores_thread_in_state(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            monitor_start()
        with MONITOR_LOCK:
            assert MONITOR_STATE["thread"] is mock_t

    def test_returns_false_if_already_running(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            monitor_start()
        with patch("netdiag.threading.Thread") as mock_thread2:
            result = monitor_start()
        assert result is False

    def test_returns_true_on_success(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = monitor_start()
        assert result is True

    def test_thread_not_started_when_already_running(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            monitor_start()
        with patch("netdiag.threading.Thread") as mock_thread2:
            mock_t2 = MagicMock()
            mock_thread2.return_value = mock_t2
            monitor_start()
        mock_t2.start.assert_not_called()


class TestMonitorStop:
    def setup_method(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = False
            MONITOR_STATE["samples"].clear()
            MONITOR_STATE["events"].clear()
            MONITOR_STATE["outages"] = {}
            MONITOR_STATE["started_at"] = None
            MONITOR_STATE["thread"] = None

    def test_sets_running_false(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = True
        result = monitor_stop()
        with MONITOR_LOCK:
            assert MONITOR_STATE["running"] is False
        assert result is True

    def test_returns_true_if_was_running(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = True
        result = monitor_stop()
        assert result is True

    def test_returns_false_if_was_not_running(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = False
        result = monitor_stop()
        assert result is False

    def test_double_stop_returns_false_second_time(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = True
        monitor_stop()
        result = monitor_stop()
        assert result is False

    def test_start_after_stop_succeeds(self):
        with patch("netdiag.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            monitor_start()
        monitor_stop()
        with patch("netdiag.threading.Thread") as mock_thread2:
            mock_t2 = MagicMock()
            mock_thread2.return_value = mock_t2
            result = monitor_start()
        assert result is True


class TestMonitorDiagnose:
    def test_few_samples_returns_empty(self):
        snapshot = {"sample_count": 3, "targets": {}}
        assert monitor_diagnose(snapshot) == []

    def test_gateway_jitter_high(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "gateway": {"loss_pct": 0, "jitter_ms": 35},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("jitter" in h["text"] for h in hints)

    def test_gateway_loss_and_external_loss(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "gateway": {"loss_pct": 0},
                "external:1.1.1.1": {"loss_pct": 10},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("ISP/upstream" in h["text"] for h in hints)

    def test_intermittent_gateway_loss(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "gateway": {"loss_pct": 25},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("cabling" in h["text"] for h in hints)

    def test_dns_loss(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "gateway": {"loss_pct": 0},
                "dns": {"loss_pct": 30},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("DNS" in h["text"] for h in hints)

    def test_tcp_loss_ping_ok(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "gateway": {"loss_pct": 0},
                "tcp": {"loss_pct": 50},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("TCP" in h["text"] for h in hints)

    def test_sporadic_loss_no_other_hints(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "external:1.1.1.1": {"loss_pct": 10},
                "external:8.8.8.8": {"loss_pct": 50},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("Sporadic" in h["text"] for h in hints)

    def test_no_issues_returns_clean(self):
        snapshot = {
            "sample_count": 10,
            "targets": {
                "gateway": {"loss_pct": 0, "jitter_ms": 10},
            },
        }
        hints = monitor_diagnose(snapshot)
        assert any("No intermittent" in h["text"] for h in hints)
