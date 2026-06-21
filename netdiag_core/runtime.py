"""Process/OS runtime primitives shared by every probe.

This module is the single, canonical home for the heavily-mocked primitives:
platform flags (IS_LINUX/IS_MACOS/IS_WINDOWS), subprocess (run_cmd), tool
detection (has_tool), timestamps (now_iso), and the activity log. Other modules
import it as ``from netdiag_core import runtime as rt`` and call ``rt.run_cmd``,
``rt.IS_LINUX``, etc. so there is exactly one place to patch in tests.
"""

import collections
import logging
import platform
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

from .constants import APT_PACKAGES
from .stats import clean_float

log = logging.getLogger("netdiag")

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
