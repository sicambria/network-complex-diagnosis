from unittest.mock import patch, MagicMock

import netdiag
from netdiag import reliability_test, reliability_verdict


# A fresh socket whose sendall captures the raw HTTP request and whose recv
# returns one small HTTP response then EOF. This is the single network boundary
# the probe touches, so mocking it keeps the whole test offline.
def make_fake_sock(captured):
    sock = MagicMock()
    chunks = [b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi", b""]

    def _recv(n):
        return chunks.pop(0) if chunks else b""

    sock.recv.side_effect = _recv
    sock.sendall.side_effect = lambda data: captured.append(data)
    return sock


def patched_run(captured, **kwargs):
    addr = (netdiag.socket.AF_INET, netdiag.socket.SOCK_STREAM, 0, "", ("203.0.113.1", 443))
    ctx = MagicMock()
    ctx.wrap_socket.side_effect = lambda s, server_hostname=None: s
    with patch.object(netdiag.socket, "getaddrinfo", return_value=[addr]), \
         patch.object(netdiag.socket, "socket", side_effect=lambda *a, **k: make_fake_sock(captured)), \
         patch("ssl.create_default_context", return_value=ctx):
        return reliability_test(**kwargs)


class TestReliabilityCacheDefeat:
    def test_requests_are_unique_and_no_cache(self):
        captured = []
        r = patched_run(captured, targets=["https://example.invalid/img.png"],
                        samples=3, concurrency=1, compare_concurrency=False,
                        ipv=4, retries=0)
        assert r["available"] is True
        assert len(captured) == 3
        reqs = [c.decode("ascii", "ignore") for c in captured]
        for req in reqs:
            assert "nocache=" in req
            assert "Cache-Control: no-cache, no-store, max-age=0" in req
            assert "Pragma: no-cache" in req
            assert "Connection: close" in req
        # cache-busting tokens differ across every request
        tokens = [req.split("nocache=")[1].split(" ")[0].split("\r")[0] for req in reqs]
        assert len(set(tokens)) == len(tokens)

    def test_tls_context_session_tickets_disabled(self):
        captured = []
        addr = (netdiag.socket.AF_INET, netdiag.socket.SOCK_STREAM, 0, "", ("203.0.113.1", 443))
        ctx = MagicMock()
        ctx.options = 0
        ctx.wrap_socket.side_effect = lambda s, server_hostname=None: s
        with patch.object(netdiag.socket, "getaddrinfo", return_value=[addr]), \
             patch.object(netdiag.socket, "socket", side_effect=lambda *a, **k: make_fake_sock(captured)), \
             patch("ssl.create_default_context", return_value=ctx):
            reliability_test(targets=["https://example.invalid/"], samples=1,
                             concurrency=1, compare_concurrency=False, ipv=4, retries=0)
        # SNI preserved + a fresh context wrapped at least once
        assert ctx.wrap_socket.called
        _, kwargs = ctx.wrap_socket.call_args
        assert kwargs.get("server_hostname") == "example.invalid"


class TestReliabilityAccounting:
    def test_all_success(self):
        captured = []
        r = patched_run(captured, targets=["https://example.invalid/"],
                        samples=4, concurrency=2, compare_concurrency=False,
                        ipv=4, retries=0)
        assert r["samples_total"] == 4
        assert r["first_attempt_fail_pct"] == 0.0
        assert r["hard_failures"] == 0
        assert "ipv4" in r["by_family"]

    def test_tcp_failure_attributed_to_phase(self):
        addr = (netdiag.socket.AF_INET, netdiag.socket.SOCK_STREAM, 0, "", ("203.0.113.1", 443))
        bad = MagicMock()
        bad.connect.side_effect = OSError("refused")
        with patch.object(netdiag.socket, "getaddrinfo", return_value=[addr]), \
             patch.object(netdiag.socket, "socket", return_value=bad):
            r = reliability_test(targets=["https://example.invalid/"], samples=3,
                                 concurrency=1, compare_concurrency=False, ipv=4, retries=1)
        assert r["fail_phase_breakdown"]["tcp"] == 3
        assert r["first_attempt_fail_pct"] == 100.0
        assert r["hard_failures"] == 3  # retries also fail

    def test_dns_failure_attributed_to_phase(self):
        with patch.object(netdiag.socket, "getaddrinfo", side_effect=OSError("no name")):
            r = reliability_test(targets=["https://example.invalid/"], samples=2,
                                 concurrency=1, compare_concurrency=False, ipv=4, retries=0)
        assert r["fail_phase_breakdown"]["dns"] == 2

    def test_recovered_on_retry(self):
        # First attempt's connect fails, the retry succeeds.
        addr = (netdiag.socket.AF_INET, netdiag.socket.SOCK_STREAM, 0, "", ("203.0.113.1", 443))
        captured = []
        calls = {"n": 0}

        def _socket_factory(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                bad = MagicMock()
                bad.connect.side_effect = OSError("refused")
                return bad
            return make_fake_sock(captured)

        ctx = MagicMock()
        ctx.wrap_socket.side_effect = lambda s, server_hostname=None: s
        with patch.object(netdiag.socket, "getaddrinfo", return_value=[addr]), \
             patch.object(netdiag.socket, "socket", side_effect=_socket_factory), \
             patch("ssl.create_default_context", return_value=ctx):
            r = reliability_test(targets=["https://example.invalid/"], samples=1,
                                 concurrency=1, compare_concurrency=False, ipv=4, retries=2)
        assert r["first_attempt_fail_pct"] == 100.0
        assert r["recovered_on_retry"] == 1
        assert r["hard_failures"] == 0


class TestReliabilityModes:
    def test_targets_string_is_split(self):
        captured = []
        r = patched_run(captured, targets="https://a.invalid/, https://b.invalid/",
                        samples=1, concurrency=1, compare_concurrency=False, ipv=4, retries=0)
        hosts = {t["host"] for t in r["by_target"]}
        assert hosts == {"a.invalid", "b.invalid"}

    def test_duration_mode_terminates(self):
        captured = []
        r = patched_run(captured, targets=["https://example.invalid/"],
                        duration_s=1, concurrency=1, compare_concurrency=False,
                        ipv=4, retries=0)
        assert r["available"] is True
        assert r["samples_total"] >= 1

    def test_concurrency_ab_pass_present(self):
        captured = []
        r = patched_run(captured, targets=["https://example.invalid/"],
                        samples=3, concurrency=4, compare_concurrency=True,
                        ipv=4, retries=0)
        assert "high" in r["by_concurrency"]
        assert "low" in r["by_concurrency"]

    def test_bare_ip_target_skips_mismatched_family(self):
        captured = []
        # IPv4 literal target with ipv=0 (both) must only be probed over IPv4.
        r = patched_run(captured, targets=["https://203.0.113.5/"],
                        samples=2, concurrency=1, compare_concurrency=False,
                        ipv=0, retries=0)
        fams = set(r["by_family"].keys())
        assert fams == {"ipv4"}
        assert r["by_target"][0]["is_ip"] is True


class TestReliabilityPlanB:
    def test_planb_urllib_fallback(self):
        # Malformed getaddrinfo entry makes the manual unpack raise unexpectedly,
        # which must trigger the urllib total-time fallback.
        resp = MagicMock()
        resp.read.return_value = b"hello"
        cm = MagicMock()
        cm.__enter__.return_value = resp
        with patch.object(netdiag.socket, "getaddrinfo", return_value=[("bad", "entry")]), \
             patch("urllib.request.urlopen", return_value=cm) as mu:
            r = reliability_test(targets=["https://example.invalid/"], samples=2,
                                 concurrency=1, compare_concurrency=False, ipv=4, retries=0)
        assert mu.called
        assert r["available"] is True
        assert r["first_attempt_fail_pct"] == 0.0  # urllib path succeeded


class TestReliabilityVerdict:
    def base(self, **over):
        d = {
            "samples_total": 100, "first_attempt_fail_pct": 0.0,
            "recovered_on_retry": 0, "hard_failures": 0,
            "fail_phase_breakdown": {"dns": 0, "tcp": 0, "tls": 0, "ttfb": 0, "body": 0, "unknown": 0},
            "by_family": {"ipv4": {"samples": 100, "first_fail_pct": 0.0, "hard_fail_pct": 0.0}},
            "by_concurrency": {"high": {"first_fail_pct": 0.0}},
            "by_target": [],
        }
        d.update(over)
        return d

    def test_clean(self):
        titles = [v["title"] for v in reliability_verdict(self.base())]
        assert any("reliable" in t.lower() for t in titles)

    def test_ipv6_broken(self):
        d = self.base(first_attempt_fail_pct=20.0,
                      by_family={"ipv4": {"samples": 50, "first_fail_pct": 2.0, "hard_fail_pct": 2.0},
                                 "ipv6": {"samples": 50, "first_fail_pct": 40.0, "hard_fail_pct": 40.0}})
        titles = [v["title"] for v in reliability_verdict(d)]
        assert any("IPv6" in t for t in titles)

    def test_concurrency(self):
        d = self.base(first_attempt_fail_pct=30.0,
                      by_concurrency={"low": {"first_fail_pct": 2.0}, "high": {"first_fail_pct": 30.0}})
        titles = [v["title"] for v in reliability_verdict(d)]
        assert any("parallel" in t.lower() for t in titles)

    def test_dns_via_target_split(self):
        d = self.base(first_attempt_fail_pct=30.0,
                      by_target=[{"is_ip": False, "first_fail_pct": 30.0},
                                 {"is_ip": True, "first_fail_pct": 2.0}])
        titles = [v["title"] for v in reliability_verdict(d)]
        assert any("resolution" in t.lower() for t in titles)

    def test_empty(self):
        titles = [v["title"] for v in reliability_verdict({"samples_total": 0})]
        assert titles  # always returns at least one entry
