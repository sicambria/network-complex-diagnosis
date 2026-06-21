"""Background live-monitor sampler: per-layer 1 Hz probing, outage tracking, and hints."""

import threading
import collections
import time

from netdiag_core import runtime as rt
from netdiag_core.probes import ping
from netdiag_core.probes import netinfo
from netdiag_core import config
from netdiag_core.stats import series_stats, clean_float, jitter_ms
from netdiag_core.constants import TCP_TARGETS, DEFAULT_HOSTS, DNS_HOSTS


MONITOR_WINDOW = 180  # ~3 minutes of samples at 1 Hz
MONITOR_LOCK = threading.Lock()
MONITOR_STATE = {
    "running": False,
    "thread": None,
    "samples": collections.deque(maxlen=MONITOR_WINDOW),
    "events": collections.deque(maxlen=50),
    "outages": {},
    "started_at": None,
    "targets": None,
}


def monitor_targets():
    cfg = config.load_config()
    tcp_target = cfg.get("monitor_tcp_target") or list(TCP_TARGETS[0])
    return {
        "gateway": netinfo.detect_gateway(),
        "external": cfg.get("monitor_external_hosts") or list(DEFAULT_HOSTS[:2]),
        "dns_host": cfg.get("monitor_dns_host") or DNS_HOSTS[0],
        "tcp_host": tcp_target[0],
        "tcp_port": tcp_target[1],
        "interval": cfg.get("monitor_interval", 1.0),
    }


def monitor_sample(targets):
    sample = {"ts": rt.now_iso()}
    if targets.get("gateway"):
        r = ping.ping_once(targets["gateway"], timeout_s=1)
        sample["gateway"] = {"ok": r["ok"], "rtt_ms": r["rtt_ms"]}
    else:
        sample["gateway"] = None
    ext = {}
    for host in targets["external"]:
        r = ping.ping_once(host, timeout_s=1)
        ext[host] = {"ok": r["ok"], "rtt_ms": r["rtt_ms"]}
    sample["external"] = ext
    dns = ping.resolve_all(targets["dns_host"])
    sample["dns"] = {"ok": dns["ok"], "rtt_ms": None}
    tcp = ping._tcp_ping(targets["tcp_host"], port=targets["tcp_port"], timeout_s=1)
    sample["tcp"] = {"ok": tcp["ok"], "rtt_ms": tcp["rtt_ms"]}
    return sample


def _flatten_sample(sample):
    flat = {}
    if sample.get("gateway") is not None:
        flat["gateway"] = sample["gateway"]
    for host, v in sample.get("external", {}).items():
        flat[f"external:{host}"] = v
    if sample.get("dns") is not None:
        flat["dns"] = sample["dns"]
    if sample.get("tcp") is not None:
        flat["tcp"] = sample["tcp"]
    return flat


def _update_outages(state, sample):
    flat = _flatten_sample(sample)
    for key, result in flat.items():
        outage = state["outages"].get(key)
        if not result.get("ok"):
            if outage is None:
                state["outages"][key] = {"target": key, "start": sample["ts"], "count": 1}
            else:
                outage["count"] += 1
        elif outage is not None:
            state["events"].append({
                "target": key,
                "start": outage["start"],
                "end": sample["ts"],
                "consecutive_failures": outage["count"],
            })
            del state["outages"][key]


def monitor_loop(state):
    state["targets"] = monitor_targets()
    refresh_at = time.monotonic() + 60
    while state["running"]:
        try:
            sample = monitor_sample(state["targets"])
            with MONITOR_LOCK:
                state["samples"].append(sample)
                _update_outages(state, sample)
        except Exception as e:
            rt.log.error("monitor sample error: %s", str(e))
        interval = state["targets"].get("interval", 1.0)
        if time.monotonic() >= refresh_at:
            state["targets"] = monitor_targets()
            refresh_at = time.monotonic() + 60
        time.sleep(max(0.2, interval))


def monitor_start():
    with MONITOR_LOCK:
        if MONITOR_STATE["running"]:
            return False
        MONITOR_STATE["running"] = True
        MONITOR_STATE["samples"].clear()
        MONITOR_STATE["events"].clear()
        MONITOR_STATE["outages"] = {}
        MONITOR_STATE["started_at"] = rt.now_iso()
    t = threading.Thread(target=monitor_loop, args=(MONITOR_STATE,), daemon=True)
    MONITOR_STATE["thread"] = t
    t.start()
    return True


