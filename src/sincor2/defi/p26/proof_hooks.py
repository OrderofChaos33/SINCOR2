"""P26 proof hooks: attach real evidence to kill decisions.

Every kill decision is annotated with the proof-ledger entry ids that were
visible for the protocol at decision time (test runs, audits, fork sims).
SINAX attestation is NOT wired: sinax/ does not exist in this repo, so
``sinax_status()`` reports unavailable honestly instead of fabricating an
integration.
"""

from __future__ import annotations

from typing import Dict, List

from ..proof_ledger import ProofLedger
from .killswitch import KillDecision
from .ranker import _sku_for, EVIDENCE_KINDS


def attach_evidence(
    decision: KillDecision, ledger: ProofLedger
) -> KillDecision:
    """Annotate a kill decision with ledger entry ids (newest per kind)."""
    sku = _sku_for(decision.tick.protocol_id)
    refs: List[str] = []
    for kind in EVIDENCE_KINDS:
        entries = ledger.read(sku=sku, kind=kind)
        if entries:
            refs.append(entries[-1]["entry_id"])
    decision.evidence_refs = refs
    return decision


def sinax_status() -> Dict:
    """Honest availability report for the SINAX attestation hook."""
    import os
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
    present = os.path.isdir(os.path.join(repo_root, "sinax"))
    return {
        "available": bool(present),
        "reason": ("sinax/ not present in repo — kill decisions carry "
                   "proof-ledger evidence refs instead")
        if not present else "sinax/ present",
    }


def decision_package(decision: KillDecision) -> Dict:
    """Serializable kill-decision package for the dashboard feed."""
    return {
        "protocol_id": decision.tick.protocol_id,
        "epoch": decision.tick.epoch,
        "realized_roi": decision.tick.realized_roi,
        "action": decision.action,
        "reason": decision.reason,
        "decided_ts": decision.decided_ts,
        "evidence_refs": list(decision.evidence_refs),
        "executed": decision.executed,
        "sinax": sinax_status(),
    }
