"""Path-layer diagnosis helpers: ISP/transit loss reconciliation, MTR, bufferbloat.

Only loss that PERSISTS to the destination hop is real; mid-hop ICMP loss that
clears by the endpoint is that router rate-limiting its own replies.
"""

from netdiag_core.probes.ping import classify_ping
from netdiag_core.analysis.reconcile import get_reconciliation


def _diag_loss(results):
    diagnoses = []
    gw_ping = results.get("gateway_ping")
    internet_pings = results.get("internet_ping", [])
    recon = get_reconciliation(results)
    per_host = recon["per_host"]
    filtered_set = set(recon["filtered_hosts"])

    # The "really 95% packet loss?" case: ICMP loss the working TCP layer disproves.
    icmp_filtered = [h for h in per_host if h["icmp_filtered"]]
    if icmp_filtered:
        hosts_str = ", ".join(h["host"] for h in icmp_filtered)
        facts = []
        for h in icmp_filtered:
            facts.append("ICMP ping to %s: %s%% loss (%s of %s echo replies returned)."
                         % (h["host"], h["loss_pct"], h.get("received"), h.get("sent")))
            if h.get("tcp_failure_pct") is not None:
                facts.append("TCP handshake to %s:%s succeeded %s%% of the time over %s attempts."
                             % (h["host"], h.get("tcp_port"),
                                round(100 - (h["tcp_failure_pct"] or 0), 1), h.get("tcp_attempts")))
        if recon["http_ok_global"]:
            facts.append("HTTPS downloads over the same connection completed successfully.")
        if recon["dns_ok_global"]:
            facts.append("DNS name resolution succeeded on the same connection.")
        diagnoses.append({
            "layer": "internet", "severity": "info",
            "title": "High ICMP \"loss\" is rate-limiting, not packet loss",
            "detail": "Ping to %s reports high loss, but TCP/HTTPS to those same addresses connect "
                      "fine. These public resolvers throttle ICMP echo to shed load." % hosts_str,
            "facts": facts,
            "assumption": "A real high packet-loss rate cannot coexist with a near-100% TCP "
                          "handshake success rate, because a handshake needs several consecutive "
                          "round trips to complete. The missing replies are deprioritized by the "
                          "destination, not lost on your line. (1.1.1.1 / 8.8.8.8 / 9.9.9.9 are "
                          "known to rate-limit ICMP by policy.)",
            "confidence": "high",
            "fix": "Disregard the ICMP loss figure for these public resolvers. Judge real loss from "
                   "the gateway ping, the TCP connection tests, and the reliability probe instead."})

    # Hosts whose loss is NOT explained by rate-limiting — treat as genuine.
    real_bad = [row for row in internet_pings
                if row.get("host") not in filtered_set and classify_ping(row) != "clean"]
    gw_clean = bool(gw_ping) and classify_ping(gw_ping) == "clean"
    if real_bad and gw_clean:
        for row in real_bad:
            loss = row.get("loss_pct", 0)
            p95 = row.get("p95_ms")
            tcp_match = next((h for h in per_host if h["host"] == row.get("host")), None)
            facts = ["ICMP ping to %s: %s%% loss, p95 %s ms, jitter %s ms."
                     % (row.get("label"), loss, p95, row.get("jitter_ms"))]
            if tcp_match and tcp_match.get("tcp_failure_pct") is not None:
                facts.append("TCP to %s:%s also degraded: %s%% connect failure."
                             % (row.get("host"), tcp_match.get("tcp_port"), tcp_match["tcp_failure_pct"]))
            sev = "bad" if (loss or 0) >= 5 else "warning"
            diagnoses.append({
                "layer": "internet", "severity": sev,
                "title": "External path unstable: %s" % row.get("label"),
                "detail": "loss=%s%%, p95=%s ms, jitter=%s ms" % (loss, p95, row.get("jitter_ms")),
                "facts": facts,
                "assumption": "The gateway ping is clean while this external host is not, so the "
                              "instability is upstream of your router (ISP or transit), not your "
                              "local LAN/WiFi.",
                "confidence": "medium",
                "fix": "Likely an ISP or upstream routing issue. Share the MTR trace and these "
                       "measurements with your ISP."})
    elif real_bad and gw_ping and not gw_clean:
        diagnoses.append({
            "layer": "meta", "severity": "warning",
            "title": "Both local and internet unstable",
            "detail": "Gateway and external hosts both show genuine instability.",
            "facts": ["Gateway ping: loss=%s%%, p95=%s ms." % (gw_ping.get("loss_pct"), gw_ping.get("p95_ms"))]
                     + ["%s: loss=%s%%, p95=%s ms." % (r.get("label"), r.get("loss_pct"), r.get("p95_ms"))
                        for r in real_bad],
            "assumption": "When the local hop is already unstable, the external numbers are "
                          "unreliable until the local issue is resolved.",
            "confidence": "medium",
            "fix": "Fix the local network issue first (see the gateway finding), then re-test the internet."})
    return diagnoses


