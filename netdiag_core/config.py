"""Persistent configuration and session-history storage (~/.netdiag)."""

import json
from datetime import datetime
from pathlib import Path

from netdiag_core.constants import DEFAULT_HOSTS, DNS_HOSTS, TCP_TARGETS, RELIABILITY_TARGETS


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
    "reliability_targets": list(RELIABILITY_TARGETS),
    "reliability_samples": 20,
    "reliability_concurrency": 8,
    "reliability_retries": 2,
    "reliability_timeout": 5,
    "reliability_duration": 0,
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
    "reliability_samples": (1, 500),
    "reliability_concurrency": (1, 64),
    "reliability_retries": (0, 5),
    "reliability_timeout": (1, 30),
    "reliability_duration": (0, 600),
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
