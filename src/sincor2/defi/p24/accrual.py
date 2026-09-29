"""P24 revenue accrual: 7-day epochs, snapshot accounting, pull claims.

A holder's claimable amount for an epoch is fixed by their balance at the
epoch-start snapshot — late joiners cannot claim past epochs. Claims are
pull-based (the holder calls claim); individual payout failures never block
other claimants (per-payout isolation, the repo's non-bricking pattern).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Tuple

from . import PARAMS


@dataclass
class Epoch:
    index: int
    start_ts: float
    end_ts: float
    snapshot: Dict[str, int] = field(default_factory=dict)  # holder -> balance wei
    revenue_wei: int = 0
    claimed: Dict[str, int] = field(default_factory=dict)   # holder -> claimed wei


class AccrualError(Exception):
    """Accrual invariant violation."""


class RevenueAccrual:
    def __init__(self, epoch_s: float | None = None) -> None:
        self.epoch_s = epoch_s if epoch_s is not None else PARAMS["epoch_s"]
        # token symbol -> epochs
        self._epochs: Dict[str, List[Epoch]] = {}
        self._balances: Dict[str, Dict[str, int]] = {}

    def _epoch_index(self, ts: float) -> int:
        return int(ts // self.epoch_s)

    def _get_epoch(self, symbol: str, index: int, now: float) -> Epoch:
        epochs = self._epochs.setdefault(symbol, [])
        while len(epochs) <= index:
            i = len(epochs)
            epochs.append(Epoch(i, i * self.epoch_s, (i + 1) * self.epoch_s))
        return epochs[index]

    # -- balance tracking (the token ledger feeds this) -------------------
    def set_balance(self, symbol: str, holder: str, balance_wei: int) -> None:
        if balance_wei < 0:
            raise AccrualError("balance cannot be negative")
        self._balances.setdefault(symbol, {})[holder] = balance_wei

    # -- epoch lifecycle ----------------------------------------------------
    def snapshot_epoch(self, symbol: str, now: float | None = None) -> Epoch:
        """Snapshot balances at epoch start. Idempotent per epoch."""
        now = time.time() if now is None else now
        epoch = self._get_epoch(symbol, self._epoch_index(now), now)
        if not epoch.snapshot:
            epoch.snapshot = dict(self._balances.get(symbol, {}))
        return epoch

    def accrue_revenue(self, symbol: str, amount_wei: int,
                       now: float | None = None) -> Epoch:
        if amount_wei < 0:
            raise AccrualError("revenue cannot be negative")
        now = time.time() if now is None else now
        epoch = self.snapshot_epoch(symbol, now)
        epoch.revenue_wei += amount_wei
        return epoch

    def claimable(self, symbol: str, holder: str, epoch_index: int) -> int:
        epochs = self._epochs.get(symbol, [])
        if epoch_index >= len(epochs):
            return 0
        epoch = epochs[epoch_index]
        snap_total = sum(epoch.snapshot.values())
        if snap_total <= 0 or epoch.revenue_wei <= 0:
            return 0
        holder_snap = epoch.snapshot.get(holder, 0)
        gross = int(Fraction(epoch.revenue_wei)
                    * Fraction(holder_snap, snap_total))
        return gross - epoch.claimed.get(holder, 0)

    def claim(self, symbol: str, holder: str, epoch_index: int) -> int:
        """Pull claim. Payout isolation: failures here never affect others."""
        amount = self.claimable(symbol, holder, epoch_index)
        if amount <= 0:
            return 0
        epochs = self._epochs[symbol]
        epochs[epoch_index].claimed[holder] = (
            epochs[epoch_index].claimed.get(holder, 0) + amount)
        return amount
