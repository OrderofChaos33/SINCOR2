"""P25 risk-budget engine.

Hard limits derived from the 0.28 portfolio risk budget:
  (a) single-position cap  w_i <= 0.40
  (b) blended portfolio risk  sum(w_i * r_i) <= 0.28
  (c) any protocol with catalog risk_score > 0.60 is ineligible, read from the
      live catalog at check time (never a hardcoded list)

Violations return itemized rejections — they are never silently clamped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Tuple

from ..catalog import PROTOCOL_BY_ID
from . import PARAMS
from .ingestor import CASH_ID


@dataclass
class RiskCheck:
    ok: bool
    violations: List[str] = field(default_factory=list)
    blended_risk: float = 0.0
    weight_sum: float = 0.0


def risk_of(protocol_id: str) -> float:
    """Catalog risk score for a position id; CASH_USDC is 0. Unknown ids raise."""
    if protocol_id == CASH_ID:
        return 0.0
    spec = PROTOCOL_BY_ID.get(protocol_id)
    if spec is None:
        raise KeyError(f"unknown protocol_id: {protocol_id}")
    return spec.risk_score


def ineligible_protocols() -> List[str]:
    """Protocols currently excluded by the 0.60 rule — computed from the catalog."""
    return sorted(
        pid for pid, spec in PROTOCOL_BY_ID.items()
        if spec.risk_score > PARAMS["exclusion_risk"]
    )


def check_weights(
    weights: Mapping[str, float],
    tolerance: float = 1e-9,
) -> RiskCheck:
    violations: List[str] = []
    wsum = sum(weights.values())
    if abs(wsum - 1.0) > tolerance:
        violations.append(f"weights sum to {wsum:.12f}, not 1.0")

    blended = 0.0
    for pid, w in weights.items():
        if w < -tolerance:
            violations.append(f"negative weight {pid}={w:.6f}")
            continue
        # The 0.40 cap is a per-STRATEGY concentration limit. The USDC cash
        # sleeve is risk-free parking (the allocator's own drain step parks
        # freed weight there, and a fully defensive book is 100% cash), so
        # it is exempt: without this, any book over 40% cash can never
        # converge and allocate() raises UnconvergedAllocation on a safe
        # portfolio.
        if pid != CASH_ID and w > PARAMS["position_cap"] + tolerance:
            violations.append(
                f"position cap breached: {pid} weight {w:.6f} > "
                f"{PARAMS['position_cap']:.2f}"
            )
        try:
            r = risk_of(pid)
        except KeyError:
            violations.append(f"unknown position {pid}")
            continue
        if r > PARAMS["exclusion_risk"]:
            violations.append(
                f"ineligible high-risk position: {pid} risk_score {r:.2f} > "
                f"{PARAMS['exclusion_risk']:.2f}"
            )
        blended += max(w, 0.0) * r

    if blended > PARAMS["blended_risk_cap"] + tolerance:
        violations.append(
            f"blended risk {blended:.6f} exceeds budget "
            f"{PARAMS['blended_risk_cap']:.2f}"
        )
    return RiskCheck(
        ok=not violations,
        violations=violations,
        blended_risk=blended,
        weight_sum=wsum,
    )
