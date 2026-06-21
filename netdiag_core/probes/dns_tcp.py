"""DNS resolution latency and TCP connect latency probes."""

import socket
import time

from netdiag_core.stats import clean_float, series_stats
from netdiag_core.probes import ping


def dns_test(host, count=10):
    times = []
    failures = 0
    addresses = []
    for _ in range(count):
        t0 = time.perf_counter()
        result = ping.resolve_all(host)
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
