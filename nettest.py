#!/usr/bin/env python3
"""
Linux-first internet diagnostics tool.

Purpose:
- Diagnose shaky internet connections.
- Separate local network/router issues from ISP/upstream issues.
- Produce readable console output and machine-readable reports.

Checks:
- Required and optional tools
- IPv4 and IPv6 gateway detection
- Gateway ping stability
- External ping stability
- DNS resolution latency and failure rate
- TCP connection latency and failure rate
- Optional route check via mtr or traceroute
- Optional speedtest

Outputs:
- diagnostics.json
- ping_samples.csv
- ping_summary.csv
- report.txt

Typical usage:
    python3 nettest.py

Fast smoke test:
    python3 nettest.py --count 5 --interval 0.2 --no-speedtest --no-trace

Useful normal test:
    python3 nettest.py --count 20 --interval 0.5 --no-speedtest

Longer serious test:
    python3 nettest.py --count 120 --interval 1 --no-speedtest
"""

import argparse
import csv
import json
import platform
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_HOSTS = ["1.1.1.1", "8.8.8.8", "9.9.9.9", "google.com"]
DNS_HOSTS = ["google.com", "cloudflare.com", "quad9.net"]
TCP_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443), ("google.com", 443)]

APT_PACKAGES = {
    "ping": "iputils-ping",
    "ip": "iproute2",
    "traceroute": "traceroute",
    "mtr": "mtr-tiny",
    "speedtest-cli": "speedtest-cli",
}


class UserInterrupted(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_cmd(cmd, timeout=30):
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Timeout after {timeout}s"
    except Exception as e:
        return 999, "", str(e)


def has_tool(name):
    return shutil.which(name) is not None


def is_linux():
    return platform.system().lower() == "linux"


def detect_package_manager():
    for tool in ["apt", "dnf", "yum", "pacman", "zypper"]:
        if has_tool(tool):
            return tool
    return None


def install_hint(missing):
    if not missing:
        return None

    pm = detect_package_manager()

    if pm == "apt":
        packages = sorted({APT_PACKAGES.get(x, x) for x in missing})
        return "sudo apt update && sudo apt install -y " + " ".join(packages)

    if pm == "dnf":
        return "sudo dnf install -y " + " ".join(sorted(missing))

    if pm == "yum":
        return "sudo yum install -y " + " ".join(sorted(missing))

    if pm == "pacman":
        return "sudo pacman -S " + " ".join(sorted(missing))

    if pm == "zypper":
        return "sudo zypper install " + " ".join(sorted(missing))

    return "Install missing tools manually: " + ", ".join(sorted(missing))


def install_missing_tools(missing):
    if not missing:
        return {"attempted": False, "message": "No missing tools."}

    pm = detect_package_manager()

    if pm != "apt":
        return {
            "attempted": False,
            "success": False,
            "message": "Automatic install is only implemented for apt-based Linux systems.",
            "hint": install_hint(missing),
        }

    packages = sorted({APT_PACKAGES.get(x, x) for x in missing})

    cmd = ["sudo", "apt", "update"]
    rc1, out1, err1 = run_cmd(cmd, timeout=120)

    if rc1 != 0:
        return {
            "attempted": True,
            "success": False,
            "command": " ".join(cmd),
            "stdout": out1[-2000:],
            "stderr": err1[-2000:],
            "hint": install_hint(missing),
        }

    cmd = ["sudo", "apt", "install", "-y", *packages]
    rc2, out2, err2 = run_cmd(cmd, timeout=300)

    return {
        "attempted": True,
        "success": rc2 == 0,
        "command": " ".join(cmd),
        "stdout": out2[-2000:],
        "stderr": err2[-2000:],
    }


def check_tools():
    required = ["ping", "ip"]
    optional = ["mtr", "traceroute", "speedtest", "speedtest-cli"]

    missing_required = [x for x in required if not has_tool(x)]
    missing_optional = [x for x in optional if not has_tool(x)]

    return {
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "install_hint_required": install_hint(missing_required),
        "install_hint_optional": install_hint(missing_optional),
    }


def ping_command(host, timeout_s=2, ipv=None):
    timeout_s = max(1, int(round(timeout_s)))

    cmd = ["ping", "-c", "1", "-W", str(timeout_s)]

    if ipv == 4:
        cmd.insert(1, "-4")
    elif ipv == 6:
        cmd.insert(1, "-6")

    cmd.append(host)
    return cmd


def parse_rtt_ms(text):
    patterns = [
        r"time[=<]\s*([0-9.]+)\s*ms",
        r"rtt min/avg/max/mdev = [0-9.]+/([0-9.]+)/",
        r"round-trip min/avg/max/stddev = [0-9.]+/([0-9.]+)/",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None

    return None


def ping_once(host, timeout_s=2, ipv=None):
    cmd = ping_command(host, timeout_s=timeout_s, ipv=ipv)
    rc, out, err = run_cmd(cmd, timeout=timeout_s + 3)
    text = (out + "\n" + err).strip()
    rtt = parse_rtt_ms(text)

    return {
        "ok": rc == 0 and rtt is not None,
        "rtt_ms": rtt,
        "rc": rc,
        "raw": text[-500:],
    }


def percentile(values, pct):
    if not values:
        return None

    values = sorted(values)
    k = (len(values) - 1) * pct / 100
    lower = int(k)
    upper = min(lower + 1, len(values) - 1)

    if lower == upper:
        return values[lower]

    return values[lower] + (values[upper] - values[lower]) * (k - lower)


def clean_float(value):
    if value is None:
        return None
    return round(float(value), 2)


def series_stats(values):
    if not values:
        return {
            "count": 0,
            "min_ms": None,
            "avg_ms": None,
            "max_ms": None,
            "stdev_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
        }

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


def ping_burst(host, count, interval, timeout_s=2, ipv=None, label=None, quiet=False):
    samples = []
    rtts = []
    lost = 0
    label = label or host

    if not quiet:
        print(
            f"Testing {label}: {count} pings, interval={interval}s, timeout={timeout_s}s",
            flush=True,
        )

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
                "timestamp": ts,
                "seq": seq,
                "label": label,
                "host": host,
                "ipv": ipv or "auto",
                "ok": result["ok"],
                "rtt_ms": result["rtt_ms"],
                "rc": result["rc"],
            })

            if not quiet:
                print(f"  {label} {seq}/{count}: {status}", flush=True)

            if seq < count and interval > 0:
                time.sleep(interval)

    except KeyboardInterrupt:
        raise UserInterrupted(f"Interrupted while testing {label}")

    sent = len(samples)

    return {
        "label": label,
        "host": host,
        "ipv": ipv or "auto",
        "sent": sent,
        "received": sent - lost,
        "loss_pct": clean_float(100 * lost / sent) if sent else None,
        "jitter_ms": jitter_ms(rtts),
        **series_stats(rtts),
        "samples": samples,
        "interrupted": sent < count,
    }


