"""diagnose() — the single source of truth for severity.

5-layer rule engine (physical -> wifi -> gateway -> ISP -> internet). The
per-layer logic lives in layers_local / layers_path / layers_app; this module
composes them in order and adds the interrupted/summary meta findings. All
consumers (console, report, ISP report, web UI) render this output verbatim and
NEVER recompute severity themselves.
"""

from netdiag_core.analysis.layers_local import _diag_interface, _diag_wifi, _diag_gateway
from netdiag_core.analysis.layers_path import _diag_loss, _diag_mtr, _diag_bufferbloat
from netdiag_core.analysis.layers_app import (
    _diag_dns, _diag_tcp, _diag_iperf, _diag_speed, _diag_download,
    _diag_connection, _diag_reliability, _diag_wellknown,
)

# Run in the exact order of the original single-function diagnose(): interface +
# ethtool, wifi, gateway, loss reconciliation, mtr, bufferbloat, dns, tcp, iperf,
# speedtest, download, connection (http/mtu), reliability, well-known reproducer.
_LAYERS = (
    _diag_interface, _diag_wifi, _diag_gateway,
    _diag_loss, _diag_mtr, _diag_bufferbloat,
    _diag_dns, _diag_tcp, _diag_iperf, _diag_speed, _diag_download,
    _diag_connection, _diag_reliability, _diag_wellknown,
)


def diagnose(results):
    diagnoses = []
    if results.get("interrupted"):
        diagnoses.append({"layer": "meta", "severity": "warning",
                          "title": "Test was interrupted",
                          "detail": "Diagnosis is based on partial results only.",
                          "fix": "Re-run the diagnostic with a longer duration."})

    for layer_fn in _LAYERS:
        diagnoses.extend(layer_fn(results))

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
