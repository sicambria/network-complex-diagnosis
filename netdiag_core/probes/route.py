"""Route/path probes: native ping traceroute, MTR, and path-MTU discovery."""

import re
import statistics

from netdiag_core import runtime as rt
from netdiag_core.stats import clean_float


def _ping_traceroute(host, max_hops=30, timeout_s=3):
    hops = []
    for ttl in range(1, max_hops + 1):
        if rt.IS_LINUX:
            cmd = ["ping", "-c", "1", "-W", str(timeout_s), "-t", str(ttl), host]
        elif rt.IS_MACOS:
            cmd = ["ping", "-c", "1", "-t", str(timeout_s), "-m", str(ttl), host]
        else:
            cmd = ["ping", "-n", "1", "-w", str(timeout_s * 1000), "-i", str(ttl), host]
        rc, out, err = rt.run_cmd(cmd, timeout=timeout_s + 3)
        hop_ip = None
        rtt = None
        m = re.search(r"From ([0-9.]+)", out + "\n" + err)
        if m:
            hop_ip = m.group(1)
        m2 = re.search(r"time[=<]\s*([0-9.]+)\s*ms", out)
        if m2:
            rtt = float(m2.group(1))
        if rc == 0:
            hops.append({"hop": ttl, "loss_pct": 0, "avg_ms": rtt, "ip": hop_ip or host,
                         "raw": out.strip()[:200]})
            break
        elif hop_ip:
            hops.append({"hop": ttl, "loss_pct": 0, "avg_ms": rtt, "ip": hop_ip,
                         "raw": (out + " " + err).strip()[:200]})
        else:
            hops.append({"hop": ttl, "loss_pct": 100, "avg_ms": None, "ip": None,
                         "raw": "*"})
        if ttl >= max_hops:
            break
    return {"tool": "ping_traceroute", "host": host, "rc": 0, "hops": hops,
            "stdout": "", "message": "Native ping-based traceroute (Plan B)"}


def mtr_test(host, count=50):
    if rt.has_tool("mtr"):
        rc, out, err = rt.run_cmd(["mtr", "-r", "-c", str(count), "-w", host], timeout=120)
        hops = []
        if rc == 0:
            for line in out.split("\n"):
                if line.startswith("HOST:") or line.startswith("Start:"):
                    continue
                m = re.match(r"\s*(\d+)\.", line.strip())
                if m:
                    hop_num = int(m.group(1))
                    parts = line.split()
                    loss_pct = 0
                    if len(parts) > 2 and "%" in parts[2]:
                        try: loss_pct = float(parts[2].replace("%", ""))
                        except: pass
                    avg = None
                    for p in parts:
                        try:
                            v = float(p)
                            if 0.1 < v < 10000:
                                avg = v
                                break
                        except:
                            continue
                    hops.append({"hop": hop_num, "loss_pct": loss_pct, "avg_ms": avg,
                                 "raw": line.strip()[:200]})
        return {"tool": "mtr", "host": host, "rc": rc, "hops": hops, "stdout": out, "stderr": err}
    if rt.has_tool("traceroute"):
        rc, out, err = rt.run_cmd(["traceroute", "-n", "-m", "30", host], timeout=90)
        hops = []
        if rc == 0:
            for line in out.split("\n"):
                m = re.match(r"\s*(\d+)\s+", line)
                if m:
                    hop_num = int(m.group(1))
                    times = re.findall(r"([\d.]+)\s*ms", line)
                    rtts = [float(t) for t in times]
                    lost = line.count("*") if "*" in line else 0
                    total = lost + len(rtts)
                    loss_pct = (lost / total * 100) if total else 0
                    avg = clean_float(statistics.mean(rtts)) if rtts else None
                    hops.append({"hop": hop_num, "loss_pct": clean_float(loss_pct),
                                 "avg_ms": avg, "raw": line.strip()[:200]})
        return {"tool": "traceroute", "host": host, "rc": rc, "hops": hops, "stdout": out}
    if rt.IS_WINDOWS:
        rc, out, err = rt.run_cmd(["tracert", "-h", "30", host], timeout=90)
        hops = []
        if rc == 0:
            for line in out.split("\n"):
                m = re.match(r"\s*(\d+)\s+", line)
                if m:
                    hop_num = int(m.group(1))
                    times = re.findall(r"([\d.]+)\s*ms", line)
                    rtts = [float(t) for t in times]
                    lost = line.count("*") if "*" in line else 0
                    total = lost + len(rtts)
                    loss_pct = (lost / total * 100) if total else 0
                    avg = clean_float(statistics.mean(rtts)) if rtts else None
                    hops.append({"hop": hop_num, "loss_pct": clean_float(loss_pct),
                                 "avg_ms": avg, "raw": line.strip()[:200]})
        return {"tool": "tracert", "host": host, "rc": rc, "hops": hops, "stdout": out}
    return _ping_traceroute(host)


def mtu_probe(host="1.1.1.1", max_size=1500):
    import shutil
    if not rt.has_tool("ping"):
        return {"available": False, "reason": "ping not found"}
    low, high = 68, max_size
    last_ok = low
    while low <= high:
        mid = (low + high) // 2
        if rt.IS_LINUX:
            cmd = ["ping", "-M", "do", "-c", "1", "-W", "2", "-s", str(mid), host]
        elif rt.IS_MACOS:
            cmd = ["ping", "-D", "-c", "1", "-t", "2", "-s", str(mid), host]
        else:
            cmd = ["ping", "-f", "-n", "1", "-w", "2000", "-l", str(mid), host]
        rc, _, _ = rt.run_cmd(cmd, timeout=5)
        if rc == 0:
            last_ok = mid
            low = mid + 1
        else:
            high = mid - 1
    return {"available": True, "mtu": last_ok + 28, "payload_size": last_ok}
