"""
SINCOR DeFi P16 — Best-Execution DEX Aggregator (reference build).

Python reference simulation of the route optimizer behind SKU
``SINCOR-DEFI-P16-DEXAGG``. Models the off-chain 1inch-Pathfinder-shaped
split solver and the RouterGateway execution semantics off-chain:

- :class:`MockVenueAdapter` — deterministic constant-product venue quote
  (Uniswap V3 / V4 / Aerodrome shapes via fee tier + gas profile).
  Venue IDs are strings only — no real venue addresses are invented here.
- :class:`SplitOptimizer` — maximizes NET output
  (``sum(out) - gas - fees``), never gross. Allocations are integer bps
  summing to exactly 10_000; at most 3 splits; legs below the $10 dust
  threshold are merged or dropped; a multi-split is only adopted when its
  marginal net gain exceeds its marginal gas.
- :func:`apply_forecast` — TOA slippage-forecast reweighting: a degraded
  venue forecast measurably shifts route weights.
- :class:`VenueRegistry` — timelocked (24h) venue allowlist; execution
  through a non-allowlisted adapter raises.
- :func:`check_min_out` / :func:`simulate_execution` — committed minOut
  enforcement and non-bricking multi-leg settlement (a reverting venue
  returns the leg's input; the route never bricks).

Safety rules (hard):
- Default mode is DRY_RUN. Plans are emitted, never executed; nothing
  here touches a chain, a pool, or funds.
- Live execution requires EXECUTE_LIVE=1 AND the auditor gate; the
  gateway-side minOut re-validation is modeled as a hard check here.
- 6 bps protocol fee on input is accounted to the canonical treasury.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

FEE_BPS = 6                        # 0.06% of input, every executed swap
DEFAULT_SLIPPAGE_BPS = 50          # minOut = quoted * 0.995
MAX_SPLITS = 3                     # at most 3 venue legs per swap
DUST_USD = 10.0                    # minimum leg notional
MIN_CAPITAL_USD = 10.0
MAX_ALLOC_PCT = 0.50               # per-tick router capital cap
TIMELOCK_SECONDS = 24 * 3600       # venue add/remove timelock
DRY_RUN = os.getenv("P16_DRY_RUN", "1").strip() != "0"
EXECUTE_LIVE = os.getenv("EXECUTE_LIVE", "0").strip() == "1"
LIVE_AUDITOR_GATE = False          # flip only with signed auditor config

# Genesis allowlist (venue IDs only — never on-chain addresses here).
VENUE_UNIV3 = "uniswap_v3"
VENUE_UNIV4 = "uniswap_v4"
VENUE_AERODROME = "aerodrome"
GENESIS_VENUES = (VENUE_UNIV3, VENUE_UNIV4, VENUE_AERODROME)


class AggregatorError(RuntimeError):
    """Base error for aggregator rule violations."""


class MinOutViolated(AggregatorError):
    """Realized output fell below the committed minOut."""


class VenueNotAllowlisted(AggregatorError):
    """Route references a venue outside the timelocked allowlist."""


class LiveExecutionBlocked(AggregatorError):
    """Live execution attempted before the auditor gate flips."""


# -- venue adapters ----------------------------------------------------------
@dataclass(frozen=True)
class VenueQuote:
    venue_id: str
    amount_in_wei: int
    amount_out_wei: int
    gas_units: int
    fee_bps: int


class MockVenueAdapter:
    """Deterministic constant-product quoter for one mock venue.

    ``quote()`` is a pure function of (amount_in, reserves): no network,
    no state. Different fee tiers / reserve depths reproduce the V3
    (5/30/100 bps tiers), V4 hook-aware, and Aerodrome volatile/stable
    shapes well enough for optimizer validation.
    """

    def __init__(
        self,
        venue_id: str,
        reserve_in_wei: int,
        reserve_out_wei: int,
        fee_bps: int,
        gas_units: int,
    ):
        if reserve_in_wei <= 0 or reserve_out_wei <= 0:
            raise ValueError("reserves must be positive")
        self.venue_id = venue_id
        self.reserve_in_wei = reserve_in_wei
        self.reserve_out_wei = reserve_out_wei
        self.fee_bps = fee_bps
        self.gas_units = gas_units

    def quote(self, amount_in_wei: int) -> VenueQuote:
        if amount_in_wei < 0:
            raise ValueError("amount_in cannot be negative")
        if amount_in_wei == 0:
            return VenueQuote(self.venue_id, 0, 0, self.gas_units, self.fee_bps)
        amount_in_after_fee = amount_in_wei * (10_000 - self.fee_bps) // 10_000
        # x * y = k, integer-exact.
        amount_out = (
            amount_in_after_fee
            * self.reserve_out_wei
            // (self.reserve_in_wei + amount_in_after_fee)
        )
        return VenueQuote(
            venue_id=self.venue_id,
            amount_in_wei=amount_in_wei,
            amount_out_wei=amount_out,
            gas_units=self.gas_units,
            fee_bps=self.fee_bps,
        )


# -- quote request / route plan ----------------------------------------------
@dataclass(frozen=True)
class QuoteRequest:
    token_in: str
    token_out: str
    amount_in_wei: int
    amount_in_usd: float        # caller-supplied notional (dust threshold)
    gas_cost_out_wei_per_leg: int  # caller-supplied (TOA/gas feed)
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS


@dataclass
class RouteLeg:
    venue_id: str
    in_bps: int                # integer bps of amount_in; legs sum to 10_000
    amount_in_wei: int
    quoted_out_wei: int


@dataclass
class RoutePlan:
    """Dry-run route. executed is always False from this module."""

    timestamp: float
    mode: str                  # "dry_run" | "live_intent"
    token_in: str
    token_out: str
    amount_in_wei: int
    legs: List[RouteLeg]
    quoted_out_wei: int
    min_out_wei: int
    fee_bps: int
    fee_wei: int
    treasury: str
    net_out_wei: int           # quoted_out minus gas, in token_out wei
    warnings: List[str] = field(default_factory=list)
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["legs"] = [asdict(leg) for leg in self.legs]
        return d


# -- split optimizer -----------------------------------------------------------
class SplitOptimizer:
    """Gas-penalized split solver. Objective is NET output, never gross.

    Search: best single-venue net, then deterministic grid search over
    2- and 3-venue allocation splits (integer bps). A multi-split wins
    only if its net (gross out minus per-leg gas) beats the best single
    venue — so marginal gas is paid only when marginal gain covers it.
    """

    def __init__(self, venues: List[MockVenueAdapter], registry: "VenueRegistry"):
        self.venues = {v.venue_id: v for v in venues}
        self.registry = registry

    def _net_out(
        self, request: QuoteRequest, alloc: List[Tuple[str, int]]
    ) -> Tuple[int, List[RouteLeg], List[str]]:
        """Net output in token_out wei for an allocation. Pure."""
        warnings: List[str] = []
        legs: List[RouteLeg] = []
        gross = 0
        for venue_id, bps in alloc:
            adapter = self.venues[venue_id]
            leg_in = request.amount_in_wei * bps // 10_000
            leg_usd = request.amount_in_usd * bps / 10_000
            if leg_usd < DUST_USD and len(alloc) > 1:
                # Dust leg: merged into the best leg by the caller.
                warnings.append(f"dust leg dropped: {venue_id} ${leg_usd:.2f}")
                continue
            q = adapter.quote(leg_in)
            legs.append(
                RouteLeg(
                    venue_id=venue_id,
                    in_bps=bps,
                    amount_in_wei=leg_in,
                    quoted_out_wei=q.amount_out_wei,
                )
            )
            gross += q.amount_out_wei
        # Re-normalize bps after dust drops so legs always sum to 10_000.
        if legs and sum(l.in_bps for l in legs) != 10_000:
            self._renormalize(request, legs)
            gross = sum(
                self.venues[l.venue_id].quote(l.amount_in_wei).amount_out_wei
                for l in legs
            )
            for l in legs:
                l.quoted_out_wei = self.venues[l.venue_id].quote(
                    l.amount_in_wei
                ).amount_out_wei
        gas = len(legs) * request.gas_cost_out_wei_per_leg
        return gross - gas, legs, warnings

    @staticmethod
    def _renormalize(request: QuoteRequest, legs: List[RouteLeg]) -> None:
        total = sum(l.in_bps for l in legs)
        if total <= 0:
            return
        # Largest-remainder to exactly 10_000.
        raw = [l.in_bps * 10_000 / total for l in legs]
        base = [int(r) for r in raw]
        remainder = 10_000 - sum(base)
        order = sorted(range(len(legs)), key=lambda i: raw[i] - base[i], reverse=True)
        for i in order[:remainder]:
            base[i] += 1
        for leg, bps in zip(legs, base):
            leg.in_bps = bps
            leg.amount_in_wei = request.amount_in_wei * bps // 10_000

    def optimize(self, request: QuoteRequest) -> RoutePlan:
        warnings: List[str] = []
        if request.amount_in_wei <= 0:
            raise ValueError("amount_in must be positive")
        if request.amount_in_usd < MIN_CAPITAL_USD:
            warnings.append(
                f"amount_in_usd ${request.amount_in_usd:.2f} below minimum "
                f"${MIN_CAPITAL_USD:.2f}"
            )
        allowlisted = [v for v in self.venues.values()
                       if self.registry.is_allowlisted(v.venue_id)]
        if not allowlisted:
            raise VenueNotAllowlisted("no allowlisted venues available")

        # Candidate 0: best single venue (net). Always selected as the
        # fallback baseline, even when gas makes every net negative.
        best_net = float("-inf")
        best_alloc: List[Tuple[str, int]] = []
        for v in allowlisted:
            net, _, _ = self._net_out(request, [(v.venue_id, 10_000)])
            if net > best_net:
                best_net = net
                best_alloc = [(v.venue_id, 10_000)]

        # Candidate splits: 2-venue (5% grid) and 3-venue (10% grid).
        venue_ids = [v.venue_id for v in allowlisted]
        grids: List[List[Tuple[str, int]]] = []
        for a, b in combinations(venue_ids, 2):
            for p in range(500, 10_000, 500):
                grids.append([(a, p), (b, 10_000 - p)])
        if len(venue_ids) >= 3:
            for a, b, c in combinations(venue_ids, 3):
                for p in range(1_000, 9_000, 1_000):
                    for q in range(1_000, 10_000 - p, 1_000):
                        grids.append([(a, p), (b, q), (c, 10_000 - p - q)])
        for alloc in grids:
            if len(alloc) > MAX_SPLITS:
                continue
            net, _, _ = self._net_out(request, alloc)
            if net > best_net:
                best_net = net
                best_alloc = alloc

        net, legs, w2 = self._net_out(request, best_alloc)
        warnings.extend(w2)
        quoted_out = sum(l.quoted_out_wei for l in legs)
        min_out = quoted_out * (10_000 - request.slippage_bps) // 10_000
        fee_wei = request.amount_in_wei * FEE_BPS // 10_000

        mode = "dry_run"
        if EXECUTE_LIVE:
            if not LIVE_AUDITOR_GATE:
                raise LiveExecutionBlocked(
                    "EXECUTE_LIVE=1 but auditor gate not flipped"
                )
            mode = "live_intent"
            warnings.append("live_intent: signing/broadcast stays external")
        else:
            warnings.append("dry_run mode (set EXECUTE_LIVE=1 to emit live intents)")

        plan = RoutePlan(
            timestamp=time.time(),
            mode=mode,
            token_in=request.token_in,
            token_out=request.token_out,
            amount_in_wei=request.amount_in_wei,
            legs=legs,
            quoted_out_wei=quoted_out,
            min_out_wei=min_out,
            fee_bps=FEE_BPS,
            fee_wei=fee_wei,
            treasury=TREASURY,
            net_out_wei=net,
            warnings=warnings,
        )
        logger.info(
            "P16 route: %d legs net_out=%d min_out=%d mode=%s",
            len(legs), net, min_out, mode,
        )
        return plan


# -- TOA forecast reweight -------------------------------------------------------
def apply_forecast(
    plan: RoutePlan,
    forecast: Dict[str, float],
    venues: Dict[str, MockVenueAdapter],
    request: QuoteRequest,
) -> RoutePlan:
    """Reweight a route plan from a TOA slippage forecast.

    ``forecast`` maps venue_id -> predicted *extra* slippage as a fraction
    (e.g. 0.02 = 2% degradation). Legs on degraded venues shrink in
    proportion; freed allocation flows to the least-degraded leg. A
    degraded-forecast JSON must measurably shift weights (integration
    test target).
    """
    if not plan.legs:
        return plan
    degraded = {vid: max(0.0, f) for vid, f in forecast.items()}
    if not any(degraded.get(l.venue_id, 0.0) > 0 for l in plan.legs):
        return plan
    # Effective leg value after predicted degradation.
    values = [
        (l, l.quoted_out_wei * (1.0 - degraded.get(l.venue_id, 0.0)))
        for l in plan.legs
    ]
    total_value = sum(v for _, v in values) or 1.0
    new_bps = [int(round(v / total_value * 10_000)) for _, v in values]
    # Fix rounding to exactly 10_000 via largest remainder.
    diff = 10_000 - sum(new_bps)
    if diff:
        order = sorted(
            range(len(new_bps)),
            key=lambda i: (values[i][1] / total_value * 10_000) - new_bps[i],
            reverse=True,
        )
        for i in order[: abs(diff)]:
            new_bps[i] += 1 if diff > 0 else -1
    new_legs: List[RouteLeg] = []
    for leg, bps in zip(plan.legs, new_bps):
        if bps <= 0:
            continue
        leg_in = request.amount_in_wei * bps // 10_000
        q = venues[leg.venue_id].quote(leg_in)
        new_legs.append(
            RouteLeg(
                venue_id=leg.venue_id,
                in_bps=bps,
                amount_in_wei=leg_in,
                quoted_out_wei=q.amount_out_wei,
            )
        )
    SplitOptimizer._renormalize(request, new_legs)
    for l in new_legs:
        l.quoted_out_wei = venues[l.venue_id].quote(l.amount_in_wei).amount_out_wei
    quoted_out = sum(l.quoted_out_wei for l in new_legs)
    plan.legs = new_legs
    plan.quoted_out_wei = quoted_out
    plan.min_out_wei = quoted_out * (10_000 - request.slippage_bps) // 10_000
    plan.warnings.append(f"TOA reweight applied: {degraded}")
    return plan


# -- execution semantics ---------------------------------------------------------
def check_min_out(realized_out_wei: int, min_out_wei: int) -> None:
    """Gateway rule: the whole swap reverts if realized < committed minOut."""
    if realized_out_wei < min_out_wei:
        raise MinOutViolated(
            f"realized {realized_out_wei} < committed minOut {min_out_wei}"
        )


def simulate_execution(
    plan: RoutePlan,
    venues: Dict[str, MockVenueAdapter],
    failing_venues: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """Non-bricking settlement: a reverting venue returns its leg's input.

    Returns realized output, returned input, and per-leg outcomes. One bad
    venue degrades the route to fewer legs — never a stuck swap.
    """
    realized = 0
    returned_in = 0
    outcomes: List[Dict[str, Any]] = []
    for leg in plan.legs:
        if leg.venue_id in failing_venues:
            returned_in += leg.amount_in_wei
            outcomes.append(
                {"venue_id": leg.venue_id, "status": "reverted",
                 "returned_in_wei": leg.amount_in_wei}
            )
            continue
        q = venues[leg.venue_id].quote(leg.amount_in_wei)
        realized += q.amount_out_wei
        outcomes.append(
            {"venue_id": leg.venue_id, "status": "settled",
             "out_wei": q.amount_out_wei}
        )
    check_min_out(realized, plan.min_out_wei - returned_in_scaling(plan, returned_in))
    return {
        "realized_out_wei": realized,
        "returned_in_wei": returned_in,
        "legs": outcomes,
        "min_out_wei": plan.min_out_wei,
    }


def returned_in_scaling(plan: RoutePlan, returned_in_wei: int) -> int:
    """Pro-rata minOut relief for input returned by reverted legs."""
    if plan.amount_in_wei <= 0:
        return 0
    return plan.min_out_wei * returned_in_wei // plan.amount_in_wei


def wrap_unwrap_path(token_in: str, token_out: str) -> Dict[str, str]:
    """Native ETH wraps to WETH at gateway entry, unwraps at exit."""
    path = {"in": token_in, "out": token_out, "wrap": "none"}
    if token_in == "ETH":
        path["in"] = "WETH"
        path["wrap"] = "wrap_in"
    if token_out == "ETH":
        path["out"] = "WETH"
        path["wrap"] = "wrap_in_unwrap_out" if path["wrap"] == "wrap_in" else "unwrap_out"
    return path


# -- venue registry --------------------------------------------------------------
class VenueRegistry:
    """Timelocked venue allowlist. Add/remove take effect after 24h."""

    def __init__(self, now: Optional[float] = None):
        self._allowlisted = set(GENESIS_VENUES)
        self._pending: Dict[str, float] = {}  # venue_id -> executable_at
        self._pending_remove: Dict[str, float] = {}
        self._now = now if now is not None else time.time()

    def set_now(self, now: float) -> None:
        self._now = now

    def is_allowlisted(self, venue_id: str) -> bool:
        return venue_id in self._allowlisted

    def propose_add(self, venue_id: str) -> float:
        at = self._now + TIMELOCK_SECONDS
        self._pending[venue_id] = at
        return at

    def propose_remove(self, venue_id: str) -> float:
        at = self._now + TIMELOCK_SECONDS
        self._pending_remove[venue_id] = at
        return at

    def execute_add(self, venue_id: str) -> None:
        at = self._pending.get(venue_id)
        if at is None or self._now < at:
            raise AggregatorError("add timelock not elapsed")
        self._allowlisted.add(venue_id)
        del self._pending[venue_id]

    def execute_remove(self, venue_id: str) -> None:
        at = self._pending_remove.get(venue_id)
        if at is None or self._now < at:
            raise AggregatorError("remove timelock not elapsed")
        self._allowlisted.discard(venue_id)
        del self._pending_remove[venue_id]

    def require_allowlisted(self, venue_id: str) -> None:
        if not self.is_allowlisted(venue_id):
            raise VenueNotAllowlisted(f"venue {venue_id} not allowlisted")


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
    from .price_oracle import wiring_for
    return wiring_for("P16_DEX_AGG", oracle)
