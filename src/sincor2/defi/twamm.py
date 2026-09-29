"""
SINCOR DeFi P12 — TWAMM Large-Order Engine (slice-schedule reference model).

Pure-Python reference for time-weighted execution of large orders: a
parent order is split into a deterministic schedule of equal sub-orders
spread across blocks. Each sub-order's price impact is estimated with a
constant-product model and capped; if any slice would breach the impact
cap the schedule auto-extends (more, smaller slices) until every slice
is within the cap or the slice ceiling is hit.

Load-bearing invariants (mirror the catalog gates):
- slice_count: a parent order is never executed as a single slice —
  schedules below the minimum slice count are rejected outright.
- impact_cap: no sub-order may move the venue price more than the cap;
  the scheduler lengthens the schedule until the cap holds.
- The schedule is deterministic and fully known one block ahead:
  identical inputs always produce the identical slice list, and the sum
  of slices equals the parent order to the cent (integer remainder
  distributed to the earliest slices).
- Slicing never underperforms a single-block dump under constant-product
  math with per-slice arbitrage replenishment: total modeled slice
  output >= dump output (concavity of dx/(x+dx)). That is the economic
  point of TWAMM — smaller marginal impact per slice, better average
  execution.
- 8 bps of executed notional routes to the canonical Treasury.
- Simulation only: live_blocked=True. No venue calls, no order
  submission; the module produces schedules, never transactions.

Money is integer cents. Time/blocks are explicit parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (catalog ProtocolSpec 12, P12_TWAMM) --------
FEE_BPS = 8                    # 0.08% of executed notional -> Treasury
MIN_SLICES = 4                 # slice_count gate: never fewer than 4 slices
MAX_SLICES = 512               # schedule-extension ceiling
IMPACT_CAP_BPS = 50            # impact_cap gate: 0.50% max per-slice impact
MIN_CAPITAL_CENTS = 7_500_00   # $75 per tick
MAX_ALLOC_PCT = 0.40           # max 40% of tick capital per parent order


class SliceCountError(RuntimeError):
    """Requested schedule below the minimum slice count: rejected."""


class ImpactCapError(RuntimeError):
    """Impact cap cannot be satisfied within the slice ceiling."""


class LiveBlockedError(RuntimeError):
    """Order submission attempted: this module only produces schedules."""


def slice_impact_bps(slice_cents: int, reserve_cents: int) -> int:
    """
    Constant-product price impact of one slice, in bps, integer math:
    impact = dx / (x + dx), where x is the venue reserve in the input
    token and dx is the slice size. Conservative (ignores fee tier).
    """
    if slice_cents <= 0:
        return 0
    if reserve_cents <= 0:
        raise ValueError("reserve must be positive")
    return slice_cents * 10_000 // (reserve_cents + slice_cents)


def slice_output_cents(slice_cents: int, reserve_in_cents: int,
                       reserve_out_cents: int) -> int:
    """
    Constant-product output of one slice: dy = y * dx / (x + dx).
    Models per-slice arbitrage replenishment (each slice faces the full
    reserves again), which is what makes TWAMM beat a single dump.
    """
    if slice_cents <= 0:
        return 0
    if reserve_in_cents <= 0 or reserve_out_cents <= 0:
        raise ValueError("reserves must be positive")
    return reserve_out_cents * slice_cents // (reserve_in_cents + slice_cents)


@dataclass(frozen=True)
class SliceSchedule:
    parent_cents: int
    slices_cents: List[int]
    start_block: int
    reserve_in_cents: int
    reserve_out_cents: int
    max_slice_impact_bps: int
    total_output_cents: int     # modeled output across all slices
    dump_output_cents: int      # single-block output for the same size
    fee_cents: int
    fee_to: str = TREASURY

    @property
    def n_slices(self) -> int:
        return len(self.slices_cents)

    @property
    def output_beats_dump(self) -> bool:
        """
        The economic point of TWAMM: under constant-product math with
        per-slice replenishment, splitting never underperforms a dump.
        (f(dx) = dx/(x+dx) is concave with f(0)=0, so the sum of slice
        outputs >= the single-dump output.)
        """
        return self.total_output_cents >= self.dump_output_cents

    def slice_at(self, block: int) -> int:
        """The sub-order due at a block: known one block ahead, always."""
        idx = block - self.start_block
        if not 0 <= idx < len(self.slices_cents):
            raise IndexError(f"block {block} outside schedule window")
        return self.slices_cents[idx]


class SliceScheduler:
    """
    Builds deterministic slice schedules. Remainder cents from integer
    division go to the earliest slices so the schedule sums exactly to
    the parent order.
    """

    def __init__(self, min_slices: int = MIN_SLICES,
                 max_slices: int = MAX_SLICES,
                 impact_cap_bps: int = IMPACT_CAP_BPS) -> None:
        if min_slices < 2:
            raise ValueError("min_slices must be >= 2")
        self.min_slices = min_slices
        self.max_slices = max_slices
        self.impact_cap_bps = impact_cap_bps

    def _spread(self, parent_cents: int, n: int) -> List[int]:
        base, rem = divmod(parent_cents, n)
        return [base + (1 if i < rem else 0) for i in range(n)]

    def schedule(self, parent_cents: int, reserve_in_cents: int,
                 reserve_out_cents: int, start_block: int,
                 requested_slices: int = MIN_SLICES) -> SliceSchedule:
        if parent_cents < MIN_CAPITAL_CENTS:
            raise ValueError(
                f"parent {parent_cents}c below ${MIN_CAPITAL_CENTS // 100} floor")
        if requested_slices < self.min_slices:
            raise SliceCountError(
                f"requested {requested_slices} slices < minimum "
                f"{self.min_slices}: single-block dumps are rejected")
        if reserve_in_cents <= 0 or reserve_out_cents <= 0:
            raise ValueError("reserves must be positive")
        # Auto-extend: double the slice count until every slice is within
        # the impact cap, or the ceiling is hit (then refuse, fail-closed).
        n = requested_slices
        while n <= self.max_slices:
            slices = self._spread(parent_cents, n)
            worst = max(slice_impact_bps(s, reserve_in_cents) for s in slices)
            if worst <= self.impact_cap_bps:
                break
            n *= 2
        else:
            raise ImpactCapError(
                f"impact cap {self.impact_cap_bps}bps unsatisfiable within "
                f"{self.max_slices} slices")
        total_output = sum(slice_output_cents(s, reserve_in_cents,
                                              reserve_out_cents)
                           for s in slices)
        dump_output = slice_output_cents(parent_cents, reserve_in_cents,
                                         reserve_out_cents)
        fee = parent_cents * FEE_BPS // 10_000
        logger.info("TWAMM schedule: %dc over %d slices, worst impact %dbps",
                    parent_cents, n, worst)
        return SliceSchedule(parent_cents=parent_cents, slices_cents=slices,
                             start_block=start_block,
                             reserve_in_cents=reserve_in_cents,
                             reserve_out_cents=reserve_out_cents,
                             max_slice_impact_bps=worst,
                             total_output_cents=total_output,
                             dump_output_cents=dump_output,
                             fee_cents=fee)


class LiveOrderBlocker:
    """Schedules only: submitting a sub-order to a venue raises."""

    def submit(self, schedule: SliceSchedule, block: int) -> None:
        raise LiveBlockedError(
            "P12 produces slice schedules only (live_blocked=True): "
            "venue submission never leaves this module")
