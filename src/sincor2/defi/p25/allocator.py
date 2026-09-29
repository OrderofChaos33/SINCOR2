"""P25 portfolio allocator: signal weights + risk budget -> target portfolio.

Strategy: scale down the highest-risk positions first until the blended risk
budget holds, parking freed weight in the USDC cash sleeve (risk 0), then
enforce the 0.40 position cap and the 5% cash floor. The loop is bounded
(allocator_max_iterations); if it cannot converge it raises
UnconvergedAllocation with the itemized risk-check violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping

from . import PARAMS
from .guards import require_cash_floor
from .ingestor import CASH_ID
from .risk import check_weights, risk_of


class UnconvergedAllocation(Exception):
    """Allocator exhausted its iteration budget without a valid portfolio."""

    def __init__(self, violations: List[str]):
        super().__init__("; ".join(violations))
        self.violations = violations


class InvalidWeights(Exception):
    """Input weight set was rejected with itemized causes."""


@dataclass
class TargetPortfolio:
    weights: Dict[str, float]  # includes CASH_USDC; sums to 1.0
    cash_pct: float
    as_of: float
    feed_hash: str
    blended_risk: float
    iterations: int

    def to_dict(self) -> Dict:
        return {
            "weights": dict(self.weights),
            "cash_pct": self.cash_pct,
            "as_of": self.as_of,
            "feed_hash": self.feed_hash,
            "blended_risk": self.blended_risk,
            "iterations": self.iterations,
        }


def _move_to_cash(weights: Dict[str, float], pid: str, amount: float) -> None:
    weights[pid] = weights.get(pid, 0.0) - amount
    if weights[pid] <= 1e-12:
        weights.pop(pid, None)
    weights[CASH_ID] = weights.get(CASH_ID, 0.0) + amount


def allocate(
    signal_weights: Mapping[str, float],
    as_of: float,
    feed_hash: str,
) -> TargetPortfolio:
    weights: Dict[str, float] = {
        pid: float(w) for pid, w in signal_weights.items() if float(w) > 0
    }
    if CASH_ID not in weights:
        weights[CASH_ID] = 0.0

    # Reject unknown / ineligible inputs up front with itemized causes.
    pre = check_weights(weights)
    hard_blockers = [
        v for v in pre.violations
        if v.startswith("unknown position") or v.startswith("ineligible high-risk")
        or v.startswith("negative weight")
    ]
    if hard_blockers:
        raise InvalidWeights("; ".join(hard_blockers))

    max_iter = PARAMS["allocator_max_iterations"]
    iterations = 0
    for iterations in range(1, max_iter + 1):
        chk = check_weights(weights)
        if chk.ok:
            break
        # 1) blended-risk breaches: drain the riskiest position into cash.
        if chk.blended_risk > PARAMS["blended_risk_cap"]:
            risky = [
                (pid, risk_of(pid)) for pid in weights
                if risk_of(pid) > 0 and weights[pid] > 0
            ]
            if not risky:
                break  # nothing left to drain; check below will fail loudly
            pid, r = max(risky, key=lambda t: t[1])
            excess = chk.blended_risk - PARAMS["blended_risk_cap"]
            drain = min(weights[pid], excess / r)
            _move_to_cash(weights, pid, drain)
            continue
        # 2) position-cap breaches: overflow the capped position into cash.
        # CASH_USDC is exempt from the cap (risk-free parking), mirroring
        # risk.check_weights: capping cash would no-op cash->cash forever.
        over = [
            pid for pid in weights
            if pid != CASH_ID
            and weights[pid] > PARAMS["position_cap"] + 1e-12
        ]
        if over:
            pid = max(over, key=lambda p: weights[p])
            _move_to_cash(weights, pid, weights[pid] - PARAMS["position_cap"])
            continue
        # 3) weight-sum drift: renormalize exactly.
        wsum = sum(weights.values())
        if abs(wsum - 1.0) > 1e-9 and wsum > 0:
            weights = {pid: w / wsum for pid, w in weights.items()}
            continue
        break

    # 4) cash floor: top up from the largest non-cash positions.
    cash = weights.get(CASH_ID, 0.0)
    floor = PARAMS["cash_floor_pct"]
    if cash < floor:
        need = floor - cash
        donors = sorted(
            (pid for pid in weights if pid != CASH_ID and weights[pid] > 0),
            key=lambda p: -weights[p],
        )
        for pid in donors:
            take = min(weights[pid], need)
            _move_to_cash(weights, pid, take)
            need -= take
            if need <= 1e-12:
                break

    final = check_weights(weights)
    if not final.ok:
        raise UnconvergedAllocation(final.violations)
    require_cash_floor(weights)

    # Exact sum fix on the largest weight (float dust only at this point).
    wsum = sum(weights.values())
    if abs(wsum - 1.0) > 0:
        biggest = max(weights, key=weights.get)
        weights[biggest] += 1.0 - wsum

    return TargetPortfolio(
        weights=dict(sorted(weights.items(), key=lambda kv: -kv[1])),
        cash_pct=weights.get(CASH_ID, 0.0),
        as_of=as_of,
        feed_hash=feed_hash,
        blended_risk=final.blended_risk,
        iterations=iterations,
    )
