"""Gateway / interface / wireless-interface detection and interface stats.

Each probe has Linux/macOS/Windows branches plus a stdlib Plan B (procfs/sysfs)
so it still works when `ip`/`ifconfig`/`iw` are unavailable.
"""

import re
from pathlib import Path

from netdiag_core import runtime as rt


def _parse_proc_net_route():
    try:
        with open("/proc/net/route") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 3 and parts[1] == "00000000" and parts[2] != "00000000":
                    gw_hex = parts[2]
                    gw = ".".join(str(int(gw_hex[i:i+2], 16)) for i in (6, 4, 2, 0))
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
    if rt.IS_LINUX:
        rc, out, _ = rt.run_cmd(["ip", "-4", "route", "show", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"default via ([0-9.]+)", out)
            if m:
                return m.group(1)
        gw = _parse_proc_net_route()
        if gw:
            return gw
    elif rt.IS_MACOS:
        rc, out, _ = rt.run_cmd(["route", "-n", "get", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"gateway: ([0-9.]+)", out)
            return m.group(1) if m else None
    else:
        rc, out, _ = rt.run_cmd(["netstat", "-rn"], timeout=10)
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
    if rt.IS_LINUX:
        rc, out, _ = rt.run_cmd(["ip", "route", "show", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"\bdev\s+(\S+)", out)
            if m:
                return m.group(1)
        iface = _parse_proc_net_route_iface()
        if iface:
            return iface
    elif rt.IS_MACOS:
        rc, out, _ = rt.run_cmd(["route", "-n", "get", "default"], timeout=10)
        if rc == 0:
            m = re.search(r"interface: (\S+)", out)
            return m.group(1) if m else None
    else:
        return None
    return None


# Interface-name markers for VPN/tunnel devices. Deliberately excludes 'ppp'
# (DSL PPPoE is a real WAN uplink, not a VPN) to avoid mislabeling DSL lines.
_VPN_IFACE_RE = re.compile(
    r"(tun|tap|wg|utun|wireguard|wintun|ipsec|nordlynx|proton|tailscale|mullvad|gpd|vpn)",
    re.I)


def _looks_like_tunnel(iface):
    return bool(iface) and bool(_VPN_IFACE_RE.search(iface.strip()))


def _sysfs_first_up_tunnel():
    # Plan B (Linux, stdlib): a tunnel-named interface that is currently up.
    # Tunnels frequently report operstate "unknown" while carrying, so accept it.
    try:
        for entry in sorted(Path("/sys/class/net").iterdir()):
            if not _looks_like_tunnel(entry.name):
                continue
            try:
                state = (entry / "operstate").read_text().strip()
            except (OSError, IOError):
                state = ""
            if state in ("up", "unknown"):
                return entry.name
    except (OSError, IOError):
        pass
    return None


def detect_vpn(probe_host="1.1.1.1"):
    # Does public-internet traffic egress through a VPN/tunnel interface? This
    # matters for diagnosis: under a VPN, traceroute hop 1 is the VPN server (not
    # the local modem), and the tunnel commonly rate-limits ICMP, so MTR "loss"
    # through it is NOT line loss. Returns {active, interface, kind}.
    iface = None
    if rt.IS_LINUX:
        rc, out, _ = rt.run_cmd(["ip", "route", "get", probe_host], timeout=10)
        if rc == 0:
            m = re.search(r"\bdev\s+(\S+)", out)
            if m:
                iface = m.group(1)
        # Only fall back to a sysfs scan when `ip` gave us nothing — trusting a
        # real egress iface avoids a false positive on split-tunnel setups where
        # this host bypasses an otherwise-up tunnel.
        if iface is None:
            iface = _sysfs_first_up_tunnel()
    elif rt.IS_MACOS:
        rc, out, _ = rt.run_cmd(["route", "-n", "get", probe_host], timeout=10)
        if rc == 0:
            m = re.search(r"interface:\s*(\S+)", out)
            if m:
                iface = m.group(1)
    else:
        rc, out, _ = rt.run_cmd(
            ["powershell", "-NoProfile", "-Command",
             "(Find-NetRoute -RemoteIPAddress %s | Select-Object -First 1).InterfaceAlias" % probe_host],
            timeout=10)
        if rc == 0 and out.strip():
            iface = out.strip().splitlines()[-1].strip()
    active = _looks_like_tunnel(iface)
    return {"active": active, "interface": iface if active else None,
            "kind": "vpn" if active else None}


def detect_wireless_interface():
    if rt.IS_LINUX:
        if rt.has_tool("iw"):
            rc, out, _ = rt.run_cmd(["iw", "dev"], timeout=10)
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
    elif rt.IS_MACOS:
        return get_default_interface()
    else:
        rc, out, _ = rt.run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=10)
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
    if rt.IS_LINUX:
        rc, out, _ = rt.run_cmd(["ip", "-s", "link", "show", "dev", iface], timeout=10)
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
    elif rt.IS_MACOS:
        rc, out, _ = rt.run_cmd(["ifconfig", iface], timeout=10)
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
        rc, out, _ = rt.run_cmd(["netstat", "-e"], timeout=10)
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


def ethtool_info(iface):
    if not rt.IS_LINUX or not iface:
        return {"available": False, "reason": "Linux-only" if rt.IS_LINUX else "No interface"}
    if not rt.has_tool("ethtool"):
        return {"available": False, "reason": "ethtool not installed"}
    rc, out, _ = rt.run_cmd(["ethtool", iface], timeout=10)
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
