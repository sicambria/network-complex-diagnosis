"""Local-layer diagnosis helpers: interface/ethtool, WiFi, gateway.

Each helper returns a list of diagnosis dicts for its layer. They are pure
functions of ``results`` (the diagnose() orchestrator concatenates them in
order); the block bodies are unchanged from the original single diagnose().
"""

from netdiag_core.probes.ping import classify_ping


def _diag_interface(results):
    diagnoses = []
    iface = results.get("interface")
    ethtool = results.get("ethtool")
    if iface and iface.get("available"):
        rx = iface.get("rx", {})
        tx = iface.get("tx", {})
        total_errors = (rx.get("errors", 0) + tx.get("errors", 0) +
                        rx.get("dropped", 0) + tx.get("dropped", 0) +
                        rx.get("overruns", 0) + tx.get("overruns", 0) +
                        rx.get("frame", 0))
        carrier = rx.get("carrier", 0)
        if total_errors > 0 or carrier > 0:
            details = []
            if rx.get("errors", 0) > 0:
                details.append(f"RX errors: {rx['errors']}")
            if tx.get("errors", 0) > 0:
                details.append(f"TX errors: {tx['errors']}")
            if rx.get("dropped", 0) > 0:
                details.append(f"RX dropped: {rx['dropped']}")
            if rx.get("overruns", 0) > 0:
                details.append(f"RX overruns: {rx['overruns']}")
            if carrier > 0:
                details.append(f"Carrier changes: {carrier}")
            diagnoses.append({"layer": "interface", "severity": "bad",
                              "title": "Interface errors detected",
                              "detail": "; ".join(details),
                              "facts": ["Driver counters on this interface: " + "; ".join(details) + "."],
                              "assumption": "Non-zero error/drop counters at the NIC mean frames are "
                                            "being corrupted or discarded at the physical/link layer — "
                                            "this is local hardware/cabling, upstream of any ISP issue.",
                              "confidence": "high",
                              "fix": "Check cable connections. Try a different cable or port. "
                                     "High overruns suggest the system is too slow to process packets."})

    if ethtool and ethtool.get("available"):
        if ethtool.get("duplex") == "Half":
            diagnoses.append({"layer": "interface", "severity": "bad",
                              "title": "Half-duplex detected",
                              "detail": "Interface is negotiated at half-duplex.",
                              "facts": ["ethtool reports duplex = Half on this interface."],
                              "assumption": "Half-duplex on a modern wired link is almost always a "
                                            "duplex-mismatch (one side auto, other forced), causing "
                                            "late collisions and severe slowdowns under load. On WiFi, "
                                            "ethtool duplex is often not meaningful — verify it is a wired link.",
                              "confidence": "medium",
                              "fix": "Force full-duplex on both sides of the link, or set both ends to auto-negotiate."})
        if ethtool.get("link_detected") is False:
            diagnoses.append({"layer": "interface", "severity": "bad",
                              "title": "No link detected",
                              "detail": "The Ethernet link appears down.",
                              "facts": ["ethtool reports link_detected = no."],
                              "assumption": "No carrier means the cable/port is not establishing a link "
                                            "at all — a purely local physical problem.",
                              "confidence": "high",
                              "fix": "Check the cable, switch port, and interface status."})
    return diagnoses


