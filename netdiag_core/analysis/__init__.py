"""Analysis layer — severity authority.

Re-exports the public analysis surface so callers use a single stable name
(``analysis.diagnose`` / ``analysis.health_score`` / ``analysis.get_reconciliation``)
which is also the canonical patch target in tests.
"""

from netdiag_core.analysis.engine import diagnose
from netdiag_core.analysis.score import health_score
from netdiag_core.analysis.reconcile import reconcile_icmp, get_reconciliation

__all__ = ["diagnose", "health_score", "reconcile_icmp", "get_reconciliation"]
