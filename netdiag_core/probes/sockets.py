"""TCP socket stats probe: ss/nettop/netstat with /proc/net/tcp stdlib fallback."""

import re
import statistics

from netdiag_core import runtime as rt
from netdiag_core.stats import clean_float


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
    if rt.IS_LINUX:
        if rt.has_tool("ss"):
            rc, out, _ = rt.run_cmd(["ss", "-itp"], timeout=10)
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
    elif rt.IS_MACOS:
        rc, out, _ = rt.run_cmd(["nettop", "-J", "tcp", "-m", "tcp", "-d", "-l", "0"], timeout=15)
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
        rc, out, _ = rt.run_cmd(["netstat", "-s"], timeout=10)
        if rc != 0:
            return {"available": False, "reason": "netstat failed"}
        retrans = 0
        for line in out.split("\n"):
            if "Segments Retransmitted" in line:
                try: retrans = int(re.search(r"(\d+)", line).group(1))
                except: pass
        return {"available": True, "connections": 0,
                "total_retransmits": retrans, "avg_rtt_ms": None, "details": []}
