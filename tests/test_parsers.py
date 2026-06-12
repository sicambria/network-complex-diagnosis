from unittest.mock import patch, mock_open

from netdiag import parse_rtt_ms, classify_ping, _parse_proc_net_route, _parse_proc_net_route_iface, _proc_net_wireless


class TestParseRttMs:
    def test_linux_ping_time(self):
        text = "64 bytes from 1.1.1.1: icmp_seq=1 ttl=53 time=12.3 ms"
        assert parse_rtt_ms(text) == 12.3

    def test_linux_ping_time_no_space(self):
        text = "64 bytes from 1.1.1.1: icmp_seq=1 ttl=53 time=12.3ms"
        assert parse_rtt_ms(text) == 12.3

    def test_linux_ping_time_equals(self):
        text = "rtt min/avg/max/mdev = 10.123/15.456/20.789/2.345 ms"
        assert parse_rtt_ms(text) == 15.456

    def test_macos_ping(self):
        text = "round-trip min/avg/max/stddev = 10.123/15.456/20.789/2.345 ms"
        assert parse_rtt_ms(text) == 15.456

    def test_windows_ping(self):
        text = "Reply from 1.1.1.1: bytes=32 time=42ms TTL=53"
        assert parse_rtt_ms(text) == 42.0

    def test_windows_time_frac(self):
        text = "Reply from 1.1.1.1: bytes=32 time=12.5ms TTL=53"
        assert parse_rtt_ms(text) == 12.5

    def test_no_match(self):
        assert parse_rtt_ms("Destination Host Unreachable") is None
        assert parse_rtt_ms("100% packet loss") is None
        assert parse_rtt_ms("") is None

    def test_macos_time_format(self):
        text = "64 bytes from 1.1.1.1: icmp_seq=0 ttl=53 time=14.700 ms"
        assert parse_rtt_ms(text) == 14.7

    def test_macos_rtt_format(self):
        text = "round-trip min/avg/max/stddev = 10.000/12.000/14.000/1.000 ms"
        assert parse_rtt_ms(text) == 12.0


class TestClassifyPing:
    def test_clean(self):
        row = {"loss_pct": 0, "p95_ms": 20, "jitter_ms": 5}
        assert classify_ping(row) == "clean"

    def test_bad_loss(self):
        assert classify_ping({"loss_pct": 5}) == "bad_loss"
        assert classify_ping({"loss_pct": 100}) == "bad_loss"

    def test_some_loss(self):
        assert classify_ping({"loss_pct": 1}) == "some_loss"
        assert classify_ping({"loss_pct": 4.9}) == "some_loss"

    def test_bad_latency_spikes(self):
        assert classify_ping({"loss_pct": 0, "p95_ms": 300}) == "bad_latency_spikes"
        assert classify_ping({"loss_pct": 0, "p95_ms": 500}) == "bad_latency_spikes"

    def test_latency_spikes(self):
        assert classify_ping({"loss_pct": 0, "p95_ms": 150}) == "latency_spikes"
        assert classify_ping({"loss_pct": 0, "p95_ms": 299}) == "latency_spikes"

    def test_high_jitter(self):
        assert classify_ping({"loss_pct": 0, "p95_ms": 20, "jitter_ms": 80}) == "high_jitter"
        assert classify_ping({"loss_pct": 0, "p95_ms": 20, "jitter_ms": 200}) == "high_jitter"

    def test_missing_keys_default_zero(self):
        assert classify_ping({}) == "clean"
        assert classify_ping({"loss_pct": None}) == "clean"

    def test_precedence_loss_over_latency(self):
        row = {"loss_pct": 5, "p95_ms": 300, "jitter_ms": 80}
        assert classify_ping(row) == "bad_loss"
        row2 = {"loss_pct": 1, "p95_ms": 300, "jitter_ms": 80}
        assert classify_ping(row2) == "some_loss"


class TestPlanB:
    def test_parse_proc_net_route_found(self):
        fake_data = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        fake_data += "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        with patch("builtins.open", mock_open(read_data=fake_data)):
            assert _parse_proc_net_route() == "192.168.1.1"

    def test_parse_proc_net_route_not_found(self):
        fake_data = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        with patch("builtins.open", mock_open(read_data=fake_data)):
            assert _parse_proc_net_route() is None

    def test_parse_proc_net_route_iface_found(self):
        fake_data = "Iface\tDestination\tGateway\nwlan0\t00000000\t0101A8C0\n"
        with patch("builtins.open", mock_open(read_data=fake_data)):
            assert _parse_proc_net_route_iface() == "wlan0"

    def test_parse_proc_net_route_error(self):
        with patch("builtins.open", side_effect=OSError):
            assert _parse_proc_net_route() is None
            assert _parse_proc_net_route_iface() is None

    def test_proc_net_wireless_found(self):
        fake = ("Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
                " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
                " wlan0: 0000   64.  -70.  -65.  \n")
        with patch("builtins.open", mock_open(read_data=fake)):
            result = _proc_net_wireless("wlan0")
            assert result is not None
            assert result["signal_dbm"] == -70

    def test_proc_net_wireless_not_found(self):
        with patch("builtins.open", side_effect=OSError):
            assert _proc_net_wireless("wlan0") is None