def monitor_stop():
    with MONITOR_LOCK:
        was_running = MONITOR_STATE["running"]
        MONITOR_STATE["running"] = False
    return was_running


def _target_stats(samples, key):
    results = [r for s in samples if (r := _flatten_sample(s).get(key)) is not None]
    total = len(results)
    if not total:
        return {"count": 0, "min_ms": None, "avg_ms": None, "max_ms": None,
                "stdev_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None,
                "loss_pct": None, "jitter_ms": None, "samples": 0}
    ok_count = sum(1 for r in results if r["ok"])
    rtts = [r["rtt_ms"] for r in results if r["ok"] and r.get("rtt_ms") is not None]
    stats = series_stats(rtts)
    stats["loss_pct"] = clean_float(100 * (total - ok_count) / total)
    stats["jitter_ms"] = jitter_ms(rtts)
    stats["samples"] = total
    return stats


def monitor_snapshot():
    with MONITOR_LOCK:
        samples = list(MONITOR_STATE["samples"])
        events = list(MONITOR_STATE["events"])[-20:]
        running = MONITOR_STATE["running"]
        started_at = MONITOR_STATE["started_at"]
        active_outages = list(MONITOR_STATE["outages"].values())
    if not samples:
        return {
            "running": running, "started_at": started_at, "sample_count": 0,
            "targets": {}, "events": [], "active_outages": [], "latest": None, "hints": [],
        }
    keys = sorted(_flatten_sample(samples[-1]).keys())
    targets_stats = {key: _target_stats(samples, key) for key in keys}
    snapshot = {
        "running": running,
        "started_at": started_at,
        "sample_count": len(samples),
        "targets": targets_stats,
        "events": list(reversed(events)),
        "active_outages": active_outages,
        "latest": samples[-1],
    }
    snapshot["hints"] = monitor_diagnose(snapshot)
    return snapshot


def monitor_diagnose(snapshot):
    hints = []
    targets = snapshot.get("targets", {})
    if snapshot.get("sample_count", 0) < 5:
        return hints
    gw = targets.get("gateway")
    ext_keys = [k for k in targets if k.startswith("external:")]
    ext_loss = [targets[k]["loss_pct"] for k in ext_keys if targets[k].get("loss_pct") is not None]
    dns = targets.get("dns")
    tcp = targets.get("tcp")

    if gw and gw.get("loss_pct") is not None:
        if gw["loss_pct"] == 0 and ext_loss and max(ext_loss) >= 5:
            hints.append({"severity": "warning",
                           "text": f"Local link is clean but {max(ext_loss):.0f}% loss reaching the internet — "
                                   f"likely an ISP/upstream problem, not your WiFi or router."})
        elif 0 < gw["loss_pct"] < 100:
            hints.append({"severity": "warning",
                           "text": f"Intermittent loss to your router ({gw['loss_pct']:.0f}%) even if signal "
                                   f"looks fine — check for interference, channel congestion, or cabling."})
        if gw.get("jitter_ms") and gw["jitter_ms"] >= 30:
            hints.append({"severity": "info",
                           "text": f"High jitter to your router ({gw['jitter_ms']:.0f} ms) — possible "
                                   f"bufferbloat, interference, or a saturated link."})

    if dns and dns.get("loss_pct"):
        hints.append({"severity": "warning",
                       "text": f"DNS resolution failed {dns['loss_pct']:.0f}% of the time — try an "
                               f"alternate DNS server (1.1.1.1, 8.8.8.8)."})

    if (tcp and tcp.get("loss_pct") is not None and tcp["loss_pct"] > 0
            and gw and gw.get("loss_pct") == 0):
        hints.append({"severity": "warning",
                       "text": f"TCP connections failing ({tcp['loss_pct']:.0f}%) despite ping working — "
                               f"possible firewall, captive portal, or carrier-grade NAT issue."})

    if ext_loss and any(0 < pct < 100 for pct in ext_loss) and not hints:
        hints.append({"severity": "info",
                       "text": "Sporadic packet loss to the internet detected — keep monitoring to "
                               "see if it correlates with WiFi signal drops."})

    if not hints:
        hints.append({"severity": "clean", "text": "No intermittent issues detected in the current sample window."})
    return hints