def _diag_mtr(results):
    diagnoses = []
    mtr_result = results.get("mtr")
    if mtr_result and mtr_result.get("hops"):
        hops = mtr_result["hops"]
        last = hops[-1] if hops else {}
        dest_loss = last.get("loss_pct", 0) or 0
        # Intermediate-hop loss that does NOT persist to the destination is that
        # router rate-limiting its own ICMP responses (same mechanism as 1.1.1.1) —
        # it is NOT packet loss on your path. Only loss that reaches the final hop
        # is real end-to-end loss. This is the cardinal rule of reading an MTR trace.
        first_mid = None
        for hop in hops[:-1]:
            if (hop.get("loss_pct", 0) or 0) > 5:
                first_mid = hop
                break

        # Same cardinal rule as the internet layer: a real >5% end-to-end loss
        # cannot coexist with working TCP handshakes + HTTPS + DNS (each needs
        # several consecutive round trips). If the transport layer is healthy, the
        # ICMP "loss" reaching the final hop is the destination — or, under a VPN,
        # the tunnel — rate-limiting echo replies, not packet loss on the path.
        recon = get_reconciliation(results)
        transport_ok = recon["dns_ok_global"] and (recon["tcp_ok_global"] or recon["http_ok_global"])
        vpn = results.get("vpn") or {}
        vpn_active = bool(vpn.get("active"))
        vpn_iface = vpn.get("interface")

        if dest_loss > 5 and transport_ok:
            facts = ["Destination hop %s reports %s%% ICMP loss." % (last.get("hop"), dest_loss)]
            if first_mid:
                facts.append("Loss first appears at hop %s (%s%%)." % (first_mid["hop"], first_mid.get("loss_pct")))
            if recon["tcp_ok_global"] or recon["http_ok_global"]:
                facts.append("Yet TCP/HTTPS connections over this same path succeed.")
            if recon["dns_ok_global"]:
                facts.append("DNS resolution over the same path succeeds.")
            if vpn_active:
                facts.append("This trace runs through a VPN tunnel (%s), so hop 1 is the VPN server, "
                             "not your modem." % vpn_iface)
            diagnoses.append({"layer": "isp", "severity": "info",
                              "title": "MTR shows ICMP loss, but the working transport disproves packet loss",
                              "detail": "The trace reports %s%% loss at the destination, but TCP/HTTPS/DNS over "
                                        "the same path succeed%s." % (dest_loss,
                                        (" (the path runs through VPN tunnel %s)" % vpn_iface) if vpn_active else ""),
                              "facts": facts,
                              "assumption": "A genuine high end-to-end loss rate cannot coexist with near-100%% "
                                            "TCP handshake and HTTPS success. The missing ICMP replies are "
                                            "deprioritized by the destination%s, not lost on your line."
                                            % (" or the VPN tunnel" if vpn_active else ""),
                              "confidence": "high",
                              "fix": "Disregard this MTR loss figure; judge real loss from the gateway ping, the "
                                     "TCP/HTTPS tests, and the reliability probe."
                                     + (" To trace the real underlying path, re-run with the VPN disconnected."
                                        if vpn_active else "")})
        elif dest_loss > 5 and vpn_active:
            # Genuine-looking loss, but the path egresses through a VPN — the early
            # hops are the encrypted tunnel to the VPN server, NOT the local modem.
            hop_num = (first_mid or last)["hop"]
            facts = ["Destination hop %s shows %s%% loss." % (last.get("hop"), dest_loss)]
            if first_mid:
                facts.append("Loss first appears at hop %s (%s%%) and persists to the destination."
                             % (first_mid["hop"], first_mid.get("loss_pct")))
            facts.append("This trace runs through a VPN tunnel (%s); hop 1 is the VPN server, not your modem."
                         % vpn_iface)
            diagnoses.append({"layer": "isp", "severity": "warning",
                              "title": "Packet loss on the VPN tunnel path",
                              "detail": "End-to-end loss begins at hop %s, but the path runs through a VPN "
                                        "tunnel (%s)." % (hop_num, vpn_iface),
                              "facts": facts,
                              "assumption": "Because traffic egresses through a VPN, the early hops are the "
                                            "encrypted tunnel to the VPN server — loss here points to the VPN "
                                            "server/route, not your modem or local uplink.",
                              "confidence": "medium",
                              "fix": "Reconnect or switch VPN server, or disconnect the VPN and re-run to see the "
                                     "real path. If the loss only appears with the VPN on, it is the VPN, not your line."})
        elif dest_loss > 5:
            culprit = first_mid or last
            hop_num = culprit["hop"]
            facts = ["Destination hop %s shows %s%% loss (loss reaches the endpoint)." % (last.get("hop"), dest_loss)]
            if first_mid:
                facts.append("Loss first appears at hop %s (%s%%) and persists to the destination."
                             % (first_mid["hop"], first_mid.get("loss_pct")))
            if hop_num <= 2:
                diagnoses.append({"layer": "isp", "severity": "bad",
                                  "title": "Real packet loss starting at the first hops",
                                  "detail": "End-to-end loss begins at hop %s and reaches the destination." % hop_num,
                                  "facts": facts,
                                  "assumption": "Loss that starts at hops 1-2 AND persists to the destination "
                                                "points to your modem or local uplink (e.g. line/signal quality).",
                                  "confidence": "high",
                                  "fix": "Restart the modem/gateway and check for line/signal issues. "
                                         "If it persists, this is evidence for your ISP."})
            else:
                diagnoses.append({"layer": "isp", "severity": "bad",
                                  "title": "Real packet loss in the ISP/transit network",
                                  "detail": "End-to-end loss begins at hop %s (inside the provider network)." % hop_num,
                                  "facts": facts,
                                  "assumption": "Loss that starts at hop %s and persists to the destination is "
                                                "upstream of your home — an ISP or transit problem, not your "
                                                "equipment." % hop_num,
                                  "confidence": "high",
                                  "fix": "Contact your ISP and share this trace and the full MTR output."})
        elif first_mid:
            diagnoses.append({"layer": "isp", "severity": "info",
                              "title": "Mid-route hop shows ICMP loss, but the destination is clean",
                              "detail": "Hop %s reports %s%% loss while the final hop shows %s%%."
                                        % (first_mid["hop"], first_mid.get("loss_pct"), dest_loss),
                              "facts": ["Hop %s: %s%% loss." % (first_mid["hop"], first_mid.get("loss_pct")),
                                        "Destination hop %s: %s%% loss." % (last.get("hop"), dest_loss)],
                              "assumption": "Because the loss clears by the destination, that intermediate "
                                            "router is simply rate-limiting its own ICMP replies — it is NOT "
                                            "dropping your traffic. This is the most common false alarm in a "
                                            "traceroute and should not be reported to an ISP as packet loss.",
                              "confidence": "high",
                              "fix": "No action needed — disregard the mid-route loss figure."})
    return diagnoses


