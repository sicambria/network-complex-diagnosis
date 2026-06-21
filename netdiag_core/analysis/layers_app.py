"""Application-layer diagnosis helpers: DNS, TCP, iPerf3, speedtest, downloads,
HTTP/MTU connection probe, reliability and well-known-site reproducer verdicts.

Each helper re-derives its inputs from ``results`` and returns a list of
diagnosis dicts; block bodies are unchanged from the original diagnose().
"""

from netdiag_core.constants import IPERF_SERVER


def _diag_dns(results):
    diagnoses = []
    dns_results = results.get("dns", [])
    dns_bad = [x for x in dns_results if (x.get("failure_pct") or 0) > 0 or (x.get("p95_ms") or 0) > 300]
    if dns_bad:
        names = ", ".join(x["host"] for x in dns_bad)
        diagnoses.append({"layer": "dns", "severity": "bad",
                          "title": "DNS resolution unreliable or slow",
                          "detail": f"Affected: {names}",
                          "facts": ["%s: %s%% lookups failed, p95 %s ms." % (
                              x.get("host"), x.get("failure_pct", 0), x.get("p95_ms", "?")) for x in dns_bad],
                          "assumption": "Failed or slow name resolution makes sites feel down or slow to "
                                        "start even when the underlying connection is fine — a classic "
                                        "'first attempt fails, reload works' cause.",
                          "confidence": "high",
                          "fix": "Switch to a different resolver such as 1.1.1.1 or 8.8.8.8. "
                                 "If your router forwards DNS, bypass it."})
    return diagnoses


def _diag_tcp(results):
    diagnoses = []
    tcp_results = results.get("tcp", [])
    tcp_bad = [x for x in tcp_results if (x.get("failure_pct") or 0) > 0 or (x.get("p95_ms") or 0) > 500]
    if tcp_bad:
        names = ", ".join(f"{x['host']}:{x['port']}" for x in tcp_bad)
        diagnoses.append({"layer": "tcp", "severity": "bad",
                          "title": "TCP connections failing or slow to establish",
                          "detail": f"Affected: {names}",
                          "facts": ["%s:%s: %s%% connect failure, p95 %s ms." % (
                              x.get("host"), x.get("port"), x.get("failure_pct", 0), x.get("p95_ms", "?")) for x in tcp_bad],
                          "assumption": "TCP is the transport real apps use. Failures or slow handshakes here "
                                        "(unlike ICMP loss) DO mean web/video/app connections will feel unreliable.",
                          "confidence": "high",
                          "fix": "Check for a firewall blocking the port or ISP throttling; "
                                 "compare wired vs WiFi to localize."})
    return diagnoses


def _diag_iperf(results):
    diagnoses = []
    iperf_result = results.get("iperf3")
    if iperf_result and iperf_result.get("available") and not iperf_result.get("error"):
        retrans_pct = iperf_result.get("retransmit_pct", 0)
        if retrans_pct and retrans_pct > 2:
            diagnoses.append({"layer": "tcp", "severity": "warning",
                              "title": "High TCP retransmits in iPerf3",
                              "detail": f"Retransmits: {retrans_pct:.1f}% during throughput test",
                              "facts": ["iPerf3 retransmits: %.1f%% of segments during the throughput test." % retrans_pct],
                              "assumption": "Elevated retransmits on a sustained transfer indicate the "
                                            "path is dropping TCP segments under load.",
                              "confidence": "medium",
                              "fix": "High retransmits suggest congestion, throttling, or line quality issues."})
    elif iperf_result and iperf_result.get("available") and iperf_result.get("error"):
        diagnoses.append({
            "layer": "internet", "severity": "info",
            "title": "iPerf3 throughput test could not complete",
            "detail": "The public iPerf3 server did not return a result.",
            "facts": ["iPerf3 to %s errored: %s" % (
                iperf_result.get("server", IPERF_SERVER), str(iperf_result.get("error"))[:120])],
            "assumption": "Public iPerf3 servers are frequently busy or rate-limited; a failure here "
                          "almost always reflects the server's availability, NOT a fault in your line.",
            "confidence": "high",
            "fix": "Treat this test as inconclusive. Use the speed test and download test for capacity."})
    return diagnoses


def _diag_speed(results):
    diagnoses = []
    speed_result = results.get("speedtest")
    if speed_result and speed_result.get("available"):
        dl = speed_result.get("download_mbps")
        if speed_result.get("error") or dl is None:
            diagnoses.append({
                "layer": "internet", "severity": "info",
                "title": "Speed test could not complete",
                "detail": "The speed test did not return a measurement.",
                "facts": ["Speed test tool: %s." % speed_result.get("tool", "?")],
                "assumption": "A failed speed test is a tool/server problem, not proof of zero "
                              "bandwidth. Do not read a missing or '0 Mbps' result as a dead connection.",
                "confidence": "medium",
                "fix": "Re-run, or install the Ookla speedtest CLI for a reliable measurement."})
        elif dl < 10:
            diagnoses.append({
                "layer": "internet", "severity": "warning",
                "title": "Low measured download speed",
                "detail": "Speed test download: %s Mbps." % dl,
                "facts": ["Download %s Mbps, upload %s Mbps (%s)." % (
                    dl, speed_result.get("upload_mbps", "?"), speed_result.get("tool", "?"))],
                "assumption": "This is a real bandwidth measurement (unlike the small-image test). A "
                              "low figure may reflect your plan's cap, WiFi, or congestion.",
                "confidence": "medium",
                "fix": "Compare against your subscribed plan speed; test wired vs WiFi to isolate."})
    return diagnoses


