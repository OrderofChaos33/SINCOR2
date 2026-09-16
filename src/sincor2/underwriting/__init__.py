"""Agent Underwriting Runtime.

Existing package: mandate tap + ledger_sim (`runtime`, `api`).
Additive: deterministic score engine + Flask /v1 surface (`engine`, `blueprint`).
"""

from sincor2.underwriting.engine import UnderwritingEngine, score_mandate
from sincor2.underwriting.store import UnderwriteStore

try:
    from sincor2.underwriting.runtime import UnderwriteRuntime, boot
except Exception:  # pragma: no cover
    UnderwriteRuntime = None  # type: ignore
    boot = None  # type: ignore

__all__ = [
    "UnderwritingEngine",
    "UnderwriteStore",
    "score_mandate",
    "UnderwriteRuntime",
    "boot",
]
