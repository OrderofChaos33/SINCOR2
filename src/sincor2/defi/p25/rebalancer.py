"""P25 rebalancer: daily drift correction with discipline gates.

A rebalance plan is emitted only when some position's absolute drift
``|current_w - target_w| >= drift_trigger`` (0.025). Delta trades below
``min_capital_usd`` ($25) are dropped as dust. The post-trade portfolio is
re-checked against the cash floor and the risk budget before the plan is
returned; otherwise the plan is rejected with itemized reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping

from . import PARAMS
from .allocator import TargetPortfolio
from .guards import require_cash_floor
from .risk import check_weights


@dataclass(frozen=True)
class DeltaTrade:
    position_id: str
    delta_weight: float   # target - current (signed)
    delta_usd: float      # signed USD at current AUM
    side: str             # "BUY" | "SELL"


@dataclass
class RebalancePlan:
    action: str                      # "REBALANCE" | "NOOP"
    trades: List[DeltaTrade] = field(default_factory=list)
    drift: Dict[str, float] = field(default_factory=dict)
    post_weights: Dict[str, float] = field(default_factory=dict)
    reason: str = ""


def plan_rebalance(
    current_weights: Mapping[str, float],
    target: TargetPortfolio,
    aum_usd: float,
) -> RebalancePlan:
    if aum_usd < 0:
        raise ValueError("aum_usd must be >= 0")

    universe = set(current_weights) | set(target.weights)
    drift = {
        pid: abs(float(current_weights.get(pid, 0.0)) - target.weights.get(pid, 0.0))
        for pid in universe
    }
    worst = max(drift.values()) if drift else 0.0
    if worst < PARAMS["drift_trigger"]:
        return RebalancePlan(
            action="NOOP",
            drift=drift,
            post_weights=dict(current_weights),
            reason=f"max drift {worst:.4f} < trigger {PARAMS['drift_trigger']:.3f}",
        )

    trades: List[DeltaTrade] = []
    post = dict(target.weights)
    for pid in sorted(universe):
        dw = target.weights.get(pid, 0.0) - float(current_weights.get(pid, 0.0))
        dusd = dw * aum_usd
        if abs(dusd) < PARAMS["min_capital_usd"]:
            # Dust: keep the current weight for this position instead of the
            # target weight, so the post-trade portfolio reflects reality.
            post[pid] = float(current_weights.get(pid, 0.0))
            continue
        trades.append(DeltaTrade(
            position_id=pid,
            delta_weight=dw,
            delta_usd=dusd,
            side="BUY" if dw > 0 else "SELL",
        ))

    # Renormalize post weights exactly (dust retention breaks the sum).
    wsum = sum(post.values())
    if wsum > 0:
        post = {pid: w / wsum for pid, w in post.items()}

    if not trades:
        return RebalancePlan(
            action="NOOP",
            drift=drift,
            post_weights=dict(current_weights),
            reason=(f"max drift {worst:.4f} >= trigger but every delta trade "
                    f"is below the ${PARAMS['min_capital_usd']:.0f} dust filter"),
        )

    # Renormalization can shave the cash sleeve: top it back up from the
    # largest non-cash positions before the gates run.
    from .ingestor import CASH_ID

    floor = PARAMS["cash_floor_pct"]
    need = floor - post.get(CASH_ID, 0.0)
    if need > 1e-12:
        for pid in sorted(
            (p for p in post if p != CASH_ID and post[p] > 0),
            key=lambda p: -post[p],
        ):
            take = min(post[pid], need)
            post[pid] -= take
            post[CASH_ID] = post.get(CASH_ID, 0.0) + take
            need -= take
            if need <= 1e-12:
                break

    # Post-trade gates: cash floor + risk budget, itemized on failure.
    require_cash_floor(post)
    chk = check_weights(post)
    if not chk.ok:
        return RebalancePlan(
            action="NOOP",
            drift=drift,
            post_weights=dict(current_weights),
            reason="post-trade portfolio failed risk check: " + "; ".join(chk.violations),
        )

    return RebalancePlan(
        action="REBALANCE",
        trades=trades,
        drift=drift,
        post_weights=post,
        reason=f"max drift {worst:.4f} >= trigger; {len(trades)} trade(s)",
    )


# -- shared price-oracle wiring ------------------------------------------------
# Declares this product's external price needs against the shared oracle
# (sincor2.defi.price_oracle). Reference-backed until live feeds are wired;
# never treated as a live integration.

PRICE_ASSETS = ['ETH/USD', 'BTC/USD', 'USDC/USD']


def price_feed_for(oracle):
    """Bind the shared price oracle to this product's declared assets.

    Returns a ProductPriceFeed; ``feed.price(asset, now)`` raises on any
    oracle failure (fail-closed). Live Chainlink/Pyth feeds are NOT wired —
    production must inject real adapters (see price_oracle module docs).
    """
    from ..price_oracle import wiring_for
    return wiring_for("P25_PORTFOLIO", oracle)