def resolve_all(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception as e:
        return {
            "host": host,
            "ok": False,
            "error": str(e),
            "addresses": [],
        }

    addresses = []
    seen = set()

    for family, _, _, _, sockaddr in infos:
        address = sockaddr[0]
        key = (family, address)

        if key in seen:
            continue

        seen.add(key)

        if family == socket.AF_INET:
            version = 4
        elif family == socket.AF_INET6:
            version = 6
        else:
            version = None

        addresses.append({
            "ip": address,
            "version": version,
        })

    return {
        "host": host,
        "ok": True,
        "addresses": addresses,
    }


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
        "host": host,
        "queries": count,
        "failures": failures,
        "failure_pct": clean_float(100 * failures / count),
        "addresses": unique,
        **series_stats(times),
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
        "host": host,
        "port": port,
        "attempts": count,
        "failures": failures,
        "failure_pct": clean_float(100 * failures / count),
        "errors": errors,
        **series_stats(times),
    }


def detect_gateway_ipv4():
    rc, out, _ = run_cmd(["ip", "-4", "route", "show", "default"], timeout=10)

    if rc != 0:
        return None

    m = re.search(r"default via ([0-9.]+)", out)
    return m.group(1) if m else None


def detect_gateway_ipv6():
    rc, out, _ = run_cmd(["ip", "-6", "route", "show", "default"], timeout=10)

    if rc != 0:
        return None

    m = re.search(r"default via ([0-9a-fA-F:]+)", out)
    return m.group(1) if m else None


def get_default_interface():
    rc, out, _ = run_cmd(["ip", "route", "show", "default"], timeout=10)

    if rc != 0:
        return None

    m = re.search(r"\bdev\s+(\S+)", out)
    return m.group(1) if m else None


def get_link_info(interface):
    if not interface:
        return None

    rc, out, err = run_cmd(["ip", "-details", "link", "show", "dev", interface], timeout=10)

    return {
        "interface": interface,
        "available": rc == 0,
        "stdout": out,
        "stderr": err,
    }


