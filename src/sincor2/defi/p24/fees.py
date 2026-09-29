"""P24 fee module: the treasury leg of every trade-fee split, wei-exact.

Each trade pays a 10% fee; :func:`split_fee` divides it
creator 4940 / platform 4940 / treasury 120 bps with dust to treasury.
This book accrues the treasury leg per token and settles to the canonical
treasury in batches of at least $10 to avoid dust transfers.

    treasury_leg_wei = floor(fee_wei * 120 / 10000) + dust_wei
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from ..catalog import TREASURY
from . import PARAMS
from .split import SplitResult, split_fee

USDC_DECIMALS = 6


@dataclass
class TreasuryAccrual:
    token: str
    fee_wei: int
    legs: SplitResult
    treasury_wei: int  # == legs.treasury_wei (floor(120 bps) + dust)


@dataclass
class TreasurySettlement:
    token: str
    amount_usdc_wei: int
    to: str


class TreasuryFeeBook:
    """Accrue the treasury leg of P24 trade fees; settle >= $10 to treasury."""

    def __init__(self) -> None:
        self._accrued: Dict[str, int] = {}  # token -> treasury wei
        self.settlements: List[TreasurySettlement] = []

    def accrue(self, token: str, fee_wei: int) -> TreasuryAccrual:
        if fee_wei < 0:
            raise ValueError("fee cannot be negative")
        legs = split_fee(fee_wei)
        self._accrued[token] = self._accrued.get(token, 0) + legs.treasury_wei
        return TreasuryAccrual(
            token=token, fee_wei=fee_wei, legs=legs,
            treasury_wei=legs.treasury_wei)

    def accrued(self, token: str) -> int:
        return self._accrued.get(token, 0)

    def settle(self, token: str) -> Optional[TreasurySettlement]:
        floor_wei = int(PARAMS["settlement_batch_floor_usd"]
                        * 10 ** USDC_DECIMALS)
        amount = self._accrued.get(token, 0)
        if amount < floor_wei:
            return None
        settlement = TreasurySettlement(token=token, amount_usdc_wei=amount,
                                        to=TREASURY)
        self.settlements.append(settlement)
        self._accrued[token] = 0
        return settlement
