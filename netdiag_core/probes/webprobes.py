"""HTTP/web probes: image-download latency, HTTP HEAD latency, and the 100-site intermittent-issue reproducer."""

from netdiag_core.stats import percentile, series_stats
from netdiag_core.constants import WELLKNOWN_SITES
from netdiag_core.probes import reliability
from netdiag_core.probes import verdicts


def download_images_test(count=100, timeout_s=15):
    import tempfile, urllib.request, os, time, concurrent.futures
    results = {"available": True, "success": 0, "failures": 0,
               "total_bytes": 0, "total_time_s": 0, "avg_mbps": 0,
               "p95_latency_ms": None, "error": None}
    latencies = []
    t0 = time.time()

    def _dl(i):
        try:
            t1 = time.time()
            url = f"https://picsum.photos/200/200?random={i}"
            req = urllib.request.Request(url, headers={"User-Agent": "NetDiag/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = resp.read()
            elapsed = (time.time() - t1) * 1000
            return {"ok": True, "bytes": len(data), "latency_ms": elapsed, "idx": i}
        except Exception as e:
            return {"ok": False, "error": str(e), "idx": i}

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_dl, n) for n in range(count)]
        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            if r["ok"]:
                results["success"] += 1
                results["total_bytes"] += r["bytes"]
                latencies.append(r["latency_ms"])
            else:
                results["failures"] += 1

    total_time = time.time() - t0
    results["total_time_s"] = round(total_time, 2)
    if latencies:
        results["p95_latency_ms"] = percentile(latencies, 95)
    if total_time > 0 and results["total_bytes"] > 0:
        results["avg_mbps"] = round(
            (results["total_bytes"] * 8) / total_time / 1_000_000, 2)
    if results["success"] == 0 and results["failures"] > 0:
        results["error"] = "All downloads failed"
    return results


def http_latency_test(hosts=None, count=5, timeout_s=5):
    import urllib.request, time
    if hosts is None:
        hosts = ["connectivitycheck.gstatic.com", "detectportal.firefox.com", "1.1.1.1"]
    results = []
    for host in hosts:
        latencies = []
        failures = 0
        for i in range(count):
            try:
                t0 = time.time()
                req = urllib.request.Request(f"http://{host}/", method="HEAD",
                    headers={"User-Agent": "NetDiag/1.0"})
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    resp.read()
                lat = (time.time() - t0) * 1000
                latencies.append(lat)
            except Exception:
                failures += 1
        entry = {"host": host, "available": True, "failures": failures}
        if latencies:
            entry.update(series_stats(latencies))
            entry["latencies"] = latencies
        results.append(entry)
    return results


def wellknown_sites_test(sites=None, duration_s=150, concurrency=12, timeout_s=5,
                         retries=2, callback=None):
    # Intermittent-connection reproducer: hammer ~100 well-known sites' favicons
    # under concurrency for ~2.5 minutes, recreating the "page with many small
    # images" load pattern. Built entirely on reliability_test so all the per-phase
    # timing, first-vs-retry and per-target accounting come for free. We force IPv4
    # because across 100 mixed sites IPv6 availability varies wildly and would
    # otherwise drown the result in false "IPv6 broken" noise; the concurrency A/B
    # pass is skipped (a 100-target sequential pass would take far too long).
    if sites is None:
        sites = WELLKNOWN_SITES
    targets = ["https://%s/favicon.ico" % s for s in sites]
    result = reliability.reliability_test(
        targets=targets, samples=10, duration_s=duration_s, concurrency=concurrency,
        retries=retries, timeout_s=timeout_s, ipv=4, compare_concurrency=False,
        callback=callback, label="wellknown")
    if result.get("available"):
        result["site_count"] = len(sites)
        result["verdict"] = wellknown_verdict(result)
    return result


def wellknown_verdict(result):
    # A site-fleet-specific headline on top of the generic reliability findings:
    # name the worst-offending sites and frame the result as page-load reliability.
    verdict = []
    total = result.get("samples_total", 0)
    if not total:
        return [{"layer": "reliability", "severity": "info",
                 "title": "Site-fleet probe collected no samples",
                 "detail": "No connections completed.", "fix": ""}]
    ff = result.get("first_attempt_fail_pct") or 0
    hard = result.get("hard_failures", 0)
    recovered = result.get("recovered_on_retry", 0)
    by_target = result.get("by_target", []) or []
    n_sites = result.get("site_count", len(by_target))
    worst = sorted(by_target, key=lambda t: -(t.get("first_fail_pct") or 0))
    worst = [t for t in worst if (t.get("first_fail_pct") or 0) > 0][:8]
    worst_facts = ["%s: %.0f%% first-attempt fail (%d samples)"
                   % (t.get("host"), t.get("first_fail_pct") or 0, t.get("samples", 0)) for t in worst]
    facts = ["Probed %d well-known sites, %d total connection attempts." % (n_sites, total),
             "First-attempt failures: %.1f%%. Hard failures (failed even after retries): %d. "
             "Recovered on retry: %d." % (ff, hard, recovered)]
    facts += worst_facts
    if ff >= 10 or hard > max(2, total * 0.02):
        sev = "bad" if (ff >= 25 or hard > max(5, total * 0.05)) else "warning"
        verdict.append({
            "layer": "reliability", "severity": sev,
            "title": "Intermittent connection failures reproduced across many sites",
            "detail": "%.1f%% of first connection attempts to well-known sites failed under load."
                      % ff,
            "facts": facts,
            "assumption": "Failures spread across many independent, healthy sites (not one) point to "
                          "something on the path common to all of them — typically the router under "
                          "concurrency (NAT/conntrack-table exhaustion or rate-limiting), Wi-Fi, or an "
                          "upstream link — rather than any individual website.",
            "confidence": "high" if total > 200 else "medium",
            "fix": "If failures recover on retry, raise the router's conntrack/NAT limits or replace "
                   "the router; test on Ethernet to rule out Wi-Fi. Attach the ISP report if the "
                   "pattern points upstream."})
    else:
        verdict.append({
            "layer": "reliability", "severity": "clean",
            "title": "Page-load reliability looks good across many sites",
            "detail": "%.1f%% first-attempt failures across %d sites — within normal range." % (ff, n_sites),
            "facts": facts,
            "assumption": "A low first-attempt failure rate across a large, diverse fleet of sites means "
                          "the connection holds up under the 'many small requests' pattern that web "
                          "pages generate.",
            "confidence": "high" if total > 200 else "medium",
            "fix": ""})
    # Fold in the generic reliability findings (phase clustering, retry-masking, etc.)
    for v in verdicts.reliability_verdict(result):
        if v.get("severity") != "clean":
            v = dict(v)
            v.setdefault("confidence", "medium")
            verdict.append(v)
    return verdict
