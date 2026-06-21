"""Verdict logic for the reliability probe — turns raw samples into findings."""


def reliability_verdict(result):
    verdict = []
    total = result.get("samples_total", 0)
    if not total:
        return [{"layer": "reliability", "severity": "info",
                 "title": "No reliability samples collected",
                 "detail": "The probe produced no usable samples.", "fix": ""}]
    ff_pct = result.get("first_attempt_fail_pct") or 0
    recovered = result.get("recovered_on_retry", 0)
    hard = result.get("hard_failures", 0)
    pb = result.get("fail_phase_breakdown", {})
    fam = result.get("by_family", {})
    conc = result.get("by_concurrency", {})
    targets = result.get("by_target", [])

    # IPv6 broken while IPv4 fine — the classic "first fails then retry works".
    v4 = fam.get("ipv4"); v6 = fam.get("ipv6")
    if v4 and v6 and v6.get("samples") and v4.get("samples"):
        v6f = v6.get("first_fail_pct") or 0
        v4f = v4.get("first_fail_pct") or 0
        if v6f >= 30 and v4f <= 10:
            verdict.append({"layer": "reliability", "severity": "bad",
                "title": "IPv6 connections failing, IPv4 fine",
                "detail": "IPv6 first-attempt failures %.0f%% vs IPv4 %.0f%%. Apps try IPv6 first "
                          "and fall back to IPv4 — this is the 'first connection fails, retry works' "
                          "symptom." % (v6f, v4f),
                "fix": "Repair IPv6 (router RA/DHCPv6) or disable/deprioritize IPv6 on this host."})

    # Concurrency-triggered failures — the "many small files/images" symptom.
    hi = conc.get("high", {}); lo = conc.get("low", {})
    if lo and hi and lo.get("first_fail_pct") is not None and hi.get("first_fail_pct") is not None:
        hif = hi["first_fail_pct"]; lof = lo["first_fail_pct"]
        if lof <= 10 and hif >= 25 and (hif - lof) >= 15:
            verdict.append({"layer": "reliability", "severity": "bad",
                "title": "Failures only under many parallel connections",
                "detail": "First-attempt failures jump from %.0f%% (sequential) to %.0f%% under "
                          "concurrency — pages with many images/small files trigger this." % (lof, hif),
                "fix": "Likely router NAT/conntrack-table exhaustion or rate-limiting. Raise conntrack "
                       "limits, reboot/replace the router, or reduce parallel connections."})

    # DNS intermittency — hostname targets fail but bare-IP targets are clean.
    host_t = [t for t in targets if not t.get("is_ip")]
    ip_t = [t for t in targets if t.get("is_ip")]
    if host_t and ip_t:
        host_ff = max((t.get("first_fail_pct") or 0) for t in host_t)
        ip_ff = max((t.get("first_fail_pct") or 0) for t in ip_t)
        if host_ff >= 25 and ip_ff <= 10:
            verdict.append({"layer": "reliability", "severity": "warning",
                "title": "Name resolution is the unreliable step",
                "detail": "Hostname targets fail %.0f%% on first try but bare-IP targets only %.0f%% — "
                          "DNS is intermittent." % (host_ff, ip_ff),
                "fix": "Switch resolver to 1.1.1.1 or 8.8.8.8, or check the router's DNS / DoH settings."})

    # Phase clustering among first-attempt failures.
    fail_phases = {k: v for k, v in pb.items() if k != "unknown" and v}
    if fail_phases:
        top = max(fail_phases, key=fail_phases.get)
        share = fail_phases[top] / max(sum(pb.values()), 1)
        if share >= 0.5 and ff_pct >= 10:
            msg = {
                "tls": ("TLS handshakes intermittently failing",
                        "Most first-attempt failures are in the TLS handshake.",
                        "Check path MTU/MSS clamping (see the MTU probe), VPN/PPPoE overhead, or a middlebox."),
                "tcp": ("TCP connects intermittently dropping",
                        "Most first-attempt failures are at TCP connect (SYN).",
                        "Suggests SYN loss, router state-table limits, or upstream congestion."),
                "ttfb": ("Server responses intermittently stalling",
                         "Most first-attempt failures occur waiting for the first byte.",
                         "Check for upstream/CDN issues or a saturated link."),
                "body": ("Transfers intermittently cut off mid-body",
                         "Connections open but the body read fails.",
                         "Suggests a flaky link dropping established connections."),
                "dns": ("DNS resolution intermittently failing",
                        "Most first-attempt failures are in DNS resolution.",
                        "Switch resolver to 1.1.1.1/8.8.8.8 or check router DNS."),
            }.get(top)
            if msg and not (top == "dns" and any(v["title"].startswith("Name resolution") for v in verdict)):
                verdict.append({"layer": "reliability", "severity": "warning",
                    "title": msg[0], "detail": msg[1] + " (%.0f%% of first attempts failed.)" % ff_pct,
                    "fix": msg[2]})

    # Retry-masked unreliability.
    if total and recovered / total >= 0.15 and hard <= max(1, total * 0.05):
        verdict.append({"layer": "reliability", "severity": "warning",
            "title": "Connections unreliable on first try but recover on retry",
            "detail": "%d of %d trials failed first but succeeded on retry. Users feel this as slow, "
                      "flaky loading even though little 'fails' outright." % (recovered, total),
            "fix": "Combine with the IPv6 / concurrency / DNS findings above to localize the cause."})

    if not verdict:
        if ff_pct >= 10:
            verdict.append({"layer": "reliability", "severity": "warning",
                "title": "Intermittent first-attempt failures",
                "detail": "%.0f%% of first attempts failed without a single dominant cause." % ff_pct,
                "fix": "Re-run with more samples and higher concurrency to localize the pattern."})
        else:
            verdict.append({"layer": "reliability", "severity": "clean",
                "title": "Connections reliable",
                "detail": "First-attempt failure rate %.0f%% across %d samples." % (ff_pct, total),
                "fix": ""})
    return verdict
