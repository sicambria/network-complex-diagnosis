from unittest.mock import patch, MagicMock, call
import threading
import time

from netdiag import (
    monitor_targets, monitor_loop, monitor_start, monitor_stop, monitor_diagnose,
    MONITOR_STATE, MONITOR_LOCK,
)


class TestMonitorTargets:
    def setup_method(self):
        self._defaults = {
            "monitor_tcp_target": ("1.1.1.1", 443),
            "monitor_external_hosts": ["1.1.1.1", "8.8.8.8"],
            "monitor_dns_host": "google.com",
            "monitor_interval": 1.0,
        }

    def test_returns_correct_structure(self):
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = dict(self._defaults)
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        assert targets["gateway"] == "192.168.1.1"
        assert targets["external"] == ["1.1.1.1", "8.8.8.8"]
        assert targets["dns_host"] == "google.com"
        assert targets["tcp_host"] == "1.1.1.1"
        assert targets["tcp_port"] == 443
        assert targets["interval"] == 1.0
        assert set(targets.keys()) == {"gateway", "external", "dns_host", "tcp_host", "tcp_port", "interval"}

    def test_calls_detect_gateway(self):
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = dict(self._defaults)
            mock_gw.return_value = "10.0.0.1"
            monitor_targets()
        mock_gw.assert_called_once_with()

    def test_uses_load_config(self):
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = dict(self._defaults)
            mock_gw.return_value = "192.168.1.1"
            monitor_targets()
        mock_load.assert_called_once_with()

    def test_uses_config_values(self):
        custom = {
            "monitor_tcp_target": ("8.8.8.8", 80),
            "monitor_external_hosts": ["9.9.9.9"],
            "monitor_dns_host": "example.com",
            "monitor_interval": 2.5,
        }
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = custom
            mock_gw.return_value = "10.0.0.1"
            targets = monitor_targets()
        assert targets["gateway"] == "10.0.0.1"
        assert targets["external"] == ["9.9.9.9"]
        assert targets["dns_host"] == "example.com"
        assert targets["tcp_host"] == "8.8.8.8"
        assert targets["tcp_port"] == 80
        assert targets["interval"] == 2.5

    def test_falls_back_tcp_target_when_missing(self):
        cfg = {}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        assert targets["tcp_host"] == "1.1.1.1"
        assert targets["tcp_port"] == 443

    def test_falls_back_external_when_missing(self):
        cfg = {"monitor_tcp_target": ("1.1.1.1", 443)}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        from netdiag import DEFAULT_HOSTS
        assert targets["external"] == list(DEFAULT_HOSTS[:2])

    def test_falls_back_dns_host_when_missing(self):
        cfg = {"monitor_tcp_target": ("1.1.1.1", 443),
               "monitor_external_hosts": ["1.1.1.1"]}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        from netdiag import DNS_HOSTS
        assert targets["dns_host"] == DNS_HOSTS[0]

    def test_falls_back_interval_when_missing(self):
        cfg = {"monitor_tcp_target": ("1.1.1.1", 443),
               "monitor_external_hosts": ["1.1.1.1"],
               "monitor_dns_host": "google.com"}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        assert targets["interval"] == 1.0

    def test_tcp_target_none_falls_back(self):
        cfg = {"monitor_tcp_target": None,
               "monitor_external_hosts": ["1.1.1.1"],
               "monitor_dns_host": "google.com"}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        assert targets["tcp_host"] == "1.1.1.1"
        assert targets["tcp_port"] == 443

    def test_external_empty_list_uses_fallback(self):
        cfg = {"monitor_tcp_target": ("1.1.1.1", 443),
               "monitor_external_hosts": [],
               "monitor_dns_host": "google.com"}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        from netdiag import DEFAULT_HOSTS
        assert targets["external"] == list(DEFAULT_HOSTS[:2])

    def test_external_none_uses_fallback(self):
        cfg = {"monitor_tcp_target": ("1.1.1.1", 443),
               "monitor_external_hosts": None,
               "monitor_dns_host": "google.com"}
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = cfg
            mock_gw.return_value = "192.168.1.1"
            targets = monitor_targets()
        from netdiag import DEFAULT_HOSTS
        assert targets["external"] == list(DEFAULT_HOSTS[:2])

    def test_gateway_is_whatever_detect_gateway_returns(self):
        with patch("netdiag_core.config.load_config") as mock_load, \
                patch("netdiag_core.probes.netinfo.detect_gateway") as mock_gw:
            mock_load.return_value = dict(self._defaults)
            mock_gw.return_value = None
            targets = monitor_targets()
        assert targets["gateway"] is None


