from unittest.mock import patch, MagicMock

from netdiag import ping_burst, resolve_all, now_iso, UserInterrupted, detect_package_manager


class TestPingBurst:
    def test_all_success(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.5, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=3, interval=1)
        assert result["host"] == "1.1.1.1"
        assert result["sent"] == 3
        assert result["received"] == 3
        assert result["loss_pct"] == 0.0
        assert result["interrupted"] is False
        assert len(result["samples"]) == 3
        for s in result["samples"]:
            assert s["ok"] is True
            assert s["rtt_ms"] == 10.5

    def test_some_loss(self):
        returns = [
            {"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""},
            {"ok": False, "rtt_ms": None, "rc": 1, "raw": ""},
            {"ok": True, "rtt_ms": 12.0, "rc": 0, "raw": ""},
        ]
        with patch("netdiag.ping_once", side_effect=returns), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=3, interval=0.5)
        assert result["sent"] == 3
        assert result["received"] == 2
        assert result["loss_pct"] == 33.33

    def test_all_lost(self):
        mock_ping = MagicMock(return_value={"ok": False, "rtt_ms": None, "rc": 1, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=2, interval=1)
        assert result["sent"] == 2
        assert result["received"] == 0
        assert result["loss_pct"] == 100.0
        assert result["avg_ms"] is None

    def test_callback_called(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 5.0, "rc": 0, "raw": ""})
        cb = MagicMock()
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            ping_burst("1.1.1.1", count=3, interval=0, callback=cb)
        assert cb.call_count == 3
        cb.assert_any_call("1.1.1.1", 1, 3, True, 5.0)
        cb.assert_any_call("1.1.1.1", 2, 3, True, 5.0)
        cb.assert_any_call("1.1.1.1", 3, 3, True, 5.0)

    def test_callback_with_loss(self):
        returns = [
            {"ok": True, "rtt_ms": 5.0, "rc": 0, "raw": ""},
            {"ok": False, "rtt_ms": None, "rc": 1, "raw": ""},
        ]
        cb = MagicMock()
        with patch("netdiag.ping_once", side_effect=returns), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            ping_burst("1.1.1.1", count=2, interval=0, callback=cb)
        assert cb.call_count == 2
        cb.assert_any_call("1.1.1.1", 1, 2, True, 5.0)
        cb.assert_any_call("1.1.1.1", 2, 2, False, None)

    def test_keyboard_interrupt_raises_userinterrupted(self):
        mock_ping = MagicMock(side_effect=[
            {"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""},
            KeyboardInterrupt,
        ])
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"), \
             patch("netdiag.series_stats"), \
             patch("netdiag.jitter_ms"):
            import pytest
            with pytest.raises(UserInterrupted):
                ping_burst("1.1.1.1", count=5, interval=0)

    def test_interrupted_flag_false_when_complete(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=2, interval=0)
        assert result["interrupted"] is False
        assert result["sent"] == 2

    def test_quiet_mode_no_print(self):
        mock_print = MagicMock()
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print", mock_print):
            ping_burst("1.1.1.1", count=2, interval=0, quiet=True)
        mock_print.assert_not_called()

    def test_non_quiet_mode_prints(self):
        mock_print = MagicMock()
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print", mock_print):
            ping_burst("1.1.1.1", count=2, interval=0, quiet=False)
        assert mock_print.call_count == 3  # header + 2 status lines

    def test_interval_zero_no_sleep_between_last(self):
        mock_sleep = MagicMock()
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep", mock_sleep), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            ping_burst("1.1.1.1", count=3, interval=0)
        mock_sleep.assert_not_called()

    def test_non_zero_interval_sleeps(self):
        mock_sleep = MagicMock()
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep", mock_sleep), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            ping_burst("1.1.1.1", count=3, interval=0.5)
        # 3 pings, sleep between: seq 1→2 and seq 2→3 = 2 calls
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.5)

    def test_default_label(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=1, interval=0, label=None)
        assert result["label"] == "1.1.1.1"

    def test_custom_label(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=1, interval=0, label="custom")
        assert result["label"] == "custom"

    def test_ipv_passed_to_result(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=1, interval=0, ipv=4)
        assert result["ipv"] == 4

    def test_ipv_auto_default(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("1.1.1.1", count=1, interval=0)
        assert result["ipv"] == "auto"

    def test_return_dict_structure(self):
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 15.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep"), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            result = ping_burst("8.8.8.8", count=2, interval=0.2, ipv=4, label="Google")
        expected_keys = {
            "label", "host", "ipv", "sent", "received", "loss_pct",
            "jitter_ms", "count", "min_ms", "avg_ms", "max_ms",
            "stdev_ms", "p50_ms", "p95_ms", "p99_ms", "samples", "interrupted",
        }
        assert set(result.keys()) == expected_keys
        assert result["label"] == "Google"
        assert result["host"] == "8.8.8.8"
        assert result["ipv"] == 4
        assert result["sent"] == 2
        assert result["received"] == 2
        assert result["loss_pct"] == 0.0
        assert result["count"] == 2
        assert result["min_ms"] == 15.0
        assert result["avg_ms"] == 15.0
        assert result["max_ms"] == 15.0
        assert result["interrupted"] is False
        assert len(result["samples"]) == 2

    def test_single_ping_no_sleep(self):
        mock_sleep = MagicMock()
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep", mock_sleep), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            ping_burst("1.1.1.1", count=1, interval=5)
        mock_sleep.assert_not_called()

    def test_negative_interval_no_sleep(self):
        mock_sleep = MagicMock()
        mock_ping = MagicMock(return_value={"ok": True, "rtt_ms": 10.0, "rc": 0, "raw": ""})
        with patch("netdiag.ping_once", mock_ping), \
             patch("netdiag.time.sleep", mock_sleep), \
             patch("netdiag.now_iso", return_value="2025-01-01T00:00:00"), \
             patch("builtins.print"):
            ping_burst("1.1.1.1", count=2, interval=-1)
        mock_sleep.assert_not_called()


class TestResolveAll:
    def test_success(self):
        mock_addr_info = [
            (2, 1, 6, "", ("1.1.1.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("one.one.one.one")
        assert result["host"] == "one.one.one.one"
        assert result["ok"] is True
        assert len(result["addresses"]) == 1
        assert result["addresses"][0]["ip"] == "1.1.1.1"
        assert result["addresses"][0]["version"] == 4

    def test_dns_failure(self):
        with patch("socket.getaddrinfo", side_effect=OSError("Name or service not known")), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("nonexistent.invalid")
        assert result["host"] == "nonexistent.invalid"
        assert result["ok"] is False
        assert "error" in result
        assert result["addresses"] == []

    def test_dns_failure_generic_exception(self):
        with patch("socket.getaddrinfo", side_effect=Exception("random error")), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("test.example")
        assert result["ok"] is False
        assert "random error" in result["error"]

    def test_deduplication(self):
        mock_addr_info = [
            (2, 1, 6, "", ("1.1.1.1", 0)),
            (2, 2, 17, "", ("1.1.1.1", 0)),
            (2, 1, 6, "", ("1.1.1.1", 0)),
            (10, 1, 6, "", ("2606:4700:4700::1111", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("one.one.one.one")
        assert len(result["addresses"]) == 2
        ips = {a["ip"] for a in result["addresses"]}
        assert ips == {"1.1.1.1", "2606:4700:4700::1111"}

    def test_ipv4_and_ipv6(self):
        mock_addr_info = [
            (2, 1, 6, "", ("1.1.1.1", 0)),
            (10, 1, 6, "", ("2606:4700:4700::1111", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("one.one.one.one")
        versions = {a["version"] for a in result["addresses"]}
        assert versions == {4, 6}

    def test_empty_address_list(self):
        with patch("socket.getaddrinfo", return_value=[]), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("empty.example")
        assert result["ok"] is True
        assert result["addresses"] == []

    def test_ipv6_address_version(self):
        mock_addr_info = [
            (10, 1, 6, "", ("::1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("localhost")
        assert len(result["addresses"]) == 1
        assert result["addresses"][0]["ip"] == "::1"
        assert result["addresses"][0]["version"] == 6

    def test_unknown_family_version_is_none(self):
        mock_addr_info = [
            (42, 1, 6, "", ("198.51.100.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("example.com")
        assert result["addresses"][0]["version"] is None

    def test_duplicate_hostname_same_addr_different_port(self):
        mock_addr_info = [
            (2, 1, 6, "", ("1.1.1.1", 80)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity"):
            result = resolve_all("example.com")
        assert len(result["addresses"]) == 1

    def test_log_activity_called_on_success(self):
        mock_log = MagicMock()
        mock_addr_info = [(2, 1, 6, "", ("1.1.1.1", 0))]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity", mock_log):
            resolve_all("one.one.one.one")
        mock_log.assert_called_once()
        args = mock_log.call_args[0]
        assert args[0] == "socket"
        assert "DNS resolve" in args[1]
        assert args[2] == 0  # rc

    def test_log_activity_called_on_failure(self):
        mock_log = MagicMock()
        with patch("socket.getaddrinfo", side_effect=OSError("fail")), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag.log_activity", mock_log):
            resolve_all("bad.example")
        mock_log.assert_called_once()
        args = mock_log.call_args[0]
        assert args[0] == "socket"
        assert args[2] == 999  # rc for error


class TestNowIso:
    def test_returns_string(self):
        result = now_iso()
        assert isinstance(result, str)

    def test_contains_date_and_time(self):
        result = now_iso()
        assert "T" in result

    def test_iso_format_basic(self):
        import re
        result = now_iso()
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result)


class TestUserInterrupted:
    def test_can_be_raised_and_caught(self):
        try:
            raise UserInterrupted("test error")
        except UserInterrupted as e:
            assert str(e) == "test error"

    def test_is_exception_subclass(self):
        assert issubclass(UserInterrupted, Exception)

    def test_default_message(self):
        try:
            raise UserInterrupted()
        except UserInterrupted as e:
            assert str(e) == ""

    def test_works_with_try_except_base(self):
        caught = False
        try:
            raise UserInterrupted("interrupted")
        except Exception as e:
            caught = isinstance(e, UserInterrupted)
        assert caught is True


class TestDetectPackageManager:
    def test_returns_apt(self):
        with patch("netdiag.has_tool") as mock_has:
            def side_effect(name):
                return name == "apt"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "apt"

    def test_returns_dnf(self):
        with patch("netdiag.has_tool") as mock_has:
            def side_effect(name):
                return name == "dnf"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "dnf"

    def test_returns_pacman(self):
        with patch("netdiag.has_tool") as mock_has:
            def side_effect(name):
                return name == "pacman"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "pacman"

    def test_returns_zypper(self):
        with patch("netdiag.has_tool") as mock_has:
            def side_effect(name):
                return name == "zypper"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "zypper"

    def test_returns_none_when_no_manager(self):
        with patch("netdiag.has_tool", return_value=False):
            assert detect_package_manager() is None

    def test_returns_first_match(self):
        with patch("netdiag.has_tool") as mock_has:
            def side_effect(name):
                return name in ("apt", "dnf")
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "apt"

    def test_avoids_yum_when_dnf_present(self):
        with patch("netdiag.has_tool") as mock_has:
            def side_effect(name):
                return name == "dnf"
            mock_has.side_effect = side_effect
            result = detect_package_manager()
            assert result == "dnf"
            assert result != "yum"
