"""Ping probes: platform-adaptive ping command, RTT parsing, single/burst pings, address resolution, and ping classification."""

import re
import socket
import time

from netdiag_core import runtime as rt
from netdiag_core.stats import clean_float, series_stats, jitter_ms


def ping_command(host, timeout_s=2, ipv=None):
    timeout_s = max(1, int(round(timeout_s)))
    if rt.IS_LINUX:
        cmd = ["ping", "-c", "1", "-W", str(timeout_s)]
        if ipv == 4:
            cmd.insert(1, "-4")
        elif ipv == 6:
            cmd.insert(1, "-6")
    elif rt.IS_MACOS:
        cmd = ["ping", "-c", "1", "-t", str(timeout_s)]
        if ipv == 4:
            cmd.insert(1, "-4")
        elif ipv == 6:
            cmd.insert(1, "-6")
    else:
        cmd = ["ping", "-n", "1", "-w", str(timeout_s * 1000)]
        if ipv == 4:
            cmd.insert(1, "-4")
        elif ipv == 6:
            cmd.insert(1, "-6")
    cmd.append(host)
    return cmd


def parse_rtt_ms(text):
    for pat in [
        r"time[=<]\s*([0-9.]+)\s*ms",
        r"rtt min/avg/max/mdev = [0-9.]+/([0-9.]+)/",
        r"round-trip min/avg/max/stddev = [0-9.]+/([0-9.]+)/",
    ]:
        m = re.search(pat, text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _tcp_ping(host, port=443, timeout_s=2, ipv=None):
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            rtt = (time.perf_counter() - t0) * 1000
        rt.log_activity("socket", f"TCP connect {host}:{port}", 0, rtt, ok=True)
        return {"ok": True, "rtt_ms": clean_float(rtt), "rc": 0, "raw": "", "_fallback": "tcp"}
    except Exception as e:
        rt.log_activity("socket", f"TCP connect {host}:{port}", 999, (time.perf_counter() - t0) * 1000, ok=False)
        return {"ok": False, "rtt_ms": None, "rc": 999, "raw": str(e), "_fallback": "tcp"}


def ping_once(host, timeout_s=2, ipv=None):
    cmd = ping_command(host, timeout_s=timeout_s, ipv=ipv)
    rc, out, err = rt.run_cmd(cmd, timeout=timeout_s + 3)
    text = (out + "\n" + err).strip()
    rtt = parse_rtt_ms(text)
    if rc == 0 and rtt is not None:
        return {"ok": True, "rtt_ms": rtt, "rc": rc, "raw": text[-500:]}
    if not rt.has_tool("ping"):
        return _tcp_ping(host, timeout_s=timeout_s, ipv=ipv)
    return {"ok": False, "rtt_ms": None, "rc": rc, "raw": text[-500:]}


def ping_burst(host, count, interval, timeout_s=2, ipv=None, label=None, quiet=False, callback=None):
    samples = []
    rtts = []
    lost = 0
    label = label or host
    if not quiet:
        print(f"Testing {label}: {count} pings, interval={interval}s", flush=True)
    try:
        for seq in range(1, count + 1):
            ts = rt.now_iso()
            result = ping_once(host, timeout_s=timeout_s, ipv=ipv)
            if result["ok"]:
                rtts.append(result["rtt_ms"])
                status = f"{result['rtt_ms']:.1f} ms"
            else:
                lost += 1
                status = "lost"
            samples.append({
                "timestamp": ts, "seq": seq, "label": label,
                "host": host, "ipv": ipv or "auto", "ok": result["ok"],
                "rtt_ms": result["rtt_ms"], "rc": result["rc"],
            })
            if callback:
                callback(label, seq, count, result["ok"], result["rtt_ms"])
            if not quiet:
                print(f"  {label} {seq}/{count}: {status}", flush=True)
            if seq < count and interval > 0:
                time.sleep(interval)
    except KeyboardInterrupt:
        raise rt.UserInterrupted(f"Interrupted while testing {label}")
    sent = len(samples)
    return {
        "label": label, "host": host, "ipv": ipv or "auto",
        "sent": sent, "received": sent - lost,
        "loss_pct": clean_float(100 * lost / sent) if sent else None,
        "jitter_ms": jitter_ms(rtts), **series_stats(rtts),
        "samples": samples, "interrupted": sent < count,
    }


def resolve_all(host):
    t0 = time.perf_counter()
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        rt.log_activity("socket", f"DNS resolve {host}", 999, (time.perf_counter() - t0) * 1000, ok=False)
        return {"host": host, "ok": False, "error": str(e), "addresses": []}
    rt.log_activity("socket", f"DNS resolve {host}", 0, (time.perf_counter() - t0) * 1000, ok=True)
    addresses = []
    seen = set()
    for family, _, _, _, sockaddr in infos:
        addr = sockaddr[0]
        key = (family, addr)
        if key in seen:
            continue
        seen.add(key)
        version = 4 if family == socket.AF_INET else (6 if family == socket.AF_INET6 else None)
        addresses.append({"ip": addr, "version": version})
    return {"host": host, "ok": True, "addresses": addresses}


def classify_ping(row):
    loss = row.get("loss_pct") or 0
    p95 = row.get("p95_ms") or 0
    jitter = row.get("jitter_ms") or 0
    if loss >= 5:
        return "bad_loss"
    if loss >= 1:
        return "some_loss"
    if p95 >= 300:
        return "bad_latency_spikes"
    if p95 >= 150:
        return "latency_spikes"
    if jitter >= 80:
        return "high_jitter"
    return "clean"
