from unittest.mock import patch

from netdiag import detect_wireless_interface, _sysfs_interface_stats, wifi_info


class TestDetectWirelessInterface:
    def test_linux_iw_returns_interface(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(0, "Interface wlan0\n", "")):
            assert detect_wireless_interface() == "wlan0"

    def test_linux_iw_fails_fallback_procfs(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="header1\nheader2\n wlan0: 0 0 0 0 0 0 0 0 0 0 0 0\n"):
            assert detect_wireless_interface() == "wlan0"

    def test_linux_no_iw_fallback_procfs(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=False), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value="header1\nheader2\n wlan0: 0 0 0 0 0 0 0 0 0 0 0 0\n"):
            assert detect_wireless_interface() == "wlan0"

    def test_linux_neither_returns_none(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=False), \
             patch("pathlib.Path.exists", return_value=False):
            assert detect_wireless_interface() is None

    def test_macos_returns_default_interface(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.probes.netinfo.get_default_interface", return_value="en0"):
            assert detect_wireless_interface() == "en0"

    def test_windows_netsh_finds_name(self):
        netsh_out = (
            "Name                   : Wi-Fi\n"
            "SSID                   : MyNetwork\n"
        )
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(0, netsh_out, "")):
            assert detect_wireless_interface() == "Wi-Fi"

    def test_windows_netsh_failure(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")):
            assert detect_wireless_interface() is None


STAT_FILES = {
    "rx_errors": "10", "tx_errors": "5",
    "rx_dropped": "2", "tx_dropped": "1",
    "rx_over_errors": "3", "rx_frame_errors": "0",
    "tx_carrier_errors": "7",
}


def _fake_read_text(self):
    return STAT_FILES[self.name]


class TestSysfsInterfaceStats:
    def test_valid_sysfs_returns_correct_structure(self):
        with patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.read_text", _fake_read_text):
            result = _sysfs_interface_stats("eth0")
            assert result["available"] is True
            assert result["interface"] == "eth0"
            assert result["rx"]["errors"] == 10
            assert result["rx"]["dropped"] == 2
            assert result["rx"]["overruns"] == 3
            assert result["rx"]["frame"] == 0
            assert result["tx"]["errors"] == 5
            assert result["tx"]["dropped"] == 1
            assert result["tx"]["overruns"] == 0
            assert result["tx"]["carrier"] == 7

    def test_missing_sysfs_dir_returns_none(self):
        with patch("pathlib.Path.is_dir", return_value=False):
            assert _sysfs_interface_stats("eth0") is None

    def test_missing_stat_files_skips_gracefully(self):
        with patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
            result = _sysfs_interface_stats("eth0")
            assert result["available"] is True
            assert result["interface"] == "eth0"
            assert result["rx"]["errors"] == 0
            assert result["tx"]["errors"] == 0

    def test_parses_all_rx_tx_fields(self):
        expected_rx = {"errors": 100, "dropped": 30, "overruns": 50, "frame": 60, "carrier": 0}
        expected_tx = {"errors": 200, "dropped": 40, "overruns": 0, "carrier": 70}

        def fake_read(self):
            return {
                "rx_errors": "100", "tx_errors": "200",
                "rx_dropped": "30", "tx_dropped": "40",
                "rx_over_errors": "50", "rx_frame_errors": "60",
                "tx_carrier_errors": "70",
            }[self.name]

        with patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.read_text", fake_read):
            result = _sysfs_interface_stats("wlan0")
            assert result["rx"] == expected_rx
            assert result["tx"] == expected_tx


class TestWifiInfo:
    def test_returns_unavailable_when_iface_none(self):
        assert wifi_info(None) == {"available": False, "reason": "No interface detected"}

    def test_returns_unavailable_when_iface_empty(self):
        assert wifi_info("") == {"available": False, "reason": "No interface detected"}

    def test_linux_iw_parses_link_output(self):
        link_out = "SSID: HomeNetwork\nsignal: -45\nfreq: 5180\n"
        survey_out = ""
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=True), \
             patch("netdiag_core.runtime.run_cmd", side_effect=[
                 (0, link_out, ""),
                 (0, survey_out, ""),
             ]):
            result = wifi_info("wlan0")
            assert result["available"] is True
            assert result["interface"] == "wlan0"
            assert result["ssid"] == "HomeNetwork"
            assert result["signal_dbm"] == -45
            assert result["frequency"] == 5180

    def test_linux_iw_survey_dump_parsing(self):
        link_out = "SSID: Test\nsignal: -60\nfreq: 2412\n"
        survey_out = (
            "survey data for wlan0\n"
            "channel active time: 1000  busy time: 300\n"
            " noise: -90\n"
        )
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=True), \
             patch("netdiag_core.runtime.run_cmd", side_effect=[
                 (0, link_out, ""),
                 (0, survey_out, ""),
             ]):
            result = wifi_info("wlan0")
            assert result["channel_util"] == 30.0
            assert result["noise_dbm"] == -90

    def test_linux_iw_fails_fallback_proc_net_wireless(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")), \
             patch("netdiag_core.probes.wifi._proc_net_wireless", return_value={
                 "available": True, "interface": "wlan0",
                 "ssid": None, "signal_dbm": -50, "noise_dbm": -95,
                 "frequency": None, "tx_retries": None, "channel_util": None,
             }):
            result = wifi_info("wlan0")
            assert result["available"] is True
            assert result["signal_dbm"] == -50
            assert result["noise_dbm"] == -95

    def test_linux_both_fail_returns_unavailable(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")), \
             patch("netdiag_core.probes.wifi._proc_net_wireless", return_value=None):
            result = wifi_info("wlan0")
            assert result["available"] is False
            assert "iw not available" in result["reason"]

    def test_linux_no_iw_both_fail_returns_unavailable(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.has_tool", return_value=False), \
             patch("netdiag_core.probes.wifi._proc_net_wireless", return_value=None):
            result = wifi_info("wlan0")
            assert result["available"] is False
            assert "/proc/net/wireless not found" in result["reason"]

    def test_macos_airport_parsing(self):
        airport_out = (
            "     SSID: OfficeWiFi\n"
            "  agrCtlRSSI: -55\n"
            " agrCtlNoise: -92\n"
        )
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.run_cmd", return_value=(0, airport_out, "")):
            result = wifi_info("en0")
            assert result["available"] is True
            assert result["interface"] == "en0"
            assert result["ssid"] == "OfficeWiFi"
            assert result["signal_dbm"] == -55
            assert result["noise_dbm"] == -92

    def test_macos_airport_failure(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), \
             patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")):
            result = wifi_info("en0")
            assert result["available"] is False
            assert result["reason"] == "airport command failed"

    def test_windows_netsh_parsing(self):
        netsh_out = (
            "Name                   : Wi-Fi\n"
            "SSID                   : CoffeeShop\n"
            "Signal                 : 75%\n"
        )
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(0, netsh_out, "")):
            result = wifi_info("Wi-Fi")
            assert result["available"] is True
            assert result["interface"] == "Wi-Fi"
            assert result["ssid"] == "CoffeeShop"
            assert result["signal_dbm"] == -25

    def test_windows_netsh_failure(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), \
             patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), \
             patch("netdiag_core.runtime.run_cmd", return_value=(1, "", "error")):
            result = wifi_info("Wi-Fi")
            assert result["available"] is False
            assert result["reason"] == "netsh wlan failed"
