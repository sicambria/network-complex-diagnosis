"""Intermittent-connection reliability probe: per-phase timing, first-vs-retry, IPv4/IPv6 + concurrency A/B."""

import socket
import time

from netdiag_core.stats import clean_float, percentile
from netdiag_core.constants import RELIABILITY_TARGETS
from netdiag_core.probes import verdicts


def _reliability_host_info(url):
    import urllib.parse
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    is_ip = False
    ip_family = None
    try:
        socket.inet_pton(socket.AF_INET, host)
        is_ip, ip_family = True, socket.AF_INET
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET6, host)
            is_ip, ip_family = True, socket.AF_INET6
        except OSError:
            pass
    return host, is_ip, ip_family


def reliability_test(targets=None, samples=20, duration_s=0, concurrency=8,
                     retries=2, timeout_s=5, ipv=0, compare_concurrency=True,
                     callback=None, label="reliability"):
    import ssl, urllib.parse, urllib.request, concurrent.futures, itertools, os
    if targets is None:
        targets = list(RELIABILITY_TARGETS)
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]
    targets = [t for t in targets if t]
    samples = max(1, int(samples))
    concurrency = max(1, int(concurrency))
    retries = max(0, int(retries))
    timeout_s = max(1, int(timeout_s))
    duration_s = max(0, int(duration_s))
    try:
        ipv = int(ipv)
    except (TypeError, ValueError):
        ipv = 0
    if ipv not in (0, 4, 6):
        ipv = 0
    if not targets:
        return {"available": False, "error": "no targets", "verdict": []}

    config = {"targets": targets, "samples": samples, "duration_s": duration_s,
              "concurrency": concurrency, "retries": retries, "timeout_s": timeout_s,
              "ipv": ipv, "compare_concurrency": bool(compare_concurrency)}

    seq = itertools.count()
    # Per-call nonce so cache-busting tokens never repeat across runs in the same
    # process (belt-and-suspenders on top of the no-cache headers / Connection: close).
    nonce = "%d-%s" % (os.getpid(), os.urandom(4).hex())

    fam_all = [("ipv4", socket.AF_INET), ("ipv6", socket.AF_INET6)]
    if ipv == 4:
        fam_sel = [fam_all[0]]
    elif ipv == 6:
        fam_sel = [fam_all[1]]
    else:
        fam_sel = fam_all

    meta = {}
    jobs_base = []
    for url in targets:
        host, is_ip, ip_family = _reliability_host_info(url)
        meta[url] = {"host": host, "is_ip": is_ip}
        if is_ip:
            fams = [f for f in fam_sel if f[1] == ip_family]
        else:
            fams = fam_sel
        for f in fams:
            jobs_base.append((url, f))

    if not jobs_base:
        return {"available": False, "error": "no usable target/family pairs",
                "config": config, "verdict": []}

    def _attempt_manual(url, fam):
        out = {"ok": False, "fail_phase": None, "error": None, "family": fam[0],
               "dns_ms": None, "tcp_ms": None, "tls_ms": None, "ttfb_ms": None,
               "body_ms": None, "bytes": 0, "ip": None}
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        is_https = parsed.scheme != "http"
        port = parsed.port or (443 if is_https else 80)
        base_path = parsed.path or "/"
        cb = "nocache=%s-%d" % (nonce, next(seq))
        if parsed.query:
            path = base_path + "?" + parsed.query + "&" + cb
        else:
            path = base_path + "?" + cb
        af = fam[1]
        sock = None
        try:
            t0 = time.perf_counter()
            try:
                infos = socket.getaddrinfo(host, port, af, socket.SOCK_STREAM)
            except Exception as e:
                out["fail_phase"] = "dns"; out["error"] = str(e); return out
            out["dns_ms"] = clean_float((time.perf_counter() - t0) * 1000)
            if not infos:
                out["fail_phase"] = "dns"; out["error"] = "no addresses"; return out
            family_, socktype, proto, _, sockaddr = infos[next(seq) % len(infos)]
            out["ip"] = sockaddr[0]
            t1 = time.perf_counter()
            try:
                sock = socket.socket(family_, socktype, proto)
                sock.settimeout(timeout_s)
                sock.connect(sockaddr)
            except Exception as e:
                out["fail_phase"] = "tcp"; out["error"] = str(e); return out
            out["tcp_ms"] = clean_float((time.perf_counter() - t1) * 1000)
            if is_https:
                t2 = time.perf_counter()
                try:
                    ctx = ssl.create_default_context()
                    try:
                        ctx.options |= ssl.OP_NO_TICKET
                    except Exception:
                        pass
                    sock = ctx.wrap_socket(sock, server_hostname=host)
                except Exception as e:
                    out["fail_phase"] = "tls"; out["error"] = str(e); return out
                out["tls_ms"] = clean_float((time.perf_counter() - t2) * 1000)
            req = (
                "GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: NetDiag/1.0\r\n"
                "Accept: */*\r\nCache-Control: no-cache, no-store, max-age=0\r\n"
                "Pragma: no-cache\r\nConnection: close\r\n\r\n" % (path, host)
            ).encode("ascii", "ignore")
            t3 = time.perf_counter()
            try:
                sock.sendall(req)
                first = sock.recv(4096)
            except Exception as e:
                out["fail_phase"] = "ttfb"; out["error"] = str(e); return out
            out["ttfb_ms"] = clean_float((time.perf_counter() - t3) * 1000)
            if not first:
                out["fail_phase"] = "ttfb"; out["error"] = "empty response"; return out
            total = len(first)
            t4 = time.perf_counter()
            try:
                while True:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    total += len(chunk)
            except Exception as e:
                out["fail_phase"] = "body"; out["error"] = str(e); return out
            out["body_ms"] = clean_float((time.perf_counter() - t4) * 1000)
            out["bytes"] = total
            out["ok"] = True
            return out
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _attempt_urllib(url, fam, prior_err):
        # Plan B: the manual socket/ssl stack hit an unexpected error. Fall back to
        # a cache-busting urllib request for total-time only (no phase breakdown).
        out = {"ok": False, "fail_phase": "unknown", "error": str(prior_err),
               "family": fam[0], "dns_ms": None, "tcp_ms": None, "tls_ms": None,
               "ttfb_ms": None, "body_ms": None, "bytes": 0, "ip": None}
        parsed = urllib.parse.urlsplit(url)
        sep = "&" if parsed.query else "?"
        full = url + sep + "nocache=%s-%d" % (nonce, next(seq))
        try:
            t0 = time.perf_counter()
            req = urllib.request.Request(full, headers={
                "User-Agent": "NetDiag/1.0", "Connection": "close",
                "Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = resp.read()
            out["ttfb_ms"] = clean_float((time.perf_counter() - t0) * 1000)
            out["bytes"] = len(data)
            out["ok"] = True
            out["fail_phase"] = None
        except Exception as e:
            out["error"] = str(e)
        return out

    def _attempt(url, fam):
        try:
            return _attempt_manual(url, fam)
        except Exception as e:
            return _attempt_urllib(url, fam, e)

    def _trial(url, fam):
        info = meta[url]
        a = _attempt(url, fam)
        attempts = 1
        good = a
        while not good["ok"] and attempts <= retries:
            attempts += 1
            good = _attempt(url, fam)
        ok_src = good if good["ok"] else a
        return {
            "url": url, "host": info["host"], "is_ip": info["is_ip"], "family": fam[0],
            "first_ok": a["ok"],
            "first_fail_phase": None if a["ok"] else (a["fail_phase"] or "unknown"),
            "eventual_ok": good["ok"], "attempts": attempts,
            "dns_ms": ok_src["dns_ms"] if good["ok"] else None,
            "tcp_ms": ok_src["tcp_ms"] if good["ok"] else None,
            "tls_ms": ok_src["tls_ms"] if good["ok"] else None,
            "ttfb_ms": ok_src["ttfb_ms"] if good["ok"] else None,
        }

    def _run_pass(conc, rounds, use_duration, label):
        out = []
        r = 0
        deadline = (time.perf_counter() + duration_s) if use_duration else None
        planned = rounds * len(jobs_base)
        with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
            while True:
                if use_duration:
                    if time.perf_counter() >= deadline:
                        break
                elif r >= rounds:
                    break
                futs = [pool.submit(_trial, u, f) for (u, f) in jobs_base]
                for fut in concurrent.futures.as_completed(futs):
                    out.append(fut.result())
                r += 1
                if callback:
                    ok = sum(1 for t in out if t["eventual_ok"])
                    total = planned if not use_duration else max(len(out), 1)
                    callback(label, len(out), total, ok, None, "running")
        return out

    use_duration = duration_s > 0
    high = _run_pass(concurrency, samples, use_duration, label)
    low = []
    if compare_concurrency and concurrency > 1 and high:
        low_rounds = min(samples, 5)
        low = _run_pass(1, low_rounds, False, label + "_low")

    def _phase_breakdown(items):
        pb = {"dns": 0, "tcp": 0, "tls": 0, "ttfb": 0, "body": 0, "unknown": 0}
        for t in items:
            if not t["first_ok"]:
                p = t["first_fail_phase"] or "unknown"
                pb[p] = pb.get(p, 0) + 1
        return pb

    def _group(items):
        n = len(items)
        ff = sum(1 for t in items if not t["first_ok"])
        hf = sum(1 for t in items if not t["eventual_ok"])
        return {
            "samples": n,
            "first_fail_pct": clean_float(100 * ff / n) if n else None,
            "hard_fail_pct": clean_float(100 * hf / n) if n else None,
            "fail_phase_breakdown": _phase_breakdown(items),
        }

    def _phase_p95(items, key):
        vals = [t[key] for t in items if t["eventual_ok"] and t.get(key) is not None]
        return percentile(vals, 95) and clean_float(percentile(vals, 95))

    total = len(high)
    first_fails = [t for t in high if not t["first_ok"]]
    recovered = sum(1 for t in high if (not t["first_ok"]) and t["eventual_ok"])
    hard = sum(1 for t in high if not t["eventual_ok"])

    by_family = {}
    for fam in fam_sel:
        items = [t for t in high if t["family"] == fam[0]]
        if items:
            by_family[fam[0]] = _group(items)

    by_target = []
    for url in targets:
        items = [t for t in high if t["url"] == url]
        if not items:
            continue
        g = _group(items)
        g.update({"url": url, "host": meta[url]["host"], "is_ip": meta[url]["is_ip"],
                  "dns_p95": _phase_p95(items, "dns_ms"), "tcp_p95": _phase_p95(items, "tcp_ms"),
                  "tls_p95": _phase_p95(items, "tls_ms"), "ttfb_p95": _phase_p95(items, "ttfb_ms")})
        by_target.append(g)

    by_concurrency = {"high": {"first_fail_pct": clean_float(100 * len(first_fails) / total) if total else None,
                               "samples": total}}
    if low:
        lff = sum(1 for t in low if not t["first_ok"])
        by_concurrency["low"] = {"first_fail_pct": clean_float(100 * lff / len(low)),
                                 "samples": len(low)}

    result = {
        "available": True, "error": None, "config": config,
        "samples_total": total,
        "first_attempt_fail_pct": clean_float(100 * len(first_fails) / total) if total else None,
        "recovered_on_retry": recovered,
        "hard_failures": hard,
        "fail_phase_breakdown": _phase_breakdown(high),
        "by_family": by_family,
        "by_concurrency": by_concurrency,
        "by_target": by_target,
        "latency": {"dns_p95": _phase_p95(high, "dns_ms"), "tcp_p95": _phase_p95(high, "tcp_ms"),
                    "tls_p95": _phase_p95(high, "tls_ms"), "ttfb_p95": _phase_p95(high, "ttfb_ms")},
    }
    result["verdict"] = verdicts.reliability_verdict(result)
    return result
