"""
SINCOR DeFi P11 — Delta-Neutral Yield (LST carry vs perp funding).

Pure-Python reference for the neutral-yield loop: deposit LSTs as
collateral, borrow the underlying native asset against them, and open an
exact matching short on a perps venue so the combined position is
delta-neutral while collecting the net yield premium
(staking carry + perp funding income - borrow cost).

Load-bearing invariants (mirror the catalog gates):
- basis_sign: the position exists only while the all-in basis is
  non-negative. Basis = staking_apr - borrow_apr + funding_apr (rates on
  deployed notional). If basis < 0 the position UNWINDS.
- funding_flip_kill: an adverse funding-rate flip beyond the threshold
  kills the position immediately, even if the blended basis is still
  positive — a flipped funding regime is a different trade.
- Delta neutrality is exact: the perp short notional equals the LST
  notional (in native units), so |net delta| == 0 by construction.
- Borrow leg respects a max LTV with a liquidation buffer; the sizer
  refuses leverage that would breach it.
- 12 bps of positive net yield routes to the canonical Treasury.
- Simulation only: live_blocked=True. The module models venues; it never
  calls one.

Money is integer cents; native amounts are integer base units (wei-style
ints) so delta math is exact. Time is an explicit parameter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (catalog ProtocolSpec 11, P11_DELTA_NEUTRAL) ---
FEE_BPS = 12                    # 0.12% of positive net yield -> Treasury
TARGET_APR = 0.09
MIN_CAPITAL_CENTS = 15_000_00   # $150 per tick
MAX_ALLOC_PCT = 0.25            # max 25% of tick capital per position
MAX_LTV = 0.60                  # borrow leg capped at 60% LTV
LIQ_BUFFER = 0.15               # 15pp safety margin under the venue liq LTV
FUNDING_FLIP_KILL_BPS = 200     # adverse funding move > 2.00% APR kills
SECONDS_PER_YEAR = 365 * 86400


class BasisNegativeError(RuntimeError):
    """Basis went negative: the position must unwind."""


class FundingFlipKill(Exception):
    """Adverse funding flip beyond the kill threshold: unwind now."""


class LTVBreachError(RuntimeError):
    """Borrow sizing would breach the max LTV: refused."""


class LiveBlockedError(RuntimeError):
    """A live venue call was attempted: blocked, simulation only."""


# -- venue models (fixture-injected, no network) -------------------------------
@dataclass(frozen=True)
class MarketState:
    """One coherent market snapshot for all three legs."""
    eth_price_cents: int        # ETH price in cents
    staking_apr: float          # LST staking carry, e.g. 0.035
    borrow_apr: float           # cost of borrowing native vs LST collateral
    funding_apr: float          # perp funding APR, + means shorts are paid
    venue_liq_ltv: float = 0.80  # venue liquidation LTV


# -- position --------------------------------------------------------------------
@dataclass
class DeltaNeutralPosition:
    lst_native_units: int       # LST holding, native base units
    short_native_units: int     # perp short, native base units (== lst leg)
    borrowed_native_units: int  # native borrowed against the LST
    opened_at: int
    entry: MarketState = field(repr=False)
    unwound: bool = False
    unwind_reason: str = ""

    @property
    def net_delta_units(self) -> int:
        """+1 per LST unit, -1 per short unit: exact by construction."""
        return self.lst_native_units - self.short_native_units


class HedgeSizer:
    """Sizes the three legs from a capital allocation."""

    def __init__(self, max_ltv: float = MAX_LTV) -> None:
        if not 0 < max_ltv < 1:
            raise ValueError("max_ltv must be in (0, 1)")
        self.max_ltv = max_ltv

    def size(self, capital_cents: int, market: MarketState,
             now: int) -> DeltaNeutralPosition:
        if capital_cents < MIN_CAPITAL_CENTS:
            raise ValueError(
                f"capital {capital_cents}c below ${MIN_CAPITAL_CENTS // 100} floor")
        if market.eth_price_cents <= 0:
            raise ValueError("ETH price must be positive")
        if self.max_ltv + LIQ_BUFFER > market.venue_liq_ltv:
            raise LTVBreachError(
                f"max LTV {self.max_ltv} + buffer {LIQ_BUFFER} breaches venue "
                f"liquidation LTV {market.venue_liq_ltv}")
        # Whole position valued in native units: capital buys LST 1:1.
        lst_units = capital_cents * 10**18 // market.eth_price_cents
        borrowed_units = int(lst_units * self.max_ltv)
        # Exact matching short: delta-neutral by construction.
        return DeltaNeutralPosition(
            lst_native_units=lst_units,
            short_native_units=lst_units,
            borrowed_native_units=borrowed_units,
            opened_at=now, entry=market)


# -- basis + funding monitor ---------------------------------------------------------
@dataclass(frozen=True)
class BasisReading:
    basis_apr: float            # staking - borrow + funding
    funding_apr: float
    funding_flip_bps: int       # adverse move vs entry, in bps of APR
    unwind: bool
    reason: str


class BasisMonitor:
    """
    basis_sign gate: unwind when the all-in basis is negative.
    funding_flip_kill gate: unwind when funding moves against the short
    beyond FUNDING_FLIP_KILL_BPS, even if blended basis is still positive.
    """

    def __init__(self, flip_kill_bps: int = FUNDING_FLIP_KILL_BPS) -> None:
        self.flip_kill_bps = flip_kill_bps

    @staticmethod
    def basis_apr(market: MarketState) -> float:
        return market.staking_apr - market.borrow_apr + market.funding_apr

    def check(self, position: DeltaNeutralPosition,
              market: MarketState) -> BasisReading:
        basis = self.basis_apr(market)
        # Adverse funding move vs entry (shorts want funding >= entry level).
        flip_bps = int(round((position.entry.funding_apr - market.funding_apr)
                             * 10_000))
        if flip_bps > self.flip_kill_bps:
            return BasisReading(basis, market.funding_apr, flip_bps, True,
                                f"funding flipped {flip_bps}bps adverse: kill")
        if basis < 0:
            return BasisReading(basis, market.funding_apr, flip_bps, True,
                                f"basis negative ({basis:.4f}): unwind")
        return BasisReading(basis, market.funding_apr, flip_bps, False, "ok")


# -- unwind + settlement ---------------------------------------------------------------
@dataclass
class UnwindResult:
    principal_cents: int
    gross_yield_cents: int
    fee_cents: int
    net_cents: int
    reason: str
    fee_to: str = TREASURY


def accrue_cents(principal_cents: int, apr: float,
                 elapsed_s: int) -> int:
    """Simple pro-rata accrual, integer cents, floor toward zero."""
    return int(principal_cents * apr * elapsed_s / SECONDS_PER_YEAR)


class PositionManager:
    """Owns the lifecycle: monitor -> unwind -> settle with fee routing."""

    def __init__(self, monitor: Optional[BasisMonitor] = None) -> None:
        self.monitor = monitor or BasisMonitor()
        self.settlements: List[UnwindResult] = []

    def maybe_unwind(self, position: DeltaNeutralPosition,
                     market: MarketState, now: int) -> Optional[UnwindResult]:
        if position.unwound:
            return None
        reading = self.monitor.check(position, market)
        if not reading.unwind:
            return None
        return self.unwind(position, market, now, reading.reason)

    def unwind(self, position: DeltaNeutralPosition, market: MarketState,
               now: int, reason: str) -> UnwindResult:
        if position.unwound:
            raise ValueError("position already unwound")
        elapsed = max(0, now - position.opened_at)
        # Principal: the LST leg's price P&L is cancelled exactly by the
        # equal-and-opposite perp short, so net principal is the ENTRY
        # value — valuing at the current price without the short leg's
        # offset would fake a directional exposure the position does not
        # have.
        principal_cents = (position.lst_native_units
                           * position.entry.eth_price_cents // 10**18)
        staking = accrue_cents(principal_cents, market.staking_apr, elapsed)
        funding = accrue_cents(principal_cents, market.funding_apr, elapsed)
        borrow_cost = accrue_cents(
            position.borrowed_native_units * market.eth_price_cents // 10**18,
            market.borrow_apr, elapsed)
        gross_yield = staking + funding - borrow_cost
        fee = gross_yield * FEE_BPS // 10_000 if gross_yield > 0 else 0
        net = gross_yield - fee
        position.unwound = True
        position.unwind_reason = reason
        result = UnwindResult(principal_cents=principal_cents,
                              gross_yield_cents=gross_yield,
                              fee_cents=fee, net_cents=net,
                              reason=reason)
        self.settlements.append(result)
        logger.info("unwound: %s principal=%dc net_yield=%dc",
                    reason, principal_cents, net)
        return result


# -- live-block gate ---------------------------------------------------------------------
class LiveIntentBlocker:
    """Simulation only: any live venue intent raises, fail-closed."""

    def place_live_orders(self, position: DeltaNeutralPosition) -> None:
        raise LiveBlockedError(
            "P11 is simulation-only (live_blocked=True): venue orders never "
            "leave this module")
