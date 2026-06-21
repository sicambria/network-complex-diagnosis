from unittest.mock import patch
import json

from netdiag import (
    log_activity, get_activity_log, ACTIVITY_LOG,
    monitor_sample, _flatten_sample, _update_outages, _target_stats,
    monitor_snapshot, monitor_diagnose, MONITOR_STATE, MONITOR_LOCK,
    load_config, save_config, config_path, CONFIG_DEFAULTS,
    check_tools,
)


class TestActivityLog:
    def setup_method(self):
        ACTIVITY_LOG.clear()

    def test_log_activity_records_entry(self):
        log_activity("cmd", "ping -c 1 1.1.1.1", 0, 12.345)
        items = get_activity_log()
        assert len(items) == 1
        assert items[0]["kind"] == "cmd"
        assert items[0]["label"] == "ping -c 1 1.1.1.1"
        assert items[0]["ok"] is True
        assert items[0]["duration_ms"] == 12.35

    def test_log_activity_failure(self):
        log_activity("socket", "DNS resolve example.com", 999, 5, ok=False)
        items = get_activity_log()
        assert items[0]["ok"] is False
        assert items[0]["rc"] == 999

    def test_get_activity_log_most_recent_first(self):
        for i in range(5):
            log_activity("cmd", f"cmd-{i}", 0, 1.0)
        items = get_activity_log()
        assert items[0]["label"] == "cmd-4"
        assert items[-1]["label"] == "cmd-0"

    def test_activity_log_ring_buffer_caps_at_maxlen(self):
        for i in range(250):
            log_activity("cmd", f"cmd-{i}", 0, 1.0)
        assert len(ACTIVITY_LOG) == 200
        items = get_activity_log(limit=200)
        assert items[0]["label"] == "cmd-249"

    def test_run_cmd_logs_activity(self):
        from netdiag import run_cmd
        ACTIVITY_LOG.clear()
        rc, out, err = run_cmd(["echo", "hello"])
        assert rc == 0
        items = get_activity_log()
        assert len(items) == 1
        assert items[0]["kind"] == "cmd"
        assert "echo hello" in items[0]["label"]


class TestMonitorSample:
    def test_monitor_sample_structure(self):
        targets = {
            "gateway": "192.168.1.1",
            "external": ["1.1.1.1", "8.8.8.8"],
            "dns_host": "example.com",
            "tcp_host": "1.1.1.1",
            "tcp_port": 443,
        }
        with patch("netdiag_core.probes.ping.ping_once") as mock_ping, \
                patch("netdiag_core.probes.ping.resolve_all") as mock_resolve, \
                patch("netdiag_core.probes.ping._tcp_ping") as mock_tcp:
            mock_ping.return_value = {"ok": True, "rtt_ms": 5.0, "rc": 0}
            mock_resolve.return_value = {"ok": True, "addresses": []}
            mock_tcp.return_value = {"ok": True, "rtt_ms": 10.0}

            sample = monitor_sample(targets)

        assert sample["gateway"] == {"ok": True, "rtt_ms": 5.0}
        assert sample["external"]["1.1.1.1"] == {"ok": True, "rtt_ms": 5.0}
        assert sample["external"]["8.8.8.8"] == {"ok": True, "rtt_ms": 5.0}
        assert sample["dns"]["ok"] is True
        assert sample["tcp"] == {"ok": True, "rtt_ms": 10.0}
        assert "ts" in sample

    def test_monitor_sample_no_gateway(self):
        targets = {
            "gateway": None,
            "external": ["1.1.1.1"],
            "dns_host": "example.com",
            "tcp_host": "1.1.1.1",
            "tcp_port": 443,
        }
        with patch("netdiag_core.probes.ping.ping_once") as mock_ping, \
                patch("netdiag_core.probes.ping.resolve_all") as mock_resolve, \
                patch("netdiag_core.probes.ping._tcp_ping") as mock_tcp:
            mock_ping.return_value = {"ok": False, "rtt_ms": None, "rc": 1}
            mock_resolve.return_value = {"ok": False, "addresses": []}
            mock_tcp.return_value = {"ok": False, "rtt_ms": None}

            sample = monitor_sample(targets)

        assert sample["gateway"] is None
        assert sample["external"]["1.1.1.1"]["ok"] is False