def get_wifi_info(interface):
    if not interface:
        return None

    if not has_tool("iw"):
        return {
            "available": False,
            "reason": "iw not installed",
        }

    rc, out, err = run_cmd(["iw", "dev", interface, "link"], timeout=10)

    return {
        "available": rc == 0,
        "interface": interface,
        "stdout": out,
        "stderr": err,
    }


def traceroute_test(host):
    if has_tool("mtr"):
        rc, out, err = run_cmd(["mtr", "-r", "-c", "20", "-w", host], timeout=90)
        return {
            "tool": "mtr",
            "host": host,
            "rc": rc,
            "stdout": out,
            "stderr": err,
        }

    if has_tool("traceroute"):
        rc, out, err = run_cmd(["traceroute", "-n", host], timeout=90)
        return {
            "tool": "traceroute",
            "host": host,
            "rc": rc,
            "stdout": out,
            "stderr": err,
        }

    return {
        "tool": None,
        "host": host,
        "available": False,
        "message": "Install mtr or traceroute for route diagnostics.",
    }


def speedtest():
    if has_tool("speedtest"):
        rc, out, err = run_cmd(["speedtest", "--format=json"], timeout=180)

        if rc == 0:
            try:
                return {
                    "available": True,
                    "tool": "speedtest",
                    "data": json.loads(out),
                }
            except Exception:
                return {
                    "available": True,
                    "tool": "speedtest",
                    "raw": out,
                }

        return {
            "available": True,
            "tool": "speedtest",
            "rc": rc,
            "error": err or out,
        }

    if has_tool("speedtest-cli"):
        rc, out, err = run_cmd(["speedtest-cli", "--json"], timeout=180)

        if rc == 0:
            try:
                return {
                    "available": True,
                    "tool": "speedtest-cli",
                    "data": json.loads(out),
                }
            except Exception:
                return {
                    "available": True,
                    "tool": "speedtest-cli",
                    "raw": out,
                }

        return {
            "available": True,
            "tool": "speedtest-cli",
            "rc": rc,
            "error": err or out,
        }

    return {
        "available": False,
        "message": "Install speedtest-cli or Ookla speedtest for bandwidth checks.",
    }


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


def diagnose(results):
    notes = []

    gw4 = results.get("gateway_ipv4_ping")
    gw6 = results.get("gateway_ipv6_ping")
    internet = results.get("internet_ping", [])
    dns = results.get("dns", [])
    tcp = results.get("tcp", [])

    gateways = [x for x in [gw4, gw6] if x]
    bad_gateways = [x for x in gateways if classify_ping(x) != "clean"]
    bad_internet = [x for x in internet if classify_ping(x) != "clean"]

    if results.get("interrupted"):
        notes.append("Test was interrupted. Diagnosis is based on partial results only.")

    if not results.get("gateway_ipv4") and not results.get("gateway_ipv6"):
        notes.append("No default gateway detected. Check routing, VPN, container environment, or NetworkManager state.")

    if bad_gateways:
        notes.append("Local network instability detected: gateway ping is lossy or spiky.")
        notes.append("Most likely causes: Wi-Fi interference, weak signal, bad cable, router overload, or local device issue.")

    if not bad_gateways and bad_internet:
        notes.append("Gateway looks stable, but external hosts are unstable.")
        notes.append("Most likely causes: ISP problem, upstream routing issue, modem issue, or line quality problem.")

    if bad_gateways and bad_internet:
        notes.append("Both gateway and internet tests are unstable.")
        notes.append("Fix local network first before blaming the ISP.")

    if gateways and not bad_gateways and internet and not bad_internet:
        notes.append("No strong ping instability detected during this measurement window.")

    for row in internet:
        status = classify_ping(row)

        if status == "bad_loss":
            notes.append(f"{row['label']}: severe packet loss detected.")
        elif status == "some_loss":
            notes.append(f"{row['label']}: mild packet loss detected.")
        elif status in ["bad_latency_spikes", "latency_spikes"]:
            notes.append(f"{row['label']}: latency spikes detected.")
        elif status == "high_jitter":
            notes.append(f"{row['label']}: high jitter detected.")

    dns_bad = [
        x for x in dns
        if (x.get("failure_pct") or 0) > 0 or (x.get("p95_ms") or 0) > 300
    ]

    if dns_bad:
        notes.append("DNS instability detected.")
        notes.append("Try testing with a known resolver such as 1.1.1.1 or 8.8.8.8, or bypass router DNS forwarding.")

    tcp_bad = [
        x for x in tcp
        if (x.get("failure_pct") or 0) > 0 or (x.get("p95_ms") or 0) > 500
    ]

    if tcp_bad:
        notes.append("TCP connection instability detected.")
        notes.append("Web browsing, video calls, or app logins may feel unreliable even if basic ping looks acceptable.")

    if not notes:
        notes.append("No clear issue detected.")

    return notes