def _diag_download(results):
    diagnoses = []
    download_result = results.get("download_test")
    if download_result and download_result.get("error") is None:
        mbps = download_result.get("avg_mbps", 0)
        success = download_result.get("success", 0)
        failures = download_result.get("failures", 0)
        total_imgs = success + failures
        fail_pct = round(100 * failures / total_imgs, 1) if total_imgs else 0
        thr_facts = ["%s of %s small images fetched; aggregate rate %s Mbps." % (success, total_imgs, mbps)]
        if download_result.get("p95_latency_ms") is not None:
            thr_facts.append("p95 per-image latency: %s ms." % download_result["p95_latency_ms"])
        if failures > 0:
            sev = "bad" if fail_pct >= 20 else "warning"
            diagnoses.append({
                "layer": "internet", "severity": sev,
                "title": "Some image downloads failed (%s%%)" % fail_pct,
                "detail": "%s of %s small images failed to download." % (failures, total_imgs),
                "facts": thr_facts,
                "assumption": "Repeated small-object fetch failures point to an intermittent "
                              "connection problem — drops under concurrency, DNS hiccups, or a flaky "
                              "link — rather than a slow connection.",
                "confidence": "medium",
                "fix": "Run the intermittent-connection reproduction (10 images x 100 sites) to "
                       "localize the pattern, then attach the result to your ISP ticket."})
        else:
            diagnoses.append({
                "layer": "internet", "severity": "clean",
                "title": "Image downloads all succeeded",
                "detail": "%s of %s small images downloaded (aggregate %s Mbps)." % (success, total_imgs, mbps),
                "facts": thr_facts,
                "assumption": "This figure is the aggregate rate of many tiny concurrent images from "
                              "a single image host. It reflects per-request latency and that host's "
                              "limits, NOT your link's bandwidth — use the speed test for capacity. A "
                              "low number here with zero failures is expected and not a fault.",
                "confidence": "high",
                "fix": ""})
    return diagnoses


def _diag_connection(results):
    diagnoses = []
    conn_result = results.get("connection_test")
    if conn_result:
        http_lat = conn_result.get("http_latency", [])
        for h in http_lat:
            fail = h.get("failures", 0) or 0
            p95 = h.get("p95_ms")
            attempts = fail + len(h.get("latencies", []) or [])
            if fail > 0:
                diagnoses.append({
                    "layer": "internet", "severity": "warning",
                    "title": "HTTP requests intermittently failing: %s" % h.get("host"),
                    "detail": "%s of %s HTTP requests to %s failed." % (fail, attempts or "?", h.get("host")),
                    "facts": ["HTTP HEAD to %s: %s failure(s)%s." % (
                        h.get("host"), fail, (", p95 %.0f ms" % p95) if p95 else "")],
                    "assumption": "A host that answers some requests but not others indicates an "
                                  "intermittent connection or a loaded endpoint, not a hard outage. "
                                  "Note this probe uses plain HTTP, which some networks block or "
                                  "redirect — confirm with the reliability probe before blaming the line.",
                    "confidence": "medium",
                    "fix": "Re-run the reliability / intermittent-connection probe: if first attempts "
                           "fail and retries succeed, it is an intermittent-connection issue to localize."})
            elif p95 and p95 > 500:
                diagnoses.append({
                    "layer": "internet", "severity": "warning",
                    "title": "High HTTP latency: %s" % h.get("host"),
                    "detail": "p95=%.0f ms over %s requests" % (p95, attempts),
                    "facts": ["HTTP HEAD to %s: p95 %.0f ms, 0 failures." % (h.get("host"), p95)],
                    "assumption": "Consistently slow responses without failures usually mean a slow "
                                  "path or distant CDN edge, not packet loss.",
                    "confidence": "low",
                    "fix": "Web pages may load slowly. Check for DNS or routing detours."})
        mtu = conn_result.get("mtu", {})
        if mtu.get("available"):
            mtu_val = mtu.get("mtu", 1500)
            if mtu_val < 1400:
                diagnoses.append({"layer": "interface", "severity": "warning",
                                  "title": f"Low path MTU: {mtu_val} bytes",
                                  "detail": f"Largest unfragmented packet is {mtu_val} bytes (below the usual 1500).",
                                  "facts": ["Path MTU probe: %s bytes (standard Ethernet is 1500)." % mtu_val],
                                  "assumption": "A reduced path MTU usually comes from VPN or PPPoE "
                                                "encapsulation overhead. If MSS is not clamped it can cause "
                                                "large transfers to stall while small requests work — another "
                                                "intermittent-feeling symptom.",
                                  "confidence": "medium",
                                  "fix": "Enable MSS clamping on the router, or reduce the interface MTU to match "
                                         "the path (check VPN/PPPoE overhead)."})
    return diagnoses


def _diag_reliability(results):
    diagnoses = []
    rel_result = results.get("reliability_test")
    if rel_result and rel_result.get("available"):
        for v in rel_result.get("verdict", []):
            if v.get("severity") != "clean":
                diagnoses.append(dict(v))
    return diagnoses


def _diag_wellknown(results):
    diagnoses = []
    wk_result = results.get("wellknown_test")
    if wk_result and wk_result.get("available"):
        # The site-fleet headline is always informative — keep its clean verdict too
        # (it is the user's evidence that the 'many small images' pattern works).
        wkv = wk_result.get("verdict", [])
        if wkv:
            diagnoses.append(dict(wkv[0]))
            for v in wkv[1:]:
                if v.get("severity") != "clean":
                    diagnoses.append(dict(v))
    return diagnoses
