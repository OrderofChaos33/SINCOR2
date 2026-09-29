"""P26 ROI ranker: evidence-driven ranking of the 25 protocols.

Score components (all from real repo evidence):
  - realized (w=0.50): latest realized ROI from telemetry, else latest
    realized fee rate scaled to an APR proxy. Absent -> contributes 0 and the
    protocol is flagged unproven.
  - catalog (w=0.30): risk-adjusted catalog target = target_apr*(1-risk).
  - evidence (w=0.20): proof-ledger confidence — distinct evidence kinds
    present (test_run, invariant_test, fork_sim, audit_report) scaled to
    min_evidence_for_live_rank kinds, plus a bonus for a clean audit report.

Confidence labels:
  - "proven":    >= min_evidence_for_live_rank distinct kinds + clean audit
  - "evidenced": >= 1 passing test_run in the ledger
  - "catalog-only": no ledger evidence at all
  - "unproven":  no ledger evidence AND no telemetry (explicit, never hidden)

The ranker never invents a number: every component cites its source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..proof_ledger import (
    KIND_AUDIT_REPORT,
    KIND_FORK_SIM,
    KIND_INVARIANT_TEST,
    KIND_TEST_RUN,
    ProofLedger,
)
from . import PARAMS
from .registry_wrap import describe, ranked_universe, risk_adjusted_target
from .telemetry import Telemetry

EVIDENCE_KINDS = (KIND_TEST_RUN, KIND_INVARIANT_TEST, KIND_FORK_SIM, KIND_AUDIT_REPORT)


@dataclass
class RankEntry:
    protocol_id: str
    score: float
    confidence: str          # proven | evidenced | catalog-only | unproven
    components: Dict[str, float]
    evidence_kinds: List[str]
    has_telemetry: bool
    detail: Dict


def _sku_for(protocol_id: str) -> str:
    from ..products import mint_sku
    return mint_sku(protocol_id)


def gate_stage_evidence(
    protocol_id: str,
    ledger: ProofLedger,
    root: str,
    registry: Optional[List[Dict]] = None,
) -> Dict:
    """Run the actual lifecycle gate for this protocol's next stage.

    This is the ranker's direct consumption of gate-stage evidence: it calls
    ``gates.evaluate()`` — the same machine that enforces lifecycle
    promotions — so a protocol whose next-stage gate currently fails carries
    that refusal (failed check names + machine-readable reasons) inside its
    ranking row. Nothing is summarized away: ``reasons`` and per-check
    ``evidence`` pointers are the real gate output.
    """
    from .. import gates
    from ..products import build_registry

    reg = registry if registry is not None else build_registry()
    product = next((p for p in reg if p.get("protocol_id") == protocol_id), None)
    if product is None:
        return {"stage": None, "next_stage": None, "gate_ok": False,
                "failed_checks": [], "reasons": ["unknown product"],
                "evidence": {}}
    stage = product.get("stage", "spec")
    to_stage = gates.next_stage(stage)
    if to_stage is None:  # final stage: no gate left to fail
        return {"stage": stage, "next_stage": None, "gate_ok": True,
                "failed_checks": [], "reasons": [], "evidence": {}}
    result = gates.evaluate(product, to_stage, ledger, root)
    return {
        "stage": stage,
        "next_stage": to_stage,
        "gate_ok": result.ok,
        "failed_checks": [r.check for r in result.reasons if not r.ok],
        "reasons": [r.reason for r in result.reasons if not r.ok],
        "evidence": {r.check: r.evidence for r in result.reasons},
    }


def evidence_confidence(
    ledger: ProofLedger, protocol_id: str
) -> tuple[float, str, List[str], Dict]:
    """(confidence_score, label, kinds_present, detail) from the ledger."""
    sku = _sku_for(protocol_id)
    kinds = sorted({e["kind"] for e in ledger.read(sku=sku)
                    if e["kind"] in EVIDENCE_KINDS})
    passing_tests = [
        e for e in ledger.read(sku=sku, kind=KIND_TEST_RUN)
        if e.get("details", {}).get("failed", 0) == 0
        and e.get("details", {}).get("passed", 0) > 0
    ]
    audits = ledger.read(sku=sku, kind=KIND_AUDIT_REPORT)
    clean_audit = any(
        int(a.get("details", {}).get("open_critical", 1)) == 0 for a in audits
    )
    distinct = len(kinds)
    score = min(1.0, distinct / PARAMS["min_evidence_for_live_rank"])
    if clean_audit:
        score = min(1.0, score + 0.25)
    if distinct >= PARAMS["min_evidence_for_live_rank"] and clean_audit:
        label = "proven"
    elif passing_tests:
        label = "evidenced"
    else:
        label = "catalog-only"
    return score, label, kinds, {
        "passing_test_runs": len(passing_tests),
        "clean_audit": clean_audit,
    }


class Ranker:
    def __init__(self, ledger: ProofLedger, telemetry: Optional[Telemetry] = None,
                 root: Optional[str] = None) -> None:
        self.ledger = ledger
        self.telemetry = telemetry or Telemetry()
        self._root = root

    def _gate_root(self) -> str:
        if self._root is not None:
            return self._root
        from ..products import REPO_ROOT
        return REPO_ROOT

    def rank(self) -> List[RankEntry]:
        entries: List[RankEntry] = []
        root = self._gate_root()
        for spec in ranked_universe():
            conf, label, kinds, detail = evidence_confidence(self.ledger, spec.protocol_id)
            gate = gate_stage_evidence(spec.protocol_id, self.ledger, root)

            roi = self.telemetry.latest_roi(spec.protocol_id)
            fee_rate = self.telemetry.latest_fee_rate(spec.protocol_id)
            has_tel = self.telemetry.has_realized_data(spec.protocol_id)
            realized = 0.0
            realized_source = "none"
            if roi is not None:
                realized, realized_source = roi, "telemetry_roi"
            elif fee_rate is not None:
                realized, realized_source = fee_rate, "telemetry_fee_rate"

            catalog_comp = risk_adjusted_target(spec)
            score = (
                PARAMS["w_realized"] * realized
                + PARAMS["w_catalog"] * catalog_comp
                + PARAMS["w_evidence"] * conf
            )
            if label == "catalog-only" and not has_tel:
                label = "unproven"
            entries.append(RankEntry(
                protocol_id=spec.protocol_id,
                score=score,
                confidence=label,
                components={
                    "realized": realized,
                    "catalog_risk_adjusted": catalog_comp,
                    "evidence_confidence": conf,
                },
                evidence_kinds=kinds,
                has_telemetry=has_tel,
                detail={
                    "realized_source": realized_source,
                    "spec": describe(spec),
                    "gate_stage": gate,
                    **detail,
                },
            ))
        entries.sort(key=lambda e: (-e.score, e.protocol_id))
        return entries
