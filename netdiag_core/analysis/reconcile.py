"""ICMP-vs-TCP reconciliation.

A genuine high packet-loss rate cannot coexist with a near-100% TCP handshake
rate, so ICMP "loss" the working transport disproves is rate-limiting, not
packet loss. reconcile_icmp() encodes this per-host and globally; downstream
severity (diagnose/health_score) reads it instead of raw ICMP figures.
"""

from netdiag_core.constants import ICMP_RATE_LIMITERS


def reconcile_icmp(results):
    # Cross-reference ICMP ping loss against TCP-connect / HTTP / DNS success to the
    # SAME hosts. A genuine high packet-loss rate cannot coexist with a near-100%
    # TCP handshake success rate (a TCP handshake needs several successful round
    # trips). So when ICMP loss is high but TCP/HTTPS to the same host succeeds, the
    # missing echo replies are being rate-limited/deprioritized by the destination,
    # not dropped on the user's line. This keeps the diagnosis engine from ever
    # reporting "packet loss" that the working transport layer disproves.
    internet = results.get("internet_ping") or []
    tcp_rows = results.get("tcp") or []
    dns_rows = results.get("dns") or []
    download = results.get("download_test") or {}

    tcp_by_host = {}
    for t in tcp_rows:
        h = t.get("host")
        if not h:
            continue
        cur = tcp_by_host.get(h)
        if cur is None or (t.get("failure_pct") or 0) < (cur.get("failure_pct") or 0):
            tcp_by_host[h] = t

    def _fp(row, default):
        # failure_pct can legitimately be 0 (perfect) — `or default` would wrongly
        # treat that as missing, so test for None explicitly.
        v = row.get("failure_pct")
        return default if v is None else v
    tcp_ok_global = any(_fp(t, 100) <= 20 for t in tcp_rows) if tcp_rows else False
    dns_ok_global = any(_fp(d, 100) <= 20 for d in dns_rows) if dns_rows else False
    http_ok_global = bool(download.get("success")) and download.get("error") is None

    per_host = []
    for row in internet:
        host = row.get("host")
        loss = row.get("loss_pct") or 0
        tcp_match = tcp_by_host.get(host)
        tcp_fail = tcp_match.get("failure_pct") if tcp_match else None
        # Direct evidence: a TCP target on this exact host connects reliably.
        tcp_contradicts = tcp_match is not None and (tcp_fail or 0) <= 20 and loss >= 20
        # Indirect evidence: no per-host TCP test, but the internet path is proven
        # working by global TCP/HTTP success AND name resolution is healthy.
        global_contradicts = (tcp_match is None and loss >= 20 and dns_ok_global and
                              (tcp_ok_global or http_ok_global))
        filtered = bool(tcp_contradicts or global_contradicts)
        per_host.append({
            "host": host, "label": row.get("label"), "loss_pct": loss,
            "p95_ms": row.get("p95_ms"), "received": row.get("received"), "sent": row.get("sent"),
            "tcp_port": tcp_match.get("port") if tcp_match else None,
            "tcp_failure_pct": tcp_fail,
            "tcp_attempts": tcp_match.get("attempts") if tcp_match else None,
            "tcp_p95_ms": tcp_match.get("p95_ms") if tcp_match else None,
            "icmp_filtered": filtered,
            "known_rate_limiter": host in ICMP_RATE_LIMITERS,
        })
    return {
        "per_host": per_host,
        "tcp_ok_global": tcp_ok_global,
        "dns_ok_global": dns_ok_global,
        "http_ok_global": http_ok_global,
        "any_filtered": any(h["icmp_filtered"] for h in per_host),
        "filtered_hosts": [h["host"] for h in per_host if h["icmp_filtered"]],
        "real_loss_hosts": [h["host"] for h in per_host
                            if (h["loss_pct"] or 0) >= 5 and not h["icmp_filtered"]],
    }


def get_reconciliation(results):
    # full_diagnostic caches the reconciliation on the results dict; diagnose() and
    # health_score() may also be called standalone, so recompute on a cache miss.
    r = results.get("icmp_reconciliation")
    if r is None:
        r = reconcile_icmp(results)
    return r
