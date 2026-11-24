"""P24 platform revenue-share book: the platform leg, accounted.

``split_fee`` divides every trade fee creator 4940 / platform 4940 /
treasury 120 bps, and ``fees.TreasuryFeeBook`` accrues the treasury leg
— but the platform leg (4940 bps of the fee, ~4.94% of trade value: the
SKU's "5% platform revenue share") accrued nowhere. There was no book,
no per-epoch statement, and therefore nothing to hand a revenue-share
counterparty. This module is that missing layer (P44 productization):

- :meth:`PlatformShareBook.accrue` books the platform leg per token
  per 7-day epoch, carving out the staking-rewards earmark
  (``staking_rewards_pct_of_platform_leg`` = 10% of the leg, per
  PARAMS) — the remainder is the share due under the revenue-share
  offer.
- :meth:`PlatformShareBook.statement` renders the counterparty-facing
  per-epoch statement: gross fees, platform leg, staking earmark,
  share due, settled, outstanding.
- :meth:`PlatformShareBook.settle` settles the outstanding share to the
  configured beneficiary in >= $10 batches (the same floor as the
  treasury book), attributing settlement oldest-epoch-first so
  statements stay exact.

Conservation: for every fee, creator + treasury (incl. dust) +
staking earmark + share due == fee, exactly — the split's structural
invariant extended by one exact carve-out.

Records-only, like the rest of P24: settlements are settlement
*records* in the module's LIVE-BLOCKED posture; no transfer is executed
here and no live intent is ever emitted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from . import PARAMS
from .split import SplitResult, split_fee

USDC_DECIMALS = 6


@dataclass
class PlatformAccrual:
    token: str
    fee_wei: int
    legs: SplitResult
    platform_wei: int   # == legs.platform_wei
    staking_wei: int    # staking-rewards earmark carved from the leg
    share_wei: int      # platform_wei - staking_wei: the share due
    epoch_index: int


@dataclass
class PlatformSettlement:
    token: str
    amount_usdc_wei: int
    to: str


@dataclass
class _EpochBook:
    gross_fees_wei: int = 0
    platform_wei: int = 0
    staking_wei: int = 0
    share_wei: int = 0
    settled_wei: int = 0


@dataclass(frozen=True)
class PlatformStatement:
    token: str
    epoch_index: int
    epoch_start_s: float
    epoch_end_s: float
    gross_fees_wei: int
    platform_leg_wei: int
    staking_earmark_wei: int
    share_due_wei: int
    settled_wei: int

    @property
    def outstanding_wei(self) -> int:
        return self.share_due_wei - self.settled_wei


class PlatformShareBook:
    """Accrue the platform leg of P24 trade fees; state and settle the share."""

    def __init__(self, beneficiary: str, epoch_s: float | None = None) -> None:
        if not beneficiary:
            raise ValueError("a revenue-share beneficiary is required")
        self.beneficiary = beneficiary
        self.epoch_s = epoch_s if epoch_s is not None else PARAMS["epoch_s"]
        self._epochs: Dict[Tuple[str, int], _EpochBook] = {}
        self._outstanding: Dict[str, int] = {}
        self.settlements: List[PlatformSettlement] = []

    def _epoch_index(self, ts: float) -> int:
        return int(ts // self.epoch_s)

    def accrue(
        self, token: str, fee_wei: int, now: float | None = None
    ) -> PlatformAccrual:
        if fee_wei < 0:
            raise ValueError("fee cannot be negative")
        now = time.time() if now is None else now
        legs = split_fee(fee_wei)
        staking = int(
            Fraction(legs.platform_wei)
            * Fraction(str(PARAMS["staking_rewards_pct_of_platform_leg"]))
        )
        share = legs.platform_wei - staking
        idx = self._epoch_index(now)
        book = self._epochs.setdefault((token, idx), _EpochBook())
        book.gross_fees_wei += fee_wei
        book.platform_wei += legs.platform_wei
        book.staking_wei += staking
        book.share_wei += share
        self._outstanding[token] = self._outstanding.get(token, 0) + share
        return PlatformAccrual(
            token=token,
            fee_wei=fee_wei,
            legs=legs,
            platform_wei=legs.platform_wei,
            staking_wei=staking,
            share_wei=share,
            epoch_index=idx,
        )

    def outstanding(self, token: str) -> int:
        return self._outstanding.get(token, 0)

    def statement(self, token: str, epoch_index: int) -> PlatformStatement:
        book = self._epochs.get((token, epoch_index), _EpochBook())
        return PlatformStatement(
            token=token,
            epoch_index=epoch_index,
            epoch_start_s=epoch_index * self.epoch_s,
            epoch_end_s=(epoch_index + 1) * self.epoch_s,
            gross_fees_wei=book.gross_fees_wei,
            platform_leg_wei=book.platform_wei,
            staking_earmark_wei=book.staking_wei,
            share_due_wei=book.share_wei,
            settled_wei=book.settled_wei,
        )

    def settle(self, token: str) -> Optional[PlatformSettlement]:
        floor_wei = int(PARAMS["settlement_batch_floor_usd"] * 10 ** USDC_DECIMALS)
        amount = self._outstanding.get(token, 0)
        if amount < floor_wei:
            return None
        # Attribute the settlement oldest-epoch-first so per-epoch
        # statements reconcile exactly with what was paid.
        remaining = amount
        for (tok, _idx), book in sorted(self._epochs.items()):
            if remaining <= 0:
                break
            if tok != token:
                continue
            due = book.share_wei - book.settled_wei
            paid = min(due, remaining)
            book.settled_wei += paid
            remaining -= paid
        settlement = PlatformSettlement(
            token=token, amount_usdc_wei=amount, to=self.beneficiary
        )
        self.settlements.append(settlement)
        self._outstanding[token] = 0
        return settlement