def _diag_wifi(results):
    diagnoses = []
    wifi = results.get("wifi")
    if wifi and wifi.get("available"):
        signal = wifi.get("signal_dbm")
        if signal is not None:
            if signal < -80:
                diagnoses.append({"layer": "wifi", "severity": "bad",
                                  "title": "Very weak WiFi signal",
                                  "detail": f"Signal strength {signal} dBm. This will cause dropouts and slow speeds.",
                                  "facts": [f"Measured WiFi signal: {signal} dBm (below -80 dBm)."],
                                  "assumption": "Below about -80 dBm the link operates near its noise floor, "
                                                "so the radio drops to low rates and retransmits heavily — "
                                                "this looks identical to 'internet problems' from the app's view.",
                                  "confidence": "high",
                                  "fix": "Move closer to the router, remove obstructions, or add a WiFi extender/mesh node."})
            elif signal < -70:
                diagnoses.append({"layer": "wifi", "severity": "warning",
                                  "title": "Weak WiFi signal",
                                  "detail": f"Signal strength {signal} dBm. May cause intermittent issues.",
                                  "facts": [f"Measured WiFi signal: {signal} dBm (-70 to -80 dBm range)."],
                                  "assumption": "-70 to -80 dBm is marginal: usable when idle but prone to "
                                                "stalls and first-attempt failures under load. A plausible "
                                                "contributor to intermittent symptoms.",
                                  "confidence": "medium",
                                  "fix": "Move closer to the router, or test on Ethernet to rule WiFi in or out."})
            elif signal < -60:
                diagnoses.append({"layer": "wifi", "severity": "info",
                                  "title": "Fair WiFi signal",
                                  "detail": f"Signal strength {signal} dBm. Adequate but not optimal for high bandwidth.",
                                  "facts": [f"Measured WiFi signal: {signal} dBm (-60 to -70 dBm range)."],
                                  "assumption": "-60 to -70 dBm is generally fine for browsing and video; it is "
                                                "unlikely to be the primary cause of an intermittent fault.",
                                  "confidence": "medium",
                                  "fix": ""})
        channel_util = wifi.get("channel_util")
        if channel_util is not None and channel_util > 60:
            diagnoses.append({"layer": "wifi", "severity": "warning",
                              "title": "Crowded WiFi channel",
                              "detail": f"Channel utilization {channel_util}%. High congestion.",
                              "facts": [f"Channel airtime utilization: {channel_util}% (busy)."],
                              "assumption": "High airtime use means neighbours/devices are saturating the "
                                            "channel, adding latency and jitter even with a strong signal.",
                              "confidence": "medium",
                              "fix": "Switch to a less congested channel or upgrade to WiFi 6/6E."})
    return diagnoses


def _diag_gateway(results):
    diagnoses = []
    gw_ping = results.get("gateway_ping")
    socket_stats = results.get("tcp_sockets")
    wifi = results.get("wifi")
    tcp_status = classify_ping(gw_ping) if gw_ping else None
    has_tcp_issue = socket_stats and socket_stats.get("available") and socket_stats.get("total_retransmits", 0) > 50
    if gw_ping:
        status = classify_ping(gw_ping)
        if status != "clean" or has_tcp_issue:
            detail_parts = []
            if gw_ping.get("loss_pct", 0) > 0:
                detail_parts.append(f"Packet loss: {gw_ping['loss_pct']}%")
            if gw_ping.get("p95_ms", 0) > 50:
                detail_parts.append(f"Latency spikes: p95={gw_ping['p95_ms']}ms")
            if has_tcp_issue:
                detail_parts.append(f"TCP retransmits: {socket_stats['total_retransmits']}")
            gw_facts = ["Gateway ping: loss=%s%%, p95=%s ms, avg=%s ms over %s probes."
                        % (gw_ping.get("loss_pct", 0), gw_ping.get("p95_ms", "?"),
                           gw_ping.get("avg_ms", "?"), gw_ping.get("sent", "?"))]
            if has_tcp_issue:
                gw_facts.append("ss reports %s TCP retransmits on active sockets."
                                % socket_stats.get("total_retransmits"))
            diagnose_gw = {"layer": "gateway", "severity": "bad",
                           "title": "Gateway instability detected",
                           "detail": "; ".join(detail_parts),
                           "facts": gw_facts,
                           "assumption": "The first hop (your own router) is already lossy or slow. "
                                         "Because this is the local link, it is NOT an ISP fault — and it "
                                         "makes every downstream measurement unreliable until fixed.",
                           "confidence": "high",
                           "fix": ""}
            if wifi and wifi.get("available") and wifi.get("signal_dbm"):
                sig = wifi["signal_dbm"]
                if sig < -70:
                    diagnose_gw["fix"] = "Gateway latency may be caused by weak WiFi. Move closer or use Ethernet."
                else:
                    diagnose_gw["fix"] = "Router may be overloaded. Reduce active downloads, reboot the router, or check QoS settings."
            else:
                diagnose_gw["fix"] = "Router may be overloaded. Reduce active downloads, reboot the router, or check QoS settings."
            diagnoses.append(diagnose_gw)
        elif status == "clean":
            diagnoses.append({"layer": "gateway", "severity": "clean",
                              "title": "Gateway (local router) stable",
                              "detail": f"p95={gw_ping.get('p95_ms', '?')} ms, loss={gw_ping.get('loss_pct', 0)}%",
                              "facts": ["Gateway ping: loss=%s%%, p95=%s ms, avg=%s ms over %s probes."
                                        % (gw_ping.get("loss_pct", 0), gw_ping.get("p95_ms", "?"),
                                           gw_ping.get("avg_ms", "?"), gw_ping.get("sent", "?"))],
                              "assumption": "A clean first hop means your local LAN/WiFi and router are "
                                            "healthy, so any genuine problem is upstream (ISP) rather than local.",
                              "confidence": "high",
                              "fix": ""})
    return diagnoses
