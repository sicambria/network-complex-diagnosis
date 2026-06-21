"""Throughput probes: speedtest, iperf3, and bufferbloat detection."""

import json
import re

from netdiag_core import runtime as rt
from netdiag_core.constants import IPERF_SERVER
from netdiag_core.stats import clean_float
from netdiag_core.probes import ping


def speedtest_result():
    if rt.has_tool("speedtest"):
        rc, out, err = rt.run_cmd(["speedtest", "--format=json"], timeout=180)
        if rc == 0:
            try:
                data = json.loads(out)
                return {"available": True, "tool": "speedtest",
                        "download_mbps": clean_float(data.get("download", {}).get("bandwidth", 0) * 8 / 1e6),
                        "upload_mbps": clean_float(data.get("upload", {}).get("bandwidth", 0) * 8 / 1e6),
                        "latency_ms": clean_float(data.get("ping", {}).get("latency")),
                        "jitter_ms": clean_float(data.get("ping", {}).get("jitter")),
                        "server": data.get("server", {}).get("name", "unknown"),
                        "isp": data.get("isp", "unknown"),
                        "data": data}
            except Exception:
                return {"available": True, "tool": "speedtest", "raw": out, "error": "parse failed"}
        return {"available": True, "tool": "speedtest", "rc": rc, "error": err or out}
    if rt.has_tool("speedtest-cli"):
        rc, out, err = rt.run_cmd(["speedtest-cli", "--json"], timeout=180)
        if rc == 0:
            try:
                data = json.loads(out)
                return {"available": True, "tool": "speedtest-cli",
                        "download_mbps": clean_float(data.get("download", 0) / 1e6),
                        "upload_mbps": clean_float(data.get("upload", 0) / 1e6),
                        "latency_ms": clean_float(data.get("ping")),
                        "jitter_ms": None, "server": data.get("server", {}).get("name", "unknown"),
                        "isp": data.get("client", {}).get("isp", "unknown"),
                        "data": data}
            except Exception:
                return {"available": True, "tool": "speedtest-cli", "raw": out, "error": "parse failed"}
        return {"available": True, "tool": "speedtest-cli", "rc": rc, "error": err or out}
    return {"available": False, "message": "Install Ookla speedtest or speedtest-cli."}


def iperf3_test(server=None, duration=10):
    if not rt.has_tool("iperf3"):
        return {"available": False, "reason": "iperf3 not installed"}
    srv = server or IPERF_SERVER
    rc, out, err = rt.run_cmd(["iperf3", "-c", srv, "-t", str(duration), "-J"], timeout=duration + 30)
    if rc != 0:
        return {"available": True, "error": err or out, "rc": rc}
    try:
        data = json.loads(out)
        end = data.get("end", {})
        sender = end.get("sum_sent", {}) or end.get("sum", {})
        receiver = end.get("sum_received", {}) or end.get("sum", {})
        return {"available": True, "server": srv,
                "download_mbps": clean_float(receiver.get("bits_per_second", 0) / 1e6),
                "upload_mbps": clean_float(sender.get("bits_per_second", 0) / 1e6),
                "retransmits": sender.get("retransmits", 0),
                "retransmit_pct": clean_float(sender.get("retransmits", 0) / max(1, sender.get("bytes", 1)) * 100),
                "cwnd_avg": clean_float(sender.get("sender", {}).get("max_tcp_cwnd", 0)),
                "data": data}
    except Exception:
        return {"available": True, "error": "parse failed", "raw": out}


def bufferbloat_test(iface):
    if not rt.IS_LINUX:
        result = {"available": False, "reason": "Bufferbloat detection requires Linux (tc)"}
        if rt.has_tool("iperf3"):
            ping_before = ping.ping_once("1.1.1.1", timeout_s=2)
            rtt_idle = ping_before.get("rtt_ms")
            rc, _, _ = rt.run_cmd(["iperf3", "-c", IPERF_SERVER, "-t", "8", "-P", "4"], timeout=30)
            ping_during = ping.ping_once("1.1.1.1", timeout_s=4)
            rtt_loaded = ping_during.get("rtt_ms")
            if rtt_idle and rtt_loaded and rtt_idle > 0:
                ratio = rtt_loaded / rtt_idle
                result["ratio"] = clean_float(ratio)
                result["rtt_idle_ms"] = rtt_idle
                result["rtt_loaded_ms"] = rtt_loaded
        return result
    if not iface:
        return {"available": False, "reason": "No interface"}
    rc, out, _ = rt.run_cmd(["tc", "-s", "qdisc", "show", "dev", iface], timeout=10)
    if rc != 0:
        return {"available": False, "reason": f"tc failed: rc={rc}"}
    backlog = 0
    drops = 0
    overlimits = 0
    for line in out.split("\n"):
        m = re.search(r"backlog\s+(\d+)", line)
        if m:
            try:
                backlog += int(m.group(1))
            except:
                pass
        m = re.search(r"drops?\s+(\d+)", line)
        if m:
            try:
                drops += int(m.group(1))
            except:
                pass
        m = re.search(r"overlimits?\s+(\d+)", line)
        if m:
            try:
                overlimits += int(m.group(1))
            except:
                pass
    result = {"available": True, "interface": iface,
              "backlog_bytes": backlog, "drops": drops, "overlimits": overlimits}
    if rt.has_tool("iperf3"):
        ping_before = ping.ping_once("1.1.1.1", timeout_s=2)
        rtt_idle = ping_before.get("rtt_ms")
        rc, _, _ = rt.run_cmd(["iperf3", "-c", IPERF_SERVER, "-t", "6", "-P", "4"], timeout=30)
        ping_during = ping.ping_once("1.1.1.1", timeout_s=4)
        rtt_loaded = ping_during.get("rtt_ms")
        if rtt_idle and rtt_loaded and rtt_idle > 0:
            result["ratio"] = clean_float(rtt_loaded / rtt_idle)
            result["rtt_idle_ms"] = rtt_idle
            result["rtt_loaded_ms"] = rtt_loaded
    return result
