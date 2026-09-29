"""P22 fee module: 10 bps on *realized* yield only, wei-exact.

Yield is measured as the delta of vault share value (USDC terms) against a
per-user, per-venue principal baseline, which is (re)set at each deposit or
rebalance. Fees accrue only when the measured value exceeds the baseline —
never on unrealized TWAP marks.

    fee_wei = floor(realized_yield_wei * fee_bps / 10000)

Fees accumulate in a per-user ledger and settle to the canonical treasury in
batches of at least $10 to avoid dust transfers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..catalog import TREASURY
from . import PARAMS

USDC_DECIMALS = 6


@dataclass
class Settlement:
    user_id: str
    amount_usdc_wei: int  # 6-decimal USDC wei
    to: str


class FeeLedger:
    def __init__(self, fee_bps: int | None = None) -> None:
        self.fee_bps = PARAMS["fee_bps"] if fee_bps is None else fee_bps
        self._baselines: Dict[Tuple[str, str], int] = {}   # (user, venue) -> wei
        self._accrued: Dict[str, int] = {}                 # user -> wei
        self.settlements: List[Settlement] = []

    def set_baseline(self, user_id: str, venue_id: str, value_wei: int) -> None:
        """(Re)set the principal baseline — called on deposit/rebalance."""
        if value_wei < 0:
            raise ValueError("baseline cannot be negative")
        self._baselines[(user_id, venue_id)] = value_wei

    def accrue(self, user_id: str, venue_id: str, share_value_wei: int) -> int:
        """Accrue the fee on realized yield since the baseline. Returns fee wei."""
        baseline = self._baselines.get((user_id, venue_id))
        if baseline is None:
            raise KeyError(
                f"no principal baseline for {user_id}@{venue_id}; "
                "set_baseline must run at deposit/rebalance"
            )
        realized = share_value_wei - baseline
        if realized <= 0:
            return 0
        fee_wei = (realized * self.fee_bps) // 10_000
        self._accrued[user_id] = self._accrued.get(user_id, 0) + fee_wei
        # Ratchet the baseline up by the *gross* realized amount so the same
        # yield is never fee'd twice. (Net-of-fee baseline would double-count.)
        self._baselines[(user_id, venue_id)] = share_value_wei
        return fee_wei

    def accrued(self, user_id: str) -> int:
        return self._accrued.get(user_id, 0)

    def settle(self, user_id: str) -> Settlement | None:
        """Settle when the batch is worth >= $10. Always to the treasury."""
        floor_wei = int(PARAMS["settlement_batch_floor_usd"] * 10 ** USDC_DECIMALS)
        amount = self._accrued.get(user_id, 0)
        if amount < floor_wei:
            return None
        settlement = Settlement(user_id=user_id, amount_usdc_wei=amount, to=TREASURY)
        self.settlements.append(settlement)
        self._accrued[user_id] = 0
        return settlement
