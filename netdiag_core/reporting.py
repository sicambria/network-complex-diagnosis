"""Report builders — CSV/text/console/ISP output from a results dict."""

import csv
import json
from pathlib import Path

from netdiag_core import analysis
from netdiag_core.probes.ping import classify_ping


def flatten_ping(results):
    rows = []
    for key in ["gateway_ping"]:
        group = results.get(key)
        if group and group.get("samples"):
            rows.extend(group["samples"])
    for group in results.get("internet_ping", []):
        rows.extend(group["samples"])
    return rows


def ping_summary_rows(results):
    rows = []
    for key in ["gateway_ping"]:
        group = results.get(key)
        if group:
            rows.append({k: v for k, v in group.items() if k != "samples"})
    for group in results.get("internet_ping", []):
        rows.append({k: v for k, v in group.items() if k != "samples"})
    return rows


def write_csv(path, rows):
    if not rows:
        return
    fieldnames = sorted({k for row in rows for k in row})
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def compact_ping(row):
    keys = ["label", "host", "ipv", "sent", "received", "loss_pct",
            "min_ms", "avg_ms", "p95_ms", "p99_ms", "max_ms", "jitter_ms"]
    return {k: row.get(k) for k in keys}


def write_report(path, results):
    lines = []
    lines.append("Internet Diagnostics Report")
    lines.append(f"Timestamp: {results['timestamp']}")
    lines.append(f"Platform: {results['platform']}")
    lines.append(f"OS: {results.get('os', 'unknown')}")
    lines.append(f"Interface: {results.get('default_interface') or 'not detected'}")
    lines.append(f"Gateway: {results.get('gateway') or 'not detected'}")
    lines.append(f"Health score: {results.get('health_score', '?')}/100")
    lines.append("")
    lines.append("Diagnosis:")
    lines.append("(Facts are measured; interpretation is what we infer from them.)")
    for d in results.get("diagnosis", []):
        conf = f" (confidence: {d['confidence']})" if d.get("confidence") else ""
        lines.append(f"- [{d['severity']}] [{d['layer']}] {d['title']}{conf}")
        if d.get("detail"):
            lines.append(f"    {d['detail']}")
        for f in d.get("facts", []):
            lines.append(f"    FACT: {f}")
        if d.get("assumption"):
            lines.append(f"    INTERPRETATION: {d['assumption']}")
        if d.get("fix"):
            lines.append(f"    FIX: {d['fix']}")
    lines.append("")
    lines.append("Ping summary:")
    for row in ping_summary_rows(results):
        lines.append(json.dumps(compact_ping(row), ensure_ascii=False))
    lines.append("")
    lines.append("DNS:")
    for row in results.get("dns", []):
        lines.append(json.dumps(row, ensure_ascii=False))
    lines.append("")
    lines.append("TCP:")
    for row in results.get("tcp", []):
        lines.append(json.dumps(row, ensure_ascii=False))
    lines.append("")
    mtr_data = results.get("mtr") or {}
    if mtr_data.get("hops"):
        lines.append("Route:")
        for h in mtr_data["hops"]:
            lines.append(f"  Hop {h['hop']}: loss={h['loss_pct']}%, avg={h['avg_ms']}ms")
    path = Path(path)
    path.write_text("\n".join(lines), encoding="utf-8")


def _sev_label(s):
    return {"bad": "PROBLEM", "warning": "WARNING", "info": "NOTE", "clean": "OK"}.get(s, s.upper())


