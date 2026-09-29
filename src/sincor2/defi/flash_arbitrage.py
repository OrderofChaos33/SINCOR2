"""
SINCOR DeFi P10 — Flash Loan Arbitrage Engine (scan-only reference model).

Pure-Python simulation of the opportunity scanner + gate pair + safety
dry-run + settlement accounting. No network calls, no keys, no chain.

OS RUNTIME CONTRACT (hard): this module is OPPORTUNITY-SCAN ONLY.
`EXECUTE_LIVE` does not enable flash loans here — any live execution path
reverts by construction (ScanOnlyGate). Scanning, filtering, TTL emission,
safety simulation, and settlement accounting all work; origination of a
real flash loan is impossible from this module.

Money is integer cents; prices/edges are floats (scan math only — every
money assertion is exact integer math).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (deep spec section 1) -------------------------
FEE_BPS = 30                       # 0.30% of net arb profit -> Treasury
PROFIT_FLOOR_CENTS = 5_000         # $50 absolute floor
PROFIT_FLOOR_PCT = 0.0015          # 0.15% of notional
GAS_CEILING_CENTS = 2_500          # $25 per attempt, hard
GAS_ESTIMATE_TOLERANCE = 0.15      # ±15% vs observed
MAX_SLIPPAGE_BPS_PER_LEG = 50
MAX_NOTIONAL_CENTS = 25_000_000    # $250k per attempt
MAX_ALLOC_PCT = 0.10
CANDIDATE_TTL_BLOCKS = 2
FLASH_PREMIUM_BPS = {"aave_v3": 5, "balancer": 0, "uniswap_v3": 30}


class LiveBlockedError(RuntimeError):
    """Any live execution path reverts while the scan-only gate is engaged."""


class SafetyAbort(Exception):
    """Safety dry-run rejected the candidate: no execution attempted."""


class CallbackTheftError(RuntimeError):
    """Flash-loan callback invoked by someone other than the bound provider."""


@dataclass(frozen=True)
class VenueQuote:
    venue: str
    pair: str            # e.g. "WETH/USDC"
    price: float         # quote currency per unit of base
    fee_bps: int         # venue swap fee per leg
    block_number: int


@dataclass(frozen=True)
class ArbCandidate:
    pair: str
    buy_venue: str
    sell_venue: str
    buy_price: float
    sell_price: float
    notional_cents: int
    gross_edge: float
    score: float
    flash_fee_cents: int
    gas_cost_cents: int
    slippage_cents: int
    net_cents: int
    min_out_buy_leg: int    # slippage-bounded calldata, committed at build
    min_out_sell_leg: int
    created_block: int
    ttl_blocks: int = CANDIDATE_TTL_BLOCKS

    def expired(self, at_block: int) -> bool:
        return at_block - self.created_block >= self.ttl_blocks


# -- scanner ---------------------------------------------------------------
class OpportunityScanner:
    """
    Venue polling (fixtures injected — no network) -> scored candidates.
    The emitter enforces the 2-block TTL; expired candidates are never
    emitted, not merely ignored downstream.
    """

    def __init__(self, flash_premium_bps: int = 5,
                 slippage_bps_per_leg: int = MAX_SLIPPAGE_BPS_PER_LEG) -> None:
        self.flash_premium_bps = flash_premium_bps
        self.slippage_bps_per_leg = slippage_bps_per_leg
        self._emitted: List[ArbCandidate] = []

    def scan(self, quotes: List[VenueQuote], notional_cents: int,
             block_number: int) -> List[ArbCandidate]:
        by_pair: Dict[str, List[VenueQuote]] = {}
        for q in quotes:
            by_pair.setdefault(q.pair, []).append(q)
        out: List[ArbCandidate] = []
        for pair, qs in by_pair.items():
            if len(qs) < 2:
                continue
            buy = min(qs, key=lambda q: q.price)
            sell = max(qs, key=lambda q: q.price)
            if buy.venue == sell.venue or sell.price <= buy.price:
                continue
            gross_edge = (sell.price - buy.price) / buy.price
            swap_fees = (buy.fee_bps + sell.fee_bps) / 10_000
            premium = self.flash_premium_bps / 10_000
            slippage = 2 * self.slippage_bps_per_leg / 10_000
            score = gross_edge - swap_fees - premium - slippage
            if score <= 0:
                continue
            # Single-sourced net (integer cents): every cost deducted exactly
            # once. Gas is unknown at scan time, so the floor filter subtracts
            # it later — nothing is ever subtracted twice.
            gross_cents = int(notional_cents * gross_edge)
            swap_fee_cents = (notional_cents
                              * (buy.fee_bps + sell.fee_bps) // 10_000)
            flash_fee = notional_cents * self.flash_premium_bps // 10_000
            slip_cents = notional_cents * 2 * self.slippage_bps_per_leg // 10_000
            net_cents = gross_cents - swap_fee_cents - flash_fee - slip_cents
            # minOut committed at build time from the observed quotes.
            # Units: centi-base (notional_cents / price), consistent with the
            # safety dry-run's comparison — scan math only.
            min_out_buy = int(notional_cents / buy.price
                              * (1 - self.slippage_bps_per_leg / 10_000))
            min_out_sell = int(notional_cents / sell.price
                               * (1 - self.slippage_bps_per_leg / 10_000))
            out.append(ArbCandidate(
                pair=pair, buy_venue=buy.venue, sell_venue=sell.venue,
                buy_price=buy.price, sell_price=sell.price,
                notional_cents=notional_cents, gross_edge=gross_edge,
                score=score, flash_fee_cents=flash_fee,
                gas_cost_cents=0, slippage_cents=slip_cents,
                net_cents=net_cents,
                min_out_buy_leg=min_out_buy, min_out_sell_leg=min_out_sell,
                created_block=block_number))
        self._emitted.extend(out)
        return out

    def actionable(self, at_block: int) -> List[ArbCandidate]:
        """Emitter-side TTL enforcement: expired candidates never leave."""
        live = [c for c in self._emitted if not c.expired(at_block)]
        self._emitted = live
        return list(live)


# -- profit floor ------------------------------------------------------------
@dataclass
class FloorDecision:
    passed: bool
    net_cents: int
    floor_cents: int
    reason: str


class ProfitFloorFilter:
    """net >= max($50, 0.15% of notional). Sub-floor candidates are dropped
    and every rejection is logged."""

    def __init__(self, floor_cents: int = PROFIT_FLOOR_CENTS,
                 floor_pct: float = PROFIT_FLOOR_PCT) -> None:
        self.floor_cents = floor_cents
        self.floor_pct = floor_pct
        self.rejections: List[Dict[str, object]] = []

    def floor_for(self, notional_cents: int) -> int:
        return max(self.floor_cents, int(notional_cents * self.floor_pct))

    def check(self, candidate: ArbCandidate,
              gas_cost_cents: int) -> FloorDecision:
        floor = self.floor_for(candidate.notional_cents)
        # candidate.net_cents already deducts swap fees, flash premium, and
        # slippage at scan time: only gas is subtracted here. Single-sourced,
        # never double-counted.
        net = candidate.net_cents - gas_cost_cents
        ok = net >= floor
        decision = FloorDecision(ok, net, floor,
                                 "pass" if ok else
                                 f"net {net}c < floor {floor}c")
        if not ok:
            self.rejections.append({"pair": candidate.pair,
                                    "net_cents": net,
                                    "floor_cents": floor})
            logger.info("profit-floor reject: %s net=%dc floor=%dc",
                        candidate.pair, net, floor)
        return decision


# -- gas ceiling ---------------------------------------------------------------
class GasCeilingEstimator:
    """Hard $25/attempt ceiling evaluated on estimated cost. The estimator
    must match observed gas within ±15% on fixtures or it is untrustworthy."""

    def __init__(self, ceiling_cents: int = GAS_CEILING_CENTS,
                 tolerance: float = GAS_ESTIMATE_TOLERANCE) -> None:
        self.ceiling_cents = ceiling_cents
        self.tolerance = tolerance
        self.rejections = 0

    def check(self, estimated_cost_cents: int) -> bool:
        ok = estimated_cost_cents <= self.ceiling_cents
        if not ok:
            self.rejections += 1
        return ok

    def accuracy_ok(self, estimated_units: int, observed_units: int) -> bool:
        if observed_units <= 0:
            return False
        return abs(estimated_units - observed_units) / observed_units <= self.tolerance


# -- execution safety ----------------------------------------------------------
class ExecutionSafety:
    """
    eth_call-style dry-run of the full calldata at the pending block:
    flash-loan callback + swaps + repayment. Aborts before any signature
    when simulated net <= 0 or slippage exceeds the committed bound.
    """

    def __init__(self, max_slippage_bps_per_leg: int = MAX_SLIPPAGE_BPS_PER_LEG) -> None:
        self.max_slippage_bps = max_slippage_bps_per_leg

    def dry_run(self, candidate: ArbCandidate, gas_cost_cents: int,
                observed_buy_price: float, observed_sell_price: float) -> int:
        """
        Returns simulated net cents. Raises SafetyAbort when the attempt
        must not proceed. minOut values are committed at build time: a
        manipulated quote (worse than minOut) aborts; an honest one passes.
        """
        buy_out = int(candidate.notional_cents / observed_buy_price)
        sell_out = int(candidate.notional_cents / observed_sell_price)
        if buy_out < candidate.min_out_buy_leg or sell_out < candidate.min_out_sell_leg:
            raise SafetyAbort("quote worse than committed minOut: abort")
        edge = (observed_sell_price - observed_buy_price) / observed_buy_price
        net = int(candidate.notional_cents * edge) - candidate.flash_fee_cents \
            - gas_cost_cents - candidate.slippage_cents
        if net <= 0:
            raise SafetyAbort(f"simulated net {net}c <= 0: abort")
        return net


# -- scan-only live-block gate ---------------------------------------------------
class ScanOnlyGate:
    """
    Security boundary, not a TODO. Default-deny: every live execution path
    raises. Scanning works. Gate state is queryable before any action.
    """

    def __init__(self) -> None:
        self._engaged = True

    def gate_state(self) -> Dict[str, object]:
        return {"scan_only": self._engaged,
                "execute_live_enabled": False,
                "note": "EXECUTE_LIVE does not enable flash loans in this module"}

    def execute(self, candidate: ArbCandidate) -> None:
        raise LiveBlockedError(
            "scan-only module: live flash-loan execution is blocked by the "
            "code-level gate (separately-gated founder release is out of scope)")


# -- callback verification -------------------------------------------------------
class CallbackVerifier:
    """The classic 'anyone can call executeOperation' theft vector, closed."""

    def __init__(self, provider: str) -> None:
        self.provider = provider.lower()

    def verify(self, caller: str) -> None:
        if caller.lower() != self.provider:
            raise CallbackTheftError(
                f"callback caller {caller} != bound provider {self.provider}")


# -- settlement (simulated atomic) -----------------------------------------------
@dataclass
class Settlement:
    candidate_pair: str
    notional_cents: int
    net_cents: int
    fee_cents: int
    fee_to: str = TREASURY
    reverted: bool = False


def simulate_settlement(net_cents: int, pair: str,
                        fee_transfer_ok: bool = True) -> Settlement:
    """
    Same-transaction fee capture model: 30 bps of net profit routes to
    Treasury. If the fee transfer fails, EVERYTHING reverts (atomic).
    """
    if net_cents <= 0:
        raise SafetyAbort("revert-on-loss: non-positive net never settles")
    fee_cents = net_cents * FEE_BPS // 10_000
    if not fee_transfer_ok:
        return Settlement(candidate_pair=pair, notional_cents=0,
                          net_cents=0, fee_cents=0, reverted=True)
    return Settlement(candidate_pair=pair, notional_cents=0,
                      net_cents=net_cents - fee_cents, fee_cents=fee_cents)


# -- self-improvement loop -------------------------------------------------------
@dataclass
class ParameterSet:
    version: int
    floor_cents: int
    floor_pct: float
    ttl_blocks: int
    gas_ceiling_cents: int
    venue_weights: Dict[str, float] = field(default_factory=dict)


class SelfImprovementLoop:
    """
    Versioned parameter sets with walk-forward backtest and exact rollback.
    Tunes on the train split, validates on the test split — never in-sample.
    """

    def __init__(self, initial: ParameterSet) -> None:
        self._history: List[ParameterSet] = [initial]
        self._results: Dict[int, Dict[str, float]] = {}

    @property
    def current(self) -> ParameterSet:
        return self._history[-1]

    def backtest(self, params: ParameterSet,
                 fixtures: List[Tuple[float, int]]) -> Dict[str, float]:
        """
        fixtures: list of (gross_edge, notional_cents). Simulates the
        floor filter over them; returns captured pnl stats.
        """
        filt = ProfitFloorFilter(floor_cents=params.floor_cents,
                                 floor_pct=params.floor_pct)
        captured = 0
        taken = 0
        for edge, notional in fixtures:
            floor = filt.floor_for(notional)
            gross = int(notional * edge)
            fee = notional * 5 // 10_000          # aave premium model
            gas = 1_200                            # $12 reference
            slip = notional * 2 * 50 // 10_000     # 50bps/leg model
            net = gross - fee - gas - slip
            if net >= floor and gas <= params.gas_ceiling_cents:
                captured += net
                taken += 1
        return {"captured_cents": float(captured), "taken": float(taken),
                "n": float(len(fixtures))}

    def walk_forward(self, fixtures: List[Tuple[float, int]],
                     candidates: List[ParameterSet]) -> ParameterSet:
        """Tune on first half, validate on second half. Returns the winner."""
        n = len(fixtures)
        train, test = fixtures[: n // 2], fixtures[n // 2:]
        scored = []
        for p in candidates:
            train_pnl = self.backtest(p, train)["captured_cents"]
            scored.append((train_pnl, p))
        scored.sort(key=lambda s: -s[0])
        winner = scored[0][1]
        test_pnl = self.backtest(winner, test)["captured_cents"]
        base_test_pnl = self.backtest(self.current, test)["captured_cents"]
        if test_pnl <= base_test_pnl:
            return self.current  # no overfit promotion
        new_version = ParameterSet(
            version=self.current.version + 1,
            floor_cents=winner.floor_cents, floor_pct=winner.floor_pct,
            ttl_blocks=winner.ttl_blocks,
            gas_ceiling_cents=winner.gas_ceiling_cents,
            venue_weights=dict(winner.venue_weights))
        self._history.append(new_version)
        self._results[new_version.version] = {
            "train_pnl": scored[0][0], "test_pnl": test_pnl,
            "base_test_pnl": base_test_pnl}
        return new_version

    def rollback(self) -> ParameterSet:
        """Restore the prior parameter set exactly."""
        if len(self._history) > 1:
            self._history.pop()
        return self.current

    def history(self) -> List[ParameterSet]:
        return list(self._history)
