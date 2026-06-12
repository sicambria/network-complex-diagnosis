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
        "tools": {},
        "diagnosis": [],
        "health_score": 0,
        "interrupted": False,
        "interrupt_reason": None,
    }
    if overrides:
        results.update(overrides)
    return results


class TestDiagnose:
    def test_no_data_yields_clean(self):
        results = _make_results()
        diag = diagnose(results)
        assert len(diag) == 1
        assert diag[0]["layer"] == "meta"
        assert diag[0]["severity"] == "clean"

    def test_interrupted(self):
        results = _make_results({"interrupted": True})
        diag = diagnose(results)
        assert any(d["layer"] == "meta" and d["severity"] == "warning" for d in diag)

    def test_interface_errors(self):
        results = _make_results({
            "interface": {"available": True, "interface": "eth0",
                          "rx": {"errors": 5, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0},
                          "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "interface" and d["severity"] == "bad" for d in diag)

    def test_interface_clean(self):
        results = _make_results({
            "interface": {"available": True, "interface": "eth0",
                          "rx": {"errors": 0, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0},
                          "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}},
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "interface" for d in diag)

    def test_wifi_very_weak(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": -85, "channel_util": 30},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "wifi" and d["severity"] == "bad" for d in diag)

    def test_wifi_weak(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": -75, "channel_util": 30},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "wifi" and d["severity"] == "warning" for d in diag)

    def test_wifi_crowded_channel(self):
        results = _make_results({
            "wifi": {"available": True, "signal_dbm": -50, "channel_util": 85},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "wifi" and d["severity"] == "warning" and "channel" in d["title"].lower() for d in diag)

    def test_gateway_bad_loss(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 10, "p95_ms": 30, "jitter_ms": 5},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "gateway" and d["severity"] == "bad" for d in diag)

    def test_gateway_clean(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 10, "jitter_ms": 5},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "gateway" and d["severity"] == "clean" for d in diag)

    def test_internet_bad_gateway_clean(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 10, "jitter_ms": 5},
            "internet_ping": [
                {"label": "1.1.1.1", "host": "1.1.1.1", "loss_pct": 10, "p95_ms": 200, "jitter_ms": 20},
            ],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "internet" for d in diag)
        assert not any(d["layer"] == "meta" and "both" in d["title"].lower() for d in diag)

    def test_internet_and_gateway_both_bad(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 10, "p95_ms": 100, "jitter_ms": 20},
            "internet_ping": [
                {"label": "1.1.1.1", "host": "1.1.1.1", "loss_pct": 10, "p95_ms": 200, "jitter_ms": 20},
            ],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "meta" and "both" in d["title"].lower() for d in diag)

    def test_mtr_first_hop_loss(self):
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 10, "avg_ms": 5},
                {"hop": 2, "loss_pct": 0, "avg_ms": 10},
            ]},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "isp" and "first hops" in d["title"].lower() for d in diag)

    def test_mtr_isp_hop_loss(self):
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 0, "avg_ms": 5},
                {"hop": 2, "loss_pct": 0, "avg_ms": 10},
                {"hop": 3, "loss_pct": 15, "avg_ms": 20},
            ]},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "isp" and "ISP hops" in d["title"] for d in diag)

    def test_bufferbloat_severe(self):
        results = _make_results({
            "bufferbloat": {"available": True, "ratio": 5.0, "rtt_idle_ms": 10, "rtt_loaded_ms": 50},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "bufferbloat" and d["severity"] == "bad" for d in diag)

    def test_bufferbloat_mild(self):
        results = _make_results({
            "bufferbloat": {"available": True, "ratio": 2.5, "rtt_idle_ms": 10, "rtt_loaded_ms": 25},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "bufferbloat" and d["severity"] == "warning" for d in diag)

    def test_dns_failures(self):
        results = _make_results({
            "dns": [{"host": "google.com", "failure_pct": 20, "p95_ms": 50}],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "dns" for d in diag)

    def test_dns_high_latency(self):
        results = _make_results({
            "dns": [{"host": "google.com", "failure_pct": 0, "p95_ms": 400}],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "dns" for d in diag)

    def test_tcp_issues(self):
        results = _make_results({
            "tcp": [{"host": "google.com", "port": 443, "failure_pct": 10, "p95_ms": 200}],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "tcp" for d in diag)

    def test_tcp_high_latency(self):
        results = _make_results({
            "tcp": [{"host": "1.1.1.1", "port": 443, "failure_pct": 0, "p95_ms": 600}],
        })
        diag = diagnose(results)
        assert any(d["layer"] == "tcp" for d in diag)


class TestHealthScore:
    def test_no_data(self):
        h = health_score(_make_results())
        assert h == 0

    def test_perfect_score(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -40},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
            "internet_ping": [{"loss_pct": 0, "p95_ms": 20}],
            "dns": [{"failure_pct": 0, "p95_ms": 20}],
            "tcp": [{"failure_pct": 0}],
            "bufferbloat": {"available": True, "ratio": 1.0},
        })
        h = health_score(results)
        assert h >= 95

    def test_poor_score(self):
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 10, "dropped": 5}, "tx": {"errors": 5, "dropped": 2}},
            "wifi": {"available": True, "signal_dbm": -90},
            "gateway_ping": {"loss_pct": 20, "p95_ms": 200},
            "internet_ping": [{"loss_pct": 20, "p95_ms": 300}],
            "dns": [{"failure_pct": 50, "p95_ms": 500}],
            "tcp": [{"failure_pct": 50}],
            "bufferbloat": {"available": True, "ratio": 5.0},
        })
        h = health_score(results)
        assert h <= 40

    def test_missing_layers_no_error(self):
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 5},
        })
        h = health_score(results)
        assert isinstance(h, (int, float))