class TestFlattenSample:
    def test_flatten_includes_all_targets(self):
        sample = {
            "ts": "2026-01-01T00:00:00",
            "gateway": {"ok": True, "rtt_ms": 1.0},
            "external": {"1.1.1.1": {"ok": True, "rtt_ms": 2.0}, "8.8.8.8": {"ok": False, "rtt_ms": None}},
            "dns": {"ok": True, "rtt_ms": None},
            "tcp": {"ok": False, "rtt_ms": None},
        }
        flat = _flatten_sample(sample)
        assert set(flat.keys()) == {"gateway", "external:1.1.1.1", "external:8.8.8.8", "dns", "tcp"}
        assert flat["gateway"]["ok"] is True
        assert flat["external:8.8.8.8"]["ok"] is False

    def test_flatten_handles_missing_gateway(self):
        sample = {"ts": "x", "gateway": None, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        flat = _flatten_sample(sample)
        assert "gateway" not in flat


class TestUpdateOutages:
    def test_outage_recorded_on_recovery(self):
        state = {"outages": {}, "events": []}
        fail_sample = {"ts": "t1", "gateway": {"ok": False, "rtt_ms": None}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        ok_sample = {"ts": "t2", "gateway": {"ok": True, "rtt_ms": 5.0}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}

        _update_outages(state, fail_sample)
        assert "gateway" in state["outages"]
        assert state["outages"]["gateway"]["count"] == 1

        _update_outages(state, fail_sample)
        assert state["outages"]["gateway"]["count"] == 2

        _update_outages(state, ok_sample)
        assert "gateway" not in state["outages"]
        assert len(state["events"]) == 1
        ev = state["events"][0]
        assert ev["target"] == "gateway"
        assert ev["start"] == "t1"
        assert ev["end"] == "t2"
        assert ev["consecutive_failures"] == 2

    def test_no_event_when_always_ok(self):
        state = {"outages": {}, "events": []}
        ok_sample = {"ts": "t1", "gateway": {"ok": True, "rtt_ms": 5.0}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}}
        _update_outages(state, ok_sample)
        assert state["outages"] == {}
        assert state["events"] == []


class TestTargetStats:
    def test_target_stats_with_loss_and_jitter(self):
        samples = [
            {"ts": "t1", "gateway": {"ok": True, "rtt_ms": 10.0}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}},
            {"ts": "t2", "gateway": {"ok": True, "rtt_ms": 20.0}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}},
            {"ts": "t3", "gateway": {"ok": False, "rtt_ms": None}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}},
            {"ts": "t4", "gateway": {"ok": True, "rtt_ms": 10.0}, "external": {}, "dns": {"ok": True}, "tcp": {"ok": True}},
        ]
        stats = _target_stats(samples, "gateway")
        assert stats["samples"] == 4
        assert stats["loss_pct"] == 25.0
        assert stats["count"] == 3
        assert stats["jitter_ms"] is not None

    def test_target_stats_empty(self):
        stats = _target_stats([], "gateway")
        assert stats["samples"] == 0
        assert stats["loss_pct"] is None
        assert stats["count"] == 0


class TestMonitorSnapshot:
    def setup_method(self):
        with MONITOR_LOCK:
            MONITOR_STATE["running"] = False
            MONITOR_STATE["samples"].clear()
            MONITOR_STATE["events"].clear()
            MONITOR_STATE["outages"] = {}
            MONITOR_STATE["started_at"] = None

    def test_snapshot_empty(self):
        snap = monitor_snapshot()
        assert snap["sample_count"] == 0
        assert snap["targets"] == {}
        assert snap["hints"] == []

    def test_snapshot_with_samples(self):
        sample = {
            "ts": "2026-01-01T00:00:00",
            "gateway": {"ok": True, "rtt_ms": 5.0},
            "external": {"1.1.1.1": {"ok": True, "rtt_ms": 30.0}},
            "dns": {"ok": True, "rtt_ms": None},
            "tcp": {"ok": True, "rtt_ms": 25.0},
        }
        with MONITOR_LOCK:
            for _ in range(6):
                MONITOR_STATE["samples"].append(sample)
        snap = monitor_snapshot()
        assert snap["sample_count"] == 6
        assert "gateway" in snap["targets"]
        assert "external:1.1.1.1" in snap["targets"]
        assert snap["hints"]
        assert snap["hints"][0]["severity"] == "clean"


