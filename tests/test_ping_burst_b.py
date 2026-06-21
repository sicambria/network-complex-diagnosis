from unittest.mock import patch, MagicMock

from netdiag import ping_burst, resolve_all, now_iso, UserInterrupted, detect_package_manager


class TestResolveAll:
    def test_success(self):
        mock_addr_info = [
            (2, 1, 6, "", ("1.1.1.1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity"):
            result = resolve_all("one.one.one.one")
        assert result["host"] == "one.one.one.one"
        assert result["ok"] is True
        assert len(result["addresses"]) == 1
        assert result["addresses"][0]["ip"] == "1.1.1.1"
        assert result["addresses"][0]["version"] == 4

    def test_dns_failure(self):
        with patch("socket.getaddrinfo", side_effect=OSError("Name or service not known")), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity"):
            result = resolve_all("nonexistent.invalid")
        assert result["host"] == "nonexistent.invalid"
        assert result["ok"] is False
        assert "error" in result
        assert result["addresses"] == []

    def test_dns_failure_generic_exception(self):
        with patch("socket.getaddrinfo", side_effect=Exception("random error")), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity"):
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
             patch("netdiag_core.runtime.log_activity"):
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
             patch("netdiag_core.runtime.log_activity"):
            result = resolve_all("one.one.one.one")
        versions = {a["version"] for a in result["addresses"]}
        assert versions == {4, 6}

    def test_empty_address_list(self):
        with patch("socket.getaddrinfo", return_value=[]), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity"):
            result = resolve_all("empty.example")
        assert result["ok"] is True
        assert result["addresses"] == []

    def test_ipv6_address_version(self):
        mock_addr_info = [
            (10, 1, 6, "", ("::1", 0)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity"):
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
             patch("netdiag_core.runtime.log_activity"):
            result = resolve_all("example.com")
        assert result["addresses"][0]["version"] is None

    def test_duplicate_hostname_same_addr_different_port(self):
        mock_addr_info = [
            (2, 1, 6, "", ("1.1.1.1", 80)),
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity"):
            result = resolve_all("example.com")
        assert len(result["addresses"]) == 1

    def test_log_activity_called_on_success(self):
        mock_log = MagicMock()
        mock_addr_info = [(2, 1, 6, "", ("1.1.1.1", 0))]
        with patch("socket.getaddrinfo", return_value=mock_addr_info), \
             patch("netdiag.time.perf_counter", return_value=0), \
             patch("netdiag_core.runtime.log_activity", mock_log):
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
             patch("netdiag_core.runtime.log_activity", mock_log):
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
        with patch("netdiag_core.runtime.has_tool") as mock_has:
            def side_effect(name):
                return name == "apt"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "apt"

    def test_returns_dnf(self):
        with patch("netdiag_core.runtime.has_tool") as mock_has:
            def side_effect(name):
                return name == "dnf"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "dnf"

    def test_returns_pacman(self):
        with patch("netdiag_core.runtime.has_tool") as mock_has:
            def side_effect(name):
                return name == "pacman"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "pacman"

    def test_returns_zypper(self):
        with patch("netdiag_core.runtime.has_tool") as mock_has:
            def side_effect(name):
                return name == "zypper"
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "zypper"

    def test_returns_none_when_no_manager(self):
        with patch("netdiag_core.runtime.has_tool", return_value=False):
            assert detect_package_manager() is None

    def test_returns_first_match(self):
        with patch("netdiag_core.runtime.has_tool") as mock_has:
            def side_effect(name):
                return name in ("apt", "dnf")
            mock_has.side_effect = side_effect
            assert detect_package_manager() == "apt"

    def test_avoids_yum_when_dnf_present(self):
        with patch("netdiag_core.runtime.has_tool") as mock_has:
            def side_effect(name):
                return name == "dnf"
            mock_has.side_effect = side_effect
            result = detect_package_manager()
            assert result == "dnf"
            assert result != "yum"
