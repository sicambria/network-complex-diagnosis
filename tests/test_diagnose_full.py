from netdiag import diagnose, health_score


def _make_results(overrides=None):
    results = {
        "timestamp": "2026-06-10T12:00:00+02:00",
        "platform": "Linux-6.x",
        "os": "Linux",
        "default_interface": "wlan0",
        "gateway": "192.168.1.1",
        "interface": None,
        "ethtool": None,
        "wifi": None,
        "gateway_ping": None,
        "internet_ping": [],
        "dns": [],
        "tcp": [],
        "tcp_sockets": None,
        "mtr": None,
        "speedtest": None,
        "iperf3": None,
        "bufferbloat": None,
        "download_test": None,
        "connection_test": None,
        "tools": {},
        "diagnosis": [],
        "health_score": 0,
        "interrupted": False,
        "interrupt_reason": None,
    }
    if overrides:
        results.update(overrides)
    return results


class TestDiagnoseMissing:

    def test_wifi_fair_signal(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": -65, "channel_util": 30},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "wifi" and d["severity"] == "info" for d in diag)

    def test_wifi_signal_none(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": None, "channel_util": 30},
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "wifi" for d in diag)

    def test_gateway_fix_with_weak_wifi(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": -75},
            "gateway_ping": {"loss_pct": 10, "p95_ms": 30, "jitter_ms": 5},
        })
        diag = diagnose(results)
        gw = [d for d in diag if d["layer"] == "gateway"][0]
        assert "WiFi" in gw["fix"]

    def test_gateway_fix_without_weak_wifi(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": -50},
            "gateway_ping": {"loss_pct": 10, "p95_ms": 30, "jitter_ms": 5},
        })
        diag = diagnose(results)
        gw = [d for d in diag if d["layer"] == "gateway"][0]
        assert "WiFi" not in gw["fix"]
        assert "Router" in gw["fix"] or "router" in gw["fix"]

    def test_gateway_fix_no_wifi(self):
        results = _make_results({
            "wifi": None,
            "gateway_ping": {"loss_pct": 10, "p95_ms": 30, "jitter_ms": 5},
        })
        diag = diagnose(results)
        gw = [d for d in diag if d["layer"] == "gateway"][0]
        assert "WiFi" not in gw["fix"]
        assert "Router" in gw["fix"] or "router" in gw["fix"]

    def test_iperf3_retransmits(self):
        results = _make_results({
            "iperf3": {"available": True, "error": None, "retransmit_pct": 5.0},
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "tcp" and d["severity"] == "warning"
            and "retransmits" in d["title"].lower()
            for d in diag
        )

    def test_download_bad(self):
        results = _make_results({
            "download_test": {"avg_mbps": 0.5, "success": 0, "failures": 3, "error": None},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "internet" and d["severity"] == "bad" for d in diag)

    def test_download_warning(self):
        # A warning now comes from image-fetch FAILURES (a reliability signal), not
        # from a low aggregate Mbps on tiny concurrent images (not a bandwidth test).
        results = _make_results({
            "download_test": {"avg_mbps": 2.0, "success": 9, "failures": 1, "error": None},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "internet" and d["severity"] == "warning" for d in diag)

    def test_download_low_mbps_zero_failures_is_clean(self):
        # 0.47 Mbps over 100 tiny images with 0 failures is expected, not a fault —
        # this is the reported 'red X + No specific fix needed' contradiction, fixed.
        results = _make_results({
            "download_test": {"avg_mbps": 0.47, "success": 100, "failures": 0, "error": None},
        })
        diag = diagnose(results)
        dl = [d for d in diag if d["layer"] == "internet" and "image" in d["title"].lower()]
        assert dl and dl[0]["severity"] == "clean"

    def test_download_clean(self):
        results = _make_results({
            "download_test": {"avg_mbps": 50.0, "success": 5, "failures": 0, "error": None},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "internet" and d["severity"] == "clean" for d in diag)

    def test_http_latency_high(self):
        results = _make_results({
            "connection_test": {
                "http_latency": [
                    {"host": "example.com", "p95_ms": 600, "failures": 0},
                ],
            },
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "internet" and d["severity"] == "warning"
            and "HTTP latency" in d["title"]
            for d in diag
        )

    def test_mtu_low(self):
        results = _make_results({
            "connection_test": {
                "http_latency": [],
                "mtu": {"available": True, "mtu": 1300},
            },
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "interface" and d["severity"] == "warning"
            and "MTU" in d["title"]
            for d in diag
        )

    def test_ethtool_half_duplex(self):
        results = _make_results({
            "ethtool": {"available": True, "duplex": "Half", "speed": 100, "link_detected": True},
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "interface" and d["severity"] == "bad"
            and "half-duplex" in d["title"].lower()
            for d in diag
        )

    def test_ethtool_no_link(self):
        results = _make_results({
            "ethtool": {"available": True, "duplex": "Full", "speed": 1000, "link_detected": False},
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "interface" and d["severity"] == "bad"
            and "no link" in d["title"].lower()
            for d in diag
        )

    def test_carrier_changes(self):
        results = _make_results({
            "interface": {"available": True, "interface": "eth0",
                          "rx": {"errors": 0, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 3},
                          "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}},
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "interface" and d["severity"] == "bad"
            and "Carrier" in d["detail"]
            for d in diag
        )

    def test_tcp_socket_retransmits(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 10, "jitter_ms": 5},
            "tcp_sockets": {"available": True, "total_retransmits": 100},
        })
        diag = diagnose(results)
        gw = [d for d in diag if d["layer"] == "gateway"]
        assert len(gw) == 1
        assert gw[0]["severity"] == "bad"
        assert "TCP retransmits" in gw[0]["detail"]

    def test_bufferbloat_ratio_exactly_2(self):
        results = _make_results({
            "bufferbloat": {"available": True, "ratio": 2.0, "rtt_idle_ms": 10, "rtt_loaded_ms": 20},
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "bufferbloat" for d in diag)

    def test_bufferbloat_ratio_exactly_3(self):
        results = _make_results({
            "bufferbloat": {"available": True, "ratio": 3.0, "rtt_idle_ms": 10, "rtt_loaded_ms": 30},
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "bufferbloat" and d["severity"] == "warning"
            for d in diag
        )

    def test_dns_failure_pct_zero_and_p95_low(self):
        results = _make_results({
            "dns": [{"host": "google.com", "failure_pct": 0, "p95_ms": 200}],
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "dns" for d in diag)

    def test_dns_failure_pct_none(self):
        results = _make_results({
            "dns": [{"host": "google.com", "failure_pct": None, "p95_ms": 200}],
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "dns" for d in diag)

    def test_dns_failure_and_high_latency_combined(self):
        results = _make_results({
            "dns": [{"host": "google.com", "failure_pct": 5, "p95_ms": 350}],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "dns" for d in diag)

    def test_interface_drops_no_errors(self):
        results = _make_results({
            "interface": {"available": True, "interface": "eth0",
                          "rx": {"errors": 0, "dropped": 5, "overruns": 0, "frame": 0, "carrier": 0},
                          "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}},
        })
        diag = diagnose(results)
        matches = [d for d in diag if d["layer"] == "interface"]
        assert len(matches) == 1
        assert matches[0]["severity"] == "bad"
        assert "dropped" in matches[0]["detail"]

    def test_all_clean_meta(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 10, "jitter_ms": 5},
            "download_test": {"avg_mbps": 50.0, "success": 5, "failures": 0, "error": None},
        })
        diag = diagnose(results)
        clean_meta = [d for d in diag if d["layer"] == "meta" and d["severity"] == "clean"]
        assert len(clean_meta) == 1
        assert clean_meta[0]["title"] == "No issues detected"

    def test_interrupted_with_reason(self):
        results = _make_results({
            "interrupted": True,
            "interrupt_reason": "timeout",
        })
        diag = diagnose(results)
        assert any(
            d["layer"] == "meta" and d["severity"] == "warning"
            and "interrupted" in d["title"].lower()
            for d in diag
        )

    def test_mtr_empty_hops(self):
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": []},
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "isp" for d in diag)


class TestHealthScoreMissing:

    def test_download_score(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "download_test": {"avg_mbps": 10, "success": 4, "failures": 1, "error": None},
        })
        h = health_score(results)
        assert isinstance(h, (int, float))
        assert h > 0

    def test_http_latency_score(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "connection_test": {
                "http_latency": [
                    {"host": "a.com", "p95_ms": 200, "failures": 0},
                ],
            },
        })
        h = health_score(results)
        assert isinstance(h, (int, float))

    def test_wifi_signal_boundary_minus_55(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -55},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "internet_ping": [{"loss_pct": 0, "p95_ms": 20}],
            "dns": [{"failure_pct": 0, "p95_ms": 20}],
            "tcp": [{"failure_pct": 0}],
            "bufferbloat": {"available": True, "ratio": 1.0},
        })
        h1 = health_score(results)
        results["wifi"]["signal_dbm"] = -70
        h2 = health_score(results)
        assert h2 < h1

    def test_wifi_signal_none_no_crash(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": None},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
        })
        h = health_score(results)
        assert isinstance(h, (int, float))

    def test_bufferbloat_ratio_none(self):
        results = _make_results({
            "bufferbloat": {"available": True, "ratio": None, "rtt_idle_ms": 10, "rtt_loaded_ms": 20},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
        })
        h = health_score(results)
        assert isinstance(h, (int, float))

    def test_dns_p95_zero(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -55},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "internet_ping": [{"loss_pct": 0, "p95_ms": 20}],
            "dns": [{"failure_pct": 0, "p95_ms": 0}],
            "tcp": [{"failure_pct": 0}],
            "bufferbloat": {"available": True, "ratio": 1.0},
        })
        h = health_score(results)
        assert h >= 95

    def test_tcp_failure_pct(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -55},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "internet_ping": [{"loss_pct": 0, "p95_ms": 20}],
            "dns": [{"failure_pct": 0, "p95_ms": 20}],
            "tcp": [{"failure_pct": 20}],
            "bufferbloat": {"available": True, "ratio": 1.0},
        })
        h = health_score(results)
        assert h == 95

    def test_multiple_internet_ping(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -55},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "internet_ping": [
                {"loss_pct": 0, "p95_ms": 20},
                {"loss_pct": 10, "p95_ms": 100},
            ],
            "dns": [{"failure_pct": 0, "p95_ms": 20}],
            "tcp": [{"failure_pct": 0}],
            "bufferbloat": {"available": True, "ratio": 1.0},
        })
        h = health_score(results)
        assert h < 95

    def test_multiple_dns_entries(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -55},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "internet_ping": [{"loss_pct": 0, "p95_ms": 20}],
            "dns": [
                {"host": "a", "failure_pct": 0, "p95_ms": 20},
                {"host": "b", "failure_pct": 0, "p95_ms": 100},
            ],
            "tcp": [{"failure_pct": 0}],
            "bufferbloat": {"available": True, "ratio": 1.0},
        })
        h = health_score(results)
        assert h < 100

    def test_download_contributions(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "download_test": {"avg_mbps": 0, "success": 3, "failures": 0, "error": None},
        })
        h1 = health_score(results)
        results["download_test"]["avg_mbps"] = 50
        h2 = health_score(results)
        assert h2 >= h1