def build_isp_report(results):
    # A detailed, plain-language evidence report a customer can attach to an ISP
    # ticket. It leads with the ICMP-vs-TCP method note so an engineer cannot
    # dismiss the report by pointing out that ping loss to 1.1.1.1 is normal, and it
    # clearly separates what is the customer's equipment from what is upstream.
    L = []
    diag = results.get("diagnosis", []) or []
    recon = analysis.get_reconciliation(results)
    sev_of = {d.get("severity") for d in diag}

    def head(t):
        L.append("")
        L.append("=" * 72)
        L.append(t)
        L.append("=" * 72)

    L.append("NETWORK DIAGNOSTIC EVIDENCE REPORT")
    L.append("Prepared for submission to the Internet Service Provider")
    L.append("")
    L.append("Generated:   %s" % results.get("timestamp", "?"))
    L.append("Tool:        NetDiag automated network diagnostics")
    L.append("Health score: %s/100 (0 = unusable, 100 = perfect)" % results.get("health_score", "?"))

    head("1. CONNECTION UNDER TEST")
    L.append("Operating system:   %s" % results.get("platform", results.get("os", "?")))
    L.append("Local interface:    %s" % (results.get("default_interface") or "not detected"))
    L.append("Default gateway:    %s (the customer's own router/modem)" % (results.get("gateway") or "not detected"))
    wifi = results.get("wifi") or {}
    if wifi.get("available") and wifi.get("signal_dbm") is not None:
        L.append("WiFi signal:        %s dBm" % wifi.get("signal_dbm"))

    head("2. HOW TO READ THIS REPORT (METHOD NOTE)")
    L.append("This report distinguishes ICMP 'ping' loss from REAL packet loss.")
    L.append("")
    L.append("Public resolvers such as 1.1.1.1, 8.8.8.8 and 9.9.9.9 intentionally")
    L.append("rate-limit ICMP echo (ping) to shed load. A high ping-loss number to")
    L.append("those addresses is therefore NOT evidence of packet loss when TCP and")
    L.append("HTTPS connections to the SAME addresses succeed (a TCP handshake needs")
    L.append("several consecutive round trips, so it cannot succeed through real high")
    L.append("loss). Every figure below already accounts for this distinction, so the")
    L.append("findings reflect genuine problems only.")
    if recon.get("filtered_hosts"):
        L.append("")
        L.append("In this run, the following hosts showed rate-limited ICMP (ignored as")
        L.append("packet loss): %s" % ", ".join(recon["filtered_hosts"]))

    head("3. LOCAL EQUIPMENT vs UPSTREAM (ISP) ATTRIBUTION")
    gw = results.get("gateway_ping") or {}
    local_bad = [d for d in diag if d.get("layer") in ("interface", "wifi") and d.get("severity") in ("bad", "warning")]
    gw_status = classify_ping(gw) if gw else None
    if gw and gw_status == "clean" and not local_bad:
        L.append("LOCAL NETWORK: HEALTHY.")
        L.append("  The first hop (the customer's router) responds cleanly (loss %s%%," % gw.get("loss_pct", "?"))
        L.append("  p95 %s ms) and no interface/WiFi faults were found. Any genuine" % gw.get("p95_ms", "?"))
        L.append("  problem below is therefore UPSTREAM of the customer's equipment.")
    else:
        L.append("LOCAL NETWORK: ISSUES PRESENT (see findings).")
        if gw:
            L.append("  Gateway ping: loss %s%%, p95 %s ms." % (gw.get("loss_pct", "?"), gw.get("p95_ms", "?")))
        for d in local_bad:
            L.append("  - %s: %s" % (d.get("title"), d.get("detail")))
        L.append("  Note: local issues should be resolved first, as they can mask or")
        L.append("  mimic upstream problems.")
    upstream_bad = [d for d in diag if d.get("layer") in ("isp", "internet") and d.get("severity") == "bad"]
    if upstream_bad:
        L.append("")
        L.append("UPSTREAM (ISP/transit): GENUINE PROBLEMS DETECTED:")
        for d in upstream_bad:
            L.append("  - %s: %s" % (d.get("title"), d.get("detail")))

    head("4. FINDINGS (EVIDENCE)")
    ranked = sorted(diag, key=lambda d: {"bad": 0, "warning": 1, "info": 2, "clean": 3}.get(d.get("severity"), 2))
    nontrivial = [d for d in ranked if d.get("severity") != "clean"] or ranked
    for i, d in enumerate(nontrivial, 1):
        conf = " | confidence: %s" % d["confidence"] if d.get("confidence") else ""
        L.append("")
        L.append("%d. [%s] %s%s" % (i, _sev_label(d.get("severity")), d.get("title"), conf))
        L.append("   Layer: %s" % d.get("layer"))
        if d.get("detail"):
            L.append("   Summary: %s" % d["detail"])
        for f in d.get("facts", []):
            L.append("   - MEASURED: %s" % f)
        if d.get("assumption"):
            L.append("   - INTERPRETATION: %s" % d["assumption"])
        if d.get("fix"):
            L.append("   - SUGGESTED ACTION: %s" % d["fix"])

    head("5. RAW MEASUREMENTS")
    if gw:
        L.append("Gateway (hop 1) ping: sent=%s, loss=%s%%, min/avg/p95/max = %s/%s/%s/%s ms, jitter=%s ms"
                 % (gw.get("sent", "?"), gw.get("loss_pct", "?"), gw.get("min_ms", "?"), gw.get("avg_ms", "?"),
                    gw.get("p95_ms", "?"), gw.get("max_ms", "?"), gw.get("jitter_ms", "?")))
    L.append("")
    L.append("External hosts — ICMP ping vs TCP connection (side by side):")
    L.append("  %-16s %-22s %-22s %s" % ("HOST", "ICMP PING", "TCP CONNECT", "VERDICT"))
    per_host = {h["host"]: h for h in recon.get("per_host", [])}
    for p in results.get("internet_ping", []) or []:
        h = per_host.get(p.get("host"), {})
        icmp = "%s%% loss, p95 %sms" % (p.get("loss_pct", "?"), p.get("p95_ms", "?"))
        if h.get("tcp_failure_pct") is not None:
            tcp = "%s%% fail/%s tries" % (h.get("tcp_failure_pct"), h.get("tcp_attempts"))
        else:
            tcp = "not tested"
        verdict = "ICMP rate-limited (ignore loss)" if h.get("icmp_filtered") else (
            "real loss" if (p.get("loss_pct") or 0) >= 5 else "ok")
        L.append("  %-16s %-22s %-22s %s" % (p.get("label", "?"), icmp, tcp, verdict))
    dns = results.get("dns", []) or []
    if dns:
        L.append("")
        L.append("DNS resolution:")
        for d in dns:
            L.append("  %-18s %s%% fail, p95 %s ms" % (d.get("host"), d.get("failure_pct", "?"), d.get("p95_ms", "?")))
    mtr = results.get("mtr") or {}
    if mtr.get("hops"):
        L.append("")
        L.append("Route trace (MTR) — full per-hop, %s:" % mtr.get("host", "?"))
        for h in mtr["hops"]:
            L.append("  hop %-3s loss=%-5s%% avg=%s ms" % (h.get("hop"), h.get("loss_pct"), h.get("avg_ms")))
        L.append("  (Reminder: loss at a middle hop that clears by the final hop is that")
        L.append("   router rate-limiting ICMP, not packet loss. Only loss reaching the")
        L.append("   destination hop is real end-to-end loss.)")
    bb = results.get("bufferbloat") or {}
    if bb.get("available") and bb.get("ratio") is not None:
        L.append("")
        L.append("Bufferbloat: idle %s ms vs loaded %s ms (ratio %.1fx)"
                 % (bb.get("rtt_idle_ms"), bb.get("rtt_loaded_ms"), bb.get("ratio")))
    sp = results.get("speedtest") or {}
    if sp.get("available") and sp.get("download_mbps") is not None:
        L.append("")
        L.append("Speed test: %s Mbps down, %s Mbps up (%s)"
                 % (sp.get("download_mbps"), sp.get("upload_mbps", "?"), sp.get("tool", "?")))
    rel = results.get("reliability_test") or {}
    if rel.get("available"):
        L.append("")
        L.append("Intermittent-connection probe: %s samples, %s%% first-attempt failures, "
                 "%s recovered on retry, %s hard failures."
                 % (rel.get("samples_total", "?"), rel.get("first_attempt_fail_pct", "?"),
                    rel.get("recovered_on_retry", "?"), rel.get("hard_failures", "?")))
    wk = results.get("wellknown_test") or {}
    if wk.get("available"):
        L.append("")
        L.append("100-site intermittent reproduction (small images from %s well-known sites):"
                 % wk.get("site_count", "?"))
        L.append("  %s attempts, %s%% first-attempt failures, %s recovered on retry, %s hard failures."
                 % (wk.get("samples_total", "?"), wk.get("first_attempt_fail_pct", "?"),
                    wk.get("recovered_on_retry", "?"), wk.get("hard_failures", "?")))
        worst = sorted(wk.get("by_target", []) or [], key=lambda t: -(t.get("first_fail_pct") or 0))
        worst = [t for t in worst if (t.get("first_fail_pct") or 0) > 0][:8]
        for t in worst:
            L.append("    %-22s %.0f%% first-attempt fail (%s samples)"
                     % (t.get("host"), t.get("first_fail_pct") or 0, t.get("samples")))

    head("6. WHAT WE ASK THE ISP TO CHECK")
    asks = []
    for d in diag:
        if d.get("layer") == "isp" and d.get("severity") == "bad":
            asks.append("Investigate packet loss on your network: %s" % d.get("detail"))
        if d.get("layer") == "internet" and d.get("severity") == "bad" and "External path" in (d.get("title") or ""):
            asks.append("Investigate routing/upstream instability: %s" % d.get("detail"))
    if "bad" not in sev_of and "warning" not in sev_of:
        asks.append("No upstream fault was reproduced during this run. If the problem is")
        asks.append("intermittent, please keep this report and run again during an episode.")
    if not asks:
        asks.append("Review the findings in section 4 and the raw measurements in section 5.")
    for a in asks:
        L.append("  - %s" % a)
    L.append("")
    L.append("Report ends. Generated by NetDiag.")
    return "\n".join(L)


def print_console_summary(results, outdir):
    print(f"\nHealth score: {results.get('health_score', '?')}/100")
    print("\nDiagnosis:")
    for d in results.get("diagnosis", []):
        icon = {"clean": "  ", "info": "  ", "warning": "! ", "bad": "!!"}.get(d["severity"], "  ")
        conf = f" (confidence: {d['confidence']})" if d.get("confidence") else ""
        print(f"  {icon}[{d['layer']}] {d['title']}{conf}")
        if d.get("detail"):
            print(f"      {d['detail']}")
        for f in d.get("facts", []):
            print(f"      fact: {f}")
        if d.get("assumption"):
            print(f"      interpretation: {d['assumption']}")
        if d.get("fix"):
            print(f"      fix: {d['fix']}")
    print("\nPing summary:")
    for row in ping_summary_rows(results):
        c = compact_ping(row)
        print(f"  {c['label']}: loss={c['loss_pct']}%, avg={c['avg_ms']}ms, p95={c['p95_ms']}ms, jitter={c['jitter_ms']}ms")
    print(f"\nFiles written to: {Path(outdir).resolve()}")
