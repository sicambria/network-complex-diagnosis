"""Health score — 0-100 weighted composite across all diagnostic layers.

The internet score ignores ICMP-filtered loss (scores reachability by TCP
instead) so phantom rate-limited "loss" never drags the score down.
"""

import statistics

from netdiag_core.analysis.reconcile import get_reconciliation


def health_score(results):
    scores = {}
    weights = {"interface": 10, "wifi": 15, "gateway": 25, "internet": 25, "dns": 10, "tcp": 5, "bufferbloat": 10}
    iface = results.get("interface")
    if iface and iface.get("available"):
        rx = iface.get("rx", {})
        tx = iface.get("tx", {})
        total = rx.get("errors", 0) + tx.get("errors", 0) + rx.get("dropped", 0) + tx.get("dropped", 0)
        scores["interface"] = max(0, 100 - total * 5)
    wifi = results.get("wifi")
    if wifi and wifi.get("available"):
        sig = wifi.get("signal_dbm")
        if sig is not None:
            scores["wifi"] = max(0, min(100, 100 - (max(0, -55 - sig) * 3)))
    gw = results.get("gateway_ping")
    if gw:
        loss = gw.get("loss_pct", 0)
        p95 = gw.get("p95_ms", 0)
        scores["gateway"] = max(0, 100 - (loss * 8) - (max(0, p95 - 10) * 0.5))
    internet = results.get("internet_ping", [])
    if internet:
        recon = get_reconciliation(results)
        filtered = set(recon.get("filtered_hosts", []))
        per_host = {h["host"]: h for h in recon.get("per_host", [])}
        internet_scores = []
        for row in internet:
            host = row.get("host")
            if host in filtered:
                # ICMP loss here is rate-limiting, not real loss — score the path by
                # actual TCP reachability instead of the phantom ICMP figure.
                tcp_fail = (per_host.get(host, {}) or {}).get("tcp_failure_pct")
                internet_scores.append(max(0, 100 - (tcp_fail or 0) * 1.5))
            else:
                loss = row.get("loss_pct", 0) or 0
                p95 = row.get("p95_ms", 0) or 0
                internet_scores.append(max(0, 100 - (loss * 8) - (max(0, p95 - 40) * 0.3)))
        scores["internet"] = statistics.mean(internet_scores) if internet_scores else 0
    dns = results.get("dns", [])
    if dns:
        dns_scores = []
        for row in dns:
            fail = row.get("failure_pct", 0)
            p95 = row.get("p95_ms", 0)
            dns_scores.append(max(0, 100 - (fail * 15) - (max(0, p95 - 50) * 0.5)))
        scores["dns"] = statistics.mean(dns_scores) if dns_scores else 0
    tcp = results.get("tcp", [])
    if tcp:
        tcp_scores = []
        for row in tcp:
            fail = row.get("failure_pct", 0)
            tcp_scores.append(max(0, 100 - (fail * 15)))
        scores["tcp"] = statistics.mean(tcp_scores) if tcp_scores else 0
    bb = results.get("bufferbloat")
    if bb and bb.get("available") and bb.get("ratio"):
        ratio = bb["ratio"]
        scores["bufferbloat"] = max(0, 100 - (max(0, ratio - 1) * 30))

    download_result = results.get("download_test")
    if download_result and download_result.get("error") is None:
        mbps = download_result.get("avg_mbps", 0)
        success = download_result.get("success", 0)
        total = success + download_result.get("failures", 0)
        score = min(100, (mbps / 20) * 50 + (success / max(total, 1)) * 50)
        scores["download"] = min(100, score)

    conn_result = results.get("connection_test")
    if conn_result:
        http_lat = conn_result.get("http_latency", [])
        if http_lat:
            avg_p95 = statistics.mean([h.get("p95_ms", 0) or 0 for h in http_lat])
            scores["http_latency"] = max(0, 100 - (max(0, avg_p95 - 50) * 0.5))

    total_weight = sum(weights.get(k, 0) for k in scores)
    if total_weight == 0:
        return 0
    weighted = sum(scores[k] * weights.get(k, 0) for k in scores)
    for k in weights:
        if k not in scores:
            total_weight += weights[k]
    return round(weighted / total_weight)