def flatten_ping(results):
    rows = []

    for key in ["gateway_ipv4_ping", "gateway_ipv6_ping"]:
        group = results.get(key)
        if group:
            rows.extend(group["samples"])

    for group in results.get("internet_ping", []):
        rows.extend(group["samples"])

    return rows


def ping_summary(results):
    rows = []

    for key in ["gateway_ipv4_ping", "gateway_ipv6_ping"]:
        group = results.get(key)
        if group:
            rows.append({k: v for k, v in group.items() if k != "samples"})

    for group in results.get("internet_ping", []):
        rows.append({k: v for k, v in group.items() if k != "samples"})

    return rows


def write_csv(path, rows):
    if not rows:
        return

    fieldnames = sorted({k for row in rows for k in row.keys()})

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compact_ping(row):
    keys = [
        "label",
        "host",
        "ipv",
        "sent",
        "received",
        "loss_pct",
        "min_ms",
        "avg_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
        "jitter_ms",
    ]

    return {k: row.get(k) for k in keys}


def write_report(path, results):
    lines = []

    lines.append("Internet Diagnostics Report")
    lines.append(f"Timestamp: {results['timestamp']}")
    lines.append(f"Platform: {results['platform']}")
    lines.append(f"Default interface: {results.get('default_interface') or 'not detected'}")
    lines.append(f"IPv4 gateway: {results.get('gateway_ipv4') or 'not detected'}")
    lines.append(f"IPv6 gateway: {results.get('gateway_ipv6') or 'not detected'}")
    lines.append(f"Interrupted: {results.get('interrupted', False)}")

    if results.get("interrupt_reason"):
        lines.append(f"Interrupt reason: {results['interrupt_reason']}")

    lines.append("")
    lines.append("Diagnosis:")

    for note in results["diagnosis"]:
        lines.append(f"- {note}")

    lines.append("")
    lines.append("Tool check:")

    tools = results["tools"]
    lines.append(f"Missing required: {tools.get('missing_required') or []}")
    lines.append(f"Missing optional: {tools.get('missing_optional') or []}")

    if tools.get("install_hint_required"):
        lines.append(f"Required install hint: {tools['install_hint_required']}")

    if tools.get("install_hint_optional"):
        lines.append(f"Optional install hint: {tools['install_hint_optional']}")

    lines.append("")
    lines.append("Ping summary:")

    for row in ping_summary(results):
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
    lines.append("Route:")

    for row in results.get("trace", []):
        lines.append(json.dumps({
            "tool": row.get("tool"),
            "host": row.get("host"),
            "rc": row.get("rc"),
            "available": row.get("available", True),
            "message": row.get("message"),
        }, ensure_ascii=False))

    path.write_text("\n".join(lines), encoding="utf-8")


