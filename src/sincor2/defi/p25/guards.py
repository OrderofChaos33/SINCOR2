"""P25 guards: cash-floor enforcement on every allocation and rebalance plan."""

from __future__ import annotations

from typing import Mapping

from . import PARAMS
from .ingestor import CASH_ID


class CashFloorBreach(Exception):
    """Raised when a plan would leave the USDC cash sleeve below the floor."""

    def __init__(self, cash_pct: float, floor: float):
        super().__init__(
            f"cash floor breached: {cash_pct:.6f} < {floor:.2f}"
        )
        self.cash_pct = cash_pct
        self.floor = floor


def cash_pct_of(weights: Mapping[str, float]) -> float:
    return float(weights.get(CASH_ID, 0.0))


def require_cash_floor(
    weights: Mapping[str, float],
    floor: float | None = None,
) -> float:
    """Return cash pct, or raise CashFloorBreach. Every plan passes through here."""
    floor = PARAMS["cash_floor_pct"] if floor is None else floor
    cash = cash_pct_of(weights)
    if cash + 1e-12 < floor:
        raise CashFloorBreach(cash, floor)
    return cash