def _diag_bufferbloat(results):
    diagnoses = []
    bufferbloat_blob = results.get("bufferbloat")
    if bufferbloat_blob and bufferbloat_blob.get("available"):
        ratio = bufferbloat_blob.get("ratio")
        bb_facts = ["Idle RTT %s ms vs loaded RTT %s ms (ratio %sx)." % (
            bufferbloat_blob.get("rtt_idle_ms"), bufferbloat_blob.get("rtt_loaded_ms"),
            ("%.1f" % ratio) if ratio else "?")]
        if ratio and ratio > 3:
            diagnoses.append({"layer": "bufferbloat", "severity": "bad",
                              "title": "Severe bufferbloat detected",
                              "detail": f"Latency under load is {ratio:.1f}x idle latency (idle: {bufferbloat_blob.get('rtt_idle_ms')}ms, loaded: {bufferbloat_blob.get('rtt_loaded_ms')}ms)",
                              "facts": bb_facts,
                              "assumption": "Latency ballooning under load means oversized buffers are "
                                            "queuing packets during saturation — this is what makes calls/games "
                                            "stutter while a download runs, even on a 'fast' line.",
                              "confidence": "high",
                              "fix": "Enable SQM/fq_codel on your router. On Linux: tc qdisc add dev eth0 root fq_codel. "
                                     "On OpenWrt: install luci-app-sqm."})
        elif ratio and ratio > 2:
            diagnoses.append({"layer": "bufferbloat", "severity": "warning",
                              "title": "Mild bufferbloat detected",
                              "detail": f"Latency under load is {ratio:.1f}x idle latency",
                              "facts": bb_facts,
                              "assumption": "A moderate latency rise under load; noticeable in latency-sensitive "
                                            "apps during heavy uploads but not severe.",
                              "confidence": "medium",
                              "fix": "Consider enabling SQM or reducing concurrent uploads during latency-sensitive use."})
    return diagnoses
