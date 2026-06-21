"""full_diagnostic() — orchestrates every probe in sequence into one results dict.

Probes are called through their module objects (netinfo.detect_gateway, ...) so
tests can patch a single canonical target. should_stop()/callback give the GUI
Stop button cooperative cancellation; on interrupt a coherent partial report is
still reconciled, diagnosed, and scored.
"""

import platform
import sys

from netdiag_core import runtime as rt
from netdiag_core import analysis
from netdiag_core import config
from netdiag_core.constants import DNS_HOSTS, TCP_TARGETS
from netdiag_core.probes import netinfo, sockets, ping, dns_tcp, route, throughput, webprobes, reliability
from netdiag_core.probes import wifi as wifi_probe


def full_diagnostic(args, callback=None, should_stop=None):
    # should_stop() is a cheap predicate (the GUI Stop button sets it). Checked at
    # probe boundaries so no new probe starts after a stop; the long callback-driven
    # probes interrupt mid-run via the callback raising UserInterrupted. Both paths
    # land in the except below, which still computes a coherent partial report.
    def _stopcheck():
        if should_stop and should_stop():
            raise rt.UserInterrupted("Stopped by user")

    tools = rt.check_tools()
    gateway = netinfo.detect_gateway()
    default_iface = netinfo.get_default_interface()

    results = {
        "timestamp": rt.now_iso(),
        "platform": platform.platform(),
        "os": rt.OS_NAME,
        "default_interface": default_iface,
        "gateway": gateway,
        "interface": None,
        "wifi": None,
        "ethtool": None,
        "gateway_ping": None,
        "internet_ping": [],
        "dns": [],
        "tcp": [],
        "tcp_sockets": None,
        "mtr": None,
        "speedtest": None,
        "iperf3": None,
        "bufferbloat": None,
        "download_test": None,
        "connection_test": None,
        "reliability_test": None,
        "wellknown_test": None,
        "tools": tools,
        "diagnosis": [],
        "health_score": 0,
        "interrupted": False,
        "interrupt_reason": None,
    }

    try:
        if default_iface:
            if callback:
                callback("interface", 0, 1, None, None, "running")
            iface_stats = netinfo.interface_stats(default_iface)
            if iface_stats:
                results["interface"] = iface_stats
            if callback:
                rx = iface_stats.get("rx", {}) if iface_stats else {}
                tx = iface_stats.get("tx", {}) if iface_stats else {}
                errs = rx.get("errors", 0) + tx.get("errors", 0) + rx.get("dropped", 0)
                callback("interface", 1, 1, 1 if errs == 0 else 0, errs, "done")

        if default_iface:
            if callback:
                callback("wifi", 0, 1, None, None, "running")
            wifi = wifi_probe.wifi_info(default_iface)
            if wifi:
                results["wifi"] = wifi
            if callback:
                sig = wifi.get("signal_dbm") if wifi else None
                ok = 1 if (sig is None or sig > -70) else (0 if sig < -80 else 0)
                callback("wifi", 1, 1, ok, sig, "done" if wifi else "error")

        if default_iface:
            if callback:
                callback("ethtool", 0, 1, None, None, "running")
            ethtool = netinfo.ethtool_info(default_iface)
            if ethtool:
                results["ethtool"] = ethtool
            if callback:
                ok = 1 if (ethtool and ethtool.get("duplex") == "Full") else 0
                callback("ethtool", 1, 1, ok, 0, "done" if ethtool else "error")

        if gateway:
            _stopcheck()
            if callback:
                callback("gateway", 0, args.count, None, None, "running")
            gw_result = ping.ping_burst(
                gateway, args.count, args.interval, timeout_s=args.timeout,
                ipv=4, label="gateway", quiet=args.quiet, callback=callback)
            results["gateway_ping"] = gw_result
            if callback:
                ok = gw_result.get("received", 0) if gw_result else 0
                callback("gateway", args.count, args.count, ok, gw_result.get("p95_ms", 0) if gw_result else 0, "done" if gw_result else "error")
        elif not args.quiet:
            print("No gateway detected.", flush=True)

        for host in args.hosts:
            _stopcheck()
            label = host
            if callback:
                callback(label, 0, args.count, None, None, "running")
            results["internet_ping"].append(
                ping.ping_burst(host, args.count, args.interval, timeout_s=args.timeout,
                                label=label, quiet=args.quiet, callback=callback))

        for host in DNS_HOSTS:
            if not args.quiet:
                print(f"Testing DNS: {host}", flush=True)
            if callback:
                callback(f"dns_{host}", 0, args.dns_count, None, None, "running")
            d = dns_tcp.dns_test(host, args.dns_count)
            results["dns"].append(d)
            if callback:
                ok = d.get("total", args.dns_count) - d.get("failures", 0)
                callback(f"dns_{host}", ok, args.dns_count, ok, d.get("avg_ms", 0), "done")

        for h, p in TCP_TARGETS:
            if not args.quiet:
                print(f"Testing TCP: {h}:{p}", flush=True)
            if callback:
                callback(f"tcp_{h}_{p}", 0, args.tcp_count, None, None, "running")
            t = dns_tcp.tcp_test(h, p, args.tcp_count)
            results["tcp"].append(t)
            if callback:
                ok = t.get("total", args.tcp_count) - t.get("failures", 0)
                callback(f"tcp_{h}_{p}", ok, args.tcp_count, ok, t.get("avg_ms", 0), "done")

        if default_iface:
            if callback:
                callback("tcp_sockets", 0, 1, None, None, "running")
            results["tcp_sockets"] = sockets.tcp_socket_stats(default_iface)
            if callback:
                ts = results["tcp_sockets"]
                ok = 1 if (ts and ts.get("retransmit_pct", 100) < 5) else 0
                callback("tcp_sockets", 1, 1, ok, ts.get("retransmit_pct", 0) if ts else 0, "done" if ts else "error")
            if not args.no_bufferbloat:
                _stopcheck()
                if callback:
                    callback("bufferbloat", 0, 1, None, None, "running")
                results["bufferbloat"] = throughput.bufferbloat_test(default_iface)
                if callback:
                    bb = results["bufferbloat"]
                    ok = 1 if (bb and bb.get("ratio", 99) < 2) else 0
                    callback("bufferbloat", 1, 1, ok, int((bb.get("ratio", 0) or 0) * 100), "done" if bb else "error")

        if not args.no_trace and args.hosts:
            _stopcheck()
            if not args.quiet:
                print(f"Testing route: {args.hosts[0]}", flush=True)
            if callback:
                callback("mtr", 0, 50, None, None, "running")
            results["mtr"] = route.mtr_test(args.hosts[0], count=50)
            if callback:
                m = results["mtr"]
                ok = 1 if (m and m.get("hops") and m["hops"][-1].get("loss_pct", 100) < 5) else 0
                callback("mtr", 1, 1, ok, 0, "done" if m else "error")

        if not args.no_speedtest:
            _stopcheck()
            if not args.quiet:
                print("Running speedtest...", flush=True)
            if callback:
                callback("speedtest", 0, 1, None, None, "running")
            results["speedtest"] = throughput.speedtest_result()
            if callback:
                s = results["speedtest"]
                ok = 1 if (s and s.get("download_mbps", 0) > 10) else 0
                callback("speedtest", 1, 1, ok, int(s.get("download_mbps", 0) or 0) if s else 0, "done" if s else "error")

        if not args.no_iperf:
            _stopcheck()
            if not args.quiet:
                print("Running iPerf3...", flush=True)
            if callback:
                callback("iperf3", 0, 1, None, None, "running")
            results["iperf3"] = throughput.iperf3_test()
            if callback:
                i3 = results["iperf3"]
                ok = 1 if (i3 and i3.get("available") and i3.get(" retransmits", 10) < 5) else 0
                mbits = int(i3.get("mbps", 0) or 0) if i3 else 0
                callback("iperf3", 1, 1, ok, mbits, "done" if (i3 and i3.get("available")) else "error")

        if getattr(args, "download_test", False):
            _stopcheck()
            if not args.quiet:
                print("Download test: 100 images...", flush=True)
            if callback:
                callback("download_test", 0, 100, None, None, "running")
            results["download_test"] = webprobes.download_images_test(count=100)
            if callback:
                dt = results["download_test"]
                ok = dt.get("success", 0)
                callback("download_test", ok, 100, ok, dt.get("avg_mbps", 0), "done" if dt.get("error") is None else "error")

        if getattr(args, "connection_test", False):
            _stopcheck()
            if not args.quiet:
                print("Connection test: HTTP latency + MTU probe...", flush=True)
            if callback:
                callback("http_latency", 0, 5, None, None, "running")
            results["connection_test"] = {"http_latency": webprobes.http_latency_test(count=5)}
            if callback:
                ht = results["connection_test"]["http_latency"]
                total_ok = sum(1 for h in ht if h.get("failures", 5) < 5)
                callback("http_latency", total_ok, len(ht), total_ok, 0, "done")
            if callback:
                callback("mtu_probe", 0, 1, None, None, "running")
            results["connection_test"]["mtu"] = route.mtu_probe()
            if callback:
                mp = results["connection_test"]["mtu"]
                ok = 1 if mp.get("available") else 0
                callback("mtu_probe", 1, 1, ok, mp.get("mtu", 0), "done" if mp.get("available") else "error")

        if getattr(args, "reliability_test", False):
            _stopcheck()
            cfg = config.load_config(getattr(args, "history_dir", "~/.netdiag"))
            if not args.quiet:
                print("Reliability test: intermittent connection detector...", flush=True)
            if callback:
                callback("reliability", 0, 1, None, None, "running")
            rel = reliability.reliability_test(
                targets=getattr(args, "reliability_targets", None) or cfg.get("reliability_targets"),
                samples=getattr(args, "reliability_samples", None) or cfg.get("reliability_samples", 20),
                duration_s=getattr(args, "reliability_duration", None) or cfg.get("reliability_duration", 0),
                concurrency=getattr(args, "reliability_concurrency", None) or cfg.get("reliability_concurrency", 8),
                retries=cfg.get("reliability_retries", 2),
                timeout_s=cfg.get("reliability_timeout", 5),
                callback=callback)
            results["reliability_test"] = rel
            if callback:
                ff = rel.get("first_attempt_fail_pct")
                ok = 1 if (ff is not None and ff < 10) else 0
                callback("reliability", rel.get("samples_total", 1), max(rel.get("samples_total", 1), 1),
                         ok, ff, "done" if rel.get("available") else "error")

        if getattr(args, "wellknown_test", False):
            _stopcheck()
            cfg = config.load_config(getattr(args, "history_dir", "~/.netdiag"))
            if not args.quiet:
                print("Intermittent reproduction: probing ~100 well-known sites (~2.5 min)...", flush=True)
            if callback:
                callback("wellknown", 0, 1, None, None, "running")
            wk = webprobes.wellknown_sites_test(
                duration_s=getattr(args, "wellknown_duration", None) or cfg.get("wellknown_duration", 150),
                concurrency=getattr(args, "wellknown_concurrency", None) or cfg.get("wellknown_concurrency", 12),
                callback=callback)
            results["wellknown_test"] = wk
            if callback:
                ff = wk.get("first_attempt_fail_pct")
                ok = 1 if (ff is not None and ff < 10) else 0
                callback("wellknown", wk.get("samples_total", 1), max(wk.get("samples_total", 1), 1),
                         ok, ff, "done" if wk.get("available") else "error")

    except rt.UserInterrupted as e:
        results["interrupted"] = True
        results["interrupt_reason"] = str(e)
        print(f"\nInterrupted: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        results["interrupted"] = True
        results["interrupt_reason"] = "Interrupted by user"
        print("\nInterrupted by user.", file=sys.stderr)

    # Compute the ICMP/TCP reconciliation once and cache it so diagnose(),
    # health_score(), the report, and the UI all read the same single source.
    results["icmp_reconciliation"] = analysis.reconcile_icmp(results)
    results["diagnosis"] = analysis.diagnose(results)
    results["health_score"] = analysis.health_score(results)
    return results