class TestMonitorDiagnose:
    def _snapshot(self, targets, sample_count=10):
        return {"sample_count": sample_count, "targets": targets}

    def test_clean_when_no_loss(self):
        targets = {
            "gateway": {"loss_pct": 0, "jitter_ms": 1.0},
            "external:1.1.1.1": {"loss_pct": 0},
            "dns": {"loss_pct": 0},
            "tcp": {"loss_pct": 0},
        }
        hints = monitor_diagnose(self._snapshot(targets))
        assert any(h["severity"] == "clean" for h in hints)

    def test_isp_issue_when_gateway_clean_but_external_loss(self):
        targets = {
            "gateway": {"loss_pct": 0, "jitter_ms": 1.0},
            "external:1.1.1.1": {"loss_pct": 20},
            "dns": {"loss_pct": 0},
            "tcp": {"loss_pct": 0},
        }
        hints = monitor_diagnose(self._snapshot(targets))
        assert any("ISP" in h["text"] or "upstream" in h["text"] for h in hints)

    def test_local_link_issue_when_gateway_loss(self):
        targets = {
            "gateway": {"loss_pct": 10, "jitter_ms": 1.0},
            "external:1.1.1.1": {"loss_pct": 0},
            "dns": {"loss_pct": 0},
            "tcp": {"loss_pct": 0},
        }
        hints = monitor_diagnose(self._snapshot(targets))
        assert any("router" in h["text"] for h in hints)

    def test_dns_issue(self):
        targets = {
            "gateway": {"loss_pct": 0, "jitter_ms": 1.0},
            "external:1.1.1.1": {"loss_pct": 0},
            "dns": {"loss_pct": 50},
            "tcp": {"loss_pct": 0},
        }
        hints = monitor_diagnose(self._snapshot(targets))
        assert any("DNS" in h["text"] for h in hints)

    def test_tcp_issue_despite_clean_ping(self):
        targets = {
            "gateway": {"loss_pct": 0, "jitter_ms": 1.0},
            "external:1.1.1.1": {"loss_pct": 0},
            "dns": {"loss_pct": 0},
            "tcp": {"loss_pct": 30},
        }
        hints = monitor_diagnose(self._snapshot(targets))
        assert any("TCP" in h["text"] for h in hints)

    def test_too_few_samples_returns_no_hints(self):
        targets = {"gateway": {"loss_pct": 50, "jitter_ms": 1.0}}
        hints = monitor_diagnose(self._snapshot(targets, sample_count=2))
        assert hints == []


class TestConfig:
    def test_load_config_defaults_when_no_file(self, tmp_path):
        cfg = load_config(str(tmp_path))
        assert cfg["ping_count"] == CONFIG_DEFAULTS["ping_count"]
        assert cfg["hosts"] == CONFIG_DEFAULTS["hosts"]

    def test_save_and_load_roundtrip(self, tmp_path):
        save_config({"ping_count": 42, "hosts": ["1.2.3.4"]}, str(tmp_path))
        cfg = load_config(str(tmp_path))
        assert cfg["ping_count"] == 42
        assert cfg["hosts"] == ["1.2.3.4"]
        assert config_path(str(tmp_path)).exists()

    def test_save_config_clamps_out_of_range_values(self, tmp_path):
        cfg = save_config({"ping_interval": 0, "ping_count": 99999}, str(tmp_path))
        assert cfg["ping_interval"] == 0.1
        assert cfg["ping_count"] == 200

    def test_save_config_ignores_unknown_keys(self, tmp_path):
        cfg = save_config({"not_a_real_key": "x"}, str(tmp_path))
        assert "not_a_real_key" not in cfg

    def test_load_config_ignores_corrupt_file(self, tmp_path):
        config_path(str(tmp_path)).parent.mkdir(parents=True, exist_ok=True)
        config_path(str(tmp_path)).write_text("not json", encoding="utf-8")
        cfg = load_config(str(tmp_path))
        assert cfg["ping_count"] == CONFIG_DEFAULTS["ping_count"]


class TestCheckToolsPlatformInfo:
    def test_check_tools_includes_platform_and_checked_lists(self):
        result = check_tools()
        assert "platform" in result
        assert isinstance(result["checked_required"], list)
        assert isinstance(result["checked_optional"], list)
        assert "ping" in result["checked_required"]