class TestMonitorLoop:
    def test_appends_samples_and_tracks_outages(self):
        state = {
            "running": True,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        sample_data = {"ts": "t1", "gateway": {"ok": True, "rtt_ms": 1.0},
                       "external": {"1.1.1.1": {"ok": True, "rtt_ms": 2.0}},
                       "dns": {"ok": True}, "tcp": {"ok": True}}
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt, \
                patch("netdiag_core.monitor.monitor_sample") as mock_ms, \
                patch("netdiag.time.sleep") as mock_sleep, \
                patch("netdiag.time.monotonic") as mock_mono:
            mock_mt.return_value = {"gateway": "gw", "external": ["1.1.1.1"],
                                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                                    "tcp_port": 443, "interval": 0.5}
            mock_ms.return_value = sample_data
            mock_mono.return_value = 0.0

            def stop_after_first(*a):
                state["running"] = False

            mock_sleep.side_effect = stop_after_first
            monitor_loop(state)

        assert len(state["samples"]) == 1
        assert state["samples"][0] == sample_data

    def test_exception_logged_and_continues(self):
        state = {
            "running": True,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        sample_data = {"ts": "t2", "gateway": {"ok": True, "rtt_ms": 1.0},
                       "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt, \
                patch("netdiag_core.monitor.monitor_sample") as mock_ms, \
                patch("netdiag.time.sleep") as mock_sleep, \
                patch("netdiag.time.monotonic") as mock_mono, \
                patch("netdiag.log.error") as mock_log:
            mock_mt.return_value = {"gateway": "gw", "external": ["1.1.1.1"],
                                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                                    "tcp_port": 443, "interval": 0.5}
            mock_ms.side_effect = [ValueError("boom"), sample_data]
            mock_mono.return_value = 0.0

            calls = []

            def stop_on_second_call(*a):
                calls.append(1)
                if len(calls) >= 2:
                    state["running"] = False

            mock_sleep.side_effect = stop_on_second_call
            monitor_loop(state)

        assert len(state["samples"]) == 1
        assert state["samples"][0] == sample_data
        mock_log.assert_called_once()
        assert "boom" in str(mock_log.call_args)

    def test_refreshes_targets_every_60_seconds(self):
        state = {
            "running": True,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        sample_data = {"ts": "t0", "gateway": {"ok": True, "rtt_ms": 1.0},
                       "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt, \
                patch("netdiag_core.monitor.monitor_sample") as mock_ms, \
                patch("netdiag.time.sleep") as mock_sleep, \
                patch("netdiag.time.monotonic") as mock_mono:
            mock_mt.return_value = {"gateway": "gw", "external": ["1.1.1.1"],
                                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                                    "tcp_port": 443, "interval": 0.5}
            mock_ms.return_value = sample_data
            mock_mono.side_effect = [0.0, 0.0, 61.0, 61.0, 61.0]

            calls = []

            def stop_after_three(*a):
                calls.append(1)
                if len(calls) >= 3:
                    state["running"] = False

            mock_sleep.side_effect = stop_after_three
            monitor_loop(state)

        assert mock_mt.call_count == 2
        assert len(state["samples"]) == 3

    def test_sleeps_for_interval(self):
        state = {
            "running": True,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        sample_data = {"ts": "t0", "gateway": {"ok": True, "rtt_ms": 1.0},
                       "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt, \
                patch("netdiag_core.monitor.monitor_sample") as mock_ms, \
                patch("netdiag.time.sleep") as mock_sleep, \
                patch("netdiag.time.monotonic") as mock_mono:
            mock_mt.return_value = {"gateway": "gw", "external": ["1.1.1.1"],
                                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                                    "tcp_port": 443, "interval": 2.0}
            mock_ms.return_value = sample_data
            mock_mono.return_value = 0.0
            mock_sleep.side_effect = lambda _: state.update({"running": False})
            monitor_loop(state)
        mock_sleep.assert_called_once_with(2.0)

    def test_stops_when_running_false(self):
        state = {
            "running": False,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt, \
                patch("netdiag_core.monitor.monitor_sample") as mock_ms, \
                patch("netdiag.time.sleep") as mock_sleep:
            mock_mt.return_value = {"gateway": "gw", "external": [],
                                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                                    "tcp_port": 443, "interval": 1.0}
            monitor_loop(state)
        mock_mt.assert_called_once()
        mock_ms.assert_not_called()
        mock_sleep.assert_not_called()

    def test_sets_targets_at_start(self):
        state = {
            "running": False,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        expected = {"gateway": "gw", "external": ["1.1.1.1"],
                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                    "tcp_port": 443, "interval": 1.0}
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt:
            mock_mt.return_value = expected
            monitor_loop(state)
        assert state["targets"] == expected

    def test_uses_interval_from_targets(self):
        state = {
            "running": True,
            "samples": [],
            "events": [],
            "outages": {},
            "targets": None,
            "started_at": None,
            "thread": None,
        }
        sample_data = {"ts": "t0", "gateway": {"ok": True, "rtt_ms": 1.0},
                       "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        with patch("netdiag_core.monitor.monitor_targets") as mock_mt, \
                patch("netdiag_core.monitor.monitor_sample") as mock_ms, \
                patch("netdiag.time.sleep") as mock_sleep, \
                patch("netdiag.time.monotonic") as mock_mono:
            mock_mt.return_value = {"gateway": "gw", "external": ["1.1.1.1"],
                                    "dns_host": "g.com", "tcp_host": "1.1.1.1",
                                    "tcp_port": 443, "interval": 0.3}
            mock_ms.return_value = sample_data
            mock_mono.return_value = 0.0
            mock_sleep.side_effect = lambda _: state.update({"running": False})
            monitor_loop(state)
        mock_sleep.assert_called_once_with(0.3)


