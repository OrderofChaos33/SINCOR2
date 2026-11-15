"""Pure reference math for MoebiusX v3.1 — no I/O, no state.

SIZING LAW (v3.1, documented in the MoebiusX ops lineage / founder
order 2026-11-12): ::

    size = min(pool * 0.15, pool * 0.5 * confidence)
    capped at the $40 on-chain per-call ceiling
    floor  = max($5.00, pool * 0.12);  size < floor  ->  0 (infeasible)

IMPORTANT PROVENANCE NOTE: no in-repo Python module implements this
law. The in-repo ``sincor2.bankroll.Bankroll`` is Polyclaw accounting
with different constants (25% max position, 3x leverage). This module
is a faithful, testable transcription of the documented v3.1 law so
the repo can verify the math deterministically; the live enforcement
lives in the external ops kits' submit path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MAX_POOL_FRACTION = 0.15
CONFIDENCE_FRACTION = 0.5
PER_CALL_CEILING_USD = 40.00
FLOOR_FRACTION = 0.12
FLOOR_MIN_USD = 5.00


@dataclass(frozen=True)
class SizingResult:
    size_usdc: float
    floor_usdc: float
    feasible: bool


def trade_floor(pool_usdc: float) -> float:
    if pool_usdc < 0:
        raise ValueError("pool must be >= 0")
    return max(FLOOR_MIN_USD, pool_usdc * FLOOR_FRACTION)


def size_trade_v31(pool_usdc: float, confidence: float) -> SizingResult:
    """v3.1 sizing: Kelly+TOA as documented; 0 when below the floor."""
    if pool_usdc < 0:
        raise ValueError("pool must be >= 0")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    floor = trade_floor(pool_usdc)
    raw = min(pool_usdc * MAX_POOL_FRACTION,
              pool_usdc * CONFIDENCE_FRACTION * confidence)
    capped = min(raw, PER_CALL_CEILING_USD)
    # Round to cents like the ops kits' submit amounts.
    size = math.floor(capped * 100) / 100
    if size < floor:
        return SizingResult(size_usdc=0.0, floor_usdc=floor, feasible=False)
    return SizingResult(size_usdc=size, floor_usdc=floor, feasible=True)


def slippage_bps(expected_out: float, actual_out: float) -> float:
    """Realised slippage in bps relative to expected output."""
    if expected_out <= 0:
        raise ValueError("expected_out must be > 0")
    return (expected_out - actual_out) / expected_out * 10_000


def within_slippage(expected_out: float, actual_out: float,
                    max_slippage_bps: float) -> bool:
    """True iff realised slippage <= cap. Negative slippage (better
    than expected) always passes."""
    if max_slippage_bps < 0:
        raise ValueError("max_slippage_bps must be >= 0")
    return slippage_bps(expected_out, actual_out) <= max_slippage_bps


def constant_product_k(reserve_in: int, reserve_out: int) -> int:
    """The x*y invariant of a constant-product pool."""
    return reserve_in * reserve_out
