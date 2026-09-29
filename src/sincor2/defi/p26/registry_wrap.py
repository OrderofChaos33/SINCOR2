"""P26 canonical protocol registry wrapper.

Thin, honest wrapper over catalog.py: the 26 ProtocolSpecs are the single
source of protocol truth. P26 exposes the ranked universe (the other 25 —
P26 itself excluded) plus live_blocked posture per protocol.
"""

from __future__ import annotations

from typing import Dict, List

from ..catalog import PROTOCOL_BY_ID, PROTOCOLS, ProtocolSpec
from . import SELF_ID


def ranked_universe() -> List[ProtocolSpec]:
    """The 25 rankable protocols (P26 excluded: it is the ranker)."""
    return [p for p in PROTOCOLS if p.protocol_id != SELF_ID]


def live_blocked_set() -> List[str]:
    """Protocol ids that may never emit live intents."""
    return sorted(p.protocol_id for p in ranked_universe() if p.live_blocked)


def live_eligible_set() -> List[str]:
    return sorted(p.protocol_id for p in ranked_universe() if not p.live_blocked)


def risk_adjusted_target(spec: ProtocolSpec) -> float:
    """Catalog-only risk-adjusted target: target_apr * (1 - risk_score)."""
    return max(spec.target_apr, 0.0) * (1.0 - spec.risk_score)


def describe(spec: ProtocolSpec) -> Dict:
    return {
        "protocol_id": spec.protocol_id,
        "name": spec.name,
        "category": spec.category,
        "risk_score": spec.risk_score,
        "target_apr": spec.target_apr,
        "fee_bps": spec.fee_bps,
        "live_blocked": spec.live_blocked,
        "gates": list(spec.gates),
    }
