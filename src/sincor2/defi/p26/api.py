"""P26 dashboard API: read-only views over rankings and kill decisions."""

from __future__ import annotations

from typing import Any, Dict, List

from ..proof_ledger import ProofLedger
from .killswitch import KillDecision, KillSwitch
from .proof_hooks import decision_package
from .ranker import RankEntry, Ranker
from .registry_wrap import live_blocked_set, live_eligible_set
from .telemetry import Telemetry
from .toa_loop import feedback


class DefiOSApi:
    def __init__(
        self,
        ledger: ProofLedger,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.ranker = Ranker(ledger, telemetry or Telemetry())
        self.killswitch = KillSwitch()
        self.ledger = ledger

    def rankings(self) -> List[Dict[str, Any]]:
        return [
            {
                "protocol_id": e.protocol_id,
                "score": round(e.score, 6),
                "confidence": e.confidence,
                "components": {k: round(v, 6) for k, v in e.components.items()},
                "evidence_kinds": e.evidence_kinds,
                "has_telemetry": e.has_telemetry,
                "gate_stage": e.detail.get("gate_stage", {}),
            }
            for e in self.ranker.rank()
        ]

    def kill_decisions(self) -> List[Dict[str, Any]]:
        from .proof_hooks import attach_evidence
        return [
            decision_package(attach_evidence(d, self.ledger))
            for d in self.killswitch.decisions
        ]

    def posture(self) -> Dict[str, Any]:
        return {
            "ranked_protocols": 25,
            "live_blocked": live_blocked_set(),
            "live_eligible": live_eligible_set(),
            "kills": len(self.killswitch.kills()),
        }

    def next_cycle_multipliers(self) -> Dict[str, float]:
        return feedback(self.ranker.rank(), self.killswitch.decisions)
