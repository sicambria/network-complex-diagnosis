from unittest.mock import patch

from netdiag import detect_gateway, get_default_interface
from netdiag_core.probes.netinfo import detect_vpn


class TestDetectGateway:
    def test_linux_ip_route(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route", return_value=None):
            m.return_value = (0, "default via 192.168.1.1 dev wlan0", "")
            assert detect_gateway() == "192.168.1.1"

    def test_linux_no_default(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route", return_value=None):
            m.return_value = (0, "", "")
            assert detect_gateway() is None

    def test_linux_failure(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route", return_value=None):
            m.return_value = (1, "", "error")
            assert detect_gateway() is None

    def test_linux_proc_fallback(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route", return_value="192.168.1.1"):
            m.return_value = (1, "", "error")
            assert detect_gateway() == "192.168.1.1"

    def test_macos(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "gateway: 10.0.0.1", "")
            assert detect_gateway() == "10.0.0.1"

    def test_windows(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "  0.0.0.0          0.0.0.0      192.168.1.1", "")
            assert detect_gateway() == "192.168.1.1"

    def test_windows_no_match(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "no default route here", "")
            assert detect_gateway() is None


class TestGetDefaultInterface:
    def test_linux_ip_route(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route_iface", return_value=None):
            m.return_value = (0, "default via 192.168.1.1 dev wlan0", "")
            assert get_default_interface() == "wlan0"

    def test_linux_no_match(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route_iface", return_value=None):
            m.return_value = (0, "", "")
            assert get_default_interface() is None

    def test_linux_proc_fallback(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._parse_proc_net_route_iface", return_value="wlp2s0"):
            m.return_value = (1, "", "error")
            assert get_default_interface() == "wlp2s0"

    def test_macos(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "interface: en0", "")
            assert get_default_interface() == "en0"

    def test_windows(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), patch("netdiag_core.runtime.run_cmd") as m:
            assert get_default_interface() is None


class TestDetectVpn:
    def test_linux_tunnel_egress(self):
        # `ip route get 1.1.1.1` egresses via a VPN tunnel device.
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "1.1.1.1 dev proton0 table 1456934651 src 10.2.0.2 uid 1000 \n    cache", "")
            v = detect_vpn()
            assert v["active"] is True
            assert v["interface"] == "proton0"
            assert v["kind"] == "vpn"

    def test_linux_wireguard_egress(self):
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "1.1.1.1 dev wg0 src 10.7.0.2 uid 1000 \n    cache", "")
            assert detect_vpn()["interface"] == "wg0"

    def test_linux_normal_egress_not_vpn(self):
        # Plain Wi-Fi/Ethernet egress is NOT a VPN; sysfs fallback isn't consulted
        # because ip already returned a (non-tunnel) interface.
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "1.1.1.1 via 192.168.50.1 dev wlp1s0 src 192.168.50.79 uid 1000 \n    cache", "")
            v = detect_vpn()
            assert v["active"] is False
            assert v["interface"] is None
            assert v["kind"] is None

    def test_linux_pppoe_is_not_vpn(self):
        # DSL PPPoE (ppp0) is a real WAN uplink, must NOT be flagged as a VPN.
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "1.1.1.1 dev ppp0 src 100.81.2.3 uid 1000 \n    cache", "")
            assert detect_vpn()["active"] is False

    def test_linux_sysfs_fallback_when_ip_unavailable(self):
        # `ip` missing/fails -> Plan B finds an up tunnel device via sysfs.
        with patch("netdiag_core.runtime.IS_LINUX", True), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m, \
             patch("netdiag_core.probes.netinfo._sysfs_first_up_tunnel", return_value="tun0"):
            m.return_value = (1, "", "ip: command not found")
            v = detect_vpn()
            assert v["active"] is True
            assert v["interface"] == "tun0"

    def test_macos_utun_egress(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "   route to: 1.1.1.1\ninterface: utun4", "")
            assert detect_vpn()["interface"] == "utun4"

    def test_macos_normal_en0_not_vpn(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", True), \
             patch("netdiag_core.runtime.IS_WINDOWS", False), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "   route to: 1.1.1.1\ninterface: en0", "")
            assert detect_vpn()["active"] is False

    def test_windows_tunnel_alias(self):
        with patch("netdiag_core.runtime.IS_LINUX", False), patch("netdiag_core.runtime.IS_MACOS", False), \
             patch("netdiag_core.runtime.IS_WINDOWS", True), patch("netdiag_core.runtime.run_cmd") as m:
            m.return_value = (0, "ProtonVPN TUN", "")
            assert detect_vpn()["active"] is True
