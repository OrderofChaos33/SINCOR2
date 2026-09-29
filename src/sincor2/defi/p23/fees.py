"""P23 fee module: 20 bps on realized per-pool yield per epoch.

Yield per epoch = max(0, NAV_end - NAV_start) per collection pool, measured
from the vault's epoch snapshots. fee = floor(yield_wei * 20 / 10000),
settled (conceptually) to the canonical treasury with per-pool accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..catalog import TREASURY
from . import PARAMS


@dataclass
class PoolFee:
    collection: str
    epoch_start_ts: float
    epoch_end_ts: float
    yield_wei: int
    fee_wei: int
    to: str


class PoolFeeLedger:
    def __init__(self, fee_bps: int | None = None) -> None:
        self.fee_bps = PARAMS["fee_bps"] if fee_bps is None else fee_bps
        self.fees: List[PoolFee] = []

    def settle_epoch(
        self,
        collection: str,
        nav_start_wei: int,
        nav_end_wei: int,
        epoch_start_ts: float,
        epoch_end_ts: float,
    ) -> PoolFee:
        realized = nav_end_wei - nav_start_wei
        if realized < 0:
            realized = 0  # losses are not negative yield; no fee, no rebate
        fee_wei = (realized * self.fee_bps) // 10_000
        record = PoolFee(collection, epoch_start_ts, epoch_end_ts,
                         realized, fee_wei, TREASURY)
        self.fees.append(record)
        return record

    def total_for(self, collection: str) -> int:
        return sum(f.fee_wei for f in self.fees if f.collection == collection)
