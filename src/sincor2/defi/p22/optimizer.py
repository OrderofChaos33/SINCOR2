"""P22 allocation optimizer.

Solves: maximize sum(w_i * net_apr_i) subject to
    sum(w_i) = 1.0 (within 1e-9),
    0 <= w_i <= max_alloc_pct (0.50),
    w_i * capital <= depth_cap * depth_usd  (never >10% of venue liquidity).

Ties within tie_break_bps (5 bps) break toward Morpho venues on Base
(morpho_preferred). Constraints hold by construction; ``verify_plan`` proves
it. Any residual capital that cannot be placed within caps sits in USDC cash.

Rebalance discipline (rotation_decision): rotate only if the proposed plan's
blended net APR beats the current plan by >= 50 bps AND the estimated gas
cost is < 25% of the annualized yield gain AND >= 4 h since the last
rotation. TOA-forced rotations go through the same gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import PARAMS
from .twap import TwapQuote


@dataclass
class AllocationPlan:
    weights: Dict[str, float]   # venue_id -> weight; includes "CASH_USDC"
    blended_net_apr: float
    ts: float

    def to_dict(self) -> Dict:
        return {
            "weights": dict(self.weights),
            "blended_net_apr": self.blended_net_apr,
            "ts": self.ts,
        }


class OptimizerError(Exception):
    """Raised when no valid plan can be constructed."""


CASH_ID = "CASH_USDC"


def _per_venue_cap(quote: TwapQuote, capital_usd: float) -> float:
    if capital_usd <= 0 or quote.depth_usd <= 0:
        return 0.0
    return min(PARAMS["max_alloc_pct"], PARAMS["depth_cap"] * quote.depth_usd / capital_usd)


def _tiebreak_order(quotes: List[TwapQuote]) -> List[TwapQuote]:
    """Sort by net APR desc; within 5 bps, Morpho venues first (stable)."""
    ordered = sorted(quotes, key=lambda q: -q.net_apr_twap)
    tol = PARAMS["tie_break_bps"] / 10_000
    # Bubble Morpho venues ahead of non-Morpho venues within the tolerance.
    changed = True
    while changed:
        changed = False
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            if (b.is_morpho and not a.is_morpho
                    and b.net_apr_twap >= a.net_apr_twap - tol):
                ordered[i], ordered[i + 1] = b, a
                changed = True
    return ordered


def optimize(quotes: List[TwapQuote], capital_usd: float, ts: float) -> AllocationPlan:
    """Greedy fill in tie-broken APR order. Optimal for this linear program:
    with per-venue caps and a single budget constraint, filling the highest
    APR venue first is the exact maximizer."""
    if capital_usd < PARAMS["min_capital_usd"]:
        raise OptimizerError(
            f"capital ${capital_usd:.2f} below min ${PARAMS['min_capital_usd']:.2f}; "
            "no position opened"
        )
    eligible = [q for q in quotes if _per_venue_cap(q, capital_usd) > 0]
    weights: Dict[str, float] = {}
    remaining = 1.0
    blended = 0.0
    for q in _tiebreak_order(eligible):
        if remaining <= 0:
            break
        w = min(_per_venue_cap(q, capital_usd), remaining)
        if w <= 0:
            continue
        weights[q.venue_id] = w
        blended += w * q.net_apr_twap
        remaining -= w
    if remaining > 1e-12:
        weights[CASH_ID] = remaining  # residual sits in USDC cash, honestly at 0%

    # Exact-sum fix on the largest weight (float dust only).
    wsum = sum(weights.values())
    if weights and abs(wsum - 1.0) > 0:
        biggest = max(weights, key=weights.get)
        weights[biggest] += 1.0 - wsum

    plan = AllocationPlan(weights=weights, blended_net_apr=blended, ts=ts)
    verify_plan(plan, quotes, capital_usd)
    return plan


def verify_plan(
    plan: AllocationPlan, quotes: List[TwapQuote], capital_usd: float
) -> None:
    """Prove the invariants. Raises OptimizerError on any violation."""
    wsum = sum(plan.weights.values())
    if abs(wsum - 1.0) > 1e-9:
        raise OptimizerError(f"weights sum to {wsum:.12f}, not 1.0")
    caps = {q.venue_id: _per_venue_cap(q, capital_usd) for q in quotes}
    for venue_id, w in plan.weights.items():
        if venue_id == CASH_ID:
            if w < 0:
                raise OptimizerError("negative cash weight")
            continue
        cap = caps.get(venue_id)
        if cap is None:
            raise OptimizerError(f"weight on unknown venue {venue_id!r}")
        if w < -1e-12 or w > cap + 1e-9:
            raise OptimizerError(
                f"venue {venue_id}: weight {w:.6f} outside [0, {cap:.6f}]"
            )
        if w * capital_usd > PARAMS["depth_cap"] * next(
            q.depth_usd for q in quotes if q.venue_id == venue_id
        ) + 1e-6:
            raise OptimizerError(f"venue {venue_id}: depth cap breached")


@dataclass
class RotationDecision:
    rotate: bool
    reason: str
    delta_apr_bps: float = 0.0
    gas_ratio: float = 0.0


def rotation_decision(
    current: AllocationPlan,
    proposed: AllocationPlan,
    last_rotation_ts: float,
    now: float,
    estimated_gas_usd: float,
    capital_usd: float,
    forced: bool = False,
) -> RotationDecision:
    """Rebalance discipline. ``forced`` (TOA) still passes through every gate."""
    delta_apr = proposed.blended_net_apr - current.blended_net_apr
    delta_bps = delta_apr * 10_000
    if delta_bps < PARAMS["rebalance_delta_bps"]:
        return RotationDecision(
            False,
            f"ΔAPR {delta_bps:.1f} bps < {PARAMS['rebalance_delta_bps']} bps threshold",
            delta_bps,
        )
    annualized_gain = delta_apr * capital_usd
    gas_ratio = (estimated_gas_usd / annualized_gain) if annualized_gain > 0 else float("inf")
    if gas_ratio >= PARAMS["gas_vs_gain_ratio"]:
        return RotationDecision(
            False,
            f"gas ${estimated_gas_usd:.2f} is {gas_ratio:.1%} of annualized gain "
            f"${annualized_gain:.2f} (>= 25%)",
            delta_bps, gas_ratio,
        )
    if now - last_rotation_ts < PARAMS["rebalance_cooldown_s"]:
        return RotationDecision(
            False,
            f"cooldown: {now - last_rotation_ts:.0f}s since last rotation "
            f"(< {PARAMS['rebalance_cooldown_s']}s)",
            delta_bps, gas_ratio,
        )
    return RotationDecision(
        True,
        f"rotate{' (TOA-forced)' if forced else ''}: ΔAPR {delta_bps:.1f} bps, "
        f"gas ratio {gas_ratio:.1%}, cooldown satisfied",
        delta_bps, gas_ratio,
    )
