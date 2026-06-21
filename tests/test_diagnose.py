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

    def test_icmp_loss_contradicted_by_tcp_is_rate_limiting(self):
        # The reported scenario: 95% ICMP "loss" to 1.1.1.1/8.8.8.8 while TCP to the
        # same hosts connects 10/10. Must be reported as ICMP rate-limiting (info),
        # NOT packet loss (bad), and must not tank the health score.
        results = _make_results({
            "interface": {"available": True, "rx": {"errors": 0, "dropped": 0}, "tx": {"errors": 0, "dropped": 0}},
            "wifi": {"available": True, "signal_dbm": -60},
            "bufferbloat": {"available": True, "ratio": 1.1},
            "gateway_ping": {"loss_pct": 0, "p95_ms": 14, "jitter_ms": 3},
            "internet_ping": [
                {"label": "1.1.1.1", "host": "1.1.1.1", "loss_pct": 95, "p95_ms": 183, "jitter_ms": 20},
                {"label": "8.8.8.8", "host": "8.8.8.8", "loss_pct": 95, "p95_ms": 183, "jitter_ms": 20},
            ],
            "tcp": [
                {"host": "1.1.1.1", "port": 443, "attempts": 10, "failures": 0, "failure_pct": 0, "p95_ms": 102},
                {"host": "8.8.8.8", "port": 443, "attempts": 10, "failures": 0, "failure_pct": 0, "p95_ms": 102},
            ],
            "dns": [{"host": "google.com", "failure_pct": 0, "p95_ms": 1}],
        })
        diag = diagnose(results)
        icmp = [d for d in diag if d["layer"] == "internet" and "rate-limit" in d["title"].lower()]
        assert icmp, "expected an ICMP rate-limiting finding"
        assert icmp[0]["severity"] == "info"
        assert icmp[0].get("confidence") == "high"
        # No 'bad' packet-loss finding for these hosts.
        assert not any(d["severity"] == "bad" and "1.1.1.1" in d.get("detail", "") for d in diag)
        # Health score must not be dragged down by the phantom loss (without the
        # reconciliation this same input scores ~40).
        assert health_score(results) >= 85

    def test_icmp_loss_without_corroboration_stays_real(self):
        # If nothing proves the path works (no TCP/DNS/HTTP), high ICMP loss is
        # treated as genuine — we do not hand-wave it away.
        results = _make_results({
            "gateway_ping": {"loss_pct": 0, "p95_ms": 10, "jitter_ms": 5},
            "internet_ping": [
                {"label": "9.9.9.9", "host": "9.9.9.9", "loss_pct": 95, "p95_ms": 200, "jitter_ms": 20},
            ],
        })
        diag = diagnose(results)
        assert not any(d["layer"] == "internet" and "rate-limit" in d["title"].lower() for d in diag)
        assert any(d["layer"] == "internet" and d["severity"] in ("bad", "warning") for d in diag)

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
        # Loss that begins at hop 1 AND persists to the destination is real
        # first-hop (modem/uplink) loss.
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 10, "avg_ms": 5},
                {"hop": 2, "loss_pct": 12, "avg_ms": 10},
            ]},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "isp" and d["severity"] == "bad"
                   and "first hops" in d["title"].lower() for d in diag)

    def test_mtr_isp_hop_loss(self):
        # Loss that begins at hop 3 and reaches the destination is real ISP loss.
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 0, "avg_ms": 5},
                {"hop": 2, "loss_pct": 0, "avg_ms": 10},
                {"hop": 3, "loss_pct": 15, "avg_ms": 20},
            ]},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "isp" and d["severity"] == "bad" for d in diag)

    def test_mtr_midhop_loss_clears_is_rate_limiting(self):
        # Loss at an intermediate hop that CLEARS by the destination is that router
        # rate-limiting its own ICMP — info, never a 'bad' ISP packet-loss finding.
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 30, "avg_ms": 5},
                {"hop": 2, "loss_pct": 0, "avg_ms": 10},
                {"hop": 3, "loss_pct": 0, "avg_ms": 20},
            ]},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "isp" and d["severity"] == "info" for d in diag)
        assert not any(d["layer"] == "isp" and d["severity"] == "bad" for d in diag)

    def test_mtr_dest_loss_with_working_transport_is_rate_limiting(self):
        # Loss reaching the destination would normally be "real", but TCP+DNS over
        # the same path succeed — so the ICMP loss is the destination (or a tunnel)
        # rate-limiting echoes, NOT packet loss. Must be info, never a 'bad' modem
        # verdict. (This is the live false-positive: 70% hop-1 / 20% dest, yet TCP
        # handshakes all succeed.)
        results = _make_results({
            "dns": [{"failure_pct": 0}],
            "tcp": [{"host": "1.1.1.1", "port": 443, "failure_pct": 0}],
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 70, "avg_ms": 5},
                {"hop": 2, "loss_pct": 30, "avg_ms": 10},
                {"hop": 3, "loss_pct": 20, "avg_ms": 15},
            ]},
        })
        diag = diagnose(results)
        isp = [d for d in diag if d["layer"] == "isp"]
        assert any(d["severity"] == "info" for d in isp)
        assert not any(d["severity"] == "bad" for d in isp)
        assert not any("first hops" in d["title"].lower() for d in isp)

    def test_mtr_first_hop_loss_under_vpn_blames_tunnel_not_modem(self):
        # Same first-hop loss as test_mtr_first_hop_loss, but the path egresses
        # through a VPN tunnel — hop 1 is the VPN server, not the modem. Must NOT
        # emit the 'your modem/local uplink' verdict.
        results = _make_results({
            "vpn": {"active": True, "interface": "proton0", "kind": "vpn"},
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 70, "avg_ms": 5},
                {"hop": 2, "loss_pct": 20, "avg_ms": 10},
            ]},
        })
        diag = diagnose(results)
        isp = [d for d in diag if d["layer"] == "isp"]
        assert any("tunnel" in d["title"].lower() for d in isp)
        assert not any("first hops" in d["title"].lower() for d in isp)
        assert not any(d["severity"] == "bad" for d in isp)

    def test_mtr_first_hop_loss_no_vpn_no_transport_still_blames_modem(self):
        # Regression guard: with neither a VPN nor proven transport, the original
        # first-hop modem verdict must still fire.
        results = _make_results({
            "mtr": {"tool": "mtr", "host": "1.1.1.1", "hops": [
                {"hop": 1, "loss_pct": 40, "avg_ms": 5},
                {"hop": 2, "loss_pct": 35, "avg_ms": 10},
            ]},
        })
        diag = diagnose(results)
        assert any(d["layer"] == "isp" and d["severity"] == "bad"
                   and "first hops" in d["title"].lower() for d in diag)

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
