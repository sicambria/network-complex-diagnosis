#!/usr/bin/env python3
"""
NetDiag — all-in-one internet diagnostics suite.

Platform-agnostic, Linux-enhanced. Reuses existing CLI tools.
Zero deps for CLI mode. Optional fastapi+uvicorn for web GUI.

Usage:
  python3 netdiag.py                    # CLI mode
  python3 netdiag.py --gui              # start web UI on http://localhost:8080
  python3 netdiag.py --daemon           # continuous monitoring + web UI
  python3 netdiag.py --count 120 --int 1 # long test

SPDX-License-Identifier: AGPL-3.0-only
Copyright (C) 2024  Sicambria

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, version 3 of the License.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import argparse
import collections
import csv
import json
import logging
import os
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("netdiag")

DEFAULT_HOSTS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "google.com"]
DNS_HOSTS = ["google.com", "cloudflare.com", "quad9.net"]
TCP_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443), ("google.com", 443)]
IPERF_SERVER = "iperf3.moji.fr"

APT_PACKAGES = {
    "ping": "iputils-ping",
    "ip": "iproute2",
    "traceroute": "traceroute",
    "mtr": "mtr-tiny",
    "speedtest-cli": "speedtest-cli",
}
OS_NAME = platform.system()
IS_LINUX = OS_NAME == "Linux"
IS_MACOS = OS_NAME == "Darwin"
IS_WINDOWS = OS_NAME == "Windows"


class UserInterrupted(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


ACTIVITY_LOG = collections.deque(maxlen=200)
ACTIVITY_LOCK = threading.Lock()


def log_activity(kind, label, rc, duration_ms, ok=None):
    entry = {
        "ts": now_iso(),
        "kind": kind,
        "label": label,
        "rc": rc,
        "ok": ok if ok is not None else (rc == 0),
        "duration_ms": clean_float(duration_ms),
    }
    with ACTIVITY_LOCK:
        ACTIVITY_LOG.append(entry)


def get_activity_log(limit=50):
    with ACTIVITY_LOCK:
        items = list(ACTIVITY_LOG)[-limit:]
    return list(reversed(items))


def run_cmd(cmd, timeout=30):
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        rc, out, err = p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"Timeout after {timeout}s"
    except Exception as e:
        rc, out, err = 999, "", str(e)
    log_activity("cmd", " ".join(str(c) for c in cmd), rc, (time.perf_counter() - t0) * 1000)
    return rc, out, err


def has_tool(name):
    return shutil.which(name) is not None


def detect_package_manager():
    for t in ["apt", "dnf", "yum", "pacman", "zypper"]:
        if has_tool(t):
            return t
    return None


def install_hint(missing):
    if not missing:
        return None
    pm = detect_package_manager()
    if pm == "apt":
        pkgs = sorted({APT_PACKAGES.get(x, x) for x in missing})
        return "sudo apt update && sudo apt install -y " + " ".join(pkgs)
    if pm == "dnf":
        return "sudo dnf install -y " + " ".join(sorted(missing))
    if pm == "yum":
        return "sudo yum install -y " + " ".join(sorted(missing))
    if pm == "pacman":
        return "sudo pacman -S " + " ".join(sorted(missing))
    if pm == "zypper":
        return "sudo zypper install " + " ".join(sorted(missing))
    return "Install missing tools manually: " + ", ".join(sorted(missing))


def check_tools():
    optional = ["mtr", "traceroute", "speedtest", "speedtest-cli", "iperf3"]
    if IS_LINUX:
        required = ["ping", "ip"]
        optional = optional + ["iw", "ethtool"]
    elif IS_MACOS:
        required = ["ping"]
        optional = optional + ["airport"]
    else:
        required = ["ping"]
        optional = optional + ["netsh"]
    missing_required = [x for x in required if not has_tool(x)]
    missing_optional = [x for x in optional if not has_tool(x)]
    return {
        "platform": OS_NAME,
        "checked_required": required,
        "checked_optional": optional,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "install_hint_required": install_hint(missing_required),
        "install_hint_optional": install_hint(missing_optional),
    }


def ping_command(host, timeout_s=2, ipv=None):
    timeout_s = max(1, int(round(timeout_s)))
    if IS_LINUX:
        cmd = ["ping", "-c", "1", "-W", str(timeout_s)]
        if ipv == 4:
            cmd.insert(1, "-4")
        elif ipv == 6:
            cmd.insert(1, "-6")
    elif IS_MACOS:
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
        log_activity("socket", f"TCP connect {host}:{port}", 0, rtt, ok=True)
        return {"ok": True, "rtt_ms": clean_float(rtt), "rc": 0, "raw": "", "_fallback": "tcp"}
    except Exception as e:
        log_activity("socket", f"TCP connect {host}:{port}", 999, (time.perf_counter() - t0) * 1000, ok=False)
        return {"ok": False, "rtt_ms": None, "rc": 999, "raw": str(e), "_fallback": "tcp"}


def ping_once(host, timeout_s=2, ipv=None):
    cmd = ping_command(host, timeout_s=timeout_s, ipv=ipv)
    rc, out, err = run_cmd(cmd, timeout=timeout_s + 3)
    text = (out + "\n" + err).strip()
    rtt = parse_rtt_ms(text)
    if rc == 0 and rtt is not None:
        return {"ok": True, "rtt_ms": rtt, "rc": rc, "raw": text[-500:]}
    if not has_tool("ping"):
        return _tcp_ping(host, timeout_s=timeout_s, ipv=ipv)
    return {"ok": False, "rtt_ms": None, "rc": rc, "raw": text[-500:]}


def percentile(values, pct):
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def clean_float(value):
    if value is None:
        return None
    return round(float(value), 2)


def series_stats(values):
    if not values:
        return {"count": 0, "min_ms": None, "avg_ms": None, "max_ms": None,
                "stdev_ms": None, "p50_ms": None, "p95_ms": None, "p99_ms": None}
    return {
        "count": len(values),
        "min_ms": clean_float(min(values)),
        "avg_ms": clean_float(statistics.mean(values)),
        "max_ms": clean_float(max(values)),
        "stdev_ms": clean_float(statistics.pstdev(values)) if len(values) > 1 else 0,
        "p50_ms": clean_float(percentile(values, 50)),
        "p95_ms": clean_float(percentile(values, 95)),
        "p99_ms": clean_float(percentile(values, 99)),
    }


def jitter_ms(values):
    if len(values) < 2:
        return None
    diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
    return clean_float(statistics.mean(diffs))


def ping_burst(host, count, interval, timeout_s=2, ipv=None, label=None, quiet=False, callback=None):
    samples = []
    rtts = []
    lost = 0
    label = label or host
    if not quiet:
        print(f"Testing {label}: {count} pings, interval={interval}s", flush=True)
    try:
        for seq in range(1, count + 1):
            ts = now_iso()
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
        raise UserInterrupted(f"Interrupted while testing {label}")
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
        log_activity("socket", f"DNS resolve {host}", 999, (time.perf_counter() - t0) * 1000, ok=False)
        return {"host": host, "ok": False, "error": str(e), "addresses": []}
    log_activity("socket", f"DNS resolve {host}", 0, (time.perf_counter() - t0) * 1000, ok=True)
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


def dns_test(host, count=10):
    times = []
    failures = 0
    addresses = []
    for _ in range(count):
        t0 = time.perf_counter()
        result = resolve_all(host)
        elapsed = (time.perf_counter() - t0) * 1000
        if result["ok"]:
            times.append(elapsed)
            addresses.extend(result["addresses"])
        else:
            failures += 1
    unique = []
    seen = set()
    for item in addresses:
        key = (item["ip"], item["version"])
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return {
        "host": host, "queries": count, "failures": failures,
        "failure_pct": clean_float(100 * failures / count),
        "addresses": unique, **series_stats(times),
    }


def tcp_test(host, port, count=10, timeout_s=3):
    times = []
    failures = 0
    errors = {}
    for _ in range(count):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout_s):
                times.append((time.perf_counter() - t0) * 1000)
        except Exception as e:
            failures += 1
            name = type(e).__name__
            errors[name] = errors.get(name, 0) + 1
    return {
        "host": host, "port": port, "attempts": count,
        "failures": failures, "failure_pct": clean_float(100 * failures / count),
        "errors": errors, **series_stats(times),
    }


def _parse_proc_net_route():
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1] == "00000000" and parts[2] != "00000000":
                    gw_hex = parts[2]
                    gw = ".".join(str(int(gw_hex[i:i+2], 16)) for i in (6,4,2,0))
                    if gw != "0.0.0.0":
                        return gw
    except (OSError, IOError, ValueError, IndexError):
        pass
    return None


def _parse_proc_net_route_iface():
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    return parts[0]
    except (OSError, IOError, IndexError):
        pass
    return None


def detect_gateway():
    if IS_LINUX:
        rc, out, _ = run_cmd(["ip", "-4", "route", "show", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"default via ([0-9.]+)", out)
            if m:
                return m.group(1)
        gw = _parse_proc_net_route()
        if gw:
            return gw
    elif IS_MACOS:
        rc, out, _ = run_cmd(["route", "-n", "get", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"gateway: ([0-9.]+)", out)
            return m.group(1) if m else None
    else:
        rc, out, _ = run_cmd(["netstat", "-rn"], timeout=10)
        if rc == 0:
            for line in out.split("\n"):
                if "0.0.0.0" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "0.0.0.0" and i + 1 < len(parts):
                            candidate = parts[i + 1]
                            if candidate != "0.0.0.0":
                                return candidate
    return None


def get_default_interface():
    if IS_LINUX:
        rc, out, _ = run_cmd(["ip", "route", "show", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"\bdev\s+(\S+)", out)
            if m:
                return m.group(1)
        iface = _parse_proc_net_route_iface()
        if iface:
            return iface
    elif IS_MACOS:
        rc, out, _ = run_cmd(["route", "-n", "get", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"interface: (\S+)", out)
            return m.group(1) if m else None
    else:
        return None
    return None


def detect_wireless_interface():
    if IS_LINUX:
        if has_tool("iw"):
            rc, out, _ = run_cmd(["iw", "dev"], timeout=10)
            if rc == 0:
                for m in re.finditer(r"Interface\s+(\S+)", out):
                    return m.group(1)
        try:
            proc = Path("/proc/net/wireless")
            if proc.exists():
                for line in proc.read_text().split("\n")[2:]:
                    parts = line.split(":")
                    if parts and parts[0].strip():
                        return parts[0].strip()
        except Exception:
            pass
        return None
    elif IS_MACOS:
        return get_default_interface()
    else:
        rc, out, _ = run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=10)
        if rc == 0:
            for line in out.split("\n"):
                if "Name" in line:
                    m = re.search(r":\s*(\S+)", line)
                    if m:
                        return m.group(1)
        return None


def _sysfs_interface_stats(iface):
    base = Path(f"/sys/class/net/{iface}/statistics")
    if not base.is_dir():
        return None
    rx = {"errors": 0, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0}
    tx = {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}
    mapping = {
        "rx_errors": ("rx", "errors"), "tx_errors": ("tx", "errors"),
        "rx_dropped": ("rx", "dropped"), "tx_dropped": ("tx", "dropped"),
        "rx_over_errors": ("rx", "overruns"), "tx_carrier_errors": ("tx", "carrier"),
        "rx_frame_errors": ("rx", "frame"),
    }
    for name, (dir_, key) in mapping.items():
        p = base / name
        try:
            v = int(p.read_text().strip())
            if dir_ == "rx":
                rx[key] = v
            else:
                tx[key] = v
        except (OSError, IOError, ValueError):
            pass
    return {"available": True, "interface": iface, "rx": rx, "tx": tx}


def interface_stats(iface):
    if not iface:
        return {"available": False, "reason": "No interface detected"}
    if IS_LINUX:
        rc, out, _ = run_cmd(["ip", "-s", "link", "show", "dev", iface], timeout=10)
        if rc == 0:
            rx = {"errors": 0, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0}
            tx = {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}
            lines = out.split("\n")
            in_rx = False
            in_tx = False
            for line in lines:
                if "RX:" in line:
                    in_rx = True
                    in_tx = False
                    parts = line.split()
                    for p in parts:
                        if "errors" in p:
                            try: rx["errors"] = int(p.split(":")[1])
                            except: pass
                        elif "dropped" in p:
                            try: rx["dropped"] = int(p.split(":")[1])
                            except: pass
                        elif "overruns" in p:
                            try: rx["overruns"] = int(p.split(":")[1])
                            except: pass
                        elif "frame" in p:
                            try: rx["frame"] = int(p.split(":")[1])
                            except: pass
                    continue
                if "TX:" in line:
                    in_rx = False
                    in_tx = True
                    parts = line.split()
                    for p in parts:
                        if "errors" in p:
                            try: tx["errors"] = int(p.split(":")[1])
                            except: pass
                        elif "dropped" in p:
                            try: tx["dropped"] = int(p.split(":")[1])
                            except: pass
                        elif "overruns" in p:
                            try: tx["overruns"] = int(p.split(":")[1])
                            except: pass
                        elif "carrier" in p:
                            try: tx["carrier"] = int(p.split(":")[1])
                            except: pass
                    continue
                if in_rx and "carrier" in line:
                    try: rx["carrier"] = int(re.search(r"carrier\s+(\d+)", line).group(1))
                    except: pass
            return {"available": True, "interface": iface, "rx": rx, "tx": tx}
        fallback = _sysfs_interface_stats(iface)
        if fallback:
            return fallback
        return {"available": False, "reason": f"ip command failed: rc={rc}"}
    elif IS_MACOS:
        rc, out, _ = run_cmd(["ifconfig", iface], timeout=10)
        if rc != 0:
            return {"available": False, "reason": f"ifconfig failed: rc={rc}"}
        rx_errors = 0
        tx_errors = 0
        rx_dropped = 0
        tx_dropped = 0
        for line in out.split("\n"):
            if "iperr" in line or "ierrors" in line:
                try: rx_errors = int(re.search(r"(\d+)", line).group(1))
                except: pass
            if "oerrors" in line:
                try: tx_errors = int(re.search(r"(\d+)", line).group(1))
                except: pass
        return {"available": True, "interface": iface,
                "rx": {"errors": rx_errors, "dropped": rx_dropped, "overruns": 0, "frame": 0, "carrier": 0},
                "tx": {"errors": tx_errors, "dropped": tx_dropped, "overruns": 0, "carrier": 0}}
    else:
        rc, out, _ = run_cmd(["netstat", "-e"], timeout=10)
        if rc != 0:
            return {"available": False, "reason": f"netstat failed: rc={rc}"}
        rx_errors = 0
        for line in out.split("\n"):
            if "Errors" in line and "Received" in line:
                try: rx_errors = int(re.search(r"(\d+)", line).group(1))
                except: pass
        return {"available": True, "interface": iface,
                "rx": {"errors": rx_errors, "dropped": 0, "overruns": 0, "frame": 0, "carrier": 0},
                "tx": {"errors": 0, "dropped": 0, "overruns": 0, "carrier": 0}}


def _proc_net_wireless(iface):
    try:
        with open("/proc/net/wireless") as f:
            for line in f:
                if iface in line:
                    parts = line.strip().split()
                    if len(parts) >= 4:
                        result = {"available": True, "interface": iface, "ssid": None, "signal_dbm": None,
                                  "frequency": None, "tx_retries": None, "channel_util": None, "noise_dbm": None}
                        try:
                            qual = parts[2].split(".")[0]
                            sig = parts[3].split(".")[0]
                            noise = parts[4].split(".")[0] if len(parts) > 4 else None
                            result["signal_dbm"] = int(sig) if sig and sig != "0" else None
                            result["noise_dbm"] = int(noise) if noise and noise != "0" else None
                        except (ValueError, IndexError):
                            pass
                        return result
    except (OSError, IOError):
        pass
    return None


def _proc_net_wireless_any():
    """Read /proc/net/wireless directly; return first interface with non-zero signal/noise."""
    try:
        with open("/proc/net/wireless") as f:
            lines = f.readlines()
            for line in lines[2:]:
                if not line.strip():
                    continue
                parts = line.strip().split()
                if len(parts) >= 4:
                    iface = parts[0].rstrip(":")
                    sig_str = parts[3].split(".")[0]
                    noise_str = parts[4].split(".")[0] if len(parts) > 4 else None
                    sig, noise = None, None
                    try:
                        v = int(sig_str) if sig_str else None
                        if v is not None and v != 0:
                            sig = v
                    except (ValueError, IndexError):
                        pass
                    if noise_str:
                        try:
                            v = int(noise_str)
                            if v != 0:
                                noise = v
                        except (ValueError, IndexError):
                            pass
                    if sig is not None or noise is not None:
                        return {"available": True, "interface": iface,
                                "ssid": None, "signal_dbm": sig, "noise_dbm": noise,
                                "frequency": None, "tx_retries": None, "channel_util": None}
    except (OSError, IOError):
        pass
    return None


def wifi_info(iface):
    if not iface:
        return {"available": False, "reason": "No interface detected"}
    if IS_LINUX:
        if has_tool("iw"):
            rc_link, out_link, _ = run_cmd(["iw", "dev", iface, "link"], timeout=10)
            rc_survey, out_survey, _ = run_cmd(["iw", "dev", iface, "survey", "dump"], timeout=10)
            if rc_link == 0:
                result = {"available": True, "interface": iface, "ssid": None, "signal_dbm": None, "frequency": None,
                          "tx_retries": None, "channel_util": None, "noise_dbm": None}
                m = re.search(r"SSID:\s*(.+)", out_link)
                if m:
                    result["ssid"] = m.group(1).strip()
                m = re.search(r"signal: (-?\d+)", out_link)
                if m:
                    result["signal_dbm"] = int(m.group(1))
                m = re.search(r"freq: (\d+)", out_link)
                if m:
                    result["frequency"] = int(m.group(1))
                for line in out_survey.split("\n"):
                    if "channel active time" in line and result["channel_util"] is None:
                        m = re.search(r"busy time:\s+(\d+)", line)
                        busy = 0
                        if m:
                            busy = int(m.group(1))
                        m = re.search(r"active time:\s+(\d+)", line)
                        if m:
                            active = int(m.group(1))
                            if active > 0:
                                result["channel_util"] = round(100 * busy / active, 1)
                        break
                m = re.search(r"noise: (-?\d+)", out_survey)
                if m:
                    result["noise_dbm"] = int(m.group(1))
                return result
        fallback = _proc_net_wireless(iface)
        if fallback:
            return fallback
        return {"available": False, "reason": "iw not available and /proc/net/wireless not found"}
    elif IS_MACOS:
        rc, out, _ = run_cmd(["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"], timeout=10)
        if rc != 0:
            return {"available": False, "reason": "airport command failed"}
        result = {"available": True, "interface": iface, "ssid": None, "signal_dbm": None, "frequency": None,
                  "tx_retries": None, "channel_util": None, "noise_dbm": None}
        for line in out.split("\n"):
            if "SSID" in line:
                try: result["ssid"] = line.split(":")[-1].strip()
                except: pass
            if "agrCtlRSSI" in line:
                try: result["signal_dbm"] = int(line.split(":")[-1].strip())
                except: pass
            if "agrCtlNoise" in line:
                try: result["noise_dbm"] = int(line.split(":")[-1].strip())
                except: pass
        return result
    else:
        rc, out, _ = run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=10)
        if rc != 0:
            return {"available": False, "reason": "netsh wlan failed"}
        result = {"available": True, "interface": iface, "ssid": None, "signal_dbm": None, "frequency": None,
                  "tx_retries": None, "channel_util": None, "noise_dbm": None}
        for line in out.split("\n"):
            if "SSID" in line and "BSSID" not in line:
                try: result["ssid"] = line.split(":")[-1].strip()
                except: pass
            if "Signal" in line:
                try: result["signal_dbm"] = int(re.search(r"(\d+)%", line).group(1)) - 100
                except: pass
        return result


def _proc_net_tcp_stats():
    try:
        with open("/proc/net/tcp") as f:
            lines = f.readlines()[1:]
        connections = 0
        retrans = 0
        states = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV",
                  "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
                  "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK",
                  "0A": "LISTEN", "0B": "CLOSING"}
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 4:
                st = parts[3]
                if st == "01":
                    connections += 1
        return {"available": True, "connections": connections,
                "total_retransmits": 0, "avg_rtt_ms": None,
                "details": [], "_source": "/proc/net/tcp"}
    except (OSError, IOError, IndexError):
        return None


def tcp_socket_stats(iface):
    if IS_LINUX:
        if has_tool("ss"):
            rc, out, _ = run_cmd(["ss", "-itp"], timeout=10)
            if rc == 0:
                connections = []
                for line in out.split("\n"):
                    m = re.search(r"retrans:(\d+)/(\d+)", line)
                    if m:
                        cur = int(m.group(1))
                        conn = {"retrans": cur}
                        for pat, key in [(r"rtt:([\d.]+)", "rtt_ms"),
                                         (r"cwnd:(\d+)", "cwnd"),
                                         (r"ssthresh:(\d+)", "ssthresh"),
                                         (r"bytes_sent:(\d+)", "bytes_sent"),
                                         (r"bytes_acked:(\d+)", "bytes_acked"),
                                         (r"segs_out:(\d+)", "segs_out")]:
                            m2 = re.search(pat, line)
                            if m2:
                                try:
                                    v = float(m2.group(1))
                                    conn[key] = v if key == "rtt_ms" else int(v)
                                except:
                                    pass
                        connections.append(conn)
                total_retrans = sum(c.get("retrans", 0) for c in connections)
                rtt_vals = [c["rtt_ms"] for c in connections if c.get("rtt_ms")]
                avg_rtt = clean_float(statistics.mean(rtt_vals)) if rtt_vals else None
                return {"available": True, "connections": len(connections),
                        "total_retransmits": total_retrans, "avg_rtt_ms": avg_rtt,
                        "details": connections[:20]}
        fallback = _proc_net_tcp_stats()
        if fallback:
            return fallback
        return {"available": False, "reason": "ss not installed, /proc/net/tcp not available"}
    elif IS_MACOS:
        rc, out, _ = run_cmd(["nettop", "-J", "tcp", "-m", "tcp", "-d", "-l", "0"], timeout=15)
        if rc != 0 and rc != 1:
            return {"available": False, "reason": "nettop failed"}
        connections = 0
        total_retrans = 0
        for line in out.split("\n"):
            if "retransmit" in line.lower() or "retrans" in line.lower():
                try: total_retrans += int(re.search(r"(\d+)", line).group(1))
                except: pass
            if "tcp" in line.lower():
                connections += 1
        return {"available": True, "connections": connections,
                "total_retransmits": total_retrans, "avg_rtt_ms": None, "details": []}
    else:
        rc, out, _ = run_cmd(["netstat", "-s"], timeout=10)
        if rc != 0:
            return {"available": False, "reason": "netstat failed"}
        retrans = 0
        for line in out.split("\n"):
            if "Segments Retransmitted" in line:
                try: retrans = int(re.search(r"(\d+)", line).group(1))
                except: pass
        return {"available": True, "connections": 0,
                "total_retransmits": retrans, "avg_rtt_ms": None, "details": []}


def _ping_traceroute(host, max_hops=30, timeout_s=3):
    hops = []
    for ttl in range(1, max_hops + 1):
        if IS_LINUX:
            cmd = ["ping", "-c", "1", "-W", str(timeout_s), "-t", str(ttl), host]
        elif IS_MACOS:
            cmd = ["ping", "-c", "1", "-t", str(timeout_s), "-m", str(ttl), host]
        else:
            cmd = ["ping", "-n", "1", "-w", str(timeout_s * 1000), "-i", str(ttl), host]
        rc, out, err = run_cmd(cmd, timeout=timeout_s + 3)
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
    if has_tool("mtr"):
        rc, out, err = run_cmd(["mtr", "-r", "-c", str(count), "-w", host], timeout=120)
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
    if has_tool("traceroute"):
        rc, out, err = run_cmd(["traceroute", "-n", "-m", "30", host], timeout=90)
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
    if IS_WINDOWS:
        rc, out, err = run_cmd(["tracert", "-h", "30", host], timeout=90)
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


def speedtest_result():
    if has_tool("speedtest"):
        rc, out, err = run_cmd(["speedtest", "--format=json"], timeout=180)
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
    if has_tool("speedtest-cli"):
        rc, out, err = run_cmd(["speedtest-cli", "--json"], timeout=180)
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
    if not has_tool("iperf3"):
        return {"available": False, "reason": "iperf3 not installed"}
    srv = server or IPERF_SERVER
    rc, out, err = run_cmd(["iperf3", "-c", srv, "-t", str(duration), "-J"], timeout=duration + 30)
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
    if not IS_LINUX:
        result = {"available": False, "reason": "Bufferbloat detection requires Linux (tc)"}
        if has_tool("iperf3"):
            ping_before = ping_once("1.1.1.1", timeout_s=2)
            rtt_idle = ping_before.get("rtt_ms")
            rc, _, _ = run_cmd(["iperf3", "-c", IPERF_SERVER, "-t", "8", "-P", "4"], timeout=30)
            ping_during = ping_once("1.1.1.1", timeout_s=4)
            rtt_loaded = ping_during.get("rtt_ms")
            if rtt_idle and rtt_loaded and rtt_idle > 0:
                ratio = rtt_loaded / rtt_idle
                result["ratio"] = clean_float(ratio)
                result["rtt_idle_ms"] = rtt_idle
                result["rtt_loaded_ms"] = rtt_loaded
        return result
    if not iface:
        return {"available": False, "reason": "No interface"}
    rc, out, _ = run_cmd(["tc", "-s", "qdisc", "show", "dev", iface], timeout=10)
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
    if has_tool("iperf3"):
        ping_before = ping_once("1.1.1.1", timeout_s=2)
        rtt_idle = ping_before.get("rtt_ms")
        rc, _, _ = run_cmd(["iperf3", "-c", IPERF_SERVER, "-t", "6", "-P", "4"], timeout=30)
        ping_during = ping_once("1.1.1.1", timeout_s=4)
        rtt_loaded = ping_during.get("rtt_ms")
        if rtt_idle and rtt_loaded and rtt_idle > 0:
            result["ratio"] = clean_float(rtt_loaded / rtt_idle)
            result["rtt_idle_ms"] = rtt_idle
            result["rtt_loaded_ms"] = rtt_loaded
    return result


def ethtool_info(iface):
    if not IS_LINUX or not iface:
        return {"available": False, "reason": "Linux-only" if IS_LINUX else "No interface"}
    if not has_tool("ethtool"):
        return {"available": False, "reason": "ethtool not installed"}
    rc, out, _ = run_cmd(["ethtool", iface], timeout=10)
    if rc != 0:
        return {"available": False, "reason": f"ethtool failed: rc={rc}"}
    speed = None
    duplex = None
    link = None
    for line in out.split("\n"):
        if "Speed:" in line:
            m = re.search(r"(\d+)", line)
            if m:
                speed = int(m.group(1))
        if "Duplex:" in line:
            if "Full" in line:
                duplex = "Full"
            elif "Half" in line:
                duplex = "Half"
        if "Link detected:" in line:
            link = "yes" in line.lower()
    return {"available": True, "interface": iface, "speed_mbps": speed, "duplex": duplex, "link_detected": link, "raw": out}


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


def download_images_test(count=100, timeout_s=15):
    import tempfile, urllib.request, os, time, concurrent.futures
    results = {"available": True, "success": 0, "failures": 0,
               "total_bytes": 0, "total_time_s": 0, "avg_mbps": 0,
               "p95_latency_ms": None, "error": None}
    latencies = []
    t0 = time.time()

    def _dl(i):
        try:
            t1 = time.time()
            url = f"https://picsum.photos/200/200?random={i}"
            req = urllib.request.Request(url, headers={"User-Agent": "NetDiag/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = resp.read()
            elapsed = (time.time() - t1) * 1000
            return {"ok": True, "bytes": len(data), "latency_ms": elapsed, "idx": i}
        except Exception as e:
            return {"ok": False, "error": str(e), "idx": i}

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_dl, n) for n in range(count)]
        for f in concurrent.futures.as_completed(futures):
            r = f.result()
            if r["ok"]:
                results["success"] += 1
                results["total_bytes"] += r["bytes"]
                latencies.append(r["latency_ms"])
            else:
                results["failures"] += 1

    total_time = time.time() - t0
    results["total_time_s"] = round(total_time, 2)
    if latencies:
        results["p95_latency_ms"] = percentile(latencies, 95)
    if total_time > 0 and results["total_bytes"] > 0:
        results["avg_mbps"] = round(
            (results["total_bytes"] * 8) / total_time / 1_000_000, 2)
    if results["success"] == 0 and results["failures"] > 0:
        results["error"] = "All downloads failed"
    return results


def http_latency_test(hosts=None, count=5, timeout_s=5):
    import urllib.request, time
    if hosts is None:
        hosts = ["connectivitycheck.gstatic.com", "detectportal.firefox.com", "1.1.1.1"]
    results = []
    for host in hosts:
        latencies = []
        failures = 0
        for i in range(count):
            try:
                t0 = time.time()
                req = urllib.request.Request(f"http://{host}/", method="HEAD",
                    headers={"User-Agent": "NetDiag/1.0"})
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    resp.read()
                lat = (time.time() - t0) * 1000
                latencies.append(lat)
            except Exception:
                failures += 1
        entry = {"host": host, "available": True, "failures": failures}
        if latencies:
            entry.update(series_stats(latencies))
            entry["latencies"] = latencies
        results.append(entry)
    return results


def mtu_probe(host="1.1.1.1", max_size=1500):
    import shutil
    if not has_tool("ping"):
        return {"available": False, "reason": "ping not found"}
    low, high = 68, max_size
    last_ok = low
    while low <= high:
        mid = (low + high) // 2
        if IS_LINUX:
            cmd = ["ping", "-M", "do", "-c", "1", "-W", "2", "-s", str(mid), host]
        elif IS_MACOS:
            cmd = ["ping", "-D", "-c", "1", "-t", "2", "-s", str(mid), host]
        else:
            cmd = ["ping", "-f", "-n", "1", "-w", "2000", "-l", str(mid), host]
        rc, _, _ = run_cmd(cmd, timeout=5)
        if rc == 0:
            last_ok = mid
            low = mid + 1
        else:
            high = mid - 1
    return {"available": True, "mtu": last_ok + 28, "payload_size": last_ok}


def diagnose(results):
    diagnoses = []
    if results.get("interrupted"):
        diagnoses.append({"layer": "meta", "severity": "warning",
                          "title": "Test was interrupted",
                          "detail": "Diagnosis is based on partial results only.",
                          "fix": "Re-run the diagnostic with a longer duration."})
    iface = results.get("interface")
    wifi = results.get("wifi")
    ethtool = results.get("ethtool")
    socket_stats = results.get("tcp_sockets")
    bufferbloat_blob = results.get("bufferbloat")
    gw_ping = results.get("gateway_ping")
    internet_pings = results.get("internet_ping", [])
    dns_results = results.get("dns", [])
    tcp_results = results.get("tcp", [])
    mtr_result = results.get("mtr")
    speed_result = results.get("speedtest")
    iperf_result = results.get("iperf3")

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
                              "fix": "Check cable connections. Try a different cable or port. "
                                     "High overruns suggest system too slow to process packets."})

    if ethtool and ethtool.get("available"):
        if ethtool.get("duplex") == "Half":
            diagnoses.append({"layer": "interface", "severity": "bad",
                              "title": "Half-duplex detected",
                              "detail": "Interface is running at half-duplex.",
                              "fix": "Force full-duplex on both sides of the link."})
        if ethtool.get("link_detected") is False:
            diagnoses.append({"layer": "interface", "severity": "bad",
                              "title": "No link detected",
                              "detail": "Ethernet link appears down.",
                              "fix": "Check cable, switch port, and interface status."})

    if wifi and wifi.get("available"):
        signal = wifi.get("signal_dbm")
        if signal is not None:
            if signal < -80:
                diagnoses.append({"layer": "wifi", "severity": "bad",
                                  "title": "Very weak WiFi signal",
                                  "detail": f"Signal strength: {signal} dBm. This will cause dropouts and slow speeds.",
                                  "fix": "Move closer to the router or add a WiFi extender/mesh node."})
            elif signal < -70:
                diagnoses.append({"layer": "wifi", "severity": "warning",
                                  "title": "Weak WiFi signal",
                                  "detail": f"Signal strength: {signal} dBm. May cause intermittent issues.",
                                  "fix": "Move closer to the router or switch to 2.4 GHz band for range."})
            elif signal < -60:
                diagnoses.append({"layer": "wifi", "severity": "info",
                                  "title": "Fair WiFi signal",
                                  "detail": f"Signal strength: {signal} dBm. Adequate but not optimal for high bandwidth."})
        channel_util = wifi.get("channel_util")
        if channel_util is not None and channel_util > 60:
            diagnoses.append({"layer": "wifi", "severity": "warning",
                              "title": "Crowded WiFi channel",
                              "detail": f"Channel utilization: {channel_util}%. High congestion.",
                              "fix": "Switch to a less congested channel or upgrade to WiFi 6/6E."})

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
            diagnose_gw = {"layer": "gateway", "severity": "bad",
                           "title": "Gateway instability detected",
                           "detail": "; ".join(detail_parts),
                           "fix": ""}
            if wifi and wifi.get("available") and wifi.get("signal_dbm"):
                sig = wifi["signal_dbm"]
                if sig < -70:
                    diagnose_gw["fix"] = "Gateway latency may be caused by weak WiFi. Move closer or use Ethernet."
                else:
                    diagnose_gw["fix"] = "Router may be overloaded. Reduce active downloads, reboot router, or check QoS settings."
            else:
                diagnose_gw["fix"] = "Router may be overloaded. Reduce active downloads, reboot router, or check QoS settings."
            diagnoses.append(diagnose_gw)
        elif status == "clean":
            diagnoses.append({"layer": "gateway", "severity": "clean",
                              "title": "Gateway stable",
                              "detail": f"p95={gw_ping.get('p95_ms', '?')}ms, loss={gw_ping.get('loss_pct', 0)}%",
                              "fix": ""})

    bad_internet = [x for x in internet_pings if classify_ping(x) != "clean"]
    if bad_internet and gw_ping and classify_ping(gw_ping) == "clean":
        for row in bad_internet:
            s = classify_ping(row)
            detail = f"loss={row.get('loss_pct', 0)}%, p95={row.get('p95_ms', '?')}ms, jitter={row.get('jitter_ms', '?')}ms"
            diagnoses.append({"layer": "internet", "severity": "bad",
                              "title": f"External instability: {row['label']}",
                              "detail": detail,
                              "fix": "Gateway is clean but external pings are unstable. Likely ISP or upstream routing issue. "
                                     "Contact your ISP with the MTR trace results."})
    elif bad_internet and gw_ping and classify_ping(gw_ping) != "clean":
        diagnoses.append({"layer": "meta", "severity": "warning",
                          "title": "Both local and internet unstable",
                          "detail": "Gateway and external pings both show issues.",
                          "fix": "Fix the local network issue first (see gateway diagnosis), then re-test internet."})

    if mtr_result and mtr_result.get("hops"):
        hops = mtr_result["hops"]
        first_loss_hop = None
        for hop in hops:
            if hop.get("loss_pct", 0) > 5:
                first_loss_hop = hop
                break
        if first_loss_hop:
            hop_num = first_loss_hop["hop"]
            detail = f"Loss of {first_loss_hop['loss_pct']}% starts at hop {hop_num}"
            if hop_num <= 2:
                diagnoses.append({"layer": "isp", "severity": "bad",
                                  "title": "Packet loss at first hops",
                                  "detail": detail,
                                  "fix": "Loss at hops 1-2 suggests modem or local uplink issue. "
                                         "Restart modem/gateway. Check for signal issues on the line."})
            else:
                diagnoses.append({"layer": "isp", "severity": "bad",
                                  "title": "Packet loss at ISP hops",
                                  "detail": detail,
                                  "fix": f"Loss starts at hop {hop_num}, which is in the ISP network. "
                                         "Contact your ISP and share this trace. Consider providing the full MTR output."})

    if bufferbloat_blob and bufferbloat_blob.get("available"):
        ratio = bufferbloat_blob.get("ratio")
        if ratio and ratio > 3:
            diagnoses.append({"layer": "bufferbloat", "severity": "bad",
                              "title": "Severe bufferbloat detected",
                              "detail": f"Latency under load is {ratio:.1f}x idle latency (idle: {bufferbloat_blob.get('rtt_idle_ms')}ms, loaded: {bufferbloat_blob.get('rtt_loaded_ms')}ms)",
                              "fix": "Enable SQM/fq_codel on your router. On Linux: tc qdisc add dev eth0 root fq_codel. "
                                     "On OpenWrt: install luci-app-sqm."})
        elif ratio and ratio > 2:
            diagnoses.append({"layer": "bufferbloat", "severity": "warning",
                              "title": "Mild bufferbloat detected",
                              "detail": f"Latency under load is {ratio:.1f}x idle latency",
                              "fix": "Consider enabling SQM or reducing concurrent uploads during latency-sensitive use."})

    dns_bad = [x for x in dns_results if (x.get("failure_pct") or 0) > 0 or (x.get("p95_ms") or 0) > 300]
    if dns_bad:
        names = ", ".join(x["host"] for x in dns_bad)
        diagnoses.append({"layer": "dns", "severity": "bad",
                          "title": "DNS instability detected",
                          "detail": f"Affected: {names}",
                          "fix": "Try using a different DNS resolver like 1.1.1.1 or 8.8.8.8. "
                                 "If using router DNS forwarding, bypass it."})

    tcp_bad = [x for x in tcp_results if (x.get("failure_pct") or 0) > 0 or (x.get("p95_ms") or 0) > 500]
    if tcp_bad:
        names = ", ".join(f"{x['host']}:{x['port']}" for x in tcp_bad)
        diagnoses.append({"layer": "tcp", "severity": "bad",
                          "title": "TCP connection instability",
                          "detail": f"Affected: {names}",
                          "fix": "Web browsing, video calls, or app logins may feel unreliable. "
                                 "Check for firewall blocking or ISP throttling."})

    if iperf_result and iperf_result.get("available") and not iperf_result.get("error"):
        retrans_pct = iperf_result.get("retransmit_pct", 0)
        if retrans_pct and retrans_pct > 2:
            diagnoses.append({"layer": "tcp", "severity": "warning",
                              "title": "High TCP retransmits in iPerf3",
                              "detail": f"Retransmits: {retrans_pct:.1f}% during throughput test",
                              "fix": "High retransmits suggest congestion, throttling, or line quality issues."})

    download_result = results.get("download_test")
    if download_result and download_result.get("error") is None:
        mbps = download_result.get("avg_mbps", 0)
        success = download_result.get("success", 0)
        failures = download_result.get("failures", 0)
        if mbps < 1 and failures > 0:
            diagnoses.append({"layer": "internet", "severity": "bad",
                              "title": "Image download test failed",
                              "detail": f"{success}/{success+failures} images downloaded, {mbps} Mbps",
                              "fix": "Check internet connectivity. Very low download throughput."})
        elif mbps < 5:
            diagnoses.append({"layer": "internet", "severity": "warning",
                              "title": "Low download throughput",
                              "detail": f"{mbps} Mbps average over {success} images",
                              "fix": "Slow internet connection may impact streaming and large file transfers."})
        else:
            diagnoses.append({"layer": "internet", "severity": "clean",
                              "title": "Download throughput OK",
                              "detail": f"{mbps} Mbps average, {success}/{success+failures} successful",
                              "fix": ""})

    conn_result = results.get("connection_test")
    if conn_result:
        http_lat = conn_result.get("http_latency", [])
        for h in http_lat:
            fail = h.get("failures", 0)
            p95 = h.get("p95_ms")
            if p95 and p95 > 500:
                diagnoses.append({"layer": "internet", "severity": "warning",
                                  "title": f"High HTTP latency: {h['host']}",
                                  "detail": f"p95={p95:.0f}ms, {fail} failures",
                                  "fix": "Web pages may load slowly. Check for DNS or routing issues."})
        mtu = conn_result.get("mtu", {})
        if mtu.get("available"):
            mtu_val = mtu.get("mtu", 1500)
            if mtu_val < 1400:
                diagnoses.append({"layer": "interface", "severity": "warning",
                                  "title": f"Low path MTU: {mtu_val}",
                                  "detail": f"MTU of {mtu_val} may reduce throughput for large transfers",
                                  "fix": "Check for VPN overhead or PPPoE encapsulation reducing MTU."})

    clean_layers = [d for d in diagnoses if d["severity"] == "clean"]
    if len(clean_layers) == len(diagnoses) and diagnoses:
        diagnoses.append({"layer": "meta", "severity": "clean",
                          "title": "No issues detected",
                          "detail": "All tests passed within normal parameters.",
                          "fix": ""})

    if not diagnoses:
        diagnoses.append({"layer": "meta", "severity": "clean",
                          "title": "No issues detected",
                          "detail": "All tests passed within normal parameters.",
                          "fix": ""})

    return diagnoses


def health_score(results):
    scores = {}
    weights = {"interface": 10, "wifi": 15, "gateway": 25, "internet": 25, "dns": 10, "tcp": 5, "bufferbloat": 10}
    iface = results.get("interface")
    if iface and iface.get("available"):
        rx = iface.get("rx", {})
        tx = iface.get("tx", {})
        total = rx.get("errors", 0) + tx.get("errors", 0) + rx.get("dropped", 0) + tx.get("dropped", 0)
        scores["interface"] = max(0, 100 - total * 5)
    wifi = results.get("wifi")
    if wifi and wifi.get("available"):
        sig = wifi.get("signal_dbm")
        if sig is not None:
            scores["wifi"] = max(0, min(100, 100 - (max(0, -55 - sig) * 3)))
    gw = results.get("gateway_ping")
    if gw:
        loss = gw.get("loss_pct", 0)
        p95 = gw.get("p95_ms", 0)
        scores["gateway"] = max(0, 100 - (loss * 8) - (max(0, p95 - 10) * 0.5))
    internet = results.get("internet_ping", [])
    if internet:
        internet_scores = []
        for row in internet:
            loss = row.get("loss_pct", 0)
            p95 = row.get("p95_ms", 0)
            internet_scores.append(max(0, 100 - (loss * 8) - (max(0, p95 - 40) * 0.3)))
        scores["internet"] = statistics.mean(internet_scores) if internet_scores else 0
    dns = results.get("dns", [])
    if dns:
        dns_scores = []
        for row in dns:
            fail = row.get("failure_pct", 0)
            p95 = row.get("p95_ms", 0)
            dns_scores.append(max(0, 100 - (fail * 15) - (max(0, p95 - 50) * 0.5)))
        scores["dns"] = statistics.mean(dns_scores) if dns_scores else 0
    tcp = results.get("tcp", [])
    if tcp:
        tcp_scores = []
        for row in tcp:
            fail = row.get("failure_pct", 0)
            tcp_scores.append(max(0, 100 - (fail * 15)))
        scores["tcp"] = statistics.mean(tcp_scores) if tcp_scores else 0
    bb = results.get("bufferbloat")
    if bb and bb.get("available") and bb.get("ratio"):
        ratio = bb["ratio"]
        scores["bufferbloat"] = max(0, 100 - (max(0, ratio - 1) * 30))

    download_result = results.get("download_test")
    if download_result and download_result.get("error") is None:
        mbps = download_result.get("avg_mbps", 0)
        success = download_result.get("success", 0)
        total = success + download_result.get("failures", 0)
        score = min(100, (mbps / 20) * 50 + (success / max(total, 1)) * 50)
        scores["download"] = min(100, score)

    conn_result = results.get("connection_test")
    if conn_result:
        http_lat = conn_result.get("http_latency", [])
        if http_lat:
            avg_p95 = statistics.mean([h.get("p95_ms", 0) or 0 for h in http_lat])
            scores["http_latency"] = max(0, 100 - (max(0, avg_p95 - 50) * 0.5))

    total_weight = sum(weights.get(k, 0) for k in scores)
    if total_weight == 0:
        return 0
    weighted = sum(scores[k] * weights.get(k, 0) for k in scores)
    for k in weights:
        if k not in scores:
            total_weight += weights[k]
    return round(weighted / total_weight)


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
    for d in results.get("diagnosis", []):
        lines.append(f"- [{d['severity']}] [{d['layer']}] {d['title']}: {d['detail']}")
        if d.get("fix"):
            lines.append(f"  Fix: {d['fix']}")
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


def print_console_summary(results, outdir):
    print(f"\nHealth score: {results.get('health_score', '?')}/100")
    print("\nDiagnosis:")
    for d in results.get("diagnosis", []):
        icon = {"clean": "  ", "info": "  ", "warning": "! ", "bad": "!!"}.get(d["severity"], "  ")
        print(f"  {icon}[{d['layer']}] {d['title']}")
        if d.get("detail"):
            print(f"      {d['detail']}")
        if d.get("fix"):
            print(f"      Fix: {d['fix']}")
    print("\nPing summary:")
    for row in ping_summary_rows(results):
        c = compact_ping(row)
        print(f"  {c['label']}: loss={c['loss_pct']}%, avg={c['avg_ms']}ms, p95={c['p95_ms']}ms, jitter={c['jitter_ms']}ms")
    print(f"\nFiles written to: {Path(outdir).resolve()}")


def full_diagnostic(args, callback=None):
    tools = check_tools()
    gateway = detect_gateway()
    default_iface = get_default_interface()

    results = {
        "timestamp": now_iso(),
        "platform": platform.platform(),
        "os": OS_NAME,
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
            iface_stats = interface_stats(default_iface)
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
            wifi = wifi_info(default_iface)
            if wifi:
                results["wifi"] = wifi
            if callback:
                sig = wifi.get("signal_dbm") if wifi else None
                ok = 1 if (sig is None or sig > -70) else (0 if sig < -80 else 0)
                callback("wifi", 1, 1, ok, sig, "done" if wifi else "error")

        if default_iface:
            if callback:
                callback("ethtool", 0, 1, None, None, "running")
            ethtool = ethtool_info(default_iface)
            if ethtool:
                results["ethtool"] = ethtool
            if callback:
                ok = 1 if (ethtool and ethtool.get("duplex") == "Full") else 0
                callback("ethtool", 1, 1, ok, 0, "done" if ethtool else "error")

        if gateway:
            if callback:
                callback("gateway", 0, args.count, None, None, "running")
            gw_result = ping_burst(
                gateway, args.count, args.interval, timeout_s=args.timeout,
                ipv=4, label="gateway", quiet=args.quiet, callback=callback)
            results["gateway_ping"] = gw_result
            if callback:
                ok = gw_result.get("received", 0) if gw_result else 0
                callback("gateway", args.count, args.count, ok, gw_result.get("p95_ms", 0) if gw_result else 0, "done" if gw_result else "error")
        elif not args.quiet:
            print("No gateway detected.", flush=True)

        for host in args.hosts:
            label = host
            if callback:
                callback(label, 0, args.count, None, None, "running")
            results["internet_ping"].append(
                ping_burst(host, args.count, args.interval, timeout_s=args.timeout,
                           label=label, quiet=args.quiet, callback=callback))

        for host in DNS_HOSTS:
            if not args.quiet:
                print(f"Testing DNS: {host}", flush=True)
            if callback:
                callback(f"dns_{host}", 0, args.dns_count, None, None, "running")
            d = dns_test(host, args.dns_count)
            results["dns"].append(d)
            if callback:
                ok = d.get("total", args.dns_count) - d.get("failures", 0)
                callback(f"dns_{host}", ok, args.dns_count, ok, d.get("avg_ms", 0), "done")

        for h, p in TCP_TARGETS:
            if not args.quiet:
                print(f"Testing TCP: {h}:{p}", flush=True)
            if callback:
                callback(f"tcp_{h}_{p}", 0, args.tcp_count, None, None, "running")
            t = tcp_test(h, p, args.tcp_count)
            results["tcp"].append(t)
            if callback:
                ok = t.get("total", args.tcp_count) - t.get("failures", 0)
                callback(f"tcp_{h}_{p}", ok, args.tcp_count, ok, t.get("avg_ms", 0), "done")

        if default_iface:
            if callback:
                callback("tcp_sockets", 0, 1, None, None, "running")
            results["tcp_sockets"] = tcp_socket_stats(default_iface)
            if callback:
                ts = results["tcp_sockets"]
                ok = 1 if (ts and ts.get("retransmit_pct", 100) < 5) else 0
                callback("tcp_sockets", 1, 1, ok, ts.get("retransmit_pct", 0) if ts else 0, "done" if ts else "error")
            if not args.no_bufferbloat:
                if callback:
                    callback("bufferbloat", 0, 1, None, None, "running")
                results["bufferbloat"] = bufferbloat_test(default_iface)
                if callback:
                    bb = results["bufferbloat"]
                    ok = 1 if (bb and bb.get("ratio", 99) < 2) else 0
                    callback("bufferbloat", 1, 1, ok, int((bb.get("ratio", 0) or 0) * 100), "done" if bb else "error")

        if not args.no_trace and args.hosts:
            if not args.quiet:
                print(f"Testing route: {args.hosts[0]}", flush=True)
            if callback:
                callback("mtr", 0, 50, None, None, "running")
            results["mtr"] = mtr_test(args.hosts[0], count=50)
            if callback:
                m = results["mtr"]
                ok = 1 if (m and m.get("hops") and m["hops"][-1].get("loss_pct", 100) < 5) else 0
                callback("mtr", 1, 1, ok, 0, "done" if m else "error")

        if not args.no_speedtest:
            if not args.quiet:
                print("Running speedtest...", flush=True)
            if callback:
                callback("speedtest", 0, 1, None, None, "running")
            results["speedtest"] = speedtest_result()
            if callback:
                s = results["speedtest"]
                ok = 1 if (s and s.get("download_mbps", 0) > 10) else 0
                callback("speedtest", 1, 1, ok, int(s.get("download_mbps", 0) or 0) if s else 0, "done" if s else "error")

        if not args.no_iperf:
            if not args.quiet:
                print("Running iPerf3...", flush=True)
            if callback:
                callback("iperf3", 0, 1, None, None, "running")
            results["iperf3"] = iperf3_test()
            if callback:
                i3 = results["iperf3"]
                ok = 1 if (i3 and i3.get("available") and i3.get(" retransmits", 10) < 5) else 0
                mbits = int(i3.get("mbps", 0) or 0) if i3 else 0
                callback("iperf3", 1, 1, ok, mbits, "done" if (i3 and i3.get("available")) else "error")

        if getattr(args, "download_test", False):
            if not args.quiet:
                print("Download test: 100 images...", flush=True)
            if callback:
                callback("download_test", 0, 100, None, None, "running")
            results["download_test"] = download_images_test(count=100)
            if callback:
                dt = results["download_test"]
                ok = dt.get("success", 0)
                callback("download_test", ok, 100, ok, dt.get("avg_mbps", 0), "done" if dt.get("error") is None else "error")

        if getattr(args, "connection_test", False):
            if not args.quiet:
                print("Connection test: HTTP latency + MTU probe...", flush=True)
            if callback:
                callback("http_latency", 0, 5, None, None, "running")
            results["connection_test"] = {"http_latency": http_latency_test(count=5)}
            if callback:
                ht = results["connection_test"]["http_latency"]
                total_ok = sum(1 for h in ht if h.get("failures", 5) < 5)
                callback("http_latency", total_ok, len(ht), total_ok, 0, "done")
            if callback:
                callback("mtu_probe", 0, 1, None, None, "running")
            results["connection_test"]["mtu"] = mtu_probe()
            if callback:
                mp = results["connection_test"]["mtu"]
                ok = 1 if mp.get("available") else 0
                callback("mtu_probe", 1, 1, ok, mp.get("mtu", 0), "done" if mp.get("available") else "error")

    except UserInterrupted as e:
        results["interrupted"] = True
        results["interrupt_reason"] = str(e)
        print(f"\nInterrupted: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        results["interrupted"] = True
        results["interrupt_reason"] = "Interrupted by user"
        print("\nInterrupted by user.", file=sys.stderr)

    results["diagnosis"] = diagnose(results)
    results["health_score"] = health_score(results)
    return results


VERSION = "1.0.0"

# -- Persistent configuration ----------------------------------------------------
#
# User-tunable settings live in ~/.netdiag/config.json. They override the CLI
# argparse defaults (so the GUI Settings tab and the CLI stay in sync) and feed
# the live monitor's target list. Unknown keys are ignored; missing keys fall
# back to CONFIG_DEFAULTS.

CONFIG_DEFAULTS = {
    "hosts": list(DEFAULT_HOSTS),
    "dns_hosts": list(DNS_HOSTS),
    "tcp_targets": [list(t) for t in TCP_TARGETS],
    "ping_count": 20,
    "ping_interval": 0.5,
    "ping_timeout": 2,
    "dns_count": 10,
    "tcp_count": 10,
    "monitor_interval": 1.0,
    "monitor_external_hosts": list(DEFAULT_HOSTS[:2]),
    "monitor_dns_host": DNS_HOSTS[0],
    "monitor_tcp_target": list(TCP_TARGETS[0]),
    "history_dir": "~/.netdiag",
}


def config_path(history_dir="~/.netdiag"):
    return Path(history_dir).expanduser() / "config.json"


def load_config(history_dir="~/.netdiag"):
    cfg = json.loads(json.dumps(CONFIG_DEFAULTS))  # deep copy
    p = config_path(history_dir)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in CONFIG_DEFAULTS:
                        cfg[k] = v
        except Exception:
            pass
    return cfg


CONFIG_LIMITS = {
    "ping_count": (1, 200),
    "ping_interval": (0.1, 10),
    "ping_timeout": (1, 10),
    "dns_count": (1, 100),
    "tcp_count": (1, 100),
    "monitor_interval": (0.5, 10),
}


def save_config(updates, history_dir="~/.netdiag"):
    cfg = load_config(history_dir)
    for k, v in updates.items():
        if k not in CONFIG_DEFAULTS:
            continue
        if k in CONFIG_LIMITS:
            lo, hi = CONFIG_LIMITS[k]
            try:
                v = max(lo, min(hi, float(v)))
                if isinstance(CONFIG_DEFAULTS[k], int):
                    v = int(v)
            except (TypeError, ValueError):
                continue
        cfg[k] = v
    d = ensure_history_dir(history_dir)
    (d / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def build_parser():
    cfg = load_config()
    parser = argparse.ArgumentParser(description="NetDiag — all-in-one internet diagnostics suite")
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument("--license", action="store_true", help="Show license information")
    parser.add_argument("--hosts", nargs="*", default=cfg["hosts"])
    parser.add_argument("--count", type=int, default=cfg["ping_count"])
    parser.add_argument("--interval", type=float, default=cfg["ping_interval"])
    parser.add_argument("--timeout", type=int, default=cfg["ping_timeout"])
    parser.add_argument("--dns-count", type=int, default=cfg["dns_count"])
    parser.add_argument("--tcp-count", type=int, default=cfg["tcp_count"])
    parser.add_argument("--outdir", default="internet_diagnostics")
    parser.add_argument("--ipv4", action="store_true")
    parser.add_argument("--ipv6", action="store_true")
    parser.add_argument("--no-speedtest", action="store_true")
    parser.add_argument("--no-trace", action="store_true")
    parser.add_argument("--no-iperf", action="store_true")
    parser.add_argument("--no-bufferbloat", action="store_true")
    parser.add_argument("--download-test", action="store_true", help="Download 100 images to measure throughput")
    parser.add_argument("--connection-test", action="store_true", help="HTTP latency + MTU probe")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-ping progress output")
    parser.add_argument("--gui", action="store_true", help="Start web GUI at http://localhost:8080")
    parser.add_argument("--daemon", action="store_true", help="Continuous monitoring + web GUI")
    parser.add_argument("--port", type=int, default=8080, help="Web server port (default: 8080)")
    parser.add_argument("--history-dir", default=cfg["history_dir"], help="Directory for history and persistent data")
    return parser


def ensure_history_dir(hist_dir):
    d = Path(hist_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_history(hist_dir, results):
    d = ensure_history_dir(hist_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"session_{ts}.json"
    (d / fname).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return fname


def load_history(hist_dir):
    d = ensure_history_dir(hist_dir)
    sessions = []
    for f in sorted(d.glob("session_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_file"] = f.name
            sessions.append(data)
        except:
            pass
    return sessions


def cli_main():
    args = build_parser().parse_args()

    if args.version:
        print(f"netdiag v{VERSION} — AGPLv3")
        return

    if args.license:
        print(__doc__.split("SPDX-License-Identifier: AGPL-3.0-only")[1].strip())
        return

    if args.gui or args.daemon:
        try:
            from fastapi import FastAPI, Request, Response
            from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
            from fastapi.staticfiles import StaticFiles
            import asyncio
            import threading
            import uvicorn
        except ImportError:
            print("Error: fastapi and uvicorn are required for GUI mode.", file=sys.stderr)
            print("Install with: pip install fastapi uvicorn", file=sys.stderr)
            sys.exit(1)
        start_server(args)
        return

    if args.count < 1:
        print("Error: --count must be at least 1", file=sys.stderr)
        sys.exit(2)
    if args.interval < 0:
        print("Error: --interval must be 0 or greater", file=sys.stderr)
        sys.exit(2)
    if args.timeout < 1:
        print("Error: --timeout must be at least 1 second", file=sys.stderr)
        sys.exit(2)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    results = full_diagnostic(args)

    results["diagnosis"] = results.get("diagnosis", [])
    results["health_score"] = results.get("health_score", 0)

    (outdir / "diagnostics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(outdir / "ping_samples.csv", flatten_ping(results))
    write_csv(outdir / "ping_summary.csv", ping_summary_rows(results))
    write_report(outdir / "report.txt", results)
    print_console_summary(results, outdir)

    save_history(args.history_dir, results)


# -- Live monitor: continuous multi-target sampler ------------------------------
#
# A single ping to the gateway can't tell "signal OK, internet flaky" apart
# from "everything's fine". This sampler runs in the background at ~1 Hz and
# probes several layers in parallel (gateway, external hosts, DNS, TCP), so
# the live monitor can compare layers and spot intermittent loss/jitter that
# a one-shot ping would miss entirely.

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
    cfg = load_config()
    tcp_target = cfg.get("monitor_tcp_target") or list(TCP_TARGETS[0])
    return {
        "gateway": detect_gateway(),
        "external": cfg.get("monitor_external_hosts") or list(DEFAULT_HOSTS[:2]),
        "dns_host": cfg.get("monitor_dns_host") or DNS_HOSTS[0],
        "tcp_host": tcp_target[0],
        "tcp_port": tcp_target[1],
        "interval": cfg.get("monitor_interval", 1.0),
    }


def monitor_sample(targets):
    sample = {"ts": now_iso()}
    if targets.get("gateway"):
        r = ping_once(targets["gateway"], timeout_s=1)
        sample["gateway"] = {"ok": r["ok"], "rtt_ms": r["rtt_ms"]}
    else:
        sample["gateway"] = None
    ext = {}
    for host in targets["external"]:
        r = ping_once(host, timeout_s=1)
        ext[host] = {"ok": r["ok"], "rtt_ms": r["rtt_ms"]}
    sample["external"] = ext
    dns = resolve_all(targets["dns_host"])
    sample["dns"] = {"ok": dns["ok"], "rtt_ms": None}
    tcp = _tcp_ping(targets["tcp_host"], port=targets["tcp_port"], timeout_s=1)
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
            log.error("monitor sample error: %s", str(e))
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
        MONITOR_STATE["started_at"] = now_iso()
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


# -- Tools Menu: OSI-layer-organized tool definitions for the GUI Tools tab --------

def _diag_args_from_kw(kw):
    cfg = load_config()
    class _NA: pass
    a = _NA()
    a.hosts = kw.get("hosts", cfg.get("hosts", list(DEFAULT_HOSTS)))
    if isinstance(a.hosts, str):
        a.hosts = [h.strip() for h in a.hosts.split(",") if h.strip()]
    a.count = int(kw.get("count", cfg.get("ping_count", 20)))
    a.interval = float(kw.get("interval", cfg.get("ping_interval", 0.5)))
    a.timeout = int(kw.get("timeout", cfg.get("ping_timeout", 2)))
    a.dns_count = int(kw.get("dns_count", cfg.get("dns_count", 10)))
    a.tcp_count = int(kw.get("tcp_count", cfg.get("tcp_count", 10)))
    a.quiet = True
    a.no_bufferbloat = not IS_LINUX or not kw.get("bufferbloat", True)
    a.no_trace = not kw.get("trace", True)
    a.no_speedtest = not kw.get("speedtest", False)
    a.no_iperf = not kw.get("iperf3", False)
    a.download_test = kw.get("download_test", False)
    a.connection_test = kw.get("connection_test", False)
    a.outdir = "internet_diagnostics"
    a.history_dir = cfg.get("history_dir", "~/.netdiag")
    return a


TOOLS_MENU = [
    # Layer 1 - Physical
    {"id": "interface_stats", "name": "Interface Statistics", "layer": 1, "layer_name": "Physical (L1)",
     "desc": "Read RX/TX errors, drops, overruns, carrier changes from the default network interface.",
     "docs": "Command: ip -s link / ifconfig / netstat -e / sysfs (stdlib fallback)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: interface_stats(get_default_interface())},
    {"id": "ethtool_info", "name": "Ethtool (Link / Duplex / Speed)", "layer": 1, "layer_name": "Physical (L1)",
     "desc": "Check Ethernet link status, negotiated speed, and duplex mode (Linux only, requires ethtool).",
     "docs": "Command: ethtool <iface>  |  Plan B: parsed from interface_stats",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: ethtool_info(get_default_interface())},
    # Layer 2 - Data Link
    {"id": "wifi_info", "name": "WiFi Info & Survey", "layer": 2, "layer_name": "Data Link (L2)",
     "desc": "Detect wireless interface, signal strength (dBm), noise, channel utilization, and link quality.",
     "docs": "Command: iw dev / iw survey dump / airport / netsh wlan / procfs (stdlib fallback)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: wifi_info(get_default_interface())},
    # Layer 3 - Network
    {"id": "ping_test", "name": "Ping (ICMP Echo)", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Send ICMP echo requests and measure RTT, packet loss, jitter, and latency distribution (p95).",
     "docs": "Command: ping -c <count> -W <timeout> <host>  |  Plan B: TCP connect RTT via socket",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 10, "min": 1, "max": 200},
         {"key": "interval", "label": "Interval (s)", "type": "number", "default": 0.5, "min": 0.1, "max": 10, "step": 0.1},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 2, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Quick (3 pings)", "values": {"host": "1.1.1.1", "count": 3, "interval": 0.2, "timeout": 2}},
         {"name": "Standard (20 pings)", "values": {"host": "1.1.1.1", "count": 20, "interval": 0.5, "timeout": 2}},
         {"name": "Stress (100 pings)", "values": {"host": "1.1.1.1", "count": 100, "interval": 0.1, "timeout": 3}},
     ],
     "run": lambda kw: ping_burst(kw.get("host", "1.1.1.1"), int(kw.get("count", 10)), float(kw.get("interval", 0.5)), timeout_s=int(kw.get("timeout", 2)), label="tool_ping")},
    {"id": "mtr_test", "name": "MTR / Traceroute", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Trace the route to a target with per-hop loss and latency. Falls back to traceroute or native ping TTL sweep.",
     "docs": "Command: mtr -r -c <count> <host>  |  Plan B: traceroute -n  |  Plan C: ping -t TTL sweep",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Probes per hop", "type": "number", "default": 10, "min": 1, "max": 100},
     ],
     "presets": [
         {"name": "Quick (5 probes)", "values": {"host": "1.1.1.1", "count": 5}},
         {"name": "Standard (10 probes)", "values": {"host": "1.1.1.1", "count": 10}},
         {"name": "Deep (30 probes)", "values": {"host": "1.1.1.1", "count": 30}},
     ],
     "run": lambda kw: mtr_test(kw.get("host", "1.1.1.1"), count=int(kw.get("count", 10)))},
    {"id": "mtu_probe", "name": "Path MTU Discovery", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Probe the maximum transmission unit along the path using ping with incrementing packet sizes.",
     "docs": "Command: ping -c 1 -M do -s <size> <host> (Linux) / ping -c 1 -D -s <size> <host> (macOS)",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "max_size", "label": "Max packet size (bytes)", "type": "number", "default": 1500, "min": 68, "max": 9000},
     ],
     "presets": [
         {"name": "Standard (1500)", "values": {"host": "1.1.1.1", "max_size": 1500}},
         {"name": "Jumbo frames (9000)", "values": {"host": "1.1.1.1", "max_size": 9000}},
     ],
     "run": lambda kw: mtu_probe(kw.get("host", "1.1.1.1"), max_size=int(kw.get("max_size", 1500)))},
    {"id": "detect_gateway", "name": "Gateway Detection", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Detect the default gateway IP address using ip route / route -n get / netstat -rn / procfs.",
     "docs": "Command: ip route show default / route -n get default / netstat -rn  |  Plan B: /proc/net/route",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: {"gateway": detect_gateway(), "interface": get_default_interface()}},
    # Layer 4 - Transport
    {"id": "tcp_test", "name": "TCP Connect Test", "layer": 4, "layer_name": "Transport (L4)",
     "desc": "Measure TCP handshake latency to a target host:port. Detects firewall drops, timeouts, and reachability issues.",
     "docs": "Method: socket.create_connection() — stdlib only, no external tool required.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "port", "label": "Port", "type": "number", "default": 443, "min": 1, "max": 65535},
         {"key": "count", "label": "Attempts", "type": "number", "default": 5, "min": 1, "max": 100},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 3, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Quick (3 attempts)", "values": {"host": "1.1.1.1", "port": 443, "count": 3, "timeout": 3}},
         {"name": "Standard (10 attempts)", "values": {"host": "1.1.1.1", "port": 443, "count": 10, "timeout": 3}},
         {"name": "Common ports", "values": {"host": "google.com", "port": 80, "count": 5, "timeout": 3}},
     ],
     "run": lambda kw: tcp_test(kw.get("host", "1.1.1.1"), int(kw.get("port", 443)), count=int(kw.get("count", 5)), timeout_s=int(kw.get("timeout", 3)))},
    {"id": "tcp_socket_stats", "name": "TCP Socket Stats (Retransmits)", "layer": 4, "layer_name": "Transport (L4)",
     "desc": "Read TCP retransmit percentage from the system. High retransmits indicate congestion or link issues.",
     "docs": "Command: ss -itp / nettop -J tcp / netstat -s  |  Plan B: /proc/net/tcp (connection count only)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: tcp_socket_stats(get_default_interface())},
    {"id": "iperf3_test", "name": "iPerf3 Throughput", "layer": 4, "layer_name": "Transport (L4)",
     "desc": "Measure TCP/UDP throughput to an iPerf3 server. Tests raw bandwidth capacity and detects retransmits.",
     "docs": "Command: iperf3 -c <server> -t <duration> -J  |  Requires iperf3 server on the remote end.",
     "params": [
         {"key": "server", "label": "iPerf3 server (optional)", "type": "text", "default": ""},
         {"key": "duration", "label": "Test duration (s)", "type": "number", "default": 10, "min": 5, "max": 60},
     ],
     "presets": [
         {"name": "Quick (5s)", "values": {"server": "", "duration": 5}},
         {"name": "Standard (10s)", "values": {"server": "", "duration": 10}},
         {"name": "Long (30s)", "values": {"server": "", "duration": 30}},
     ],
     "run": lambda kw: iperf3_test(server=kw.get("server") or None, duration=int(kw.get("duration", 10)))},
    # Layer 5-7 - Application
    {"id": "dns_test", "name": "DNS Resolution Test", "layer": 5, "layer_name": "Application (L5-7)",
     "desc": "Measure DNS resolution latency and failure rate using socket.getaddrinfo().",
     "docs": "Method: socket.getaddrinfo() — stdlib only, no external tool required.",
     "params": [
         {"key": "host", "label": "Hostname to resolve", "type": "text", "default": "google.com"},
         {"key": "count", "label": "Queries", "type": "number", "default": 10, "min": 1, "max": 100},
     ],
     "presets": [
         {"name": "Quick (3 queries)", "values": {"host": "google.com", "count": 3}},
         {"name": "Standard (10 queries)", "values": {"host": "google.com", "count": 10}},
         {"name": "All hosts", "values": {"host": "google.com", "count": 10}},
     ],
     "run": lambda kw: dns_test(kw.get("host", "google.com"), count=int(kw.get("count", 10)))},
    {"id": "http_latency", "name": "HTTP Latency Test", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Measure HTTP request latency to multiple endpoints. Detects slow web servers or CDN issues.",
     "docs": "Method: urllib.request — stdlib only.",
     "params": [
         {"key": "hosts", "label": "URLs (comma-separated)", "type": "text", "default": "https://1.1.1.1,https://8.8.8.8,https://google.com"},
         {"key": "count", "label": "Requests per host", "type": "number", "default": 3, "min": 1, "max": 20},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 5, "min": 1, "max": 15},
     ],
     "presets": [
         {"name": "Quick (1 request)", "values": {"hosts": "https://1.1.1.1,https://google.com", "count": 1, "timeout": 5}},
         {"name": "Standard (3 requests)", "values": {"hosts": "https://1.1.1.1,https://8.8.8.8,https://google.com", "count": 3, "timeout": 5}},
     ],
     "run": lambda kw: http_latency_test(hosts=[h.strip() for h in kw.get("hosts", "https://1.1.1.1").split(",") if h.strip()], count=int(kw.get("count", 3)), timeout_s=int(kw.get("timeout", 5)))},
    {"id": "speedtest", "name": "Speedtest (Ookla)", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Measure download/upload speed and latency using Ookla's speedtest.net infrastructure.",
     "docs": "Command: speedtest --format=json  |  Plan B: speedtest-cli --json",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: speedtest_result()},
    {"id": "download_test", "name": "Download Test (Images)", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Download images from multiple CDNs to measure real-world HTTP download throughput.",
     "docs": "Method: urllib.request on a set of known image URLs — stdlib only.",
     "params": [
         {"key": "count", "label": "Images to download", "type": "number", "default": 50, "min": 1, "max": 200},
         {"key": "timeout", "label": "Timeout per image (s)", "type": "number", "default": 10, "min": 5, "max": 30},
     ],
     "presets": [
         {"name": "Quick (10 images)", "values": {"count": 10, "timeout": 10}},
         {"name": "Standard (50 images)", "values": {"count": 50, "timeout": 10}},
         {"name": "Heavy (100 images)", "values": {"count": 100, "timeout": 15}},
     ],
     "run": lambda kw: download_images_test(count=int(kw.get("count", 50)), timeout_s=int(kw.get("timeout", 10)))},
     {"id": "bufferbloat", "name": "Bufferbloat Test", "layer": 7, "layer_name": "Application (L5-7)",
     "desc": "Run concurrent ping+iPerf3 to measure latency under load. High ratios (>3x) indicate bufferbloat.",
     "docs": "Command: tc -s qdisc + iperf3 (Linux enhanced)  |  Plan B: iperf3 concurrent ping (non-Linux)",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: bufferbloat_test(get_default_interface())},
    # Additional standalone tools
    {"id": "quick_ping", "name": "Quick Ping (Single)", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Send a single ICMP echo request for an instant reachability and RTT check. Faster than the burst ping.",
     "docs": "Command: ping -c 1 -W <timeout> <host>  |  Plan B: TCP connect RTT via socket",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 2, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Cloudflare", "values": {"host": "1.1.1.1", "timeout": 2}},
         {"name": "Google", "values": {"host": "8.8.8.8", "timeout": 2}},
         {"name": "Gateway", "values": {"host": "", "timeout": 2}},
     ],
     "run": lambda kw: {"tool": "quick_ping", "gateway_hint": detect_gateway(), **ping_once(kw.get("host", "1.1.1.1"), timeout_s=int(kw.get("timeout", 2)))}},
    {"id": "dns_resolve", "name": "DNS Resolve (Single)", "layer": 5, "layer_name": "Application (L5-7)",
     "desc": "Resolve a hostname to IP addresses using a single DNS query. Quick check if DNS is working.",
     "docs": "Method: socket.getaddrinfo() — stdlib only, no external tool required.",
     "params": [
         {"key": "host", "label": "Hostname to resolve", "type": "text", "default": "google.com"},
     ],
     "presets": [
         {"name": "google.com", "values": {"host": "google.com"}},
         {"name": "cloudflare.com", "values": {"host": "cloudflare.com"}},
         {"name": "quad9.net", "values": {"host": "quad9.net"}},
     ],
     "run": lambda kw: resolve_all(kw.get("host", "google.com"))},
    {"id": "classify_ping", "name": "Ping Classification", "layer": 3, "layer_name": "Network (L3)",
     "desc": "Run a ping burst then classify the result into categories: clean, bad_loss, some_loss, latency_spikes, high_jitter.",
     "docs": "Classification thresholds: loss>=5%→bad_loss, loss>=1%→some_loss, p95>=300ms→bad_latency_spikes, p95>=150ms→latency_spikes, jitter>=80ms→high_jitter.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 10, "min": 1, "max": 100},
         {"key": "timeout", "label": "Timeout (s)", "type": "number", "default": 2, "min": 1, "max": 10},
     ],
     "presets": [
         {"name": "Quick (5 pings)", "values": {"host": "1.1.1.1", "count": 5, "timeout": 2}},
         {"name": "Standard (20 pings)", "values": {"host": "1.1.1.1", "count": 20, "timeout": 2}},
     ],
     "run": lambda kw: {"classification": classify_ping(ping_burst(kw.get("host", "1.1.1.1"), int(kw.get("count", 10)), 0.5, timeout_s=int(kw.get("timeout", 2)), label="classify")), "host": kw.get("host", "1.1.1.1")}},
    {"id": "check_tools", "name": "Tool Availability Check", "layer": 0, "layer_name": "System",
     "desc": "Check which external command-line tools (ping, ip, mtr, iperf3, speedtest, etc.) are installed and available.",
     "docs": "Scans PATH for required and optional diagnostic tools. Missing optional tools reduce diagnostic detail but stdlib fallbacks are always available.",
     "params": [],
     "presets": [{"name": "Default", "values": {}}],
     "run": lambda kw: check_tools()},
    {"id": "full_diagnostic", "name": "Full Diagnostic (All Layers)", "layer": 0, "layer_name": "All Layers",
     "desc": "Orchestrate every probe in sequence: interface, wifi, ethtool, gateway ping, internet ping, DNS, TCP, MTR, speedtest, iPerf3, bufferbloat, download test, HTTP latency, MTU probe.",
     "docs": "This is the same engine used by Troubleshoot/Dashboard, exposed here with every toggle for fine-grained control. Caution: enabling all probes can take 60-120s.",
     "params": [
         {"key": "hosts", "label": "Ping hosts (comma-separated)", "type": "text", "default": "1.1.1.1,8.8.8.8"},
         {"key": "count", "label": "Ping count per host", "type": "number", "default": 5, "min": 1, "max": 200},
         {"key": "speedtest", "label": "Run speedtest", "type": "checkbox", "default": False},
         {"key": "trace", "label": "Run MTR trace", "type": "checkbox", "default": False},
         {"key": "bufferbloat", "label": "Run bufferbloat test", "type": "checkbox", "default": False},
         {"key": "iperf3", "label": "Run iPerf3", "type": "checkbox", "default": False},
         {"key": "download_test", "label": "Download test", "type": "checkbox", "default": False},
         {"key": "connection_test", "label": "HTTP latency + MTU", "type": "checkbox", "default": False},
     ],
     "presets": [
         {"name": "Minimal (ping only)", "values": {"hosts": "1.1.1.1", "count": 5, "speedtest": False, "trace": False, "bufferbloat": False, "iperf3": False, "download_test": False, "connection_test": False}},
         {"name": "Standard diagnostic", "values": {"hosts": "1.1.1.1,8.8.8.8", "count": 10, "speedtest": False, "trace": False, "bufferbloat": False, "iperf3": False, "download_test": False, "connection_test": False}},
         {"name": "Full (everything)", "values": {"hosts": "1.1.1.1,8.8.8.8,google.com", "count": 20, "speedtest": True, "trace": True, "bufferbloat": True, "iperf3": True, "download_test": True, "connection_test": True}},
     ],
     "run": lambda kw: full_diagnostic(_diag_args_from_kw(kw))},
    {"id": "diagnose_engine", "name": "Diagnose Results (Analysis)", "layer": 0, "layer_name": "All Layers",
     "desc": "Run the 5-layer diagnostic rule engine on fresh results. Analyzes interface errors, WiFi signal, gateway stability, ISP routing, and internet health.",
     "docs": "Five layers: Physical (L1) → WiFi (L2) → Gateway (L3) → ISP (L3-L4) → Internet (L5-7). Each diagnosis includes severity, title, detail, and fix recommendation.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 5, "min": 1, "max": 20},
     ],
     "presets": [
         {"name": "Quick (3 pings)", "values": {"host": "1.1.1.1", "count": 3}},
         {"name": "Standard (5 pings)", "values": {"host": "1.1.1.1", "count": 5}},
     ],
     "run": lambda kw: {"diagnosis": diagnose(full_diagnostic(_diag_args_from_kw({"hosts": kw.get("host", "1.1.1.1"),"count": int(kw.get("count", 5)),"trace": False,"bufferbloat": False,"speedtest": False,"iperf3": False,"download_test": False,"connection_test": False})))}},
    {"id": "health_score_tool", "name": "Health Score Calculator", "layer": 0, "layer_name": "All Layers",
     "desc": "Compute the composite 0-100 health score from a fresh diagnostic run. Weighted: interface 10%, wifi 15%, gateway 25%, internet 25%, dns 10%, tcp 5%, bufferbloat 10%.",
     "docs": "Formula: weighted average of per-layer scores. Score >=70 = clean, 40-69 = warning, <40 = bad.",
     "params": [
         {"key": "host", "label": "Target host", "type": "text", "default": "1.1.1.1"},
         {"key": "count", "label": "Ping count", "type": "number", "default": 5, "min": 1, "max": 20},
     ],
     "presets": [
         {"name": "Quick (3 pings)", "values": {"host": "1.1.1.1", "count": 3}},
         {"name": "Standard (5 pings)", "values": {"host": "1.1.1.1", "count": 5}},
     ],
     "run": lambda kw: {"health_score": health_score(full_diagnostic(_diag_args_from_kw({"hosts": kw.get("host", "1.1.1.1"),"count": int(kw.get("count", 5)),"trace": False,"bufferbloat": False,"speedtest": False,"iperf3": False,"download_test": False,"connection_test": False})))}},
]


def build_app():
    try:
        from fastapi import FastAPI, Request, Response
        from fastapi.responses import HTMLResponse, JSONResponse
        import asyncio
        import threading
    except ImportError:
        return None, None, None

    app = FastAPI(title="NetDiag")
    lock = threading.Lock()
    current_run = {"status": "idle", "progress": {}, "results": None, "error": None}

    def run_diag(args, run_state):
        try:
            run_state["status"] = "running"
            run_state["progress"] = {}
            run_state["results"] = None
            run_state["error"] = None

            def cb(label, seq, total, ok, rtt, status_override=None):
                st = status_override or ("running" if seq < total else "done")
                with lock:
                    run_state["progress"][label] = {
                        "seq": seq, "total": total, "ok": ok,
                        "rtt_ms": rtt, "status": st, "label": label}

            results = full_diagnostic(args, callback=cb)
            with lock:
                run_state["status"] = "done"
                run_state["results"] = results
                save_history(args.history_dir, results)
        except Exception as e:
            with lock:
                run_state["status"] = "error"
                run_state["error"] = str(e)

    TEMPLATE_DIR = Path(__file__).parent / "templates"
    INDEX_FILE = TEMPLATE_DIR / "index.html"
    REPORT_DIR = Path.cwd() / "internet_diagnostics"

    INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><title>NetDiag</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f172a;--fg:#e2e8f0;--card:#1e293b;--border:#334155;--accent:#38bdf8;--green:#22c55e;--yellow:#eab308;--red:#ef4444;--info:#64748b;--orange:#f97316}
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);min-height:100vh}
header{background:var(--card);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
header h1{font-size:18px;color:var(--accent)}
nav{display:flex;gap:4px}
nav button{padding:8px 16px;border:none;background:none;color:var(--fg);cursor:pointer;border-radius:6px;font-size:13px}
nav button:hover,nav button.active{background:var(--border)}
main{padding:20px;max-width:1100px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px}
.card h2{font-size:15px;margin-bottom:12px;color:var(--fg)}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.metric{text-align:center;padding:12px}
.metric .value{font-size:28px;font-weight:600}
.metric .label{font-size:11px;color:var(--info);margin-top:4px}
.metric.green .value{color:var(--green)}
.metric.yellow .value{color:var(--yellow)}
.metric.red .value{color:var(--red)}
.stack-layer{display:flex;align-items:center;padding:12px 16px;border-radius:6px;margin:4px 0;cursor:pointer;border:1px solid var(--border);transition:background .15s}
.stack-layer:hover{background:#33415544}
.stack-icon{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;margin-right:12px;flex-shrink:0}
.stack-icon.clean{background:#22c55e33;color:var(--green)}
.stack-icon.warning{background:#eab30833;color:var(--yellow)}
.stack-icon.bad{background:#ef444433;color:var(--red)}
.stack-icon.info{background:#64748b33;color:var(--info)}
.stack-icon.running{background:#38bdf833;color:var(--accent);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.stack-body{flex:1}
.stack-title{font-weight:600;font-size:13px}
.stack-detail{font-size:11px;color:var(--info);margin-top:2px}
.stack-fix{font-size:12px;margin-top:6px;padding:8px 12px;background:#1e293b;border-radius:4px;border-left:3px solid var(--accent);display:none}
.stack-layer.expanded .stack-fix{display:block}
.btn{background:var(--accent);color:#000;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}
.btn:hover{opacity:.9}
.btn:disabled{opacity:.5;cursor:default}
.btn-secondary{background:var(--border);color:var(--fg)}
.btn-orange{background:var(--orange);color:#fff;font-size:16px;padding:14px 36px;box-shadow:0 0 24px rgba(249,115,22,0.45);transition:all .2s}
.btn-orange:hover{box-shadow:0 0 36px rgba(249,115,22,0.65);transform:translateY(-1px)}
.btn-orange:disabled{box-shadow:none;transform:none}
@keyframes pulse-orange{0%,100%{box-shadow:0 0 24px rgba(249,115,22,0.45)}50%{box-shadow:0 0 48px rgba(249,115,22,0.8)}}
.progress-bar{height:4px;background:var(--border);border-radius:2px;margin:8px 0;overflow:hidden}
.progress-bar-fill{height:100%;background:var(--orange);transition:width .3s}
.chart-container{height:200px;margin:12px 0}
table{width:100%;border-collapse:collapse}
th,td{padding:8px 12px;text-align:left;font-size:12px;border-bottom:1px solid var(--border)}
th{color:var(--info);font-weight:500}
.event-bar{display:flex;align-items:center;padding:6px 0;gap:10px;font-size:12px}
.event-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.event-dot.bad{background:var(--red)}.event-dot.warning{background:var(--yellow)}.event-dot.clean{background:var(--green)}
.sessions-list{display:flex;flex-direction:column;gap:8px}
.session-row{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:var(--card);border:1px solid var(--border);border-radius:6px}
.session-info .time{font-weight:600}.session-info .summary{font-size:12px;color:var(--info)}
.session-actions{display:flex;gap:8px}
.export-section{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap}
.tab-content{display:none}
.tab-content.active{display:block}
#log-output{max-height:300px;overflow-y:auto;font-family:monospace;font-size:11px;line-height:1.8;background:#0f172a;padding:12px;border-radius:4px;display:none}
.prog-entry{display:flex;align-items:center;padding:5px 10px;margin:1px 0;border-radius:4px;gap:8px;font-size:12px;transition:background .2s}
.prog-entry.running{background:#38bdf808}
.prog-entry.clean{background:#22c55e08}
.prog-entry.warning{background:#eab30808}
.prog-entry.bad{background:#ef444408}
.prog-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:1px}
.prog-dot.running{background:var(--accent);animation:pulse 1s infinite}
.prog-dot.clean{background:var(--green)}
.prog-dot.warning{background:var(--yellow)}
.prog-dot.bad{background:var(--red)}
.prog-name{font-weight:600;min-width:100px;font-size:12px;white-space:nowrap}
.prog-result{margin-left:auto;font-family:monospace;font-size:11px;color:var(--info);text-align:right}
.health-live{display:flex;align-items:center;gap:10px;padding:10px 14px;margin-top:8px;border-radius:6px;border:1px solid var(--border);background:var(--card)}
.health-live-label{font-size:12px;font-weight:600;color:var(--info);white-space:nowrap}
.health-bar-outer{height:8px;border-radius:4px;flex:1;background:var(--border);overflow:hidden;min-width:60px}
.health-bar-inner{height:100%;border-radius:4px;transition:width .5s ease,background .5s ease}
.health-bar-inner.good{background:var(--green)}
.health-bar-inner.warning{background:var(--yellow)}
.health-bar-inner.bad{background:var(--red)}
.health-live-val{font-size:14px;font-weight:700;min-width:36px;text-align:right}

/* Options panel */
.options-toggle{font-size:12px;color:var(--info);cursor:pointer;display:inline-flex;align-items:center;gap:4px;margin-top:8px;padding:6px 12px;border-radius:4px;background:var(--card);border:1px solid var(--border)}
.options-toggle:hover{background:var(--border)}
.options-panel{display:none;padding:16px;margin-top:8px;background:var(--card);border:1px solid var(--border);border-radius:6px}
.options-panel.open{display:block}
.option-row{display:flex;align-items:center;gap:10px;padding:6px 0}
.option-row label{font-size:13px;cursor:pointer}
.option-row input[type=checkbox]{cursor:pointer;accent-color:var(--orange);width:16px;height:16px}

/* Live Monitor */
.live-controls{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.live-timer{font-size:14px;font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums;letter-spacing:1px}
#live-container{text-align:center}
.live-signal{display:inline-flex;flex-direction:column;align-items:center;margin:12px 0}
.live-signal-circle{width:160px;height:160px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;font-weight:700;position:relative;transition:background .5s,box-shadow .5s}
.live-signal-circle.green{background:radial-gradient(circle at 30% 30%,#22c55e44,#22c55e22);box-shadow:0 0 40px #22c55e44;border:3px solid var(--green)}
.live-signal-circle.yellow{background:radial-gradient(circle at 30% 30%,#eab30844,#eab30822);box-shadow:0 0 40px #eab30844;border:3px solid var(--yellow)}
.live-signal-circle.red{background:radial-gradient(circle at 30% 30%,#ef444444,#ef444422);box-shadow:0 0 40px #ef444444;border:3px solid var(--red)}
.live-signal-circle .sig-value{font-size:42px;line-height:1}
.live-signal-circle .sig-unit{font-size:14px;opacity:.7;margin-top:2px}
.live-signal-circle .sig-label{font-size:11px;opacity:.6;margin-top:4px}
.live-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-top:16px}
.live-stat{text-align:center;padding:12px 8px;background:var(--card);border:1px solid var(--border);border-radius:6px}
.live-stat .value{font-size:22px;font-weight:600}
.live-stat .label{font-size:10px;color:var(--info);margin-top:2px}
.live-stat .value.green{color:var(--green)}.live-stat .value.yellow{color:var(--yellow)}.live-stat .value.red{color:var(--red)}
.live-chart{height:160px;margin:16px 0}
.live-health-bar{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:6px;border:1px solid var(--border);background:var(--card);margin-top:12px}
.live-health-bar .label{font-size:12px;font-weight:600;color:var(--info);white-space:nowrap}
.live-health-bar .bar{height:10px;border-radius:5px;flex:1;background:var(--border);overflow:hidden}
.live-health-bar .bar-inner{height:100%;border-radius:5px;transition:width .5s,background .5s}
.live-health-bar .bar-inner.good{background:var(--green)}
.live-health-bar .bar-inner.warning{background:var(--yellow)}
.live-health-bar .bar-inner.bad{background:var(--red)}
.live-health-bar .val{font-size:16px;font-weight:700;min-width:36px;text-align:right}
.live-session-stats{display:flex;gap:24px;margin-top:16px;padding:12px 16px;border-radius:6px;border:1px solid var(--border);background:var(--card)}
.live-session-stats .stat-group{flex:1}
.live-session-stats .stat-title{font-size:11px;font-weight:600;color:var(--info);margin-bottom:6px}
.live-session-stats .stat-row{display:flex;gap:16px;font-size:12px;color:var(--info)}
.live-session-stats .stat-row b{color:var(--fg);font-weight:600}
.live-session-stats .stat-row .value{color:var(--fg)}
.quality-table{width:100%;border-collapse:collapse;margin-top:12px}
.quality-table th,.quality-table td{padding:6px 10px;font-size:12px;text-align:right;border-bottom:1px solid var(--border)}
.quality-table th:first-child,.quality-table td:first-child{text-align:left}
.quality-table td.loss-ok{color:var(--green)}
.quality-table td.loss-warn{color:var(--yellow)}
.quality-table td.loss-bad{color:var(--red)}
.hint-list{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.hint{padding:10px 12px;border-radius:6px;font-size:12px;border-left:3px solid var(--info);background:var(--bg)}
.hint.warning{border-color:var(--yellow)}
.hint.bad{border-color:var(--red)}
.hint.info{border-color:var(--accent)}
.hint.clean{border-color:var(--green)}
.event-list{display:flex;flex-direction:column;gap:6px;margin-top:8px;max-height:200px;overflow-y:auto}
.event-row{display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--info);padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:4px}
.event-row b{color:var(--fg)}
.empty-note{font-size:12px;color:var(--info);padding:8px 0}
.activity-list{display:flex;flex-direction:column;gap:4px;max-height:240px;overflow-y:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;margin-top:8px}
.activity-row{display:flex;gap:8px;padding:4px 8px;border-radius:4px;background:var(--bg);border:1px solid var(--border);align-items:center}
.activity-row .ok{color:var(--green)}
.activity-row .fail{color:var(--red)}
.activity-row .ts{color:var(--info);white-space:nowrap}
.activity-row .alabel{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.activity-row .dur{color:var(--info);white-space:nowrap}
.activity-row .kind{color:var(--info);white-space:nowrap;text-transform:uppercase;font-size:9px;opacity:.7}
.settings-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-top:12px}
.settings-field{display:flex;flex-direction:column;gap:4px}
.settings-field label{font-size:12px;color:var(--info)}
.settings-field input{padding:6px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg);font-size:13px}
.settings-actions{margin-top:16px;display:flex;gap:8px;align-items:center}
.settings-status{font-size:12px;color:var(--info)}
.tools-table td.tool-ok{color:var(--green);text-align:right}
.tools-table td.tool-missing{color:var(--red);text-align:right}
/* Tools tab */
.tool-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px;transition:border-color .2s}
.tool-card.running{border-color:var(--orange)}
.tool-card.error{border-color:var(--red)}
.tool-card.done{border-color:var(--green)}
.tool-layer{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--accent);font-weight:600;margin-bottom:2px}
.tool-card h3{font-size:15px;margin:2px 0 4px}
.tool-desc{font-size:12px;color:var(--info);margin-bottom:8px;line-height:1.5}
.tool-docs{font-size:11px;color:var(--info);background:var(--bg);padding:6px 10px;border-radius:4px;margin-bottom:10px;font-family:monospace}
.tool-presets{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.tool-preset-btn{font-size:11px;padding:4px 12px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);cursor:pointer}
.tool-preset-btn:hover{background:var(--border);color:var(--accent)}
.tool-params{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin-bottom:10px;padding:10px;background:var(--bg);border-radius:6px}
.tool-param-row{display:flex;flex-direction:column;gap:2px}
.tool-param-row label{font-size:11px;color:var(--info)}
.tool-param-row input{padding:5px 8px;background:var(--card);border:1px solid var(--border);border-radius:4px;color:var(--fg);font-size:12px}
.tool-param-row input:focus{border-color:var(--accent);outline:none}
.tool-actions{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.tool-status{font-size:12px;color:var(--info)}
.tool-result{padding:10px;background:#0f172a;border-radius:6px;font-family:monospace;font-size:11px;line-height:1.6;overflow-x:auto;max-height:400px;overflow-y:auto;display:none;white-space:pre-wrap;color:var(--fg)}
.tool-result.show{display:block}
.tool-result .rkey{color:var(--accent)}
.tool-result .rval{color:var(--green)}
.tool-result .rerr{color:var(--red)}
.tool-result .rwarn{color:var(--yellow)}
.tool-err{border:1px solid var(--red);background:#ef444411}
@media(max-width:640px){.metrics{grid-template-columns:1fr 1fr}nav button{padding:6px 10px;font-size:11px}.live-signal-circle{width:120px;height:120px}.live-signal-circle .sig-value{font-size:32px}}
</style>
</head>
<body>
<header>
<h1>NetDiag</h1>
<nav>
<button class="active" data-tab="dashboard">Dashboard</button>
<button data-tab="troubleshoot">Troubleshoot</button>
<button data-tab="monitor">Live Monitor</button>
<button data-tab="history">History & Reports</button>
<button data-tab="tools">Tools</button>
<button data-tab="settings">Settings</button>
<button data-tab="about">About</button>
</nav>
</header>
<main>
<div id="tab-dashboard" class="tab-content active">
<div style="text-align:center;margin-bottom:24px">
<button class="btn btn-orange" id="dash-run-btn" onclick="dashRunDiag()">Start Diagnosis</button>
<span id="dash-run-status" style="margin-left:12px;font-size:13px;color:var(--info)"></span>
</div>
<div id="dash-progress" style="display:none">
<div class="progress-bar"><div class="progress-bar-fill" id="dash-progress-fill" style="width:0%"></div></div>
<div style="font-size:11px;color:var(--info);text-align:center;margin-top:4px" id="dash-progress-label"></div>
</div>
<div class="metrics">
<div class="metric">
<div class="value" id="health-val">--</div>
<div class="label">Health Score</div>
</div>
<div class="metric">
<div class="value" id="sig-val">--</div>
<div class="label">WiFi Signal</div>
</div>
<div class="metric">
<div class="value" id="gw-val">--</div>
<div class="label">Gateway Latency</div>
</div>
<div class="metric">
<div class="value" id="spd-val">--</div>
<div class="label">Speed</div>
</div>
</div>
<div class="card">
<h2>Connection Health</h2>
<canvas id="scoreChart" width="240" height="120" style="max-height:120px"></canvas>
<div id="events-list"></div>
</div>
</div>

<div id="tab-troubleshoot" class="tab-content">
<div class="card">
<h2>Network Diagnostic</h2>
<div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px">
<button class="btn" id="run-btn" onclick="runDiagnostic()">Run Full Diagnostic</button>
<span id="run-status" style="font-size:12px;color:var(--info)"></span>
</div>
<div class="options-toggle" onclick="toggleOptions()">&#x25B6; Options</div>
<div class="options-panel" id="options-panel">
<div class="option-row"><input type="checkbox" id="opt-speedtest" checked><label for="opt-speedtest">Speed test</label></div>
<div class="option-row"><input type="checkbox" id="opt-download"><label for="opt-download">Download test (100 images)</label></div>
<div class="option-row"><input type="checkbox" id="opt-connection"><label for="opt-connection">Connection testing (HTTP latency + MTU)</label></div>
<div class="option-row"><input type="checkbox" id="opt-trace" checked><label for="opt-trace">Route trace (MTR)</label></div>
<div class="option-row"><input type="checkbox" id="opt-bufferbloat" checked><label for="opt-bufferbloat">Bufferbloat test</label></div>
<div class="option-row"><input type="checkbox" id="opt-iperf3"><label for="opt-iperf3">iPerf3 throughput</label></div>
</div>
<div id="progress-container" style="display:none">
<div class="progress-bar"><div class="progress-bar-fill" id="progress-fill" style="width:0%"></div></div>
<div style="font-size:11px;color:var(--info)" id="progress-label"></div>
</div>
<div id="log-output"></div>
<div id="prog-list" style="margin-top:8px;max-height:300px;overflow-y:auto;display:none"></div>
<div id="health-live" class="health-live" style="display:none">
  <span class="health-live-label">Live Health</span>
  <div class="health-bar-outer"><div class="health-bar-inner" id="health-bar-inner" style="width:0%"></div></div>
  <span class="health-live-val" id="health-live-val">--</span>
</div>
</div>
<div class="card" id="stack-card" style="display:none">
<h2>Network Stack</h2>
<div id="stack-layers"></div>
</div>
<div class="card">
<h2>Under the Hood</h2>
<p style="font-size:12px;color:var(--info);margin-bottom:4px">Commands and probes NetDiag has actually run, most recent first.</p>
<div id="activity-list" class="activity-list"><div class="empty-note">No activity yet.</div></div>
</div>
</div>

<div id="tab-monitor" class="tab-content">
<div class="card">
<h2>Live Monitor</h2>
<p style="font-size:12px;color:var(--info);margin-bottom:12px">Move around with your laptop to see real-time signal changes, and watch a background sampler probe your gateway, the internet, DNS and TCP every second to catch intermittent issues. Refreshes every 1.5s.</p>
<div class="live-controls">
<button class="btn btn-orange" id="live-toggle-btn" onclick="toggleLiveMonitor()">Start Monitoring</button>
<span class="live-timer" id="live-timer">00:00</span>
</div>
<div id="live-container" style="display:none">
<div class="live-signal">
<div class="live-signal-circle" id="live-circle">
<span class="sig-value" id="live-sig-val">--</span>
<span class="sig-unit">dBm</span>
<span class="sig-label">WiFi Signal</span>
</div>
<span style="font-size:11px;color:var(--info);margin-top:6px" id="live-sig-text">Waiting...</span>
<span style="font-size:14px;font-weight:600;margin-top:2px;color:var(--fg)" id="live-ssid">--</span>
</div>
<div class="live-stats">
<div class="live-stat"><div class="value" id="live-latency">--</div><div class="label">Latency (ms)</div></div>
<div class="live-stat"><div class="value" id="live-noise">--</div><div class="label">Noise (dBm)</div></div>
<div class="live-stat"><div class="value" id="live-channel">--</div><div class="label">Channel Util %</div></div>
<div class="live-stat"><div class="value" id="live-iface">--</div><div class="label">Interface</div></div>
<div class="live-stat"><div class="value" id="live-ssid-stat">--</div><div class="label">Network</div></div>
</div>
<div class="live-chart">
<canvas id="liveChart" height="160"></canvas>
</div>
<div class="live-health-bar">
<span class="label">Live Health</span>
<div class="bar"><div class="bar-inner" id="live-health-bar" style="width:50%"></div></div>
<span class="val" id="live-health-val">--</span>
</div>
<div class="live-session-stats">
<div class="stat-group">
<div class="stat-title">Signal (dBm)</div>
<div class="stat-row">
<span>Avg: <b id="stat-sig-avg">--</b></span>
<span>Med: <b id="stat-sig-med">--</b></span>
<span>Min: <b id="stat-sig-min">--</b></span>
<span>Max: <b id="stat-sig-max">--</b></span>
</div>
</div>
<div class="stat-group">
<div class="stat-title">Latency (ms)</div>
<div class="stat-row">
<span>Avg: <b id="stat-lat-avg">--</b></span>
<span>Med: <b id="stat-lat-med">--</b></span>
<span>Min: <b id="stat-lat-min">--</b></span>
<span>Max: <b id="stat-lat-max">--</b></span>
</div>
</div>
</div>
</div>
</div>

<div class="card" id="quality-card" style="display:none">
<h2>Connection Quality (last 3 min)</h2>
<p style="font-size:12px;color:var(--info);margin-bottom:4px">Probing your gateway, two external hosts, DNS and TCP separately so a problem in one layer doesn't hide behind a healthy result in another.</p>
<table class="quality-table">
<thead><tr><th>Target</th><th>Loss %</th><th>Avg ms</th><th>Jitter ms</th><th>p95 ms</th><th>Samples</th></tr></thead>
<tbody id="quality-tbody"></tbody>
</table>
<div id="hint-list" class="hint-list"></div>
</div>

<div class="card" id="events-card" style="display:none">
<h2>Outage / Loss Events</h2>
<p style="font-size:12px;color:var(--info);margin-bottom:4px">Each entry is a streak of consecutive failed probes against one target.</p>
<div id="event-list" class="event-list"><div class="empty-note">No events recorded yet.</div></div>
</div>
</div>

<div id="tab-history" class="tab-content">
<div class="card"><h2>History & Saved Reports</h2><div id="sessions-list" class="sessions-list"></div></div>
</div>

<div id="tab-settings" class="tab-content">
<div class="card">
<h2>Diagnostic Settings</h2>
<p style="font-size:12px;color:var(--info)">These settings control the full diagnostic run, the live monitor sampler, and are saved to <code>~/.netdiag/config.json</code> on this machine.</p>
<div class="settings-grid">
<div class="settings-field"><label for="cfg-hosts">Ping hosts (space-separated)</label><input type="text" id="cfg-hosts"></div>
<div class="settings-field"><label for="cfg-ping-count">Ping count per host</label><input type="number" id="cfg-ping-count" min="1" max="200"></div>
<div class="settings-field"><label for="cfg-ping-interval">Ping interval (s)</label><input type="number" id="cfg-ping-interval" min="0.1" max="10" step="0.1"></div>
<div class="settings-field"><label for="cfg-ping-timeout">Ping timeout (s)</label><input type="number" id="cfg-ping-timeout" min="1" max="10"></div>
<div class="settings-field"><label for="cfg-dns-hosts">DNS test hosts (space-separated)</label><input type="text" id="cfg-dns-hosts"></div>
<div class="settings-field"><label for="cfg-dns-count">DNS queries per host</label><input type="number" id="cfg-dns-count" min="1" max="100"></div>
<div class="settings-field"><label for="cfg-tcp-count">TCP attempts per target</label><input type="number" id="cfg-tcp-count" min="1" max="100"></div>
</div>
<h2 style="margin-top:20px">Live Monitor Settings</h2>
<p style="font-size:12px;color:var(--info)">Targets the background sampler probes once per second while Live Monitor is running.</p>
<div class="settings-grid">
<div class="settings-field"><label for="cfg-monitor-external">External hosts (space-separated)</label><input type="text" id="cfg-monitor-external"></div>
<div class="settings-field"><label for="cfg-monitor-dns">DNS host to resolve</label><input type="text" id="cfg-monitor-dns"></div>
<div class="settings-field"><label for="cfg-monitor-tcp-host">TCP target host</label><input type="text" id="cfg-monitor-tcp-host"></div>
<div class="settings-field"><label for="cfg-monitor-tcp-port">TCP target port</label><input type="number" id="cfg-monitor-tcp-port" min="1" max="65535"></div>
<div class="settings-field"><label for="cfg-monitor-interval">Sample interval (s)</label><input type="number" id="cfg-monitor-interval" min="0.5" max="10" step="0.5"></div>
</div>
<div class="settings-actions">
<button class="btn btn-orange" id="settings-save-btn" onclick="saveSettings()">Save Settings</button>
<button class="btn btn-secondary" onclick="resetSettings()">Reset to Defaults</button>
<span class="settings-status" id="settings-status"></span>
</div>
</div>
<div class="card">
<h2>Available Commands & Tools</h2>
<p style="font-size:12px;color:var(--info);margin-bottom:4px">NetDiag falls back to stdlib-only probes when a tool below is missing, but installing them unlocks more detail.</p>
<table class="tools-table">
<thead><tr><th>Tool</th><th>Status</th></tr></thead>
<tbody id="tools-tbody"></tbody>
</table>
<div id="tools-hint" style="font-size:12px;color:var(--info);margin-top:8px"></div>
</div>
</div>

<div id="tab-about" class="tab-content">
<div class="card">
<h2>About NetDiag</h2>
<p style="margin-bottom:12px;line-height:1.7">Platform-agnostic internet diagnostics suite that isolates local network issues from ISP/upstream problems, detects WiFi signal problems, interface errors, bufferbloat, and per-hop routing issues.</p>
<table>
<tr><th>Version</th><td>1.0.0</td></tr>
<tr><th>License</th><td>GNU Affero General Public License v3.0</td></tr>
<tr><th>Author</th><td><a href="https://github.com/sicambria/">Sicambria</a></td></tr>
<tr><th>Language</th><td>Python 3.12+</td></tr>
<tr><th>CLI Deps</th><td>stdlib only (zero pip dependencies)</td></tr>
<tr><th>GUI Deps</th><td>fastapi + uvicorn (optional)</td></tr>
</table>
</div>
<div class="card">
<h2>License</h2>
<pre style="font-size:11px;line-height:1.5;max-height:300px;overflow-y:auto;background:#0f172a;padding:16px;border-radius:4px;margin-top:8px">                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007

 Copyright (C) 2007 Free Software Foundation, Inc. &lt;https://fsf.org/&gt;
 Everyone is permitted to copy and distribute verbatim copies
 of this license document, but changing it is not allowed.

                            Preamble

  The GNU Affero General Public License is a free, copyleft license for
software and other kinds of works, specifically designed to ensure
cooperation with the community in the case of network server software.

  The licenses for most software and other practical works are designed
to take away your freedom to share and change the works.  By contrast,
our General Public Licenses are intended to guarantee your freedom to
share and change all versions of a program--to make sure it remains free
software for all its users.

  When we speak of free software, we are referring to freedom, not
price.  Our General Public Licenses are designed to make sure that you
have the freedom to distribute copies of free software (and charge for
them if you wish), that you receive source code or can get it if you
want it, that you can change the software or use pieces of it in new
free programs, and that you know you can do these things.

  Developers that use our General Public Licenses protect your rights
with two steps: (1) assert copyright on the software, and (2) offer
you this License which gives you legal permission to copy, distribute
and/or modify the software.

  A secondary benefit of defending all users' freedom is that
improvements made in alternate versions of the program, if they
receive widespread use, may become available for other developers to
incorporate.  Many developers of free software are heartened and
encouraged by the resulting cooperation.

  However, in the case of software used on network servers, this result
may fail to come about.  The GNU General Public License permits making
a modified version and letting the public access it on a server without
ever releasing its source code to the public.  The GNU Affero General
Public License is designed specifically to ensure that, in such cases,
the modified source code becomes available to the community.  It
requires the operator of a network server to provide the source code of
the modified version running there to the users of that server.
Therefore, public use of a modified version, on a publicly accessible
server, gives the public access to the source code of the modified
version.

  An older license, called the Affero General Public License and
published by Affero, was designed to accomplish similar goals.  This is
a different license, not a version of the Affero GPL, but Affero has
released a new version of the Affero GPL which permits relicensing under
this license.

  The precise terms and conditions for copying, distribution and
modification follow.

                       TERMS AND CONDITIONS

  0. Definitions.

  "This License" refers to version 3 of the GNU Affero General Public
License.

  "Copyright" also means laws-like principles that apply to other kinds
of works that mimic copyright, such as semiconductor masks.

  "The Program" refers to any copyrightable work licensed under this
License.  Each licensee is addressed as "you".  "Licensees" and
"recipients" may be individuals or organizations.

  To "modify" a work means to copy from or adapt all or part of the work
in a fashion requiring copyright permission, other than the making of an
exact copy.  The resulting work is called a "modified version" of the
earlier work or a work "based on" the earlier work.

  A "covered work" means either the unmodified Program or a work based
on the Program.

  To "propagate" a work means to do anything with it that, without
permission, would make you directly or secondarily liable for
infringement under applicable copyright law, except executing it on a
computer or modifying a private copy.  Propagation includes copying,
distribution (with or without modification), making available to the
public, and in some countries other activities as well.

  To "convey" a work means any kind of propagation that enables other
parties to make or receive copies.  Mere interaction with a user through
a computer network, with no transfer of a copy, is not conveying.

  An interactive user interface displays "Appropriate Legal Notices"
to the extent that it includes a convenient and prominently visible
feature that (1) displays an appropriate copyright notice, and (2)
tells the user that there is no warranty for the work (except to the
extent that warranties are provided), that licensees may convey the
work under this License, and how to view a copy of this License.  If
the interface presents a list of user commands or options, such as a
menu, a prominent item in the list meets this criterion.

  1. Source Code.

  The "source code" for a work means the preferred form of the work
for making modifications to it.  "Object code" means any non-source
form of a work.

  A "Standard Interface" means an interface that either is an official
standard defined by a recognized standards body, or, in the case of
interfaces specified for a particular programming language, one that
is widely used among developers working in that language.

  The "System Libraries" of an executable work include anything, other
than the work as a whole, that (a) is included in the normal form of
packaging a Major Component, but which is not part of that Major
Component, and (b) serves only to enable use of the work with that
Major Component, or to implement a Standard Interface for which an
implementation is available to the public in source code form.  A
"Major Component", in this context, means a major essential component
(kernel, window system, and so on) of the specific operating system
(if any) on which the executable work runs, or a compiler used to
produce the work, or an object code interpreter used to run it.

  The "Corresponding Source" for a work in object code form means all
the source code needed to generate, install, and (for an executable
work) run the object code and to modify the work, including scripts to
control those activities.  However, it does not include the work's
System Libraries, or general-purpose tools or generally available free
programs which are used unmodified in performing those activities but
which are not part of the work.  For example, Corresponding Source
includes interface definition files associated with source files for
the work, and the source code for shared libraries and dynamically
linked subprograms that the work is specifically designed to require,
such as by intimate data communication or control flow between those
subprograms and other parts of the work.

  The Corresponding Source need not include anything that users
can regenerate automatically from other parts of the Corresponding
Source.

  The Corresponding Source for a work in source code form is that
same work.

  2. Basic Permissions.

  All rights granted under this License are granted for the term of
copyright on the Program, and are irrevocable provided the stated
conditions are met.  This License explicitly affirms your unlimited
permission to run the unmodified Program.  The output from running a
covered work is covered by this License only if the output, given its
content, constitutes a covered work.  This License acknowledges your
rights of fair use or other equivalent, as provided by copyright law.

  Subject to the terms of this License, you may make, run, propagate,
and convey covered works of the Program without modifying them,
provided you keep all notices of the presence of this License.
You may convey covered works to others for the sole purpose of
having them make modifications exclusively for you, or provide you
with facilities for running those works, provided that you comply with
the terms of this License in conveying all material for which you do
not control copyright.  Those thus making or running the covered works
for you must do so exclusively on your behalf, under your direction
and control, on terms that prohibit them from making any copies of
your copyrighted material outside their relationship with you.

  Conveying under any other circumstances is permitted solely under
the conditions stated below.  Sublicensing is not allowed; section 10
makes it unnecessary.

  3. Protecting Users' Legal Rights From Anti-Circumvention Law.

  No covered work shall be deemed part of an effective technological
measure under any applicable law fulfilling obligations under article
11 of the WIPO copyright treaty adopted on 20 December 1996, or
similar laws prohibiting or restricting circumvention of such
measures.

  When you convey a covered work, you waive any legal power to forbid
circumvention of technological measures to the extent such circumvention
is effected by exercising rights under this License with respect to
the covered work, and you disclaim any intention to limit operation or
modification of the work as a means of enforcing, against the work's
users, your or third parties' legal rights to forbid circumvention of
technological measures.

  4. Conveying Verbatim Copies.

  You may convey verbatim copies of the Program's source code as you
receive it, in any medium, provided that you conspicuously and
appropriately publish on each copy an appropriate copyright notice;
keep intact all notices stating that this License and any
non-permissive terms added in accord with section 7 apply to the code;
keep intact all notices of the absence of any warranty; and give all
recipients a copy of this License along with the Program.

  You may charge any price or no price for each copy that you convey,
and you may offer support or warranty protection for a fee.

  5. Conveying Modified Source Versions.

  You may convey a work based on the Program, or the modifications to
produce it from the Program, in the form of source code under the
terms of section 4, provided that you also meet all of these conditions:

    a) The work must carry prominent notices stating that you modified
    it, and giving a relevant date.

    b) The work must carry prominent notices stating that it is
    released under this License and any conditions added under section
    7.  This requirement modifies the requirement in section 4 to
    "keep intact all notices".

    c) You must license the entire work, as a whole, under this
    License to anyone who comes into possession of a copy.  This
    License will therefore apply, along with any applicable section 7
    additional terms, to the whole of the work, and all its parts,
    regardless of how they are packaged.  This License gives no
    permission to license the work in any other way, but it does not
    invalidate such permission if you have separately received it.

    d) If the work has interactive user interfaces, each must display
    Appropriate Legal Notices; however, if the Program has interactive
    interfaces that do not display Appropriate Legal Notices, your
    work need not make them do so.

  A compilation of a covered work with other separate and independent
works, which are not by their nature extensions of the covered work,
and which are not combined with it such as to form a larger program,
in or on a volume of a storage or distribution medium, is called an
"aggregate" if the compilation and its resulting copyright are not
used to limit the access or legal rights of the compilation's users
beyond what the individual works permit.  Inclusion of a covered work
in an aggregate does not cause this License to apply to the other
parts of the aggregate.

  6. Conveying Non-Source Forms.

  You may convey a covered work in object code form under the terms
of sections 4 and 5, provided that you also convey the
machine-readable Corresponding Source under the terms of this License,
in one of these ways:

    a) Convey the object code in, or embodied in, a physical product
    (including a physical distribution medium), accompanied by the
    Corresponding Source fixed on a durable physical medium
    customarily used for software interchange.

    b) Convey the object code in, or embodied in, a physical product
    (including a physical distribution medium), accompanied by a
    written offer, valid for at least three years and valid for as
    long as you offer spare parts or customer support for that product
    model, to give anyone who possesses the object code either (1) a
    copy of the Corresponding Source for all the software in the
    product that is covered by this License, on a durable physical
    medium customarily used for software interchange, for a price no
    more than your reasonable cost of physically performing this
    conveying of source, or (2) access to copy the
    Corresponding Source from a network server at no charge.

    c) Convey individual copies of the object code with a copy of the
    written offer to provide the Corresponding Source.  This
    alternative is allowed only occasionally and noncommercially, and
    only if you received the object code with such an offer, in accord
    with subsection 6b.

    d) Convey the object code by offering access from a designated
    place (gratis or for charge), and offer equivalent access to the
    Corresponding Source in the same way through the same place at no
    further charge.  You need not require recipients to copy the
    Corresponding Source along with the object code.  If the place to
    copy the object code is a network server, the Corresponding Source
    may be on a different server (operated by you or a third party)
    that supports equivalent copying facilities, provided you maintain
    clear directions next to the object code saying where to find the
    Corresponding Source.  Regardless of what server hosts the
    Corresponding Source, you remain obligated to ensure that it is
    available for as long as needed to satisfy these requirements.

    e) Convey the object code using peer-to-peer transmission, provided
    you inform other peers where the object code and Corresponding
    Source of the work are being offered to the general public at no
    charge under subsection 6d.

  A separable portion of the object code, whose source code is excluded
from the Corresponding Source as a System Library, need not be
included in conveying the object code work.

  A "User Product" is either (1) a "consumer product", which means any
tangible personal property which is normally used for personal, family,
or household purposes, or (2) anything designed or sold for incorporation
into a dwelling.  In determining whether a product is a consumer product,
doubtful cases shall be resolved in favor of coverage.  For a particular
product received by a particular user, "normally used" refers to a
typical or common use of that class of product, regardless of the status
of the particular user or of the way in which the particular user
actually uses, or expects or is expected to use, the product.  A product
is a consumer product regardless of whether the product has substantial
commercial, industrial or non-consumer uses, unless such uses represent
the only significant mode of use of the product.

  "Installation Information" for a User Product means any methods,
procedures, authorization keys, or other information required to install
and execute modified versions of a covered work in that User Product from
a modified version of its Corresponding Source.  The information must
suffice to ensure that the continued functioning of the modified object
code is in no case prevented or interfered with solely because
modification has been made.

  If you convey an object code work under this section in, or with, or
specifically for use in, a User Product, and the conveying occurs as
part of a transaction in which the right of possession and use of the
User Product is transferred to the recipient in perpetuity or for a
fixed term (regardless of how the transaction is characterized), the
Corresponding Source conveyed under this section must be accompanied
by the Installation Information.  But this requirement does not apply
if neither you nor any third party retains the ability to install
modified object code on the User Product (for example, the work has
been installed in ROM).

  The requirement to provide Installation Information does not include a
requirement to continue to provide support service, warranty, or updates
for a work that has been modified or installed by the recipient, or for
the User Product in which it has been modified or installed.  Access to a
network may be denied when the modification itself materially and
adversely affects the operation of the network or violates the rules and
protocols for communication across the network.

  Corresponding Source conveyed, and Installation Information provided,
in accord with this section must be in a format that is publicly
documented (and with an implementation available to the public in
source code form), and must require no special password or key for
unpacking, reading or copying.

  7. Additional Terms.

  "Additional permissions" are terms that supplement the terms of this
License by making exceptions from one or more of its conditions.
Additional permissions that are applicable to the entire Program shall
be treated as though they were included in this License, to the extent
that they are valid under applicable law.  If additional permissions
apply only to part of the Program, that part may be used separately
under those permissions, but the entire Program remains governed by
this License without regard to the additional permissions.

  When you convey a copy of a covered work, you may at your option
remove any additional permissions from that copy, or from any part of
it.  (Additional permissions may be written to require their own
removal in certain cases when you modify the work.)  You may place
additional permissions on material, added by you to a covered work,
for which you have or can give appropriate copyright permission.

  Notwithstanding any other provision of this License, for material you
add to a covered work, you may (if authorized by the copyright holders of
that material) supplement the terms of this License with terms:

    a) Disclaiming warranty or limiting liability differently from the
    terms of sections 15 and 16 of this License; or

    b) Requiring preservation of specified reasonable legal notices or
    author attributions in that material or in the Appropriate Legal
    Notices displayed by works containing it; or

    c) Prohibiting misrepresentation of the origin of that material, or
    requiring that modified versions of such material be marked in
    reasonable ways as different from the original version; or

    d) Limiting the use for publicity purposes of names of licensors or
    authors of the material; or

    e) Declining to grant rights under trademark law for use of some
    trade names, trademarks, or service marks; or

    f) Requiring indemnification of licensors and authors of that
    material by anyone who conveys the material (or modified versions of
    it) with contractual assumptions of liability to the recipient, for
    any liability that these contractual assumptions directly impose on
    those licensors and authors.

  All other non-permissive additional terms are considered "further
restrictions" within the meaning of section 10.  If the Program as you
received it, or any part of it, contains a notice stating that it is
governed by this License along with a term that is a further restriction,
you may remove that term.  If a license document contains a further
restriction but permits relicensing or conveying under this License, you
may add to a covered work material governed by the terms of that license
document, provided that the further restriction does not survive such
relicensing or conveying.

  If you add terms to a covered work in accord with this section, you
must place, in the relevant source files, a statement of the
additional terms that apply to those files, or a notice indicating
where to find the applicable terms.

  Additional terms, permissive or non-permissive, may be stated in the
form of a separately written license, or stated as exceptions;
the above requirements apply either way.

  8. Termination.

  You may not propagate or modify a covered work except as expressly
provided under this License.  Any attempt otherwise to propagate or
modify it is void, and will automatically terminate your rights under
this License (including any patent licenses granted under the third
paragraph of section 11).

  However, if you cease all violation of this License, then your
license from a particular copyright holder is reinstated (a)
provisionally, unless and until the copyright holder explicitly and
finally terminates your license, and (b) permanently, if the copyright
holder fails to notify you of the violation by some reasonable means
prior to 60 days after the cessation.

  Moreover, your license from a particular copyright holder is
reinstated permanently if the copyright holder notifies you of the
violation by some reasonable means, this is the first time you have
received notice of violation of this License (for any work) from that
copyright holder, and you cure the violation prior to 30 days after
your receipt of the notice.

  Termination of your rights under this section does not terminate the
licenses of parties who have received copies or rights from you under
this License.  If your rights have been terminated and not permanently
reinstated, you do not qualify to receive new licenses for the same
material under section 10.

  9. Acceptance Not Required for Having Copies.

  You are not required to accept this License in order to receive or
run a copy of the Program.  Ancillary propagation of a covered work
occurring solely as a consequence of using peer-to-peer transmission
to receive a copy likewise does not require acceptance.  However,
nothing other than this License grants you permission to propagate or
modify any covered work.  These actions infringe copyright if you do
not accept this License.  Therefore, by modifying or propagating a
covered work, you indicate your acceptance of this License to do so.

  10. Automatic Licensing of Downstream Recipients.

  Each time you convey a covered work, the recipient automatically
receives a license from the original licensors, to run, modify and
propagate that work, subject to this License.  You are not responsible
for enforcing compliance by third parties with this License.

  An "entity transaction" is a transaction transferring control of an
organization, or substantially all assets of one, or subdividing an
organization, or merging organizations.  If propagation of a covered
work results from an entity transaction, each party to that
transaction who receives a copy of the work also receives whatever
licenses to the work the party's predecessor in interest had or could
give under the previous paragraph, plus a right to possession of the
Corresponding Source of the work from the predecessor in interest, if
the predecessor has it or can get it with reasonable efforts.

  You may not impose any further restrictions on the exercise of the
rights granted or affirmed under this License.  For example, you may
not impose a license fee, royalty, or other charge for exercise of
rights granted under this License, and you may not initiate litigation
(including a cross-claim or counterclaim in a lawsuit) alleging that
any patent claim is infringed by making, using, selling, offering for
sale, or importing the Program or any portion of it.

  11. Patents.

  A "contributor" is a copyright holder who authorizes use under this
License of the Program or a work on which the Program is based.  The
work thus licensed is called the contributor's "contributor version".

  A contributor's "essential patent claims" are all patent claims
owned or controlled by the contributor, whether already acquired or
hereafter acquired, that would be infringed by some manner, permitted
by this License, of making, using, or selling its contributor version,
but do not include claims that would be infringed only as a
consequence of further modification of the contributor version.  For
purposes of this definition, "control" includes the right to grant
patent sublicenses in a manner consistent with the requirements of
this License.

  Each contributor grants you a non-exclusive, worldwide, royalty-free
patent license under the contributor's essential patent claims, to
make, use, sell, offer for sale, import and otherwise run, modify and
propagate the contents of its contributor version.

  In the following three paragraphs, a "patent license" is any express
agreement or commitment, however denominated, not to enforce a patent
(such as an express permission to practice a patent or covenant not to
sue for patent infringement).  To "grant" such a patent license to a
party means to make such an agreement or commitment not to enforce a
patent against the party.

  If you convey a covered work, knowingly relying on a patent license,
and the Corresponding Source of the work is not available for anyone
to copy, free of charge and under the terms of this License, through a
publicly available network server or other readily accessible means,
then you must either (1) cause the Corresponding Source to be so
available, or (2) arrange to deprive yourself of the benefit of the
patent license for this particular work, or (3) arrange, in a manner
consistent with the requirements of this License, to extend the patent
license to downstream recipients.  "Knowingly relying" means you have
actual knowledge that, but for the patent license, your conveying the
covered work in a country, or your recipient's use of the covered work
in a country, would infringe one or more identifiable patents in that
country that you have reason to believe are valid.

  If, pursuant to or in connection with a single transaction or
arrangement, you convey, or propagate by procuring conveyance of, a
covered work, and grant a patent license to some of the parties
receiving the covered work authorizing them to use, propagate, modify
or convey a specific copy of the covered work, then the patent license
you grant is automatically extended to all recipients of the covered
work and works based on it.

  A patent license is "discriminatory" if it does not include within
the scope of its coverage, prohibits the exercise of, or is
conditioned on the non-exercise of one or more of the rights that are
specifically granted under this License.  You may not convey a covered
work if you are a party to an arrangement with a third party that is
in the business of distributing software, under which you make payment
to the third party based on the extent of your activity of conveying
the work, and under which the third party grants, to any of the
parties who would receive the covered work from you, a discriminatory
patent license (a) in connection with copies of the covered work
conveyed by you (or copies made from those copies), or (b) primarily
for and in connection with specific products or compilations that
contain the covered work, unless you entered into that arrangement,
or that patent license was granted, prior to 28 March 2007.

  Nothing in this License shall be construed as excluding or limiting
any implied license or other defenses to infringement that may
otherwise be available to you under applicable patent law.

  12. No Surrender of Others' Freedom.

  If conditions are imposed on you (whether by court order, agreement or
otherwise) that contradict the conditions of this License, they do not
excuse you from the conditions of this License.  If you cannot convey a
covered work so as to satisfy simultaneously your obligations under this
License and any other pertinent obligations, then as a consequence you may
not convey it at all.  For example, if you agree to terms that obligate you
to collect a royalty for further conveying from those to whom you convey
the Program, the only way you could satisfy both those terms and this
License would be to refrain entirely from conveying the Program.

  13. Remote Network Interaction; Use with the GNU General Public License.

  Notwithstanding any other provision of this License, if you modify the
Program, your modified version must prominently offer all users
interacting with it remotely through a computer network (if your version
supports such interaction) an opportunity to receive the Corresponding
Source of your version by providing access to the Corresponding Source
from a network server at no charge, through some standard or customary
means of facilitating copying of software.  This Corresponding Source
shall include the Corresponding Source for any work covered by version 3
of the GNU General Public License that is incorporated pursuant to the
following paragraph.

  Notwithstanding any other provision of this License, you have permission
to link or combine any covered work with a work licensed under version 3
of the GNU General Public License into a single combined work, and to
convey the resulting work.  The terms of this License will continue to
apply to the part which is the covered work, but the work with which it is
combined will remain governed by version 3 of the GNU General Public
License.

  14. Revised Versions of this License.

  The Free Software Foundation may publish revised and/or new versions of
the GNU Affero General Public License from time to time.  Such new versions
will be similar in spirit to the present version, but may differ in detail to
address new problems or concerns.

  Each version is given a distinguishing version number.  If the
Program specifies that a certain numbered version of the GNU Affero
General Public License "or any later version" applies to it, you have the
option of following the terms and conditions either of that numbered
version or of any later version published by the Free Software
Foundation.  If the Program does not specify a version number of the
GNU Affero General Public License, you may choose any version ever published
by the Free Software Foundation.

  If the Program specifies that a proxy can decide whether future
versions of the GNU Affero General Public License can be used, that
proxy's public statement of acceptance of a version permanently
authorizes you to choose that version for the Program.

  Later license versions may give you additional or different
permissions.  However, no additional obligations are imposed on any
author or copyright holder as a result of your choosing to follow a
later version.

  15. Disclaimer of Warranty.

  THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY
APPLICABLE LAW.  EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT
HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM "AS IS" WITHOUT WARRANTY
OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE.  THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM
IS WITH YOU.  SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF
ALL NECESSARY SERVICING, REPAIR OR CORRECTION.

  16. Limitation of Liability.

  IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW OR AGREED TO IN WRITING
WILL ANY COPYRIGHT HOLDER, OR ANY OTHER PARTY WHO MODIFIES AND/OR CONVEYS
THE PROGRAM AS PERMITTED ABOVE, BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY
GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE
USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF
DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD
PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS),
EVEN IF SUCH HOLDER OR OTHER PARTY HAS BEEN ADVISED OF THE POSSIBILITY OF
SUCH DAMAGES.

  17. Interpretation of Sections 15 and 16.

  If the disclaimer of warranty and limitation of liability provided
above cannot be given local legal effect according to their terms,
reviewing courts shall apply local law that most closely approximates
an absolute waiver of all civil liability in connection with the
Program, unless a warranty or assumption of liability accompanies a
copy of the Program in return for a fee.</pre>
</div>
</div>
</div>

<div id="tab-tools" class="tab-content">
<div class="card">
<h2>Diagnostic Tools</h2>
<p style="font-size:12px;color:var(--info);margin-bottom:12px">Run individual diagnostic probes organized by OSI layer. Each tool has configurable parameters and safe presets. Results are displayed below each tool.</p>
<div id="tools-container"></div>
</div>
</div>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
let scoreChart=null, liveChart=null, liveTimer=null, liveTimerTick=null, liveData=[], liveDataLat=[], liveStartTime=0, liveTimerSec=0;
let isRunning=false;

function initScoreChart(canvas,val){if(!canvas)return;let ctx=canvas.getContext('2d');let ok=val==null?0:val;let color=ok>=70?'#22c55e':ok>=40?'#eab308':'#ef4444';if(scoreChart)scoreChart.destroy();scoreChart=new Chart(ctx,{type:'doughnut',data:{datasets:[{data:[ok,100-ok],backgroundColor:[color,'#334155'],borderWidth:0,circumference:180,rotation:270}],labels:['Score','']},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{enabled:false}}}});}

function getOpts(){
  return {
    speedtest: document.getElementById('opt-speedtest').checked,
    download_test: document.getElementById('opt-download').checked,
    connection_test: document.getElementById('opt-connection').checked,
    trace: document.getElementById('opt-trace').checked,
    bufferbloat: document.getElementById('opt-bufferbloat').checked,
    iperf3: document.getElementById('opt-iperf3').checked
  };
}

function saveOpts(){
  let k='netdiag_opts';
  try{localStorage.setItem(k,JSON.stringify(getOpts()));}catch(e){}
}

function loadOpts(){
  try{
    let d=JSON.parse(localStorage.getItem('netdiag_opts'));
    if(d){for(let k in d){let el=document.getElementById('opt-'+k);if(el)el.checked=d[k];}}
  }catch(e){}
}

function toggleOptions(){
  let p=document.getElementById('options-panel');
  p.classList.toggle('open');
}

function switchTab(tab){
  document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+tab).classList.add('active');
  document.querySelector('nav button[data-tab="'+tab+'"]').classList.add('active');
  if(tab==='history')loadSessions();
  if(tab==='settings'){loadSettings();loadTools();}
  if(tab==='tools')loadToolsMenu();
  if(tab==='troubleshoot')startActivityPoll();else stopActivityPoll();
}

document.querySelectorAll('nav button').forEach(b=>b.addEventListener('click',function(){switchTab(b.dataset.tab);}));
loadOpts();

// -- Activity log ("under the hood") ---------------------------------------------

let activityTimer=null;

function renderActivity(items){
  let list=document.getElementById('activity-list');
  if(!list)return;
  if(!items||!items.length){list.innerHTML='<div class="empty-note">No activity yet.</div>';return;}
  list.innerHTML=items.map(function(a){
    let ts=(a.ts||'').split('T')[1]||a.ts||'';
    let okCls=a.ok?'ok':'fail';
    let okIcon=a.ok?'OK':'FAIL';
    let dur=a.duration_ms!=null?a.duration_ms.toFixed(1)+' ms':'';
    return '<div class="activity-row"><span class="ts">'+ts+'</span>'+
      '<span class="kind">'+(a.kind||'')+'</span>'+
      '<span class="alabel">'+(a.label||'')+'</span>'+
      '<span class="dur">'+dur+'</span>'+
      '<span class="'+okCls+'">'+okIcon+'</span></div>';
  }).join('');
}

function pollActivity(){
  fetch('/api/activity').then(function(r){return r.json();}).then(function(d){
    renderActivity(d.activity||[]);
  }).catch(function(){});
}

function startActivityPoll(){
  if(activityTimer)return;
  pollActivity();
  activityTimer=setInterval(pollActivity,2000);
}

function stopActivityPoll(){
  if(activityTimer){clearInterval(activityTimer);activityTimer=null;}
}

// -- Settings tab -------------------------------------------------------------------

function applyConfigToForm(cfg){
  document.getElementById('cfg-hosts').value=(cfg.hosts||[]).join(' ');
  document.getElementById('cfg-ping-count').value=cfg.ping_count;
  document.getElementById('cfg-ping-interval').value=cfg.ping_interval;
  document.getElementById('cfg-ping-timeout').value=cfg.ping_timeout;
  document.getElementById('cfg-dns-hosts').value=(cfg.dns_hosts||[]).join(' ');
  document.getElementById('cfg-dns-count').value=cfg.dns_count;
  document.getElementById('cfg-tcp-count').value=cfg.tcp_count;
  document.getElementById('cfg-monitor-external').value=(cfg.monitor_external_hosts||[]).join(' ');
  document.getElementById('cfg-monitor-dns').value=cfg.monitor_dns_host||'';
  let tcp=cfg.monitor_tcp_target||['1.1.1.1',443];
  document.getElementById('cfg-monitor-tcp-host').value=tcp[0];
  document.getElementById('cfg-monitor-tcp-port').value=tcp[1];
  document.getElementById('cfg-monitor-interval').value=cfg.monitor_interval;
}

function loadSettings(){
  fetch('/api/config').then(function(r){return r.json();}).then(function(cfg){
    applyConfigToForm(cfg);
    document.getElementById('settings-status').textContent='';
  }).catch(function(){
    document.getElementById('settings-status').textContent='Could not load settings.';
  });
}

function saveSettings(){
  let status=document.getElementById('settings-status');
  let body={
    hosts:document.getElementById('cfg-hosts').value.trim().split(/\s+/).filter(Boolean),
    ping_count:parseInt(document.getElementById('cfg-ping-count').value,10),
    ping_interval:parseFloat(document.getElementById('cfg-ping-interval').value),
    ping_timeout:parseInt(document.getElementById('cfg-ping-timeout').value,10),
    dns_hosts:document.getElementById('cfg-dns-hosts').value.trim().split(/\s+/).filter(Boolean),
    dns_count:parseInt(document.getElementById('cfg-dns-count').value,10),
    tcp_count:parseInt(document.getElementById('cfg-tcp-count').value,10),
    monitor_external_hosts:document.getElementById('cfg-monitor-external').value.trim().split(/\s+/).filter(Boolean),
    monitor_dns_host:document.getElementById('cfg-monitor-dns').value.trim(),
    monitor_tcp_target:[document.getElementById('cfg-monitor-tcp-host').value.trim(),parseInt(document.getElementById('cfg-monitor-tcp-port').value,10)],
    monitor_interval:parseFloat(document.getElementById('cfg-monitor-interval').value)
  };
  status.textContent='Saving...';
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json();}).then(function(cfg){
    applyConfigToForm(cfg);
    status.textContent='Saved. Takes effect on the next diagnostic run / monitor restart.';
  }).catch(function(){
    status.textContent='Save failed.';
  });
}

function resetSettings(){
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    hosts:['1.1.1.1','8.8.8.8','9.9.9.9','google.com'],
    dns_hosts:['google.com','cloudflare.com','quad9.net'],
    tcp_targets:[['1.1.1.1',443],['8.8.8.8',443],['google.com',443]],
    ping_count:20,ping_interval:0.5,ping_timeout:2,
    dns_count:10,tcp_count:10,monitor_interval:1.0,monitor_external_hosts:['1.1.1.1','8.8.8.8'],
    monitor_dns_host:'google.com',monitor_tcp_target:['1.1.1.1',443]
  })}).then(function(){loadSettings();});
}

function loadTools(){
  fetch('/api/tools').then(function(r){return r.json();}).then(function(d){
    let missing=new Set((d.missing_required||[]).concat(d.missing_optional||[]));
    let required=new Set(d.checked_required||[]);
    let all=(d.checked_required||[]).concat(d.checked_optional||[]);
    let tbody=document.getElementById('tools-tbody');
    tbody.innerHTML=all.map(function(t){
      let ok=!missing.has(t);
      let label=t+(required.has(t)?' (required)':'');
      return '<tr><td>'+label+'</td><td class="'+(ok?'tool-ok':'tool-missing')+'">'+(ok?'Available':'Missing')+'</td></tr>';
    }).join('');
    let hints=[];
    if(d.install_hint_required)hints.push('Required: '+d.install_hint_required);
    if(d.install_hint_optional)hints.push('Optional: '+d.install_hint_optional);
    document.getElementById('tools-hint').textContent=hints.join('  |  ')||'All checked tools are available.';
  }).catch(function(){});
}
document.querySelectorAll('#options-panel input').forEach(function(el){el.addEventListener('change',saveOpts);});

function pretty(l){return l.replace(/_/g,' ').replace(/(^\w|\s\w)/g,function(m){return m.toUpperCase();});}

function sev(label,ok,total,rtt){
  if(total==null||total===0)return 'running';
  let loss=total>0?(1-(ok||0)/total)*100:0;
  if(label==='interface')return rtt>0?'bad':'clean';
  if(label==='wifi')return rtt!=null&&rtt<-80?'bad':(rtt!=null&&rtt<-70?'warning':'clean');
  if(label==='ethtool')return ok>0?'clean':'bad';
  if(label.startsWith('dns_')||label.startsWith('tcp_'))return loss>0?'bad':'clean';
  if(label==='tcp_sockets')return ok>0?'clean':'warning';
  if(label==='bufferbloat')return (rtt||0)>300?'bad':(rtt>200?'warning':'clean');
  if(label==='mtr')return ok>0?'clean':'warning';
  if(label==='speedtest')return rtt<1?'bad':(rtt<10?'warning':'clean');
  if(label==='iperf3')return ok>0?'clean':'warning';
  if(label==='download_test')return rtt<1?'bad':rtt<5?'warning':'clean';
  if(label==='http_latency')return ok>0?'clean':'warning';
  if(label==='mtu_probe')return ok>0?'clean':'warning';
  if(loss>=5)return 'bad';
  if(loss>=1)return 'warning';
  if(rtt!=null&&rtt>=300)return 'bad';
  if(rtt!=null&&rtt>=150)return 'warning';
  if(rtt!=null&&rtt>=80)return 'warning';
  return 'clean';
}

function summary(label,ok,total,rtt,st){
  if(st==='running')return 'Running...';
  if(st==='error')return 'Failed';
  if(label==='interface')return rtt>0?rtt+' errors':'No errors';
  if(label==='wifi')return rtt!=null?rtt+' dBm':'N/A';
  if(label==='ethtool')return ok>0?'Full duplex':'Half duplex';
  if(label.startsWith('dns_')||label.startsWith('tcp_')){
    let loss=total>0?(1-(ok||0)/total)*100:0;
    return (ok||0)+'/'+total+', '+(rtt||'?')+'ms'+(loss>0?', '+loss.toFixed(0)+'% loss':'');
  }
  if(label==='tcp_sockets')return ok>0?'Clean':'Retrans: '+rtt+'%';
  if(label==='bufferbloat')return ((rtt||0)/100).toFixed(1)+'x';
  if(label==='mtr')return ok>0?'Route clean':'Loss detected';
  if(label==='speedtest')return (rtt||0)+' Mbps';
  if(label==='iperf3')return (rtt||0)+' Mbps';
  if(label==='download_test')return (rtt||0)+' Mbps, '+ok+' images';
  if(label==='http_latency')return ok+'/'+total+' hosts OK';
  if(label==='mtu_probe')return rtt+' MTU';
  if(total>0){
    let loss=total>0?(1-(ok||0)/total)*100:0;
    return (rtt||'?')+'ms, '+loss.toFixed(1)+'% loss';
  }
  return 'Done';
}

function startDiagnostic(opts,dash){
  let prefix=dash?'dash-':'';
  let statusEl=document.getElementById(prefix+'run-status');
  let progressEl=document.getElementById(prefix+'progress');
  let fillEl=document.getElementById(prefix+'progress-fill');
  let labelEl=document.getElementById(prefix+'progress-label');
  let btn=document.getElementById(prefix+'run-btn');
  let logDiv=document.getElementById('log-output');
  let progList=document.getElementById('prog-list');
  let stack=document.getElementById('stack-card');
  let stackLayers=document.getElementById('stack-layers');
  let healthLive=document.getElementById('health-live');
  let healthInner=document.getElementById('health-bar-inner');
  let healthVal=document.getElementById('health-live-val');

  isRunning=true;
  if(btn){btn.disabled=true;btn.textContent='Running...';}
  if(statusEl)statusEl.textContent='Running...';
  if(progressEl)progressEl.style.display='block';
  if(logDiv)logDiv.textContent='';
  if(progList){progList.innerHTML='';progList.style.display='block';}
  if(healthLive)healthLive.style.display='none';
  if(stack)stack.style.display='none';
  if(fillEl)fillEl.style.width='0%';
  if(labelEl)labelEl.textContent='';

  fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(opts||getOpts())}).then(function(r){return r.json();}).then(function(data){
    if(data.status!=='ok'){if(statusEl)statusEl.textContent='Error: '+data.message;if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}isRunning=false;return;}
    function poll(){
      fetch('/api/status').then(function(r){return r.json();}).then(function(s){
        let entries=Object.values(s.progress||{});
        let run=entries.filter(function(e){return e.status==='running';}).length;
        let don=entries.filter(function(e){return e.status!=='running';}).length;
        let tot=run+don;
        if(fillEl)fillEl.style.width=(tot>0?don/tot*100:0)+'%';
        if(labelEl)labelEl.textContent=don+'/'+tot+' checks';

        let html='',hScore=0,hCnt=0;
        for(let i=0;i<entries.length;i++){
          let e=entries[i];
          let c=sev(e.label,e.ok,e.total,e.rtt_ms);
          if(e.status!=='running'){
            hScore+=c==='clean'?100:c==='warning'?50:0;
            hCnt++;
          }
          html+='<div class="prog-entry '+c+'"><span class="prog-dot '+c+'"></span><span class="prog-name">'+pretty(e.label)+'</span><span class="prog-result">'+summary(e.label,e.ok,e.total,e.rtt_ms,e.status)+'</span></div>';
        }
        if(progList)progList.innerHTML=html;

        if(hCnt>0&&healthLive){
          healthLive.style.display='flex';
          let avg=Math.round(hScore/hCnt);
          let cls=avg>=70?'good':(avg>=40?'warning':'bad');
          if(healthInner){healthInner.style.width=avg+'%';healthInner.className='health-bar-inner '+cls;}
          if(healthVal)healthVal.textContent=avg;
        }

        if(s.status==='done'||s.status==='error'){
          isRunning=false;
          if(s.status==='error'){if(statusEl)statusEl.textContent='Error: '+s.error;if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}return;}
          if(statusEl)statusEl.textContent='Diagnostic complete.';
          if(fillEl)fillEl.style.width='100%';
          if(labelEl)labelEl.textContent='Done';
          if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}
          if(stack){stack.style.display='block';renderResults(s.results,stackLayers,logDiv);}
          if(s.results&&s.results.health_score!=null){updateDashboard(s.results);}
        }else{setTimeout(poll,500);}
      }).catch(function(){setTimeout(poll,500);});
    }
    poll();
  }).catch(function(e){if(statusEl)statusEl.textContent='Error: '+e;if(btn){btn.disabled=false;btn.textContent='Start Diagnosis';}isRunning=false;});
}

function dashRunDiag(){
  startDiagnostic({speedtest:false,download_test:false,connection_test:false,trace:false,bufferbloat:false,iperf3:false},true);
}

function runDiagnostic(){
  startDiagnostic(null,false);
}

function renderStackLayers(container){
  fetch('/api/status').then(function(r){return r.json();}).then(function(s){
    if(!s.results)return;
    let r=s.results;
    let layers=[];
    if(r.wifi&&r.wifi.available){let sev='clean';if(r.wifi.signal_dbm!=null&&r.wifi.signal_dbm<-70)sev='warning';if(r.wifi.signal_dbm!=null&&r.wifi.signal_dbm<-80)sev='bad';
      layers.push({name:'WiFi',icon:sev,detail:'Signal: '+(r.wifi.signal_dbm!=null?r.wifi.signal_dbm+' dBm':'N/A'),fix:''});}
    if(r.interface&&r.interface.available){let rx=r.interface.rx||{},tx=r.interface.tx||{},errs=rx.errors+tx.errors+rx.dropped+tx.dropped;
      let sev=errs>0?'bad':'clean';
      layers.push({name:'Interface',icon:sev,detail:errs>0?('Errors: '+errs):'Clean',fix:''});}
    if(r.ethtool&&r.ethtool.available&&r.ethtool.duplex==='Half'){layers.push({name:'Ethernet',icon:'bad',detail:'Half-duplex',fix:'Force full-duplex on both sides.'});}
    if(r.gateway_ping){let l=r.gateway_ping,sev='clean';if(l.loss_pct>=5)sev='bad';else if(l.loss_pct>=1)sev='warning';else if(l.p95_ms>=50)sev='warning';
      layers.push({name:'Gateway',icon:sev,detail:'p95: '+l.p95_ms+'ms, loss: '+l.loss_pct+'%',fix:''});}
    if(r.bufferbloat&&r.bufferbloat.available&&r.bufferbloat.ratio){let sev=r.bufferbloat.ratio>3?'bad':r.bufferbloat.ratio>2?'warning':'clean';
      layers.push({name:'Bufferbloat',icon:sev,detail:'Ratio: '+r.bufferbloat.ratio.toFixed(1)+'x',fix:''});}
    if(r.mtr&&r.mtr.hops&&r.mtr.hops.length){let badHop=r.mtr.hops.find(function(h){return h.loss_pct>5;});let sev=badHop?'bad':'clean';
      layers.push({name:'ISP Route',icon:sev,detail:badHop?('Loss at hop '+badHop.hop):'Clean',fix:''});}
    if(r.internet_ping){let bad=r.internet_ping.find(function(p){let l=p.loss_pct||0;return l>=1||(p.p95_ms||0)>=150;});
      layers.push({name:'Internet',icon:bad?'warning':'clean',detail:bad?bad.label+' unstable':'Stable',fix:''});}
    if(r.dns){let bad=r.dns.find(function(d){return (d.failure_pct||0)>0;});layers.push({name:'DNS',icon:bad?'bad':'clean',detail:bad?bad.host+' fails: '+bad.failure_pct+'%':'Clean',fix:''});}
    if(r.tcp){let bad=r.tcp.find(function(t){return (t.failure_pct||0)>0||(t.p95_ms||0)>500;});layers.push({name:'TCP',icon:bad?'bad':'clean',detail:bad?bad.host+':'+bad.port+' issues':'Clean',fix:''});}
    if(r.download_test&&r.download_test.error==null){let mbps=r.download_test.avg_mbps||0;let sev=mbps<1?'bad':mbps<5?'warning':'clean';
      layers.push({name:'Download',icon:sev,detail:mbps+' Mbps, '+r.download_test.success+'/'+(r.download_test.success+r.download_test.failures)+' images',fix:''});}
    if(r.connection_test&&r.connection_test.http_latency){let bad=r.connection_test.http_latency.find(function(h){return (h.p95_ms||0)>300;});
      layers.push({name:'HTTP Latency',icon:bad?'warning':'clean',detail:bad?bad.host+' slow':'OK',fix:''});}
    if(r.connection_test&&r.connection_test.mtu&&r.connection_test.mtu.available){
      layers.push({name:'MTU',icon:r.connection_test.mtu.mtu<1400?'warning':'clean',detail:r.connection_test.mtu.mtu+' bytes',fix:''});}
    container.innerHTML=layers.map(function(l,i){
      let ico={clean:'O',warning:'!',bad:'X',info:'i'}[l.icon]||'?';
      return '<div class="stack-layer" onclick="this.classList.toggle(\'expanded\')">'+
        '<div class="stack-icon '+l.icon+'">'+ico+'</div>'+
        '<div class="stack-body"><div class="stack-title">'+l.name+'</div><div class="stack-detail">'+l.detail+'</div></div>'+
        '<div class="stack-fix">'+(l.fix||'No specific fix needed.')+'</div></div>';
    }).join('');
  });
}

function renderResults(results,stackLayers,logDiv){
  renderStackLayers(stackLayers);
  let diagnoses=results.diagnosis||[];
  logDiv.textContent+='\n\n--- Results ---\nHealth Score: '+(results.health_score||'?')+'/100\n\nDiagnosis:';
  diagnoses.forEach(function(d){logDiv.textContent+='\n['+d.severity+'] ['+d.layer+'] '+d.title;if(d.detail)logDiv.textContent+='\n  '+d.detail;if(d.fix)logDiv.textContent+='\n  Fix: '+d.fix;});
  logDiv.scrollTop=logDiv.scrollHeight;
}

function updateDashboard(results){
  document.getElementById('health-val').textContent=results.health_score!=null?results.health_score:'--';
  let sig=results.wifi&&results.wifi.signal_dbm;document.getElementById('sig-val').textContent=sig!=null?sig+' dBm':'--';
  let gw=results.gateway_ping;document.getElementById('gw-val').textContent=gw?gw.p95_ms+'ms':'--';
  let sp=results.speedtest;document.getElementById('spd-val').textContent=sp&&sp.download_mbps?sp.download_mbps+'M':'--';
  initScoreChart(document.getElementById('scoreChart'),results.health_score);
  let events=document.getElementById('events-list');
  let items=[];
  (results.diagnosis||[]).forEach(function(d){if(d.severity!=='clean')items.push({dot:d.severity,text:'['+d.layer+'] '+d.title,time:results.timestamp});});
  if(!items.length&&results.timestamp)items.push({dot:'clean',text:'All clear',time:results.timestamp});
  events.innerHTML=items.map(function(i){return '<div class="event-bar"><span class="event-dot '+i.dot+'"></span><span>'+i.text+'</span><span style="color:var(--info);margin-left:auto">'+(i.time||'').slice(11,19)+'</span></div>';}).join('');
}

function toggleLiveMonitor(){
  if(liveTimer){stopLiveMonitor();}
  else{startLiveMonitor();}
}

function startLiveMonitor(){
  if(liveTimer)return;
  document.getElementById('live-container').style.display='block';
  document.getElementById('quality-card').style.display='block';
  document.getElementById('events-card').style.display='block';
  let btn=document.getElementById('live-toggle-btn');
  btn.textContent='Stop Monitoring';
  btn.className='btn btn-secondary';
  if(!liveChart && typeof Chart!=='undefined'){
    let c=document.getElementById('liveChart');
    if(c){
      try{
        liveChart=new Chart(c,{type:'line',data:{labels:[],datasets:[{label:'Signal dBm',data:[],borderColor:'#f97316',backgroundColor:'rgba(249,115,22,0.1)',fill:true,tension:0.3,pointRadius:2,borderWidth:2}]},options:{responsive:true,maintainAspectRatio:false,scales:{x:{display:true,ticks:{color:'#64748b',maxTicksLimit:10,font:{size:10}},grid:{color:'#334155'}},y:{display:true,ticks:{color:'#64748b',font:{size:10}},grid:{color:'#334155'}}},plugins:{legend:{display:false}}}});
      }catch(e){console.log('chart init failed',e);}
    }
  }
  liveData=[];liveDataLat=[];
  liveStartTime=Date.now();
  liveTimerSec=0;
  updateTimer();
  liveTimerTick=setInterval(function(){liveTimerSec++;updateTimer();},1000);
  fetch('/api/monitor/start',{method:'POST'}).catch(function(){});
  pollMonitor();
  liveTimer=setInterval(pollMonitor,1500);
}

function stopLiveMonitor(){
  if(liveTimer){clearInterval(liveTimer);liveTimer=null;}
  if(liveTimerTick){clearInterval(liveTimerTick);liveTimerTick=null;}
  fetch('/api/monitor/stop',{method:'POST'}).catch(function(){});
  let btn=document.getElementById('live-toggle-btn');
  btn.textContent='Start Monitoring';
  btn.className='btn btn-orange';
}

function updateTimer(){
  let m=Math.floor(liveTimerSec/60);
  let s=liveTimerSec%60;
  document.getElementById('live-timer').textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
}

function arrStats(arr,key){
  if(!arr||arr.length<2)return null;
  var vals=arr.map(function(p){return p.v;}).filter(function(v){return v!=null;});
  if(vals.length<2)return null;
  vals.sort(function(a,b){return a-b;});
  var sum=0, i;
  for(i=0;i<vals.length;i++)sum+=vals[i];
  var avg=(sum/vals.length).toFixed(1);
  var med=vals.length%2?vals[(vals.length-1)/2]:(vals[vals.length/2-1]+vals[vals.length/2])/2;
  return {avg:avg,med:med.toFixed(1),min:vals[0].toFixed(1),max:vals[vals.length-1].toFixed(1)};
}

function updateLiveStats(){
  var ss=arrStats(liveData);
  var ls=arrStats(liveDataLat);
  if(ss){
    document.getElementById('stat-sig-avg').textContent=ss.avg;
    document.getElementById('stat-sig-med').textContent=ss.med;
    document.getElementById('stat-sig-min').textContent=ss.min;
    document.getElementById('stat-sig-max').textContent=ss.max;
  }
  if(ls){
    document.getElementById('stat-lat-avg').textContent=ls.avg;
    document.getElementById('stat-lat-med').textContent=ls.med;
    document.getElementById('stat-lat-min').textContent=ls.min;
    document.getElementById('stat-lat-max').textContent=ls.max;
  }
}

function pollMonitor(){
  var t0=Date.now();
  fetch('/api/monitor').then(function(r){return r.json();}).then(function(d){
    let sig=d.wifi&&d.wifi.signal_dbm;
    let el=document.getElementById('live-sig-val');
    let circle=document.getElementById('live-circle');
    let textEl=document.getElementById('live-sig-text');

    if(sig!=null){
      el.textContent=sig;
      let cls=sig>=-50?'green':sig>=-70?'yellow':'red';
      circle.className='live-signal-circle '+cls;
      if(sig>=-50)textEl.textContent='Excellent signal';
      else if(sig>=-60)textEl.textContent='Good signal';
      else if(sig>=-70)textEl.textContent='Fair signal';
      else if(sig>=-80)textEl.textContent='Weak signal';
      else textEl.textContent='Very weak signal';
    }else{
      el.textContent='--';
      circle.className='live-signal-circle red';
      textEl.textContent='No WiFi data';
    }

    let lat=d.gateway_latency_ms;
    let latEl=document.getElementById('live-latency');
    if(lat!=null){
      latEl.textContent=lat.toFixed(0);
      latEl.className='value '+(lat<50?'green':lat<150?'yellow':'red');
    }else{latEl.textContent='--';latEl.className='value';}

    let noise=d.wifi&&d.wifi.noise_dbm;
    let noiseEl=document.getElementById('live-noise');
    noiseEl.textContent=noise!=null?noise+' dBm':'N/A';

    let cu=d.wifi&&d.wifi.channel_util;
    let cuEl=document.getElementById('live-channel');
    if(cu!=null){cuEl.textContent=cu+'%';cuEl.className='value '+(cu<40?'green':cu<70?'yellow':'red');}
    else{cuEl.textContent='--';cuEl.className='value';}

    let ifaceEl=document.getElementById('live-iface');
    ifaceEl.textContent=d.wifi&&d.wifi.interface?d.wifi.interface:(d.wifi&&d.wifi.available?'wifi':'--');

    let ssid=d.wifi&&d.wifi.ssid;
    let ssidEl=document.getElementById('live-ssid');
    let ssidStatEl=document.getElementById('live-ssid-stat');
    if(ssid){
      ssidEl.textContent=ssid;
      ssidStatEl.textContent=ssid;
      ssidStatEl.className='value';
    }else{
      ssidEl.textContent='--';
      ssidStatEl.textContent='--';
      ssidStatEl.className='value';
    }

    if(sig!=null){
      liveData.push({t:new Date(),v:sig});
      if(liveData.length>60)liveData.shift();
    }
    if(lat!=null){
      liveDataLat.push({t:new Date(),v:lat});
      if(liveDataLat.length>60)liveDataLat.shift();
    }
    if(liveChart){
      liveChart.data.labels=liveData.map(function(p){return p.t.toLocaleTimeString();});
      liveChart.data.datasets[0].data=liveData.map(function(p){return p.v;});
      liveChart.update('none');
    }
    updateLiveStats();

    let health=d.health_score||0;
    let hBar=document.getElementById('live-health-bar');
    let hVal=document.getElementById('live-health-val');
    let hCls=health>=70?'good':health>=40?'warning':'bad';
    hBar.style.width=health+'%';
    hBar.className='bar-inner '+hCls;
    hVal.textContent=health;
    renderAdvancedMonitor(d.advanced);
    console.log('poll ok',Date.now()-t0+'ms','sig='+sig,'lat='+lat);
  }).catch(function(e){
    console.log('poll fail',Date.now()-t0+'ms',e);
  });
}

function targetLabel(key){
  if(key==='gateway')return 'Gateway (router)';
  if(key==='dns')return 'DNS resolution';
  if(key==='tcp')return 'TCP handshake';
  if(key.indexOf('external:')===0)return 'Internet ('+key.slice(9)+')';
  return key;
}

function lossClass(loss){
  if(loss==null)return '';
  if(loss>=5)return 'loss-bad';
  if(loss>0)return 'loss-warn';
  return 'loss-ok';
}

function renderAdvancedMonitor(adv){
  if(!adv)return;
  let tbody=document.getElementById('quality-tbody');
  let targets=adv.targets||{};
  let keys=Object.keys(targets);
  if(!keys.length){
    tbody.innerHTML='<tr><td colspan="6" class="empty-note">Collecting samples...</td></tr>';
  }else{
    tbody.innerHTML=keys.sort().map(function(key){
      let t=targets[key];
      let loss=t.loss_pct;
      return '<tr><td>'+targetLabel(key)+'</td>'+
        '<td class="'+lossClass(loss)+'">'+(loss!=null?loss.toFixed(1):'--')+'</td>'+
        '<td>'+(t.avg_ms!=null?t.avg_ms.toFixed(1):'--')+'</td>'+
        '<td>'+(t.jitter_ms!=null?t.jitter_ms.toFixed(1):'--')+'</td>'+
        '<td>'+(t.p95_ms!=null?t.p95_ms.toFixed(1):'--')+'</td>'+
        '<td>'+(t.samples||0)+'</td></tr>';
    }).join('');
  }

  let hintList=document.getElementById('hint-list');
  let hints=adv.hints||[];
  if(!hints.length){
    hintList.innerHTML='';
  }else{
    hintList.innerHTML=hints.map(function(h){
      return '<div class="hint '+(h.severity||'info')+'">'+h.text+'</div>';
    }).join('');
  }

  let eventList=document.getElementById('event-list');
  let events=adv.events||[];
  if(!events.length){
    eventList.innerHTML='<div class="empty-note">No events recorded yet.</div>';
  }else{
    eventList.innerHTML=events.map(function(ev){
      let start=(ev.start||'').split('T')[1]||ev.start;
      let end=(ev.end||'').split('T')[1]||ev.end;
      return '<div class="event-row"><span><b>'+targetLabel(ev.target)+'</b> failed '+ev.consecutive_failures+
        ' time(s)</span><span>'+start+' &rarr; '+end+'</span></div>';
    }).join('');
  }
}

function loadSessions(){
  Promise.all([
    fetch('/api/history').then(function(r){return r.json();}),
    fetch('/api/reports').then(function(r){return r.json();})
  ]).then(function(results){
    let sessions=results[0].sessions||[];
    let files=results[1].reports||[];
    let list=document.getElementById('sessions-list');
    let html='';
    if(sessions.length){
      html+='<div style="font-size:11px;color:var(--info);margin-bottom:8px">DIAGNOSTIC SESSIONS</div>';
      html+=sessions.map(function(s){
        let ts=s._file?s._file.replace('session_','').replace('.json',''):'';
        let score=s.health_score!=null?s.health_score:'?';
        let badCount=(s.diagnosis||[]).filter(function(d){return d.severity!=='clean';}).length;
        let summary=badCount>0?badCount+' issue(s)':'Clean';
        return '<div class="session-row">'+
          '<div class="session-info"><div class="time">'+ts.replace('_',' ')+'</div><div class="summary">Score '+score+'/100 &mdash; '+summary+'</div></div>'+
          '<div class="session-actions">'+
          '<button class="btn btn-secondary" onclick="viewSession(\''+s._file+'\')">View</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'json\')">JSON</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'html\')">HTML</button>'+
          '<button class="btn btn-secondary" onclick="exportReport(\''+s._file+'\',\'csv\')">CSV</button>'+
          '</div></div>';
      }).join('');
    }
    if(files.length){
      if(html)html+='<div style="border-top:1px solid var(--border);margin:12px 0"></div>';
      html+='<div style="font-size:11px;color:var(--info);margin-bottom:8px">REPORT FILES</div>';
      html+=files.map(function(f){
        let kb=(f.size/1024).toFixed(1);
        return '<div class="session-row"><div class="session-info"><div class="time">'+f.name+'</div><div class="summary">'+kb+' KB &mdash; '+f.mtime.slice(0,19).replace('T',' ')+'</div></div>'+
          '<div class="session-actions"><button class="btn btn-secondary" onclick="window.open(\'/api/report/'+f.name+'\')">Open</button></div></div>';
      }).join('');
    }
    if(list)list.innerHTML=html||'<p style="color:var(--info)">No sessions or reports yet. Run a diagnostic to create one.</p>';
  });
}



function viewSession(file){
  fetch('/api/session/'+file).then(function(r){return r.json();}).then(function(data){
    let stackLayers=document.createElement('div'),logDiv=document.getElementById('log-output');
    document.getElementById('prog-list').style.display='none';
    document.getElementById('health-live').style.display='none';
    logDiv.style.display='block';logDiv.textContent='';
    document.getElementById('stack-card').style.display='block';
    updateDashboard(data);
    renderResults(data,stackLayers,logDiv);
    switchTab('troubleshoot');
  });
}

function exportReport(file,format){
  window.open('/api/export/'+file+'?format='+format,'_blank');
}

document.addEventListener('visibilitychange',function(){if(document.hidden&&liveTimer)stopLiveMonitor();});
window.addEventListener('beforeunload',function(){if(liveTimer)stopLiveMonitor();});
fetch('/api/status').then(function(r){return r.json();}).then(function(s){if(s.results)updateDashboard(s.results);});
loadSessions();

// -- Tools Menu --------------------------------------------------------------------

let toolsMenuLoaded=false;

function loadToolsMenu(){
  if(toolsMenuLoaded)return;
  fetch('/api/tools/menu').then(function(r){return r.json();}).then(function(d){
    var container=document.getElementById('tools-container');
    if(!container)return;
    var tools=d.tools||[];
    if(!tools.length){container.innerHTML='<div class="empty-note">No tools available.</div>';return;}
    var html='';
    var layers=[1,2,3,4,5];
    var layerNames={1:'Physical (L1)',2:'Data Link (L2)',3:'Network (L3)',4:'Transport (L4)',5:'Application (L5-7)'};
    var layerDescs={1:'Cables, signal, interface hardware errors',2:'WiFi, switching, frame-level issues',3:'IP routing, ICMP, path MTU, gateway',4:'TCP/UDP, connections, retransmits, throughput',5:'DNS, HTTP, speed tests, bufferbloat'};
    for(var li=0;li<layers.length;li++){
      var layerNum=layers[li];
      var layerTools=tools.filter(function(t){return t.layer===layerNum;});
      if(!layerTools.length)continue;
      html+='<div class="card" style="margin-top:16px;padding:12px 16px">';
      html+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">';
      html+='<span style="font-size:14px;font-weight:700;color:var(--accent)">Layer '+layerNum+' &mdash; '+layerNames[layerNum]+'</span>';
      html+='<span style="font-size:11px;color:var(--info)">'+layerDescs[layerNum]+'</span>';
      html+='</div>';
      for(var ti=0;ti<layerTools.length;ti++){
        html+=renderToolCard(layerTools[ti]);
      }
      html+='</div>';
    }
    container.innerHTML=html;
    toolsMenuLoaded=true;
  }).catch(function(e){console.log('tools menu load error',e);});
}

function renderToolCard(tool){
  var html='<div class="tool-card" id="tool-card-'+tool.id+'">';
  html+='<div class="tool-layer">'+(tool.layer===0?'':tool.layer_name)+'</div>';
  html+='<h3>'+tool.name+'</h3>';
  html+='<div class="tool-desc">'+tool.desc+'</div>';
  if(tool.docs)html+='<div class="tool-docs">'+tool.docs+'</div>';
  if(tool.presets&&tool.presets.length){
    html+='<div class="tool-presets">';
    for(var pi=0;pi<tool.presets.length;pi++){
      var p=tool.presets[pi];
      html+='<button class="tool-preset-btn" onclick="applyPreset(\''+tool.id+'\',\''+p.name.replace(/'/g,"\\'")+'\')">'+p.name+'</button>';
    }
    html+='</div>';
  }
  if(tool.params&&tool.params.length){
    html+='<div class="tool-params" id="tool-params-'+tool.id+'">';
    for(var pi=0;pi<tool.params.length;pi++){
      var p=tool.params[pi];
      var ptype=p.type||'text';
      if(ptype==='checkbox'){
        html+='<div class="tool-param-row"><label style="flex-direction:row;align-items:center;gap:6px;cursor:pointer">';
        html+='<input id="tool-param-'+tool.id+'-'+p.key+'" class="tool-param" type="checkbox" data-tool="'+tool.id+'" data-key="'+p.key+'"'+(p.default?' checked':'')+'>';
        html+=p.label+'</label></div>';
      }else{
        var extra='';
        if(ptype==='number'){
          if(p.min!=null)extra+=' min="'+p.min+'"';
          if(p.max!=null)extra+=' max="'+p.max+'"';
          if(p.step!=null)extra+=' step="'+p.step+'"';
        }
        html+='<div class="tool-param-row"><label for="tool-param-'+tool.id+'-'+p.key+'">'+p.label+'</label>';
        html+='<input id="tool-param-'+tool.id+'-'+p.key+'" class="tool-param" type="'+ptype+'" value="'+p.default+'" data-tool="'+tool.id+'" data-key="'+p.key+'"'+extra+'>';
        html+='</div>';
      }
    }
    html+='</div>';
  }
  html+='<div class="tool-actions">';
  html+='<button class="btn" id="tool-run-'+tool.id+'" onclick="runTool(\''+tool.id+'\')">Run</button>';
  html+='<span class="tool-status" id="tool-status-'+tool.id+'"></span>';
  html+='</div>';
  html+='<div class="tool-result" id="tool-result-'+tool.id+'"></div>';
  html+='</div>';
  return html;
}

function getParamValue(el){
  if(el.type==='checkbox')return el.checked;
  return el.value;
}

function applyPreset(toolId,presetName){
  fetch('/api/tools/menu').then(function(r){return r.json();}).then(function(d){
    var tools=d.tools||[];
    for(var i=0;i<tools.length;i++){
      if(tools[i].id===toolId){
        var presets=tools[i].presets||[];
        for(var j=0;j<presets.length;j++){
          if(presets[j].name===presetName){
            var vals=presets[j].values;
            for(var k in vals){
              var el=document.getElementById('tool-param-'+toolId+'-'+k);
              if(el){
                if(el.type==='checkbox')el.checked=!!vals[k];
                else el.value=vals[k];
              }
            }
            return;
          }
        }
      }
    }
  });
}

function runTool(toolId){
  var btn=document.getElementById('tool-run-'+toolId);
  var statusEl=document.getElementById('tool-status-'+toolId);
  var resultEl=document.getElementById('tool-result-'+toolId);
  var card=document.getElementById('tool-card-'+toolId);
  if(!btn||btn.disabled)return;
  btn.disabled=true;
  btn.textContent='Running...';
  statusEl.textContent='Running...';
  resultEl.classList.remove('show');
  resultEl.textContent='';
  if(card)card.className='tool-card running';
  var params={};
  document.querySelectorAll('#tool-params-'+toolId+' .tool-param').forEach(function(el){
    params[el.dataset.key]=getParamValue(el);
  });
  fetch('/api/tool/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tool_id:toolId,params:params})}).then(function(r){
    return r.json();
  }).then(function(data){
    if(data.error){
      statusEl.textContent='Error: '+data.error;
      if(card)card.className='tool-card error';
      btn.disabled=false;
      btn.textContent='Run';
      return;
    }
    statusEl.textContent='Running...';
    pollToolResult(toolId);
  }).catch(function(e){
    statusEl.textContent='Request failed';
    if(card)card.className='tool-card error';
    btn.disabled=false;
    btn.textContent='Run';
  });
}

function pollToolResult(toolId){
  var btn=document.getElementById('tool-run-'+toolId);
  var statusEl=document.getElementById('tool-status-'+toolId);
  var resultEl=document.getElementById('tool-result-'+toolId);
  var card=document.getElementById('tool-card-'+toolId);
  fetch('/api/tool/status').then(function(r){return r.json();}).then(function(s){
    if(s.running){
      setTimeout(function(){pollToolResult(toolId);},300);
      return;
    }
    btn.disabled=false;
    btn.textContent='Run';
    if(s.error){
      statusEl.textContent='Error';
      if(card)card.className='tool-card error';
      resultEl.textContent='ERROR: '+s.error;
      resultEl.classList.add('show');
    }else if(s.result){
      statusEl.textContent='Done';
      if(card)card.className='tool-card done';
      renderToolResult(toolId,s.result,resultEl);
      resultEl.classList.add('show');
    }else{
      statusEl.textContent='No result';
      if(card)card.className='tool-card';
    }
  }).catch(function(e){
    setTimeout(function(){pollToolResult(toolId);},500);
  });
}

function renderToolResult(toolId,result,el){
  if(!result){el.textContent='(no result)';return;}
  if(typeof result==='string'){el.textContent=result;return;}
  if(result instanceof Array){
    el.textContent=JSON.stringify(result,null,2);
    return;
  }
  if(toolId==='diagnose_engine'){
    var diags=result.diagnosis||result||[];
    if(diags instanceof Array){
      var dhtml='';
      for(var di=0;di<diags.length;di++){
        var d=diags[di];
        var sev=d.severity||'info';
        var icon={clean:'✓',warning:'!',bad:'✗',info:'i'}[sev]||'?';
        dhtml+='<div style="padding:8px 10px;margin:4px 0;border-radius:4px;border-left:3px solid '+
          ({clean:'#22c55e',warning:'#eab308',bad:'#ef4444',info:'#38bdf8'}[sev]||'#64748b')+
          ';background:var(--card)">';
        dhtml+='<div style="font-weight:600;font-size:12px">'+icon+' ['+d.layer+'] '+d.title+'</div>';
        if(d.detail)dhtml+='<div style="font-size:11px;color:var(--info);margin-top:2px">'+d.detail+'</div>';
        if(d.fix)dhtml+='<div style="font-size:11px;color:var(--accent);margin-top:2px">Fix: '+d.fix+'</div>';
        dhtml+='</div>';
      }
      el.innerHTML=dhtml||'<span class="rval">No diagnoses</span>';
      return;
    }
  }
  if(toolId==='health_score_tool'){
    var hs=result.health_score;
    if(hs!=null){
      var cls=hs>=70?'rval':(hs>=40?'rwarn':'rerr');
      el.innerHTML='<span style="font-size:24px;font-weight:700" class="'+cls+'">'+hs+'</span><span style="font-size:14px;color:var(--info);margin-left:8px">/ 100</span>';
      return;
    }
  }
  if(toolId==='classify_ping'){
    var cls=result.classification||'unknown';
    var ccls={clean:'rval',bad_loss:'rerr',some_loss:'rwarn',bad_latency_spikes:'rerr',latency_spikes:'rwarn',high_jitter:'rwarn'}[cls]||'';
    el.innerHTML='<span style="font-size:18px;font-weight:700" class="'+ccls+'">'+cls.replace(/_/g,' ')+'</span>';
    var host=result.host||'';
    if(host)el.innerHTML+='<br><span class="rkey">Host</span>: <span class="rval">'+host+'</span>';
    return;
  }
  if(toolId==='check_tools'){
    var html='';
    var all=(result.checked_required||[]).concat(result.checked_optional||[]);
    var missing=new Set((result.missing_required||[]).concat(result.missing_optional||[]));
    html+='<table style="width:auto;border-collapse:collapse;font-size:12px">';
    html+='<tr><th style="padding:4px 12px;text-align:left;color:var(--info)">Tool</th><th style="padding:4px 12px;text-align:left;color:var(--info)">Status</th></tr>';
    for(var ci=0;ci<all.length;ci++){
      var ok=!missing.has(all[ci]);
      html+='<tr><td style="padding:3px 12px">'+all[ci]+'</td><td style="padding:3px 12px" class="'+(ok?'rval':'rerr')+'">'+(ok?'Available':'Missing')+'</td></tr>';
    }
    html+='</table>';
    if(result.install_hint_required)html+='<div style="margin-top:6px;font-size:11px;color:var(--info)">'+result.install_hint_required+'</div>';
    if(result.install_hint_optional)html+='<div style="font-size:11px;color:var(--info)">'+result.install_hint_optional+'</div>';
    el.innerHTML=html;
    return;
  }
  if(toolId==='full_diagnostic'){
    var highlights=[];
    if(result.health_score!=null){
      var sc=result.health_score;
      var scls=sc>=70?'rval':(sc>=40?'rwarn':'rerr');
      highlights.push('<span class="rkey">Health Score</span>: <span class="'+scls+'" style="font-size:18px;font-weight:700">'+sc+'</span><span style="font-size:12px;color:var(--info)">/100</span>');
    }
    if(result.gateway)highlights.push('<span class="rkey">Gateway</span>: <span class="rval">'+result.gateway+'</span>');
    if(result.default_interface)highlights.push('<span class="rkey">Interface</span>: <span class="rval">'+result.default_interface+'</span>');
    var diags=result.diagnosis||[];
    var bad=diags.filter(function(d){return d.severity!=='clean';}).length;
    highlights.push('<span class="rkey">Issues found</span>: <span class="'+(bad>0?'rwarn':'rval')+'">'+bad+'</span>');
    el.innerHTML=highlights.join(' &middot; ')+'<br><br>'+el.textContent;
  }

  var lines=[];
  for(var k in result){
    var v=result[k];
    if(v===null||v===undefined)continue;
    if(k==='available'||k==='error'||k==='_file'||k==='_source'||k==='raw'||k==='stdout'||k==='stderr'||k==='diagnosis'||k==='samples'||k==='health_score'||k==='timestamp'||k==='platform'||k==='os'||k==='tools')continue;
    var display=v;
    if(typeof v==='object'){
      if(v instanceof Array){
        if(v.length>5)display='['+v.length+' items]';
        else display=JSON.stringify(v);
      }else if(v&&v.available!==undefined){
        display=v.available?'Available':'Unavailable';
      }else{
        display=JSON.stringify(v);
      }
    }
    if(k==='avg_ms'||k==='rtt_ms'||k==='p95_ms'||k==='p50_ms'||k==='p99_ms'||k==='min_ms'||k==='max_ms'||k==='stdev_ms'||k==='jitter_ms'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+' ms</span>');
    }else if(k==='loss_pct'||k==='failure_pct'||k==='retransmit_pct'){
      var cls=parseFloat(v)>5?'rerr':(parseFloat(v)>0?'rwarn':'rval');
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="'+cls+'">'+display+'%</span>');
    }else if(k==='signal_dbm'){
      var cls=v<-80?'rerr':(v<-70?'rwarn':'rval');
      lines.push('<span class="rkey">Signal</span>: <span class="'+cls+'">'+display+' dBm</span>');
    }else if(k==='download_mbps'||k==='upload_mbps'||k==='mbps'||k==='avg_mbps'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+' Mbps</span>');
    }else if(k==='ratio'){
      var cls=parseFloat(v)>3?'rerr':(parseFloat(v)>2?'rwarn':'rval');
      lines.push('<span class="rkey">Bufferbloat ratio</span>: <span class="'+cls+'">'+display+'x</span>');
    }else if(k==='gateway'||k==='default_interface'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+'</span>');
    }else if(k==='rx'||k==='tx'){
      if(typeof v==='object'){
        var sub=result[k]||{};
        lines.push('<span class="rkey">'+k.toUpperCase()+'</span>');
        for(var sk in sub){
          lines.push('  <span class="rkey">'+sk+'</span>: <span class="rval">'+sub[sk]+'</span>');
        }
      }
    }else if(k==='addresses'&&v instanceof Array){
      lines.push('<span class="rkey">Resolved addresses</span>:');
      v.forEach(function(a){lines.push('  <span class="rval">'+(a.ip||a)+'</span>');});
    }else if(k==='hops'&&v instanceof Array){
      lines.push('<span class="rkey">Route hops</span>:');
      v.forEach(function(h){lines.push('  Hop '+h.hop+': <span class="rval">'+(h.host||'*')+'</span> loss: '+(h.loss_pct!=null?h.loss_pct+'%':'?')+' avg: '+(h.avg_ms!=null?h.avg_ms+'ms':'?'));});
    }else if(k==='received'||k==='sent'||k==='queries'||k==='attempts'||k==='failures'||k==='success'||k==='total'||k==='ok'||k==='classification'){
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+'</span>');
    }else{
      lines.push('<span class="rkey">'+(k.replace(/_/g,' '))+'</span>: <span class="rval">'+display+'</span>');
    }
  }
  el.innerHTML=el.innerHTML+lines.join('\n');
}
</script>
</body>
</html>"""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(INDEX_HTML, encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Response(content=INDEX_FILE.read_text(encoding="utf-8"), media_type="text/html")

    @app.get("/api/status")
    def api_status(response_class=JSONResponse):
        with lock:
            r = dict(current_run)
        r.pop("_lock", None)
        if r.get("results"):
            r["results"] = {k: v for k, v in r["results"].items()
                            if k not in ("raw", "stdout", "stderr")}
            for key in ["gateway_ping", "internet_ping"]:
                if isinstance(r["results"].get(key), list):
                    for item in r["results"][key]:
                        item.pop("samples", None)
                elif isinstance(r["results"].get(key), dict):
                    r["results"][key].pop("samples", None)
        return JSONResponse(content=r)

    @app.get("/api/monitor")
    def api_monitor():
        try:
            wifi = None
            if IS_LINUX:
                wifi = _proc_net_wireless_any()
                if not wifi and has_tool("iw"):
                    iface = detect_wireless_interface()
                    if iface:
                        info = wifi_info(iface)
                        if info and info.get("signal_dbm") is not None and info["signal_dbm"] < 0:
                            wifi = info
            elif IS_MACOS:
                iface = detect_wireless_interface()
                if iface:
                    info = wifi_info(iface)
                    if info and info.get("signal_dbm") is not None:
                        wifi = info
            elif IS_WINDOWS:
                iface = detect_wireless_interface()
                if iface:
                    info = wifi_info(iface)
                    if info and info.get("signal_dbm") is not None:
                        wifi = info

            gateway = detect_gateway()
            latency = None
            if gateway:
                r = ping_once(gateway, timeout_s=1)
                if r and r.get("ok"):
                    latency = r["rtt_ms"]
                if latency is None:
                    t = _tcp_ping(gateway, port=80, timeout_s=1)
                    if t and t.get("ok"):
                        latency = t["rtt_ms"]
            health = 50
            if wifi and wifi.get("signal_dbm") is not None:
                sig = wifi["signal_dbm"]
                sig_score = max(1, min(100, 100 - (max(0, -55 - sig) * 3)))
                health = sig_score
            if latency is not None:
                lat_score = max(1, 100 - max(0, latency - 10) * 2)
                health = (health + lat_score) // 2 if wifi and wifi.get("signal_dbm") is not None else lat_score
            log.info("poll ok sig=%s lat=%s health=%s",
                     wifi.get("signal_dbm") if wifi else None, latency, health)
            return JSONResponse(content={
                "wifi": wifi,
                "gateway_latency_ms": latency,
                "health_score": health,
                "timestamp": now_iso(),
                "advanced": monitor_snapshot(),
            })
        except Exception as e:
            log.error("poll error: %s", str(e), exc_info=True)
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @app.post("/api/monitor/start")
    def api_monitor_start():
        started = monitor_start()
        return JSONResponse(content={"status": "ok", "started": started})

    @app.post("/api/monitor/stop")
    def api_monitor_stop():
        stopped = monitor_stop()
        return JSONResponse(content={"status": "ok", "stopped": stopped})

    @app.get("/api/activity")
    def api_activity():
        return JSONResponse(content={"activity": get_activity_log(50)})

    @app.get("/api/tools")
    def api_tools():
        return JSONResponse(content=check_tools())

    @app.get("/api/config")
    def api_config_get():
        return JSONResponse(content=load_config())

    @app.post("/api/config")
    async def api_config_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(content={"error": "expected a JSON object"}, status_code=400)
        cfg = save_config(body)
        return JSONResponse(content=cfg)

    @app.post("/api/run")
    async def api_run(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

        with lock:
            if current_run["status"] == "running":
                return JSONResponse(content={"status": "error", "message": "Diagnostic already running"})
            current_run["status"] = "running"
            current_run["progress"] = {}
            current_run["results"] = None
            current_run["error"] = None

        parser = build_parser()
        args = parser.parse_args([])
        if not IS_LINUX:
            args.no_bufferbloat = True

        args.no_speedtest = not body.get("speedtest", False)
        args.no_trace = not body.get("trace", True)
        args.no_bufferbloat = not body.get("bufferbloat", IS_LINUX)
        args.no_iperf = not body.get("iperf3", False)
        args.download_test = body.get("download_test", False)
        args.connection_test = body.get("connection_test", False)

        thread = threading.Thread(target=run_diag, args=(args, current_run), daemon=True)
        thread.start()
        return JSONResponse(content={"status": "ok", "session_id": now_iso().replace(":", "")})

    @app.get("/api/reports")
    def api_reports(response_class=JSONResponse):
        files = []
        if REPORT_DIR.is_dir():
            for f in sorted(REPORT_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat()
                })
        return JSONResponse(content={"reports": files, "dir": str(REPORT_DIR)})

    @app.get("/api/report/{name}")
    def api_report(name: str, response_class=Response):
        fpath = REPORT_DIR / name
        if not fpath.exists() or not fpath.is_file():
            return JSONResponse(content={"error": "Not found"}, status_code=404)
        media = "text/plain"
        if name.endswith(".json"):
            media = "application/json"
        elif name.endswith(".csv"):
            media = "text/csv"
        return Response(content=fpath.read_bytes(), media_type=media)

    @app.get("/api/history")
    def api_history(response_class=JSONResponse):
        sessions = load_history("~/.netdiag")
        for s in sessions:
            s.pop("raw", None)
            s.pop("stdout", None)
            s.pop("stderr", None)
            for key in list(s.keys()):
                if isinstance(s.get(key), list):
                    pass
                elif isinstance(s.get(key), dict) and "samples" in (s.get(key) or {}):
                    s[key].pop("samples", None)
        return JSONResponse(content={"sessions": sessions})

    @app.get("/api/session/{file}")
    def api_session(file: str, response_class=JSONResponse):
        d = ensure_history_dir("~/.netdiag")
        fpath = d / file
        if not fpath.exists():
            return JSONResponse(content={"error": "Not found"}, status_code=404)
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            return JSONResponse(content=data)
        except:
            return JSONResponse(content={"error": "Parse error"}, status_code=500)

    @app.get("/api/export/{file}")
    def api_export(file: str, format: str = "json", response_class=Response):
        d = ensure_history_dir("~/.netdiag")
        fpath = d / file
        if not fpath.exists():
            return Response(content="Not found", status_code=404)
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except:
            return Response(content="Parse error", status_code=500)

        if format == "json":
            return Response(content=json.dumps(data, indent=2, ensure_ascii=False),
                            media_type="application/json",
                            headers={"Content-Disposition": f"attachment; filename={file}"})

        if format == "csv":
            rows = flatten_ping(data)
            if rows:
                import io
                buf = io.StringIO()
                fieldnames = sorted({k for row in rows for k in row})
                w = csv.DictWriter(buf, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
                return Response(content=buf.getvalue(), media_type="text/csv",
                                headers={"Content-Disposition": f"attachment; filename={file.replace('.json','.csv')}"})
            return Response(content="No ping data", status_code=404)

        if format == "html":
            html = "<!DOCTYPE html><html><head><meta charset=utf-8><title>NetDiag Report</title>"
            html += "<style>body{font:14px system-ui;max-width:800px;margin:40px auto;padding:20px;background:#0f172a;color:#e2e8f0}"
            html += "h1{color:#38bdf8}h2{color:#e2e8f0;margin-top:24px}.card{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px;margin:8px 0}"
            html += ".bad{color:#ef4444}.warning{color:#eab308}.clean{color:#22c55e}pre{background:#0f172a;padding:12px;border-radius:4px;overflow-x:auto}</style></head><body>"
            html += f"<h1>NetDiag Report</h1><p>{data.get('timestamp','')} | {data.get('platform','')} | Score: {data.get('health_score','?')}/100</p>"
            html += "<h2>Diagnosis</h2>"
            for d in data.get("diagnosis", []):
                html += f"<div class='card'><strong class='{d['severity']}'>[{d['layer']}] {d['title']}</strong><br>{d.get('detail','')}<br><em>{d.get('fix','')}</em></div>"
            html += "<h2>Ping Summary</h2><pre>" + json.dumps(ping_summary_rows(data), indent=2, ensure_ascii=False) + "</pre>"
            html += "</body></html>"
            return Response(content=html, media_type="text/html",
                            headers={"Content-Disposition": f"attachment; filename={file.replace('.json','.html')}"})

        return Response(content="Unknown format", status_code=400)

    # -- Tools Menu routes ------------------------------------------------------------

    tools_run_state = {"running": False, "tool_id": None, "result": None, "error": None}

    @app.get("/api/tools/menu")
    def api_tools_menu():
        tlist = []
        for t in TOOLS_MENU:
            entry = {k: t[k] for k in ("id", "name", "layer", "layer_name", "desc", "docs", "params", "presets")}
            # Strip run function for JSON
            tlist.append(entry)
        return JSONResponse(content={"tools": tlist})

    @app.post("/api/tool/run")
    async def api_tool_run(request: Request):
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(content={"error": "Invalid JSON"}, status_code=400)
        tool_id = body.get("tool_id")
        params = body.get("params", {})
        if not isinstance(params, dict):
            params = {}

        tool = None
        for t in TOOLS_MENU:
            if t["id"] == tool_id:
                tool = t
                break
        if not tool:
            return JSONResponse(content={"error": f"Tool '{tool_id}' not found"}, status_code=404)

        with lock:
            if tools_run_state["running"]:
                return JSONResponse(content={"error": "A tool is already running", "tool_id": tools_run_state["tool_id"]}, status_code=409)
            tools_run_state["running"] = True
            tools_run_state["tool_id"] = tool_id
            tools_run_state["result"] = None
            tools_run_state["error"] = None

        def _run_tool():
            try:
                result = tool["run"](params)
                with lock:
                    tools_run_state["result"] = result
                    tools_run_state["running"] = False
            except Exception as e:
                log.error("tool %s error: %s", tool_id, str(e), exc_info=True)
                with lock:
                    tools_run_state["error"] = str(e)
                    tools_run_state["running"] = False

        thread = threading.Thread(target=_run_tool, daemon=True)
        thread.start()
        return JSONResponse(content={"status": "ok", "tool_id": tool_id})

    @app.get("/api/tool/status")
    def api_tool_status():
        with lock:
            return JSONResponse(content={
                "running": tools_run_state["running"],
                "tool_id": tools_run_state["tool_id"],
                "result": tools_run_state["result"],
                "error": tools_run_state["error"],
            })

    @app.get("/api/results/{file}/json")
    def api_results_json(file: str, response_class=Response):
        d = ensure_history_dir("~/.netdiag")
        fpath = d / file
        if not fpath.exists():
            return Response(content="Not found", status_code=404)
        data = json.loads(fpath.read_text(encoding="utf-8"))
        return Response(content=json.dumps(data, indent=2, ensure_ascii=False),
                        media_type="application/json")

    return app, current_run, build_parser


def start_server(args):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    app, current_run, parser = build_app()
    if app is None:
        print("Error: fastapi and uvicorn required.", file=sys.stderr)
        sys.exit(1)

    import uvicorn

    if args.daemon:
        diag_args = parser().parse_args([])
        if not IS_LINUX:
            diag_args.no_bufferbloat = True

        import threading

        def daemon_loop(diag_args, current_run):
            while True:
                with current_run.get("_lock", threading.Lock()):
                    if current_run.get("status") != "running":
                        current_run["status"] = "running"
                        current_run["progress"] = {}

                        def cb(label, seq, total, ok, rtt, status_override=None):
                            st2 = status_override or ("running" if seq < total else "done")
                            with current_run.get("_lock", threading.Lock()):
                                current_run["progress"][label] = {"seq": seq, "total": total, "ok": ok, "rtt_ms": rtt, "status": st2}

                        try:
                            res = full_diagnostic(diag_args, callback=cb)
                            with current_run.get("_lock", threading.Lock()):
                                current_run["status"] = "done"
                                current_run["results"] = res
                                save_history(diag_args.history_dir, res)
                        except Exception as e:
                            with current_run.get("_lock", threading.Lock()):
                                current_run["status"] = "error"
                                current_run["error"] = str(e)
                time.sleep(600)

        current_run["_lock"] = threading.Lock()
        t = threading.Thread(target=daemon_loop, args=(diag_args, current_run), daemon=True)
        t.start()

    log.info("NetDiag web UI starting at http://localhost:%s", args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    cli_main()
