from unittest.mock import patch

from netdiag import detect_gateway, get_default_interface


class TestDetectGateway:
    def test_linux_ip_route(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route", return_value=None):
            m.return_value = (0, "default via 192.168.1.1 dev wlan0", "")
            assert detect_gateway() == "192.168.1.1"

    def test_linux_no_default(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route", return_value=None):
            m.return_value = (0, "", "")
            assert detect_gateway() is None

    def test_linux_failure(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route", return_value=None):
            m.return_value = (1, "", "error")
            assert detect_gateway() is None

    def test_linux_proc_fallback(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route", return_value="192.168.1.1"):
            m.return_value = (1, "", "error")
            assert detect_gateway() == "192.168.1.1"

    def test_macos(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", True), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m:
            m.return_value = (0, "gateway: 10.0.0.1", "")
            assert detect_gateway() == "10.0.0.1"

    def test_windows(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", True), patch("netdiag.run_cmd") as m:
            m.return_value = (0, "  0.0.0.0          0.0.0.0      192.168.1.1", "")
            assert detect_gateway() == "192.168.1.1"

    def test_windows_no_match(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", True), patch("netdiag.run_cmd") as m:
            m.return_value = (0, "no default route here", "")
            assert detect_gateway() is None


class TestGetDefaultInterface:
    def test_linux_ip_route(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route_iface", return_value=None):
            m.return_value = (0, "default via 192.168.1.1 dev wlan0", "")
            assert get_default_interface() == "wlan0"

    def test_linux_no_match(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route_iface", return_value=None):
            m.return_value = (0, "", "")
            assert get_default_interface() is None

    def test_linux_proc_fallback(self):
        with patch("netdiag.IS_LINUX", True), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m, \
             patch("netdiag._parse_proc_net_route_iface", return_value="wlp2s0"):
            m.return_value = (1, "", "error")
            assert get_default_interface() == "wlp2s0"

    def test_macos(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", True), \
             patch("netdiag.IS_WINDOWS", False), patch("netdiag.run_cmd") as m:
            m.return_value = (0, "interface: en0", "")
            assert get_default_interface() == "en0"

    def test_windows(self):
        with patch("netdiag.IS_LINUX", False), patch("netdiag.IS_MACOS", False), \
             patch("netdiag.IS_WINDOWS", True), patch("netdiag.run_cmd") as m:
            assert get_default_interface() is None
