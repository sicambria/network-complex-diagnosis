"""WiFi info probe: iw/airport/netsh with /proc/net/wireless stdlib fallback."""

import re

from netdiag_core import runtime as rt


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
    if rt.IS_LINUX:
        if rt.has_tool("iw"):
            rc_link, out_link, _ = rt.run_cmd(["iw", "dev", iface, "link"], timeout=10)
            rc_survey, out_survey, _ = rt.run_cmd(["iw", "dev", iface, "survey", "dump"], timeout=10)
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
    elif rt.IS_MACOS:
        rc, out, _ = rt.run_cmd(["/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport", "-I"], timeout=10)
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
        rc, out, _ = rt.run_cmd(["netsh", "wlan", "show", "interfaces"], timeout=10)
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