def print_console_summary(results, outdir):
    print("\nDiagnosis:")

    for note in results["diagnosis"]:
        print(f"- {note}")

    print("\nPing summary:")

    rows = ping_summary(results)

    if not rows:
        print("- No ping samples collected.")
    else:
        for row in rows:
            c = compact_ping(row)
            print(
                f"- {c['label']}: "
                f"loss={c['loss_pct']}%, "
                f"avg={c['avg_ms']}ms, "
                f"p95={c['p95_ms']}ms, "
                f"max={c['max_ms']}ms, "
                f"jitter={c['jitter_ms']}ms"
            )

    missing_required = results["tools"].get("missing_required") or []
    missing_optional = results["tools"].get("missing_optional") or []

    if missing_required:
        print("\nMissing required tools:")
        print("- " + ", ".join(missing_required))

        hint = results["tools"].get("install_hint_required")
        if hint:
            print(hint)

    if missing_optional:
        print("\nMissing optional tools:")
        print("- " + ", ".join(missing_optional))

        hint = results["tools"].get("install_hint_optional")
        if hint:
            print(hint)

    print(f"\nFiles written to: {outdir.resolve()}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Linux-first shaky internet diagnostic tool"
    )

    parser.add_argument("--hosts", nargs="*", default=DEFAULT_HOSTS)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=2)
    parser.add_argument("--dns-count", type=int, default=10)
    parser.add_argument("--tcp-count", type=int, default=10)
    parser.add_argument("--outdir", default="internet_diagnostics")

    parser.add_argument("--ipv4", action="store_true", help="Force IPv4 external ping")
    parser.add_argument("--ipv6", action="store_true", help="Force IPv6 external ping")

    parser.add_argument("--no-speedtest", action="store_true")
    parser.add_argument("--no-trace", action="store_true")
    parser.add_argument("--install-missing", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-ping progress output")

    return parser


def write_all_outputs(outdir, results):
    results["diagnosis"] = diagnose(results)

    (outdir / "diagnostics.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    write_csv(outdir / "ping_samples.csv", flatten_ping(results))
    write_csv(outdir / "ping_summary.csv", ping_summary(results))
    write_report(outdir / "report.txt", results)
    print_console_summary(results, outdir)


def main():
    args = build_parser().parse_args()

    if not is_linux():
        print(
            "Warning: this script is Linux-first. Some checks may not work correctly on this OS.",
            file=sys.stderr,
        )

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

    tools = check_tools()

    if args.install_missing:
        missing = tools["missing_required"] + tools["missing_optional"]
        install_result = install_missing_tools(missing)
        tools = check_tools()
        tools["install_result"] = install_result

    if tools["missing_required"]:
        print("Missing required tools:", ", ".join(tools["missing_required"]), file=sys.stderr)

        if tools.get("install_hint_required"):
            print(tools["install_hint_required"], file=sys.stderr)

        sys.exit(1)

    gateway_ipv4 = detect_gateway_ipv4()
    gateway_ipv6 = detect_gateway_ipv6()
    default_interface = get_default_interface()

    results = {
        "timestamp": now_iso(),
        "platform": platform.platform(),
        "default_interface": default_interface,
        "link_info": get_link_info(default_interface),
        "wifi_info": get_wifi_info(default_interface),
        "gateway_ipv4": gateway_ipv4,
        "gateway_ipv6": gateway_ipv6,
        "gateway_ipv4_ping": None,
        "gateway_ipv6_ping": None,
        "internet_ping": [],
        "dns": [],
        "tcp": [],
        "trace": [],
        "speedtest": None,
        "tools": tools,
        "diagnosis": [],
        "interrupted": False,
        "interrupt_reason": None,
    }

    try:
        if gateway_ipv4:
            results["gateway_ipv4_ping"] = ping_burst(
                gateway_ipv4,
                args.count,
                args.interval,
                timeout_s=args.timeout,
                ipv=4,
                label="gateway_ipv4",
                quiet=args.quiet,
            )
        elif not args.quiet:
            print("No IPv4 gateway detected.", flush=True)

        if gateway_ipv6:
            results["gateway_ipv6_ping"] = ping_burst(
                gateway_ipv6,
                args.count,
                args.interval,
                timeout_s=args.timeout,
                ipv=6,
                label="gateway_ipv6",
                quiet=args.quiet,
            )
        elif not args.quiet:
            print("No IPv6 gateway detected.", flush=True)

        if args.ipv4 and args.ipv6:
            external_ip_versions = [4, 6]
        elif args.ipv4:
            external_ip_versions = [4]
        elif args.ipv6:
            external_ip_versions = [6]
        else:
            external_ip_versions = [None]

        for host in args.hosts:
            for ipv in external_ip_versions:
                label = f"{host}_ipv{ipv}" if ipv else host

                results["internet_ping"].append(
                    ping_burst(
                        host,
                        args.count,
                        args.interval,
                        timeout_s=args.timeout,
                        ipv=ipv,
                        label=label,
                        quiet=args.quiet,
                    )
                )

        for host in DNS_HOSTS:
            if not args.quiet:
                print(f"Testing DNS: {host}", flush=True)

            results["dns"].append(dns_test(host, args.dns_count))

        for host, port in TCP_TARGETS:
            if not args.quiet:
                print(f"Testing TCP: {host}:{port}", flush=True)

            results["tcp"].append(tcp_test(host, port, args.tcp_count))

        if not args.no_trace and args.hosts:
            if not args.quiet:
                print(f"Testing route: {args.hosts[0]}", flush=True)

            results["trace"].append(traceroute_test(args.hosts[0]))

        if not args.no_speedtest:
            if not args.quiet:
                print("Running speedtest...", flush=True)

            results["speedtest"] = speedtest()

    except UserInterrupted as e:
        results["interrupted"] = True
        results["interrupt_reason"] = str(e)
        print(f"\nInterrupted: {e}", file=sys.stderr)
        print("Writing partial results...", file=sys.stderr)

    except KeyboardInterrupt:
        results["interrupted"] = True
        results["interrupt_reason"] = "Interrupted by user"
        print("\nInterrupted by user.", file=sys.stderr)
        print("Writing partial results...", file=sys.stderr)

    write_all_outputs(outdir, results)


if __name__ == "__main__":
    main()
